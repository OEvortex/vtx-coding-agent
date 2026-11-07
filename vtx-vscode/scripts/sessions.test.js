// Tests for what this window remembers about its chats: the session list it
// builds from the agent's answer (src/session/sessionList.ts) and the store that
// outlives every agent (src/session/sessionStore.ts).
//
// Both hold things nothing else has a copy of. A pruned id takes its working
// directory and its unsent draft with it, and the interrupted list is the only
// record that a turn died with the window, so the risk in both is the same:
// forgetting something on the strength of an answer that was never a fact.

const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const esbuild = require("esbuild");

const ROOT = path.resolve(__dirname, "..");
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "devin-sessions-"));

function build(rel) {
  const outfile = path.join(TMP, path.basename(rel).replace(/\.ts$/, ".js"));
  esbuild.buildSync({
    entryPoints: [path.join(ROOT, rel)],
    outfile,
    bundle: true,
    platform: "node",
    format: "cjs",
    logLevel: "error",
    alias: { vscode: path.join(__dirname, "vscode-stub.js") }
  });
  return require(outfile);
}

const { fromAcpRows, listSessions } = build("src/session/sessionList.ts");
const { SessionStore } = build("src/session/sessionStore.ts");

// A Memento over a plain object, which is all SessionStore asks for.
function memento() {
  const map = new Map();
  return {
    get: (k, fallback) => (map.has(k) ? map.get(k) : fallback),
    update: async (k, v) => { map.set(k, v); },
    keys: () => [...map.keys()]
  };
}

function listCli(log) {
  const file = path.join(TMP, `list-${Date.now()}-${Math.random()}.js`);
  fs.writeFileSync(file, `#!/usr/bin/env node\nrequire("fs").appendFileSync(process.env.LIST_LOG, process.cwd() + "\\n");\nprocess.stdout.write("[]");\n`);
  fs.chmodSync(file, 0o755);
  return file;
}

const posixOnly = { skip: process.platform === "win32" };

function slowListCli(state, delay = 200) {
  const file = path.join(TMP, `slow-list-${Date.now()}-${Math.random()}.js`);
  fs.writeFileSync(file, `#!/usr/bin/env node\nconst fs = require("fs");\nconst path = require("path");\nconst marker = path.join(process.env.LIST_STATE, String(process.pid));\nfs.writeFileSync(marker, "");\nsetTimeout(() => { fs.rmSync(marker, { force: true }); process.stdout.write("[]"); }, ${delay});\n`);
  fs.chmodSync(file, 0o755);
  return file;
}

test("an empty tracked list does not start the CLI", posixOnly, async () => {
  const log = path.join(TMP, "empty-list.log");
  const result = await listSessions({
    cliPath: listCli(log),
    env: { ...process.env, LIST_LOG: log },
    trackedIds: [],
    cwdById: {},
    folders: [TMP]
  });

  assert.deepStrictEqual(result, { sessions: [], prunedIds: [] });
  assert.strictEqual(fs.existsSync(log), false, "no CLI list process is needed");
});

test("a session the agent did not mention is pruned, unless it mentioned none at all", () => {
  const tracked = ["kept", "gone"];
  const rows = [{ sessionId: "kept", cwd: "/w", title: "Kept", updatedAt: "2026-08-15T10:00:00Z" }];

  const answered = fromAcpRows(rows, tracked);
  assert.deepStrictEqual(answered.sessions.map((s) => s.id), ["kept"]);
  assert.deepStrictEqual(answered.prunedIds, ["gone"], "an id the agent does not know is gone");
  assert.strictEqual(answered.sessions[0].last_activity_at, Math.floor(Date.parse("2026-08-15T10:00:00Z") / 1000));

  // An agent answering at all has its own session to report, so no rows is the
  // call not working: a renamed field after a CLI upgrade, a reset store. Pruning
  // on it emptied the whole workspace list, and every cwd and draft with it.
  const empty = fromAcpRows([], tracked);
  assert.deepStrictEqual(empty.prunedIds, [], "an empty answer prunes nothing");
});

