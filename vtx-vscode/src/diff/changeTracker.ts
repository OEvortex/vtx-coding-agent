import * as vscode from "vscode";
import * as crypto from "crypto";
import * as fs from "fs";
import * as path from "path";

interface Snapshot {
  // The file as it was named when it was first recorded. The map is keyed on a
  // normalised form of it (see `key`), so this is the one to show and write to.
  path: string;
  original: string | null; // null means the file did not exist before the session
  // What the agent left behind: a hash of the text it reported, and the file's
  // own timestamp and size just after. An undo writes `original` over whatever is
  // there now, so these are how it tells a file still holding the agent's edit
  // from one the user has worked on since, and asks before discarding that work.
  // A hash rather than the text, since the store already carries every original
  // and this only ever answers yes or no.
  agentHash?: string;
  agentStat?: { mtimeMs: number; size: number };
  // Sessions that have edited this file. The original content belongs to the
  // file, but the working set is per session: each chat shows what it changed,
  // and reopening one gets its own files back rather than the last chat's.
  sessions: Set<string>;
  // Kept or undone: out of the working set, but the original text is still held
  // so an open diff keeps rendering against it. Dropping it outright made the
  // left hand side resolve to empty, which drew the whole file as newly added.
  resolved?: boolean;
  // Lines the change added and removed, so the working set can say so without
  // waiting for the edit to be reported again.
  added?: number;
  removed?: number;
}

// One file as it is written to disk, so a window reload does not forget what is
// waiting to be reviewed.
interface StoredSnapshot {
  path: string;
  original: string | null;
  agentHash?: string;
  agentStat?: { mtimeMs: number; size: number };
  sessions: string[];
  resolved?: boolean;
  added?: number;
  removed?: number;
}

// Past this the working set is not written out. A handful of edited files is
// kilobytes; a repository's worth of generated output is not worth carrying
// across a reload.
const MAX_STORE_BYTES = 8 * 1024 * 1024;

// The same file arrives written two ways: as the agent wrote it, and as VS Code
// hands it back from a URI (which lower cases a Windows drive letter and always
// uses backslashes). One key for both, so a lookup finds what was recorded.
// One file, one entry, whatever it was called on the way in. Two spellings of the
// same file made two working set rows and two originals, and undoing them in order
// wrote the older text over the newer content.
const realNames = new Map<string, string>();

// Line endings and a byte order mark are not the user's work: the agent reports
// the content it meant to write, and what lands on disk can differ in both (see
// the CRLF and BOM handling in the write path), so hashing them in would call
// every file changed and ask about an undo nobody needs to be asked about.
function contentHash(text: string): string {
  return crypto.createHash("sha1").update(text.replace(/^\uFEFF/, "").replace(/\r\n/g, "\n")).digest("hex");
}

function statOf(fsPath: string): { mtimeMs: number; size: number } | undefined {
  try {
    const s = fs.statSync(fsPath);
    return { mtimeMs: s.mtimeMs, size: s.size };
  } catch {
    return undefined;
  }
}

function key(fsPath: string): string {
  const resolved = path.resolve(fsPath);
  const known = realNames.get(resolved);
  if (known) {
    return known;
  }
  let real = resolved;
  let answered = false;
  try {
    // What the filesystem itself calls it: the on-disk spelling on a case
    // insensitive volume, which macOS is by default, and one name for a file reached
    // through a symlinked directory.
    real = fs.realpathSync.native(resolved);
    answered = true;
  } catch {
    // Not there, so the resolved path is the best name available.
  }
  const folded = process.platform === "win32" ? real.toLowerCase() : real;
  // The finished key is what gets cached, not the step before it: caching the
  // unfolded path meant the second lookup of a file answered differently from the
  // first, so on Windows an undo went looking for a snapshot that was filed under
  // another name and quietly did nothing. Only cached once the filesystem answered,
  // so a file that does not exist yet is not pinned to the name it was guessed by.
  if (answered && realNames.size < 2000) {
    realNames.set(resolved, folded);
  }
  return folded;
}

