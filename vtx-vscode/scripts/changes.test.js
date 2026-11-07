// Tests for the working set (src/diff/changeTracker.ts), the one piece of the
// host that touches the user's files. It is loaded outside VS Code through a
// small `vscode` stub (scripts/vscode-stub.js).
//
// The behaviour that matters here is what a file's ORIGINAL text resolves to
// after the change is kept or undone: the diff editor's left hand side reads it
// through the content provider, so dropping it made an accepted change render as
// if the whole file had just been created.

const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const esbuild = require("esbuild");

const ROOT = path.resolve(__dirname, "..");
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "devin-changes-"));

const outfile = path.join(TMP, "changeTracker.js");
esbuild.buildSync({
  entryPoints: [path.join(ROOT, "src/diff/changeTracker.ts")],
  outfile,
  bundle: true,
  platform: "node",
  format: "cjs",
  logLevel: "error",
  alias: { vscode: path.join(__dirname, "vscode-stub.js") }
});
const { ChangeTracker } = require(outfile);

// The line counting is a pure function, so it is loaded on its own.
const statFile = path.join(TMP, "diffStat.js");
esbuild.buildSync({
  entryPoints: [path.join(ROOT, "src/diff/diffStat.ts")],
  outfile: statFile,
  bundle: true,
  platform: "node",
  format: "cjs",
  logLevel: "error"
});
const { diffStat } = require(statFile);

function write(name, body) {
  const p = path.join(TMP, name);
  fs.writeFileSync(p, body, "utf8");
  return p;
}
// What the diff editor's left hand side would show for this path.
function original(tracker, p) {
  return tracker.provideTextDocumentContent({ query: p, fsPath: p });
}

test("keeping a change leaves the diff rendering against the real original", async () => {
  const tracker = new ChangeTracker();
  tracker.register();
  const file = write("kept.ts", "line one\nline two\nline three\n");

  tracker.recordDiff(file, "line one\n", "line one\nline two\nline three\n", "A");
  assert.deepStrictEqual(tracker.pathsFor("A"), [file], "the file is in the session's working set");
  assert.ok(tracker.provideOriginalResource({ fsPath: file }), "and has gutter markers while it awaits review");
  assert.strictEqual(original(tracker, file), "line one\n");

  tracker.accept(file);
  assert.deepStrictEqual(tracker.pathsFor("A"), [], "Keep drops it from the working set");
  assert.deepStrictEqual(tracker.changedPaths(), [], "and from the Source Control group");
  assert.strictEqual(
    tracker.provideOriginalResource({ fsPath: file }),
    undefined,
    "a kept file has nothing left to review, so no gutter markers"
  );
  // The regression: this used to fall back to "", so an open diff drew every
  // line as added, as though Devin had created the file.
  assert.strictEqual(original(tracker, file), "line one\n", "the original text is still what the diff resolves");
  assert.strictEqual(fs.readFileSync(file, "utf8"), "line one\nline two\nline three\n", "Keep does not touch the file");
});

test("Keep All keeps a file shared with another chat", () => {
  const tracker = new ChangeTracker();
  tracker.register();
  const file = write("shared-keep.ts", "v2\n");
  const vscode = globalThis.__dvVscode;
  vscode.window.shown.warning.length = 0;

  tracker.recordDiff(file, "v1\n", "v2\n", "A");
  tracker.recordDiff(file, "v2\n", "v3\n", "B");
  tracker.acceptAll("A");

  assert.deepStrictEqual(tracker.pathsFor("A"), [], "Keep All resolves the current chat's file");
  assert.deepStrictEqual(tracker.changedPaths(), [], "the shared file leaves the working set");
  assert.deepStrictEqual(vscode.window.shown.warning, [], "Keep All does not warn about a safe operation");
});

test("a later Undo All does not inherit a kept chat's session ownership", async () => {
  const tracker = new ChangeTracker();
  tracker.register();
  const file = write("shared-undo.ts", "v2\n");
  const vscode = globalThis.__dvVscode;
  vscode.window.shown.warning.length = 0;

  tracker.recordDiff(file, "v1\n", "v2\n", "A");
  tracker.acceptAll("A");
  tracker.recordDiff(file, "v2\n", "v3\n", "B");
  await tracker.rejectAll("B");

  assert.strictEqual(fs.readFileSync(file, "utf8"), "v2\n", "Undo All restores the later edit");
  assert.deepStrictEqual(vscode.window.shown.warning, [], "the later edit is not treated as shared");
});

