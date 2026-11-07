import * as vscode from "vscode";
import type { VtxSession } from "./sessionList";

// Tracks which Vtx session ids belong to the current VS Code window.
// `workspaceState` is automatically scoped per `.code-workspace` file and per
// folder, so CRM and Outreach workspaces keep separate lists for free.
export class SessionStore {
  private static readonly IDS_KEY = "vtx.sessionIds.v1";
  private static readonly ACTIVE_KEY = "vtx.activeSession.v1";
  private static readonly VIEWING_KEY = "vtx.viewingSession.v1";
  private static readonly TITLES_KEY = "vtx.sessionTitles.v1";
  private static readonly PINNED_KEY = "vtx.pinnedTitles.v1";
  private static readonly OPTIONS_KEY = "vtx.options.v1";
  private static readonly CWDS_KEY = "vtx.sessionCwd.v1";
  private static readonly INTERRUPTED_KEY = "vtx.interrupted.v1";
  private static readonly DRAFTS_KEY = "vtx.drafts.v1";
  private static readonly SESSION_LIST_KEY = "vtx.sessionList.v1";
  // The composer in the sessions list is a "new chat" box with no session of its
  // own, so its unsent text is stored under this key.
  private static readonly NEW_DRAFT = "__new__";
  // A draft is a prompt someone is writing, not a place to keep a pasted file.
  private static readonly MAX_DRAFT = 100000;

  constructor(private readonly state: vscode.Memento) {}

  // Sessions whose turn was still in flight when the window reloaded or the
  // extension shut down. A `vtx acp` agent cannot outlive its extension host
  // (it runs its commands and file writes through us), so the turn dies with it;
  // recording that lets the next window say so instead of silently losing it.
  interrupted(): string[] {
    return this.state.get<string[]>(SessionStore.INTERRUPTED_KEY, []);
  }

  // Returns the Thenable so a shutdown path can await the write: an unawaited
  // update is not guaranteed to flush before the host exits.
  markInterrupted(ids: string[]): Thenable<void> {
    if (!ids.length) {
      return Promise.resolve();
    }
    // Newest first, because the cap drops the tail: with the old ids in front, a
    // list that had filled up with sessions nobody reopened silently stopped
    // recording new ones, and the notice a reload owes the user never appeared.
    const next = [...new Set([...ids, ...this.interrupted()])].slice(0, 50);
    return this.state.update(SessionStore.INTERRUPTED_KEY, next);
  }

  clearInterrupted(id: string): void {
    const current = this.interrupted();
    if (!current.includes(id)) {
      return;
    }
    void this.state.update(SessionStore.INTERRUPTED_KEY, current.filter((x) => x !== id));
  }

  // Cache the last-known mode/model options so the composer dropdowns are
  // populated immediately on open, before any session exists.
  options(): unknown | undefined {
    return this.state.get<unknown>(SessionStore.OPTIONS_KEY, undefined);
  }

  cacheOptions(payload: unknown): void {
    void this.state.update(SessionStore.OPTIONS_KEY, payload);
  }

  sessionList(): { at: number; sessions: VtxSession[] } | undefined {
    const cached = this.state.get<{ at?: unknown; sessions?: unknown } | undefined>(SessionStore.SESSION_LIST_KEY, undefined);
    if (typeof cached?.at !== "number" || !Array.isArray(cached.sessions)) {
      return undefined;
    }
    return { at: cached.at, sessions: cached.sessions as VtxSession[] };
  }

  cacheSessionList(list: { at: number; sessions: VtxSession[] }): void {
    void this.state.update(SessionStore.SESSION_LIST_KEY, list);
  }

  titles(): Record<string, string> {
    return this.state.get<Record<string, string>>(SessionStore.TITLES_KEY, {});
  }

  // Names we set ourselves (a rename), held until the CLI's own listing reports
  // the same name back. A `vtx list` already in flight when the rename landed
  // still carries the old name, and without this it would quietly undo it.
  pinnedTitles(): Record<string, string> {
    return this.state.get<Record<string, string>>(SessionStore.PINNED_KEY, {});
  }

  setTitle(id: string, title: string): void {
    if (!id || !title) {
      return;
    }
    void this.state.update(SessionStore.TITLES_KEY, { ...this.titles(), [id]: title });
    void this.state.update(SessionStore.PINNED_KEY, { ...this.pinnedTitles(), [id]: title });
  }

  // Remember titles so names show instantly on reopen, before `vtx list`
  // has returned (or for sessions no longer in a listed directory).
  cacheTitles(map: Record<string, string>): void {
    const current = this.titles();
    const pins = this.pinnedTitles();
    let changed = false;
    let unpinned = false;
    for (const [id, title] of Object.entries(map)) {
      if (!title) {
        continue;
      }
      const pinned = pins[id];
      if (pinned !== undefined) {
        if (pinned !== title) {
          continue; // the listing has not caught up with our rename yet
        }
        delete pins[id];
        unpinned = true;
      }
      if (current[id] !== title) {
        current[id] = title;
        changed = true;
      }
    }
    if (changed) {
      void this.state.update(SessionStore.TITLES_KEY, current);
    }
    if (unpinned) {
      void this.state.update(SessionStore.PINNED_KEY, pins);
    }
  }

  // Unsent composer text, kept per session so leaving a chat, reloading it, or
  // closing the window keeps the prompt you were part way through writing. It
  // outlives the agent on purpose: come back tomorrow, finish the sentence, and
  // sending it wakes the session up.
  draft(id?: string): string {
    return this.drafts()[id || SessionStore.NEW_DRAFT] || "";
  }