test("the newest interrupted turns are the ones kept", async () => {
  const store = new SessionStore(memento());
  // More than the cap, so the oldest have to go: they are the ones nobody came
  // back to. Dropping the newest instead meant that once the list filled up with
  // chats the user never reopened, a reload mid turn stopped being reported at
  // all, which is the one thing this list exists to say.
  const old = Array.from({ length: 50 }, (_, i) => `old-${i}`);
  await store.markInterrupted(old);
  await store.markInterrupted(["the-one-that-just-died"]);

  const held = store.interrupted();
  assert.strictEqual(held.length, 50, "still capped");
  assert.ok(held.includes("the-one-that-just-died"), "and the newest is in it");
});

test("a session that is gone takes its interrupted mark and its title with it", async () => {
  const store = new SessionStore(memento());
  store.add("s1", "/w");
  store.setTitle("s1", "A chat");
  await store.markInterrupted(["s1"]);

  store.remove("s1");
  assert.deepStrictEqual(store.interrupted(), [], "nothing left pointing at a session that has gone");
  assert.deepStrictEqual(store.titles(), {}, "and no name kept for it for ever");
});

test("known session directories do not also list workspace roots", posixOnly, async () => {
  const known = fs.mkdtempSync(path.join(TMP, "known-"));
  const extra = fs.mkdtempSync(path.join(TMP, "extra-"));
  const log = path.join(TMP, "known-list.log");
  await listSessions({
    cliPath: listCli(log),
    env: { ...process.env, LIST_LOG: log },
    trackedIds: ["known"],
    cwdById: { known },
    folders: [known, extra]
  });

  const listed = fs.readFileSync(log, "utf8").trim().split("\n").sort();
  assert.deepStrictEqual(listed, [fs.realpathSync(known)]);
});

test("CLI fallback limits concurrent session list processes", posixOnly, async () => {
  const state = fs.mkdtempSync(path.join(TMP, "state-"));
  const dirs = Array.from({ length: 9 }, () => fs.mkdtempSync(path.join(TMP, "cwd-")));
  const ids = dirs.map((_, i) => `s${i}`);
  let peak = 0;
  const sample = setInterval(() => {
    peak = Math.max(peak, fs.readdirSync(state).length);
  }, 5);
  try {
    await listSessions({
      cliPath: slowListCli(state),
      env: { ...process.env, LIST_STATE: state },
      trackedIds: ids,
      cwdById: Object.fromEntries(ids.map((id, i) => [id, dirs[i]])),
      folders: []
    });
  } finally {
    clearInterval(sample);
  }

  assert.ok(peak <= 4, `at most four CLI list processes run at once, saw ${peak}`);
});

test("CLI fallback uses one deadline across all session directories", posixOnly, async () => {
  const state = fs.mkdtempSync(path.join(TMP, "deadline-"));
  const dirs = Array.from({ length: 9 }, () => fs.mkdtempSync(path.join(TMP, "deadline-cwd-")));
  const ids = dirs.map((_, i) => `s${i}`);
  const startedAt = Date.now();

  await listSessions({
    cliPath: slowListCli(state, 1000),
    env: { ...process.env, LIST_STATE: state },
    timeoutMs: 400,
    trackedIds: ids,
    cwdById: Object.fromEntries(ids.map((id, i) => [id, dirs[i]])),
    folders: []
  });

  assert.ok(Date.now() - startedAt < 750, `session listing exceeded its 400ms total deadline`);
});

test("adding a session reports the tracked ids it evicts", () => {
  const store = new SessionStore(memento());
  for (let i = 0; i < 200; i++) {
    store.add(`s${i}`, "/w");
  }

  assert.deepStrictEqual(store.add("new", "/w"), ["s0"]);
});

test("removing a session also removes its cached list row", () => {
  const store = new SessionStore(memento());
  store.add("kept", "/w");
  store.add("gone", "/w");
  store.cacheSessionList({
    at: 1,
    sessions: [
      { id: "kept", short_id: "kept", working_directory: "/w" },
      { id: "gone", short_id: "gone", working_directory: "/w" }
    ]
  });

  store.remove("gone");

  assert.deepStrictEqual(store.sessionList().sessions.map((session) => session.id), ["kept"]);
});

test.after(() => {
  fs.rmSync(TMP, { recursive: true, force: true });
});