test("undoing a change restores the file and still resolves its original", async () => {
  const tracker = new ChangeTracker();
  tracker.register();
  const file = write("undone.ts", "changed\n");

  tracker.recordDiff(file, "before\n", "changed\n", "A");
  await tracker.reject(file);

  assert.strictEqual(fs.readFileSync(file, "utf8"), "before\n", "Undo puts the original content back");
  assert.deepStrictEqual(tracker.pathsFor("A"), [], "and drops it from the working set");
  assert.strictEqual(original(tracker, file), "before\n", "so an open diff shows no difference, not a new file");
});

test("a file Devin created is deleted by Undo", async () => {
  const tracker = new ChangeTracker();
  tracker.register();
  const file = path.join(TMP, "created.ts");
  fs.writeFileSync(file, "brand new\n", "utf8");

  tracker.recordDiff(file, null, "brand new\n", "A");
  assert.strictEqual(original(tracker, file), "", "a created file has no original text");
  await tracker.reject(file);
  assert.strictEqual(fs.existsSync(file), false, "Undo removes it rather than leaving it empty");
});

test("editing a kept file again puts it back in the working set", async () => {
  const tracker = new ChangeTracker();
  tracker.register();
  const file = write("again.ts", "v2\n");

  tracker.recordDiff(file, "v1\n", "v2\n", "A");
  tracker.accept(file);
  assert.deepStrictEqual(tracker.pathsFor("A"), []);

  tracker.recordDiff(file, "v2\n", "v3\n", "A");
  assert.deepStrictEqual(tracker.pathsFor("A"), [file], "the next edit is reviewable again");
  // Reviewing the next edit means reviewing THAT edit: keeping the older text made
  // its diff show every change of the session again, including the kept ones.
  assert.strictEqual(original(tracker, file), "v2\n", "against what was kept, not the whole session");
});

test("opening the same diff twice resolves its original once", async () => {
  // The diff editor's left hand side and the Source Control gutter both want the
  // original, and when they raced to create it VS Code refused the second with
  // "Cannot add model because it already exists" and opened nothing.
  const tracker = new ChangeTracker();
  tracker.register();
  const file = write("raced.ts", "after\n");
  tracker.recordDiff(file, "before\n", "after\n", "A");

  globalThis.__dvOpened = [];
  await Promise.all([tracker.openDiff(file), tracker.openDiff(file)]);
  const originals = (globalThis.__dvOpened || []).filter((u) => u.startsWith("vtx-original"));
  assert.strictEqual(originals.length, 1, "two clicks, one original: " + JSON.stringify(originals));

  // And it is resolved before the diff is asked for, so nothing else has to.
  assert.ok(originals[0].includes(file.replace(/\\/g, "/")) || originals[0].includes(file));
});

test("the working set survives a window reload, counts and all", async () => {
  const dir = fs.mkdtempSync(path.join(TMP, "store-"));
  const store = { scheme: "file", path: dir, fsPath: dir, query: "", toString: () => "file://" + dir };
  const file = write("kept-open.ts", "v2\n");
  const gone = write("deleted-since.ts", "v2\n");

  const before = new ChangeTracker();
  before.register();
  await before.useStore(store);
  before.recordDiff(file, "v1\n", "v2\n", "A", { added: 3, removed: 1 });
  before.recordDiff(gone, "v1\n", "v2\n", "A", { added: 1, removed: 0 });
  // The save is debounced, so give it its moment.
  await new Promise((r) => setTimeout(r, 600));
  fs.rmSync(gone);

  // A new window: the agent is gone, but a change waiting to be reviewed is not.
  const after = new ChangeTracker();
  after.register();
  await after.useStore(store);
  assert.deepStrictEqual(after.pathsFor("A"), [file], "what is still there comes back");
  assert.deepStrictEqual(after.changesFor("A"), [{ path: file, added: 3, removed: 1 }],
    "with its line counts, so the tray is not a list of bare names");
  assert.strictEqual(original(after, file), "v1\n", "and its diff still has a left hand side");

  // Keeping it is remembered too: it must not come back a third time.
  after.accept(file);
  await new Promise((r) => setTimeout(r, 600));
  const last = new ChangeTracker();
  last.register();
  await last.useStore(store);
  assert.deepStrictEqual(last.pathsFor("A"), [], "a change already dealt with stays dealt with");
});