  setDraft(id: string | undefined, text: string): void {
    const key = id || SessionStore.NEW_DRAFT;
    const next = (text || "").slice(0, SessionStore.MAX_DRAFT);
    const map = this.drafts();
    if ((map[key] || "") === next) {
      return;
    }
    if (next) {
      map[key] = next;
    } else {
      delete map[key];
    }
    void this.state.update(SessionStore.DRAFTS_KEY, map);
  }

  // Messages that were waiting behind a running turn and never got sent, handed back
  // to the chat's draft. They only ever lived on the runtime, so terminating a chat
  // or reloading the window took the user's own words with them, while an unsent
  // draft in the same box survived. Returns the write so a shutdown can await it.
  queuedBackToDrafts(entries: { id: string; text: string }[]): Thenable<void> {
    const map = this.drafts();
    let changed = false;
    for (const { id, text } of entries) {
      if (!id || !text.trim()) {
        continue;
      }
      const existing = map[id] ? map[id].trimEnd() + "\n\n" : "";
      map[id] = (existing + text).slice(0, SessionStore.MAX_DRAFT);
      changed = true;
    }
    return changed ? this.state.update(SessionStore.DRAFTS_KEY, map) : Promise.resolve();
  }

  private drafts(): Record<string, string> {
    return this.state.get<Record<string, string>>(SessionStore.DRAFTS_KEY, {});
  }

  ids(): string[] {
    return this.state.get<string[]>(SessionStore.IDS_KEY, []);
  }

  // The exact directory each tracked session was created in. `vtx list` is
  // exact-match on cwd, so we query these to reconcile which sessions still
  // exist, and the webview groups by them.
  cwds(): Record<string, string> {
    return this.state.get<Record<string, string>>(SessionStore.CWDS_KEY, {});
  }

  setCwd(id: string, cwd: string): void {
    if (!id || !cwd) {
      return;
    }
    const map = this.cwds();
    if (map[id] === cwd) {
      return;
    }
    map[id] = cwd;
    void this.state.update(SessionStore.CWDS_KEY, map);
  }

  add(id: string, cwd?: string): string[] {
    if (!id) {
      return [];
    }
    const ids = this.ids().filter((x) => x !== id);
    ids.unshift(id);
    const capped = ids.slice(0, 200);
    const evicted = ids.slice(200);
    void this.state.update(SessionStore.IDS_KEY, capped);
    if (cwd) {
      this.setCwd(id, cwd);
    }
    // Keep the cwd and draft maps bounded to the capped id list, so they don't
    // grow without limit as old sessions fall off the end. (Titles are
    // deliberately left untouched: they are also cached for external sessions
    // that are not in the tracked id list, and are used to fill names in the
    // list.)
    this.pruneCwds(capped);
    this.pruneDrafts(capped);
    this.pruneSessionList(capped);
    return evicted;
  }

  private pruneCwds(ids: string[]): void {
    const keep = new Set(ids);
    const cwds = this.cwds();
    let changed = false;
    for (const key of Object.keys(cwds)) {
      if (!keep.has(key)) {
        delete cwds[key];
        changed = true;
      }
    }
    if (changed) {
      void this.state.update(SessionStore.CWDS_KEY, cwds);
    }
  }

  private pruneDrafts(ids: string[]): void {
    const keep = new Set([...ids, SessionStore.NEW_DRAFT]);
    const drafts = this.drafts();
    let changed = false;
    for (const key of Object.keys(drafts)) {
      if (!keep.has(key)) {
        delete drafts[key];
        changed = true;
      }
    }
    if (changed) {
      void this.state.update(SessionStore.DRAFTS_KEY, drafts);
    }
  }

  private pruneSessionList(ids: string[]): void {
    const list = this.sessionList();
    if (!list) {
      return;
    }
    const keep = new Set(ids);
    const sessions = list.sessions.filter((session) => keep.has(session.id));
    if (sessions.length !== list.sessions.length) {
      this.cacheSessionList({ at: list.at, sessions });
    }
  }

  remove(id: string): void {
    const ids = this.ids().filter((x) => x !== id);
    void this.state.update(SessionStore.IDS_KEY, ids);
    this.pruneSessionList(ids);
    const pins = this.pinnedTitles();
    if (id in pins) {
      delete pins[id];
      void this.state.update(SessionStore.PINNED_KEY, pins);
    }
    const cwds = this.cwds();
    if (id in cwds) {
      delete cwds[id];
      void this.state.update(SessionStore.CWDS_KEY, cwds);
    }
    this.setDraft(id, "");
    // A session that has gone will never be reopened to clear this itself, and the
    // list it sits in is capped.
    this.clearInterrupted(id);
    const titles = this.titles();
    if (id in titles) {
      delete titles[id];
      void this.state.update(SessionStore.TITLES_KEY, titles);
    }
    if (this.activeId() === id) {
      this.setActive(undefined);
    }
  }

  has(id: string): boolean {
    return this.ids().includes(id);
  }

  activeId(): string | undefined {
    return this.state.get<string | undefined>(SessionStore.ACTIVE_KEY, undefined);
  }

  setActive(id: string | undefined): void {
    void this.state.update(SessionStore.ACTIVE_KEY, id);
  }

  // The session the panel was last showing, as opposed to the last one used:
  // undefined means it was on the sessions list. A window reload builds a brand
  // new webview with an empty transcript, so this is what puts the reader back
  // where they were instead of dropping them on the list.
  viewing(): string | undefined {
    return this.state.get<string | undefined>(SessionStore.VIEWING_KEY, undefined);
  }

  setViewing(id: string | undefined): void {
    if (this.viewing() === id) {
      return;
    }
    void this.state.update(SessionStore.VIEWING_KEY, id);
  }
}