// Tracks agent file edits so the user can review them as native diffs and
// accept or reject them. Uses a QuickDiffProvider so the editor shows gutter
// change markers with inline per-hunk "Revert Change" actions for free, plus a
// Source Control group that acts as the working set (accept/reject per file).
export class ChangeTracker
  implements vscode.TextDocumentContentProvider, vscode.QuickDiffProvider
{
  static readonly scheme = "vtx-original";
  // Either side of a single edit, so an edit row can show just what it did.
  static readonly editScheme = "vtx-edit";

  private readonly snapshots = new Map<string, Snapshot>();
  private readonly spilledResolved = new Map<string, string>();
  private resolvedBytes = 0;
  private readonly contentChanged = new vscode.EventEmitter<vscode.Uri>();
  private readonly listChanged = new vscode.EventEmitter<string[]>();
  // A change was kept or undone, wherever from: the chat, the Source Control view
  // or a command. The chat listens so the edit it drew says so, whichever surface
  // resolved it.
  private readonly resolved = new vscode.EventEmitter<{ paths: string[]; action: "accept" | "reject" }>();

  readonly onDidChange = this.contentChanged.event;
  readonly onDidChangeList = this.listChanged.event;
  readonly onDidResolve = this.resolved.event;

  private scm?: vscode.SourceControl;
  private group?: vscode.SourceControlResourceGroup;

  // Where the working set is kept between windows. A `vtx acp` agent cannot
  // outlive the extension host, but a change waiting to be reviewed has nothing to
  // do with the agent: forgetting it on a reload lost the diffs and left Keep and
  // Undo with nothing to act on, so the next edit looked like a brand new set.
  private store?: vscode.Uri;
  private spillDir?: string;
  // Originals being resolved right now, so two clicks do not race each other to
  // create the same model.
  private readonly warming = new Map<string, Promise<void>>();
  private saveTimer?: NodeJS.Timeout;
  private saving?: Promise<void>;

  async useStore(dir: vscode.Uri | undefined): Promise<void> {
    if (!dir) {
      return;
    }
    this.store = vscode.Uri.joinPath(dir, "changes.json");
    this.removeStaleSpills(dir.fsPath);
    this.spillDir = path.join(dir.fsPath, "resolved-originals", `${process.pid}-${crypto.randomBytes(8).toString("hex")}`);
    try {
      const raw = Buffer.from(await vscode.workspace.fs.readFile(this.store)).toString("utf8");
      const parsed = JSON.parse(raw) as StoredSnapshot[];
      for (const s of Array.isArray(parsed) ? parsed : []) {
        // A file that has since gone is nothing to review, and a change with no
        // session behind it can never be shown in a chat.
        if (!s || !s.path || !Array.isArray(s.sessions) || !s.sessions.length) {
          continue;
        }
        if (s.original !== null && !fs.existsSync(s.path)) {
          continue;
        }
        this.snapshots.set(key(s.path), {
          path: s.path,
          original: s.original ?? null,
          agentHash: s.agentHash,
          agentStat: s.agentStat,
          sessions: new Set(s.sessions),
          resolved: s.resolved,
          added: s.added,
          removed: s.removed
        });
      }
    } catch {
      // Nothing kept, or it is unreadable: start with an empty working set.
    }
    this.refreshGroup();
  }

  // Write the working set out now rather than in 400ms. The window is small and
  // it is exactly the one a reload lands in: a Keep pressed just before one was
  // never written, so the next window brought the change back as still pending,
  // holding text older than the file it would put back.
  async flush(): Promise<void> {
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
      this.saveTimer = undefined;
    }
    await this.save();
  }

  dispose(): void {
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
      this.saveTimer = undefined;
    }
    this.contentChanged.dispose();
    this.listChanged.dispose();
    this.resolved.dispose();
    for (const file of this.spilledResolved.values()) {
      try { fs.unlinkSync(file); } catch {}
    }
    this.spilledResolved.clear();
  }

  private scheduleSave(): void {
    if (!this.store || this.saveTimer) {
      return;
    }
    this.saveTimer = setTimeout(() => {
      this.saveTimer = undefined;
      void this.save();
    }, 400);
    this.saveTimer.unref?.();
  }

  // One at a time. Both saves of one window shared a scratch file, and a save takes
  // as long as writing every held original takes, so the next one could start while
  // it was running: one write landed inside the other's file, a half written one was
  // renamed into place, and an unparseable store is dropped whole.
  private async save(): Promise<void> {
    // The catch is what keeps the chain usable: a chain of `then` alone carries a
    // rejection forward for ever, so one failed link would leave the working set
    // unsaved for the rest of the window, silently, which is the loss the chaining was
    // added to prevent.
    this.saving = (this.saving ?? Promise.resolve()).then(() => this.writeStore()).catch(() => undefined);
    return this.saving;
  }

  private async writeStore(): Promise<void> {
    if (!this.store) {
      return;
    }
    const out: StoredSnapshot[] = [...this.snapshots.values()]
      .filter((s) => !s.resolved)
      .map((s) => ({
        path: s.path,
        original: s.original,
        agentHash: s.agentHash,
        agentStat: s.agentStat,
        sessions: [...s.sessions],
        added: s.added,
        removed: s.removed
      }));
    try {
      // The cap is on the whole file, so the biggest originals are what has to go when
      // it is exceeded, one at a time. Dropping the lot instead meant a single generated
      // file the agent rewrote took every other original with it, and a reload came back
      // with nothing to undo. Measured by the text each one holds rather than by
      // encoding the whole set again per drop, which is quadratic in the bytes and would
      // be paid on every save from then on. Inside the try, because stringifying a very
      // large set can throw, and a save that rejects poisons the chain it runs in.
      let held = out.reduce((n, s) => n + (s.original?.length ?? 0), 0);
      while (out.length && held > MAX_STORE_BYTES) {
        let biggest = 0;
        for (let i = 1; i < out.length; i++) {
          if ((out[i].original?.length ?? 0) > (out[biggest].original?.length ?? 0)) {
            biggest = i;
          }
        }
        held -= out[biggest].original?.length ?? 0;
        out.splice(biggest, 1);
      }
      if (!out.length) {
        await vscode.workspace.fs.delete(this.store);
        return;
      }
      const body = Buffer.from(JSON.stringify(out), "utf8");
      if (body.byteLength > MAX_STORE_BYTES) {
        // What is left still does not fit, so there is nothing to keep, and a stale file
        // would restore older originals than the ones held now.
        await vscode.workspace.fs.delete(this.store);
        return;
      }
      await vscode.workspace.fs.createDirectory(vscode.Uri.joinPath(this.store, ".."));
      // Beside it, then over it: this file is the only copy of the text every
      // pending undo would restore, so a half written one loses all of them.
      // Named for this process: two windows on one workspace share the storage
      // directory, and a shared scratch name lets one window's write land inside
      // another's, renaming a corrupt file into place and losing every original.
      const tmp = this.store.with({ path: `${this.store.path}.${process.pid}.tmp` });
      await vscode.workspace.fs.writeFile(tmp, body);
      await vscode.workspace.fs.rename(tmp, this.store, { overwrite: true });
    } catch {
      // Nothing to delete, or the storage is not writable: the working set is
      // still correct in this window, it just will not survive a reload.
    }
  }

  register(): vscode.Disposable {
    const disposables: vscode.Disposable[] = [];
    disposables.push(
      vscode.workspace.registerTextDocumentContentProvider(ChangeTracker.scheme, this),
      vscode.workspace.registerTextDocumentContentProvider(ChangeTracker.editScheme, this)
    );

    this.scm = vscode.scm.createSourceControl("vtx", "Vtx");
    this.scm.quickDiffProvider = this;
    this.group = this.scm.createResourceGroup("vtxChanges", "Vtx Changes");
    disposables.push(this.scm);

    disposables.push(
      vscode.commands.registerCommand("vtx.acceptChange", (r?: vscode.SourceControlResourceState) =>
        this.accept(pathOf(r))
      ),
      vscode.commands.registerCommand("vtx.rejectChange", (r?: vscode.SourceControlResourceState) =>
        this.reject(pathOf(r))
      ),
      vscode.commands.registerCommand("vtx.acceptAllChanges", () => this.acceptAll()),
      vscode.commands.registerCommand("vtx.rejectAllChanges", () => this.rejectAll()),
      vscode.commands.registerCommand("vtx.openChangeDiff", (r?: vscode.SourceControlResourceState) =>
        this.openDiff(pathOf(r))
      )
    );

    return vscode.Disposable.from(...disposables);
  }

  // QuickDiffProvider: the "original" to diff the working file against. A file
  // whose change has been kept or undone has nothing left to review, so it gets
  // no gutter markers.
  provideOriginalResource(uri: vscode.Uri): vscode.Uri | undefined {
    const snap = this.snapshots.get(key(uri.fsPath));
    return snap && !snap.resolved ? this.originalUri(snap.path) : undefined;
  }

  // The original text, including for a resolved file, so a diff the user still
  // has open keeps rendering against what the file really was, and either side
  // of a single edit, for the diff an edit row opens.
  provideTextDocumentContent(uri: vscode.Uri): string {
    if (uri.scheme === ChangeTracker.editScheme) {
      const [id, side] = uri.query.split("\u0000");
      const edit = this.edits.get(id);
      return (side === "after" ? edit?.after : edit?.before) ?? "";
    }
    const snapshotKey = key(uri.query || uri.fsPath);
    const snap = this.snapshots.get(snapshotKey);
    return snap ? this.resolvedOriginal(snapshotKey, snap) ?? "" : "";
  }

  // --- One edit's own diff ---------------------------------------------------

  // What a single edit did, as opposed to what the file is still holding: the
  // working set answers "what has Vtx changed here", an edit row answers "what
  // did this change do". Keyed by the row that reports it, and capped: this is
  // two copies of a file's text per edit, kept only to show a diff nobody has to
  // ask for twice.
  private readonly edits = new Map<string, { path: string; before: string; after: string }>();
  private editBytes = 0;
  private static readonly MAX_EDIT_BYTES = 8 * 1024 * 1024;
  private static readonly MAX_RESOLVED_BYTES = 8 * 1024 * 1024;

  // Extend the edit under `id` (the first call sets what it started from, later
  // ones move its end), so a row that reports a file several times still opens
  // everything that row stands for.
  recordEdit(id: string, fsPath: string, before: string | null, after: string): void {
    const existing = this.edits.get(id);
    if (existing) {
      this.editBytes += after.length - existing.after.length;
      existing.after = after;
    } else {
      this.edits.set(id, { path: fsPath, before: before ?? "", after });
      this.editBytes += (before?.length ?? 0) + after.length;
    }
    for (const [k, e] of this.edits) {
      if (this.editBytes <= ChangeTracker.MAX_EDIT_BYTES) {
        break;
      }
      this.edits.delete(k);
      this.editBytes -= e.before.length + e.after.length;
    }
  }

  // Open what one edit did. Falls back to the file's working set diff when the
  // text behind it has been dropped (a reloaded transcript, or a long session).
  async openEdit(id: string, fsPath?: string): Promise<void> {
    const edit = this.edits.get(id);
    if (!edit) {
      await this.openDiff(fsPath);
      return;
    }
    const side = (s: "before" | "after") =>
      vscode.Uri.from({
        scheme: ChangeTracker.editScheme,
        path: "/" + edit.path.replace(/\\/g, "/").replace(/^\/+/, ""),
        query: `${id}\u0000${s}`
      });
    const left = side("before");
    await this.warmOriginal(left);
    await vscode.commands.executeCommand("vscode.diff", left, side("after"), `${path.basename(edit.path)} (this edit)`);
  }

  recordDiff(fsPath: string, oldText: string | null, newText: string, sessionId: string, stat?: { added: number; removed: number }): void {
    const snapshotKey = key(fsPath);
    const snap = this.snapshots.get(snapshotKey);
    if (snap) {
      snap.added = stat?.added;
      snap.removed = stat?.removed;
      snap.agentHash = contentHash(newText);
      snap.agentStat = statOf(fsPath);
      if (snap.resolved) {
        // Kept or undone, and now edited again. The review starts from what was
        // kept, not from what the file was before Vtx first touched it: keeping
        // the older text made the next diff show every change of the session over
        // again, including the ones already dealt with. `oldText` is what the file
        // held immediately before this edit, which is exactly that baseline.
        this.resolvedBytes -= Buffer.byteLength(snap.original ?? "", "utf8");
        this.forgetSpilled(snapshotKey);
        snap.original = oldText;
        snap.sessions = new Set([sessionId]);
        snap.resolved = false;
      } else {
        snap.sessions.add(sessionId);
      }
    } else {
      this.snapshots.set(snapshotKey, {
        path: fsPath,
        original: oldText,
        agentHash: contentHash(newText),
        agentStat: statOf(fsPath),
        sessions: new Set([sessionId]),
        added: stat?.added,
        removed: stat?.removed
      });
    }
    this.contentChanged.fire(this.originalUri(fsPath));
    this.refreshGroup();
  }

  // Every file still awaiting review, whichever chat changed it: what the Source
  // Control view and "Open all" work on.
  changedPaths(): string[] {
    return [...this.snapshots.values()].filter((s) => !s.resolved).map((s) => s.path);
  }

  // What one chat changed and has not resolved, which is its working set.
  pathsFor(sessionId?: string): string[] {
    return this.unresolvedFor(sessionId).map((s) => s.path);
  }

  // What one chat changed, with the line counts, so a working set restored after a
  // reload arrives complete rather than as bare names.
  changesFor(sessionId?: string): { path: string; added?: number; removed?: number }[] {
    return this.unresolvedFor(sessionId).map((s) => ({ path: s.path, added: s.added, removed: s.removed }));
  }

  private unresolvedFor(sessionId?: string): Snapshot[] {
    if (!sessionId) {
      return [];
    }
    return [...this.snapshots.values()].filter((s) => !s.resolved && s.sessions.has(sessionId));
  }

  // Whether this file is still awaiting review. A kept or undone one is excluded:
  // its snapshot holds the text from before Vtx first touched the file, which is
  // older than any checkpoint taken since, so a revert must use the agent's own
  // plan for it rather than winding the file all the way back.
  hasUnresolvedChange(fsPath: string): boolean {
    const snap = this.snapshots.get(key(fsPath));
    return !!snap && !snap.resolved;
  }

  // Forget files this chat changed, leaving them on disk as they are. `paths`
  // limits it to the ones a revert actually put back: anything else is still on
  // disk holding the agent's content, and dropping it would take away the only
  // way left to undo it.
  clearFor(sessionId: string, paths?: string[]): void {
    const only = paths ? new Set(paths.map((p) => key(p))) : undefined;
    for (const [k, snap] of [...this.snapshots]) {
      if (!snap.sessions.has(sessionId) || (only && !only.has(k))) {
        continue;
      }
      this.snapshots.delete(k);
      this.resolvedBytes -= snap.resolved ? Buffer.byteLength(snap.original ?? "", "utf8") : 0;
      this.forgetSpilled(k);
      this.contentChanged.fire(this.originalUri(snap.path));
    }
    this.refreshGroup();
  }

  async openDiff(fsPath?: string): Promise<void> {
    if (!fsPath) {
      return;
    }
    const left = this.originalUri(fsPath);
    const right = vscode.Uri.file(fsPath);
    await this.warmOriginal(left);
    await vscode.commands.executeCommand("vscode.diff", left, right, `${path.basename(fsPath)} (Vtx)`);
  }

  // Resolve the original once, before anything else asks for it. The diff
  // editor's left hand side and the Source Control gutter both want this same
  // document, and when they ask at the same moment each finds no model and each
  // sets out to make one, so the second is told it already exists and the editor
  // fails to open. Opening it here, and waiting, means both find one waiting.
  private async warmOriginal(uri: vscode.Uri): Promise<void> {
    const key = uri.toString();
    const inflight = this.warming.get(key);
    if (inflight) {
      await inflight;
      return;
    }
    const p = Promise.resolve(vscode.workspace.openTextDocument(uri)).then(
      () => undefined,
      () => undefined
    );
    this.warming.set(key, p);
    try {
      await p;
    } finally {
      this.warming.delete(key);
    }
  }

  // Open every tracked change in a single multi-diff editor, falling back to
  // individual diffs if the multi-diff command is unavailable.
  async openAll(): Promise<void> {
    const paths = this.changedPaths();
    if (!paths.length) {
      return;
    }
    await Promise.all(paths.map((p) => this.warmOriginal(this.originalUri(p))));
    const list = paths.map((p) => [vscode.Uri.file(p), this.originalUri(p), vscode.Uri.file(p)]);
    try {
      await vscode.commands.executeCommand("vscode.changes", "Vtx Changes", list);
    } catch {
      for (const p of paths) {
        await this.openDiff(p);
      }
    }
  }

  // Keep: leave the current content alone and drop the file from the working
  // set. The original text stays held (see provideTextDocumentContent), and no
  // content change is fired because nothing about the original changed.
  accept(fsPath?: string): void {
    const snap = fsPath ? this.snapshots.get(key(fsPath)) : undefined;
    if (snap) {
      this.settle(snap, "accept");
    }
  }

  // Out of the working set, whichever way it was dealt with.
  private removeStaleSpills(root: string): void {
    const spills = path.join(root, "resolved-originals");
    try {
      for (const entry of fs.readdirSync(spills, { withFileTypes: true })) {
        if (!entry.isDirectory() || !/^\d+-/.test(entry.name)) {
          continue;
        }
        const pid = Number(entry.name.split("-", 1)[0]);
        try {
          process.kill(pid, 0);
        } catch (err) {
          if ((err as NodeJS.ErrnoException).code === "EPERM") {
            continue;
          }
          fs.rmSync(path.join(spills, entry.name), { recursive: true, force: true });
        }
      }
    } catch {}
  }

  private resolvedOriginal(snapshotKey: string, snap: Snapshot): string | null | undefined {
    const file = this.spilledResolved.get(snapshotKey);
    if (!file) {
      return snap.original;
    }
    try {
      return fs.readFileSync(file, "utf8");
    } catch {
      return undefined;
    }
  }

  private spillResolved(snapshotKey: string, snap: Snapshot): boolean {
    if (!this.spillDir || typeof snap.original !== "string") {
      return false;
    }
    try {
      fs.mkdirSync(this.spillDir, { recursive: true });
      const file = path.join(this.spillDir, crypto.randomBytes(16).toString("hex"));
      fs.writeFileSync(file, snap.original, "utf8");
      this.spilledResolved.set(snapshotKey, file);
      snap.original = null;
      return true;
    } catch {
      return false;
    }
  }

  private forgetSpilled(snapshotKey: string): void {
    const file = this.spilledResolved.get(snapshotKey);
    if (!file) {
      return;
    }
    this.spilledResolved.delete(snapshotKey);
    try { fs.unlinkSync(file); } catch {}
  }

  private trimResolved(): void {
    for (const [snapshotKey, snap] of this.snapshots) {
      if (this.resolvedBytes <= ChangeTracker.MAX_RESOLVED_BYTES) {
        return;
      }
      if (snap.resolved && typeof snap.original === "string") {
        const bytes = Buffer.byteLength(snap.original, "utf8");
        if (this.spillResolved(snapshotKey, snap)) {
          this.resolvedBytes -= bytes;
        }
      }
    }
  }

  private settle(snap: Snapshot, action: "accept" | "reject"): void {
    if (!snap.resolved) {
      snap.resolved = true;
      this.resolvedBytes += Buffer.byteLength(snap.original ?? "", "utf8");
      this.trimResolved();
    }
    this.resolved.fire({ paths: [snap.path], action });
    this.refreshGroup();
  }

  // Undo: restore the original content (or delete the file if it was new), then
  // drop it from the working set. The original stays held so an open diff renders
  // against it, now showing no difference, rather than the whole file as added.
  // Answers whether the file really was put back, so a caller that goes on to
  // forget it does not forget one that is still holding the agent's content.
  async reject(fsPath?: string): Promise<boolean> {
    const snapshotKey = fsPath ? key(fsPath) : undefined;
    const snap = snapshotKey ? this.snapshots.get(snapshotKey) : undefined;
    if (!snap) {
      return false;
    }
    // The file has gone since it was changed, so there is nothing to put back and
    // nothing to keep: whatever happened to it after Vtx touched it is the answer, and
    // its row is only in the way. Writing the original back would recreate a file that
    // was deleted on purpose, and when the folder had gone with it the write failed and
    // left a row whose buttons did nothing at all.
    if (!fs.existsSync(snap.path)) {
      this.settle(snap, "reject");
      return true;
    }
    if (!(await this.confirmNoLaterWork(snap))) {
      return false;
    }
    const original = this.resolvedOriginal(snapshotKey!, snap);
    if (original === undefined) {
      void vscode.window.showErrorMessage(`Couldn't undo ${path.basename(snap.path)}: the original is unavailable.`);
      return false;
    }
    try {
      if (original === null) {
        await fs.promises.rm(snap.path, { force: true });
      } else if (!(await this.writeThroughEditor(snap.path, original))) {
        await fs.promises.writeFile(snap.path, original, "utf8");
      }
    } catch (err) {
      // A read only file, a lock held by another process, a directory that has
      // since gone. Resolving anyway would drop the original from the working set
      // and from the store, leaving the agent's content in place with nothing left
      // to put back, and the row saying it had been undone. So say so and keep it.
      void vscode.window.showErrorMessage(
        `Couldn't undo ${path.basename(snap.path)}: ${err instanceof Error ? err.message : String(err)}`
      );
      return false;
    }
    this.settle(snap, "reject");
    return true;
  }

  // An undo writes the text from before the agent's edit, so anything the user
  // did to the file since goes with it, and that work is in no diff and no
  // history: this is the one place it can be lost for good. Only asks when the
  // file really has moved on, and a snapshot recorded before this was tracked
  // has nothing to compare, so it undoes as it always did.
  private async confirmNoLaterWork(snap: Snapshot): Promise<boolean> {
    if (!snap.agentHash) {
      return true;
    }
    let current: string;
    const doc = this.openDocument(snap.path);
    if (doc?.isDirty) {
      // Unsaved work is exactly what this is protecting, and the file on disk
      // cannot see it.
      current = doc.getText();
    } else {
      // The file's own timestamp answers the ordinary case without reading it,
      // and it is the half of the answer the text cannot give on its own: what
      // the agent reports is the content it meant to write, which is not always
      // byte for byte what landed. Both have to say the file has moved on, or an
      // undo of a perfectly ordinary edit would stop to ask about nothing.
      const now = statOf(snap.path);
      if (!snap.agentStat || !now || (now.mtimeMs === snap.agentStat.mtimeMs && now.size === snap.agentStat.size)) {
        return true;
      }
      try {
        current = await fs.promises.readFile(snap.path, "utf8");
      } catch {
        // Unreadable, so there is nothing to compare it against. The write below
        // will fail too, and it reports that properly.
        return true;
      }
    }
    if (contentHash(current) === snap.agentHash) {
      return true;
    }
    const name = path.basename(snap.path);
    const choice = await vscode.window.showWarningMessage(
      `${name} has changed since Vtx edited it.`,
      {
        modal: true,
        detail: `Undo puts back the version from before Vtx's edit, which discards everything changed since.`
      },
      "Undo Anyway"
    );
    return choice === "Undo Anyway";
  }

  private openDocument(fsPath: string): vscode.TextDocument | undefined {
    const k = key(fsPath);
    return vscode.workspace.textDocuments.find(
      (d) => d.uri.scheme === "file" && !d.isClosed && key(d.uri.fsPath) === k
    );
  }

  // Through the editor when the file is open, so an unsaved buffer is put back
  // with it: writing the file underneath a dirty editor left the agent's text in
  // the buffer, so the undo looked like it had done nothing and the next save
  // wrote the agent's version back. Answers false when the file is not open,
  // which is the ordinary case and the one the plain write serves.
  private async writeThroughEditor(fsPath: string, text: string): Promise<boolean> {
    const doc = this.openDocument(fsPath);
    if (!doc) {
      return false;
    }
    const edit = new vscode.WorkspaceEdit();
    const whole = new vscode.Range(doc.positionAt(0), doc.positionAt(doc.getText().length));
    edit.replace(doc.uri, whole, text);
    if (!(await vscode.workspace.applyEdit(edit))) {
      return false;
    }
    return doc.save();
  }

  // Keep is safe even when another chat also changed the file: it leaves the current
  // content alone. With a session, only that chat's files are kept. Without one,
  // everything is kept, which is what the Source Control title action means.
  acceptAll(sessionId?: string): void {
    const paths = sessionId ? this.unresolvedFor(sessionId).map((s) => s.path) : this.changedPaths();
    for (const p of paths) {
      this.accept(p);
    }
  }

  async rejectAll(sessionId?: string): Promise<void> {
    for (const p of this.bulkPaths(sessionId)) {
      await this.reject(p);
    }
  }

  // What a bulk undo from one chat may touch. A file is held once, with one
  // original, from before whichever chat edited it first, so a file two chats have
  // both edited cannot be undone for one of them: writing that original back would
  // discard the other chat's later work, from a button in a tray that does not
  // mention it. Those are left alone and named, so the decision is the user's.
  private bulkPaths(sessionId?: string): string[] {
    if (!sessionId) {
      return this.changedPaths();
    }
    const mine: string[] = [];
    const shared: string[] = [];
    for (const snap of this.unresolvedFor(sessionId)) {
      (snap.sessions.size > 1 ? shared : mine).push(snap.path);
    }
    if (shared.length) {
      void vscode.window.showWarningMessage(
        shared.length === 1
          ? `${path.basename(shared[0])} was also changed by another chat, so it was left alone. Undo it from its own row.`
          : `${shared.length} files were also changed by another chat, so they were left alone.`
      );
    }
    return mine;
  }

  private refreshGroup(): void {
    if (!this.group) {
      return;
    }
    this.group.resourceStates = this.changedPaths().map((p) => ({
      resourceUri: vscode.Uri.file(p),
      command: {
        command: "vtx.openChangeDiff",
        title: "Open Diff",
        arguments: [{ resourceUri: vscode.Uri.file(p) }]
      },
      decorations: { tooltip: "Changed by Vtx" }
    }));
    this.listChanged.fire(this.changedPaths());
    this.scheduleSave();
  }

  private originalUri(fsPath: string): vscode.Uri {
    // The exact fsPath is carried in the query (and used for content lookup).
    // The path component is normalised to a valid POSIX-style URI path so a
    // Windows drive path (C:\...) does not produce a malformed URI.
    const uriPath = "/" + fsPath.replace(/\\/g, "/").replace(/^\/+/, "");
    return vscode.Uri.from({ scheme: ChangeTracker.scheme, path: uriPath, query: fsPath });
  }
}

function pathOf(r?: vscode.SourceControlResourceState): string | undefined {
  return r?.resourceUri?.fsPath;
}