test("a Keep taken just before the window goes is not forgotten", async () => {
  // The save is debounced by 400ms and the timer is unref'd, so the host is free to
  // exit with it pending. Press Keep and reload inside that window and the change
  // came back as still pending, holding text older than the file: pressing Undo on
  // that row would wind back an edit already accepted. `deactivate` waits for this.
  const dir = fs.mkdtempSync(path.join(TMP, "flush-"));
  const store = { scheme: "file", path: dir, fsPath: dir, query: "", toString: () => "file://" + dir };
  const file = write("kept-then-reloaded.ts", "v2\n");

  const before = new ChangeTracker();
  before.register();
  await before.useStore(store);
  before.recordDiff(file, "v1\n", "v2\n", "A", { added: 1, removed: 1 });
  await new Promise((r) => setTimeout(r, 600));
  before.accept(file);
  await before.flush();

  const after = new ChangeTracker();
  after.register();
  await after.useStore(store);
  assert.deepStrictEqual(after.pathsFor("A"), [], "the Keep was written before the window went");
});

test("two saves of the working set do not write over each other", async () => {
  // The scratch file is named for the process, which stops two windows colliding,
  // and both saves of one window still shared it. A save takes as long as it takes
  // to write megabytes of held text, so the next one can start while it is running,
  // and then one write lands inside the other's file and a half written one is
  // renamed into place. That reads back as unparseable, and an unparseable store is
  // dropped whole: every pending undo, gone.
  const vscode = globalThis.__dvVscode;
  const realWrite = vscode.workspace.fs.writeFile;
  // A write that takes long enough to still be running when the next save begins,
  // and lands in two halves the way a real one does.
  vscode.workspace.fs.writeFile = async (uri, body) => {
    const half = Math.ceil(body.length / 2);
    await fs.promises.writeFile(uri.fsPath, body.slice(0, half));
    await new Promise((r) => setTimeout(r, 600));
    await fs.promises.appendFile(uri.fsPath, body.slice(half));
  };
  try {
    const dir = fs.mkdtempSync(path.join(TMP, "race-"));
    const store = { scheme: "file", path: dir, fsPath: dir, query: "", toString: () => "file://" + dir };
    const one = write("race-one.ts", "v2\n");
    const two = write("race-two.ts", "v2\n");

    const before = new ChangeTracker();
    before.register();
    await before.useStore(store);
    before.recordDiff(one, "v1\n", "v2\n", "A", { added: 1, removed: 1 });
    // Long enough for the first save to be in the middle of its write.
    await new Promise((r) => setTimeout(r, 450));
    before.recordDiff(two, "v1\n", "v2\n", "A", { added: 1, removed: 1 });
    await new Promise((r) => setTimeout(r, 2500));

    const after = new ChangeTracker();
    after.register();
    await after.useStore(store);
    assert.deepStrictEqual(after.pathsFor("A").sort(), [one, two].sort(), "both are still there to undo");
  } finally {
    vscode.workspace.fs.writeFile = realWrite;
  }
});

test("a file that has gone is dismissed, not put back", async () => {
  // A file the agent changed can be deleted afterwards, and its row stays in the changed
  // files tray. There is nothing left to keep and nothing to put back, so whichever
  // action is chosen the row goes: undoing used to recreate a file that had been deleted
  // on purpose, and when the folder had gone with it, it failed and left a row whose
  // buttons did nothing at all.
  const vscode = globalThis.__dvVscode;
  vscode.window.shown.error.length = 0;
  const tracker = new ChangeTracker();
  tracker.register();

  // Deleted, with its folder still there: writing the original back would recreate it.
  const deleted = write("deleted-since.ts", "v2\n");
  tracker.recordDiff(deleted, "v1\n", "v2\n", "A", { added: 1, removed: 1 });
  fs.rmSync(deleted);
  assert.strictEqual(await tracker.reject(deleted), true, "undo answers that it is dealt with");
  assert.deepStrictEqual(tracker.pathsFor("A"), [], "and the row goes");
  assert.ok(!fs.existsSync(deleted), "the file stays deleted");

  // Deleted along with the folder it was in, which is what used to fail outright.
  const dir = path.join(TMP, "gone-folder");
  fs.mkdirSync(dir, { recursive: true });
  const inFolder = path.join(dir, "in-a-gone-folder.ts");
  fs.writeFileSync(inFolder, "v2\n");
  tracker.recordDiff(inFolder, "v1\n", "v2\n", "B", { added: 1, removed: 1 });
  fs.rmSync(dir, { recursive: true });
  assert.strictEqual(await tracker.reject(inFolder), true, "undo still answers");
  assert.deepStrictEqual(tracker.pathsFor("B"), [], "and that row goes too");
  assert.deepStrictEqual(vscode.window.shown.error, [], "with nothing to report: this is not a failure");

  // And keeping one is the same: there is nothing to keep either.
  const keptGone = write("kept-and-gone.ts", "v2\n");
  tracker.recordDiff(keptGone, "v1\n", "v2\n", "C", { added: 1, removed: 1 });
  fs.rmSync(keptGone);
  tracker.accept(keptGone);
  assert.deepStrictEqual(tracker.pathsFor("C"), [], "keep dismisses it as well");
  assert.ok(!fs.existsSync(keptGone), "and does not bring it back either");
});

test("one save that fails does not stop every save after it", async () => {
  // The saves are chained so they cannot write over each other. A chain built out of
  // `then` alone carries a rejection forward for ever, so one failed write would leave
  // the working set unsaved for the rest of the window, silently, which is the loss the
  // chaining was added to prevent.
  const vscode = globalThis.__dvVscode;
  const realWrite = vscode.workspace.fs.writeFile;
  let failNext = true;
  vscode.workspace.fs.writeFile = async (uri, body) => {
    if (failNext) {
      failNext = false;
      throw new Error("no space left on device");
    }
    return realWrite(uri, body);
  };
  try {
    const dir = fs.mkdtempSync(path.join(TMP, "failed-"));
    const store = { scheme: "file", path: dir, fsPath: dir, query: "", toString: () => "file://" + dir };
    const first = write("after-a-failure-one.ts", "v2\n");
    const second = write("after-a-failure-two.ts", "v2\n");

    const before = new ChangeTracker();
    before.register();
    await before.useStore(store);
    before.recordDiff(first, "v1\n", "v2\n", "A", { added: 1, removed: 1 });
    await new Promise((r) => setTimeout(r, 600));
    before.recordDiff(second, "v1\n", "v2\n", "A", { added: 1, removed: 1 });
    await new Promise((r) => setTimeout(r, 600));

    const after = new ChangeTracker();
    after.register();
    await after.useStore(store);
    assert.deepStrictEqual(after.pathsFor("A").sort(), [first, second].sort(), "the next save still wrote");
  } finally {
    vscode.workspace.fs.writeFile = realWrite;
  }
});

test("one original too big to keep does not take the others with it", async () => {
  // The working set is written out whole, and past a cap it was deleted outright,
  // cap included: one generated file, lock file or data fixture the agent rewrote
  // and every other original went with it, so after a reload nothing at all could
  // be undone and nothing said so.
  const dir = fs.mkdtempSync(path.join(TMP, "big-"));
  const store = { scheme: "file", path: dir, fsPath: dir, query: "", toString: () => "file://" + dir };
  const small = write("small.ts", "v2\n");
  const huge = write("generated.json", "v2\n");

  const before = new ChangeTracker();
  before.register();
  await before.useStore(store);
  before.recordDiff(small, "v1\n", "v2\n", "A", { added: 1, removed: 1 });
  before.recordDiff(huge, "x".repeat(9 * 1024 * 1024), "v2\n", "A", { added: 1, removed: 1 });
  await new Promise((r) => setTimeout(r, 600));

  const after = new ChangeTracker();
  after.register();
  await after.useStore(store);
  assert.deepStrictEqual(after.pathsFor("A"), [small], "the ones that fit come back");
  assert.strictEqual(original(after, small), "v1\n", "with the text an undo needs");
});

test("a one line change in a large file is counted, and counted quickly", async () => {
  // The count ran a full table over both sides, so replaying a session's worth of
  // edits was seconds of work. Trimming what the two sides share at each end makes
  // it proportional to the change rather than to the file.
  const big = Array.from({ length: 20000 }, (_, i) => "line " + i).join("\n");
  const edited = big.replace("line 9000", "line 9000 changed");
  const started = Date.now();
  assert.deepStrictEqual(diffStat(big, edited), { added: 1, removed: 1 });
  assert.ok(Date.now() - started < 250, "and does not take seconds over it");

  // The plain cases still hold.
  // Two lines, not three: the newline ending the last line is not a line of its
  // own, and counting it made every file Devin created disagree with git by one.
  assert.deepStrictEqual(diffStat(null, "a\nb\n"), { added: 2, removed: 0 }, "a new file is all additions");
  assert.deepStrictEqual(diffStat("a\nb\n", ""), { added: 0, removed: 2 }, "an emptied one is all removals");
  assert.deepStrictEqual(diffStat(null, "a\nb"), { added: 2, removed: 0 }, "with or without the final newline");
  assert.deepStrictEqual(diffStat("a\nb\n", "a\nb\n"), { added: 0, removed: 0 }, "no change, no counts");
  assert.deepStrictEqual(diffStat("a\nb\nc\n", "a\nx\ny\nc\n"), { added: 2, removed: 1 });
});

test("a CRLF file rewritten with LF is not counted as every line changed", async () => {
  assert.deepStrictEqual(
    diffStat("a\r\nb\r\nc\r\n", "a\nb\nc\n"),
    { added: 0, removed: 0 },
    "the endings are not the change"
  );
  assert.deepStrictEqual(diffStat("a\r\nb\r\nc\r\n", "a\nx\nc\n"), { added: 1, removed: 1 }, "only the line that changed");
});

test("an edit row opens what that edit did, not the whole file", async () => {
  const tracker = new ChangeTracker();
  tracker.register();
  const file = write("twice.ts", "one\ntwo\nthree\n");

  // Two turns touching the same file: the working set spans both, each row only
  // its own.
  tracker.recordEdit("turn1", file, "one\n", "one\ntwo\n");
  tracker.recordDiff(file, "one\n", "one\ntwo\n", "A");
  tracker.recordEdit("turn2", file, "one\ntwo\n", "one\ntwo\nthree\n");
  tracker.recordDiff(file, "one\ntwo\n", "one\ntwo\nthree\n", "A");

  const read = (id, side) =>
    tracker.provideTextDocumentContent({ scheme: "vtx-edit", query: id + "\u0000" + side, fsPath: file });
  assert.strictEqual(read("turn2", "before"), "one\ntwo\n", "the second row starts where the first left off");
  assert.strictEqual(read("turn2", "after"), "one\ntwo\nthree\n");
  assert.strictEqual(read("turn1", "before"), "one\n", "and the first still shows its own");

  globalThis.__dvOpened = [];
  await tracker.openEdit("turn2", file);
  assert.ok(
    (globalThis.__dvOpened || []).some((u) => u.startsWith("vtx-edit")),
    "the row opens its own two sides: " + JSON.stringify(globalThis.__dvOpened)
  );
  // The working set is still the whole change, which is what the tray opens.
  assert.strictEqual(original(tracker, file), "one\n");

  // An edit whose text is no longer held falls back to the file's own diff.
  globalThis.__dvOpened = [];
  await tracker.openEdit("forgotten", file);
  assert.ok((globalThis.__dvOpened || []).some((u) => u.startsWith("vtx-original")));
});

test("a file is the same file however its path is spelled", async () => {
  const tracker = new ChangeTracker();
  tracker.register();
  const file = write("spelled.ts", "after\n");
  // What the agent reports and what VS Code hands back from a URI are the same
  // file written two ways: an unresolved path, and (on Windows) another casing.
  const roundabout = path.join(TMP, ".", "spelled.ts");

  tracker.recordDiff(file, "before\n", "after\n", "A");
  assert.strictEqual(tracker.hasUnresolvedChange(roundabout), true, "it is found by either spelling");
  assert.ok(tracker.provideOriginalResource({ fsPath: roundabout }), "and keeps its gutter markers");
  assert.deepStrictEqual(tracker.pathsFor("A"), [file], "the working set keeps the path as it was reported");

  await tracker.reject(roundabout);
  assert.strictEqual(fs.readFileSync(file, "utf8"), "before\n", "and Undo puts the one file back");
});

test("each session gets its own working set, and a revert forgets only its own", async () => {
  const tracker = new ChangeTracker();
  tracker.register();
  const mine = write("mine.ts", "mine\n");
  const theirs = write("theirs.ts", "theirs\n");
  const shared = write("shared.ts", "shared\n");

  tracker.recordDiff(mine, "", "mine\n", "A");
  tracker.recordDiff(theirs, "", "theirs\n", "B");
  tracker.recordDiff(shared, "", "shared\n", "A");
  tracker.recordDiff(shared, "shared\n", "shared again\n", "B");

  assert.deepStrictEqual(tracker.pathsFor("A").sort(), [mine, shared].sort(), "A lists only what A changed");
  assert.deepStrictEqual(tracker.pathsFor("B").sort(), [theirs, shared].sort(), "B lists only what B changed");
  assert.strictEqual(tracker.changedPaths().length, 3, "the Source Control view lists every file");
  assert.deepStrictEqual(tracker.pathsFor(undefined), [], "no session means no working set");

  // A revert forgets that chat's files outright, including the one it shared.
  tracker.clearFor("A");
  assert.deepStrictEqual(tracker.pathsFor("A"), []);
  assert.deepStrictEqual(tracker.pathsFor("B"), [theirs], "B keeps its own");
  assert.strictEqual(tracker.hasUnresolvedChange(mine), false, "and nothing of A's is held any more");
});

test("a bulk undo leaves a file another chat also changed", async () => {
  // One file is held once, with one original from before whichever chat touched it
  // first. So undoing it for chat A would write that over chat B's later work, from
  // a button in a tray that never mentions B.
  const vscode = globalThis.__dvVscode;
  vscode.window.shown.warning.length = 0;
  const tracker = new ChangeTracker();
  tracker.register();
  const mine = write("only-mine.ts", "A wrote this\n");
  const shared = write("both.ts", "B wrote this last\n");

  tracker.recordDiff(mine, "mine before\n", "A wrote this\n", "A");
  tracker.recordDiff(shared, "shared before\n", "A wrote this\n", "A");
  tracker.recordDiff(shared, "A wrote this\n", "B wrote this last\n", "B");

  await tracker.rejectAll("A");

  assert.strictEqual(fs.readFileSync(mine, "utf8"), "mine before\n", "A's own file is undone");
  assert.strictEqual(fs.readFileSync(shared, "utf8"), "B wrote this last\n",
    "the shared file keeps the newer work rather than being wound back past it");
  assert.strictEqual(tracker.hasUnresolvedChange(shared), true, "and it stays reviewable");
  assert.strictEqual(vscode.window.shown.warning.length, 1, "and the user is told it was left");
  assert.match(vscode.window.shown.warning[0], /both\.ts/);
});

test("a failed undo is not reported to the caller as restored", async () => {
  // The revert flow forgets the files it put back. One it could not write is still
  // holding the agent's content, so forgetting it would take away the only way back.
  const tracker = new ChangeTracker();
  tracker.register();
  const file = write("locked-too.ts", "devin wrote this\n");
  tracker.recordDiff(file, "the original\n", "devin wrote this\n", "A");

  fs.chmodSync(file, 0o444);
  let ok;
  try {
    ok = await tracker.reject(file);
  } finally {
    fs.chmodSync(file, 0o644);
  }
  assert.strictEqual(ok, false, "it says it did not manage it");
  assert.strictEqual(await tracker.reject(file), true, "and true once it can");
});

test("one file is one entry, however it was spelled", async () => {
  // Whether two spellings are one file is the filesystem's business, not ours:
  // macOS and Windows say yes by default, Linux says no. Either way the working set
  // has to agree with it, because two entries for one file means two originals, and
  // undoing them in order writes the older text over the newer.
  const tracker = new ChangeTracker();
  tracker.register();
  const file = write("Casing.ts", "devin wrote this\n");
  const other = path.join(path.dirname(file), "casing.ts");
  const sameFile = fs.existsSync(other);

  tracker.recordDiff(file, "the original\n", "devin wrote this\n", "A");
  tracker.recordDiff(other, "devin wrote this\n", "devin wrote more\n", "A");

  if (sameFile) {
    assert.strictEqual(tracker.pathsFor("A").length, 1, "one file, one entry");
    await tracker.reject(other);
    assert.strictEqual(fs.readFileSync(file, "utf8"), "the original\n",
      "and undoing it goes back to before the first edit, not to the middle");
  } else {
    assert.strictEqual(tracker.pathsFor("A").length, 2,
      "on a case sensitive filesystem these really are two files");
    await tracker.reject(file);
    assert.strictEqual(fs.readFileSync(file, "utf8"), "the original\n", "each with its own original");
  }
});

test("an undo that cannot write says so, and keeps the original", async () => {
  // The one operation where a silent failure is unacceptable: resolving anyway
  // drops the original from the working set and from the store, so the agent's
  // content stays on disk with nothing left to put it back, under a row that
  // claims it was undone.
  // The copy the bundle loaded, not a second one required here.
  const vscode = globalThis.__dvVscode;
  vscode.window.shown.error.length = 0;
  const tracker = new ChangeTracker();
  tracker.register();
  const file = write("locked.ts", "devin wrote this\n");
  tracker.recordDiff(file, "the original\n", "devin wrote this\n", "A");

  fs.chmodSync(file, 0o444);
  try {
    await tracker.reject(file);
  } finally {
    fs.chmodSync(file, 0o644);
  }

  assert.strictEqual(fs.readFileSync(file, "utf8"), "devin wrote this\n", "the write really did fail");
  assert.strictEqual(vscode.window.shown.error.length, 1, "the user is told, rather than it passing quietly");
  assert.match(vscode.window.shown.error[0], /locked\.ts/);
  assert.strictEqual(tracker.hasUnresolvedChange(file), true, "and it stays in the working set");

  // Which means it can still be undone once the file is writable again.
  await tracker.reject(file);
  assert.strictEqual(fs.readFileSync(file, "utf8"), "the original\n");
  assert.strictEqual(tracker.hasUnresolvedChange(file), false);
});

test("undoing everything in one chat leaves the other chat's edits alone", async () => {
  // The tray these buttons live in shows one chat's files and counts them in its
  // header, so it must not reach into a chat the user cannot even see.
  const tracker = new ChangeTracker();
  tracker.register();
  const mine = write("mine.ts", "devin wrote mine\n");
  const theirs = write("theirs.ts", "devin wrote theirs\n");

  tracker.recordDiff(mine, "mine\n", "devin wrote mine\n", "A");
  tracker.recordDiff(theirs, "theirs\n", "devin wrote theirs\n", "B");

  await tracker.rejectAll("A");
  assert.strictEqual(fs.readFileSync(mine, "utf8"), "mine\n", "A's file is put back");
  assert.strictEqual(fs.readFileSync(theirs, "utf8"), "devin wrote theirs\n", "B's file is untouched");
  assert.deepStrictEqual(tracker.pathsFor("B"), [theirs], "and B still has it to review");

  // Keeping everything in one chat is the same promise in the other direction.
  const alsoMine = write("also-mine.ts", "devin wrote this\n");
  tracker.recordDiff(alsoMine, "before\n", "devin wrote this\n", "A");
  tracker.acceptAll("A");
  assert.deepStrictEqual(tracker.pathsFor("B"), [theirs], "B is not resolved by A's Keep all");

  // The Source Control title action has no chat, and still means everything.
  await tracker.rejectAll();
  assert.strictEqual(fs.readFileSync(theirs, "utf8"), "theirs\n", "unscoped still reaches every file");
});

test("a rewind only forgets the files it really put back", async () => {
  // The agent's revert reports a file plan that is empty even for files it edited
  // through us, and its own rewind leaves the disk alone. So forgetting the whole
  // chat would strand the edits on disk with no way left to undo them.
  const tracker = new ChangeTracker();
  tracker.register();
  const putBack = write("restored.ts", "agent wrote this\n");
  const stillThere = write("untouched.ts", "agent wrote this too\n");

  tracker.recordDiff(putBack, "original\n", "agent wrote this\n", "A");
  tracker.recordDiff(stillThere, "original\n", "agent wrote this too\n", "A");

  tracker.clearFor("A", [putBack]);

  assert.deepStrictEqual(tracker.pathsFor("A"), [stillThere],
    "the file the rewind did not touch stays reviewable");
  assert.strictEqual(tracker.hasUnresolvedChange(putBack), false, "the restored one is done with");
  assert.strictEqual(tracker.hasUnresolvedChange(stillThere), true, "and the other can still be undone");

  // Undoing it afterwards still works, which is the whole point of keeping it.
  await tracker.reject(stillThere);
  assert.strictEqual(fs.readFileSync(stillThere, "utf8"), "original\n");
});

test("a kept file is no longer offered for review, but can still be put back", async () => {
  const tracker = new ChangeTracker();
  tracker.register();
  const file = write("checkpoint.ts", "after\n");

  tracker.recordDiff(file, "before\n", "after\n", "A");
  tracker.accept(file);
  // A revert asks this before winding a file back itself. A kept file must answer
  // no: its snapshot is the text from before Devin first touched it, which is older
  // than any checkpoint taken since, so the agent's own plan is the accurate one.
  assert.strictEqual(tracker.hasUnresolvedChange(file), false, "Keep settles the review");

  // The original text is still held, so an explicit undo still restores it.
  await tracker.reject(file);
  assert.strictEqual(fs.readFileSync(file, "utf8"), "before\n", "and an undo still restores it");
});

test("undoing a file that was worked on since asks before discarding that work", async () => {
  // The one place a user's own work can be lost for good: Undo writes the text from
  // before Vtx's edit, and anything typed since is in no diff and no history. It
  // used to go without a word, including from "Undo all", which has no confirmation
  // of its own.
  const vscode = globalThis.__dvVscode;
  const tracker = new ChangeTracker();
  tracker.register();
  const file = write("worked-on.ts", "agent\n");
  tracker.recordDiff(file, "before\n", "agent\n", "A", { added: 1, removed: 1 });

  vscode.window.shown.warning.length = 0;
  vscode.window.answer = undefined;
  assert.strictEqual(await tracker.reject(file), true, "a file still as Devin left it undoes straight away");
  assert.strictEqual(fs.readFileSync(file, "utf8"), "before\n");
  assert.deepStrictEqual(vscode.window.shown.warning, [], "with nothing to ask about");

  // Edited by hand after Vtx's edit, and the undo declined.
  fs.writeFileSync(file, "agent\n", "utf8");
  tracker.recordDiff(file, "before\n", "agent\n", "A", { added: 1, removed: 1 });
  fs.writeFileSync(file, "agent\nmine\n", "utf8");
  assert.strictEqual(await tracker.reject(file), false, "declining answers that nothing was put back");
  assert.strictEqual(fs.readFileSync(file, "utf8"), "agent\nmine\n", "so the later work is still there");
  assert.deepStrictEqual(tracker.pathsFor("A"), [file], "and the row stays, still offering to undo");
  assert.strictEqual(vscode.window.shown.warning.length, 1, "asked once");

  vscode.window.answer = "Undo Anyway";
  assert.strictEqual(await tracker.reject(file), true, "and answering it undoes as before");
  assert.strictEqual(fs.readFileSync(file, "utf8"), "before\n");
  vscode.window.answer = undefined;
});

test("a file rewritten with other line endings is not mistaken for work to protect", async () => {
  // The agent reports the content it meant to write, and what lands on disk can
  // differ in its line endings and byte order mark (the write path adds both back
  // deliberately). Counting that as a change would ask about every undo.
  const vscode = globalThis.__dvVscode;
  const tracker = new ChangeTracker();
  tracker.register();
  const file = write("crlf.ts", "one\r\ntwo\r\n");
  tracker.recordDiff(file, "one\n", "one\ntwo\n", "A", { added: 1, removed: 0 });

  vscode.window.shown.warning.length = 0;
  assert.strictEqual(await tracker.reject(file), true);
  assert.deepStrictEqual(vscode.window.shown.warning, [], "no question for a line ending");
});

test("resolved snapshots spill old originals beyond their byte budget", async () => {
  const budget = ChangeTracker.MAX_RESOLVED_BYTES;
  ChangeTracker.MAX_RESOLVED_BYTES = 20;
  try {
    const tracker = new ChangeTracker();
    tracker.register();
    await tracker.useStore(globalThis.__dvVscode.Uri.file(path.join(TMP, "resolved-store")));
    const first = write("first-resolved.ts", "after one\n");
    const second = write("second-resolved.ts", "after two\n");
    tracker.recordDiff(first, "before one\n", "after one\n", "A");
    tracker.accept(first);
    tracker.recordDiff(second, "before two\n", "after two\n", "A");
    tracker.accept(second);

    assert.strictEqual(original(tracker, first), "before one\n");
    assert.strictEqual(original(tracker, second), "before two\n");
    assert.strictEqual(await tracker.reject(first), true);
    assert.strictEqual(fs.readFileSync(first, "utf8"), "before one\n");
    const spilled = [...tracker.spilledResolved.values()][0];
    assert.strictEqual(fs.existsSync(spilled), true);
    tracker.dispose();
    assert.strictEqual(fs.existsSync(spilled), false);
  } finally {
    ChangeTracker.MAX_RESOLVED_BYTES = budget;
  }
});

test("opening change storage removes stale resolved-original spill directories", async () => {
  const root = path.join(TMP, "stale-resolved-store");
  const stale = path.join(root, "resolved-originals", "99999999-stale");
  fs.mkdirSync(stale, { recursive: true });
  fs.writeFileSync(path.join(stale, "original"), "before\n");
  const tracker = new ChangeTracker();

  await tracker.useStore(globalThis.__dvVscode.Uri.file(root));

  assert.strictEqual(fs.existsSync(stale), false);
  tracker.dispose();
});
