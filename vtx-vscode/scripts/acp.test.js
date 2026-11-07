// Protocol tests for the ACP client, run against the real AcpClient compiled out
// of src/ and talking to a scripted fake agent over stdio.
//
// These guard the parts of Vtx's protocol that are documented nowhere and were
// established by probing a real agent (see the matrix in AGENTS.md). A wrong
// assumption here fails silently: the agent stops sending something, or rejects a
// reply, and the panel still looks like it works. So each test pins one contract.
//
// The fake agent is the one driving in most of these, because that is the shape of
// the real thing: the agent pulls diagnostics and pushes its step list, unasked.

const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const esbuild = require("esbuild");

const ROOT = path.resolve(__dirname, "..");
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "devin-acp-"));

function load(rel, name) {
  const outfile = path.join(TMP, name + ".js");
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
const { AcpClient } = load("src/acp/client.ts", "client");
const { loginShellEnv } = load("src/cli/locate.ts", "locate");
const { checkHealth } = load("src/cli/locate.ts", "locate-health");
const { JsonRpcConnection } = load("src/acp/connection.ts", "connection");
const { withQuerySession, shutdownQueryAgents } = load("src/acp/queryClient.ts", "query-client");
const { diagnosticItems, MAX_DIAGNOSTICS } = load("src/acp/diagnostics.ts", "diagnostics");
const vscode = globalThis.__dvVscode;

// VS Code's own diagnostic shape, as much of it as the mapper reads.
function diag(message, severity, { code, source, line = 0 } = {}) {
  return {
    message,
    severity,
    code,
    source,
    range: { start: { line, character: 2 }, end: { line, character: 9 } }
  };
}
const ERROR = 0;
const WARNING = 1;
const INFO = 2;
const HINT = 3;

// Absolute paths in the platform own form. The workspace filter compares with
// path.sep, so a POSIX literal is not inside the root on Windows and every
// diagnostic would be dropped there for a reason that has nothing to do with the
// behaviour under test.
const abs = (...parts) => path.resolve(path.sep, ...parts);
const W = abs("w");
const f = (name) => path.join(W, name);

// A scripted fake agent. It answers `initialize` with `agentMeta`, records every
// message it receives (including the client's replies to its own requests), and
// runs `onMessage` for anything else. `afterInit` is source that runs once the
// initialize reply has gone out, which is where an agent-driven test starts.
function fakeAgent(name, agentMeta, { onMessage = "", afterInit = "" } = {}) {
  const log = path.join(TMP, name + ".log");
  const js = path.join(TMP, name + ".js");
  // The client spawns `<cliPath> acp`, so the fake agent has to look like the CLI
  // itself. Windows refuses to spawn a shebang script, and a .cmd is what npm
  // installs the real CLI as there, which the client already knows to run through
  // a shell.
  const shim = path.join(TMP, name + (process.platform === "win32" ? ".cmd" : ".sh"));
  fs.writeFileSync(js, `
    const fs = require("fs");
    const LOG = ${JSON.stringify(log)};
    let buf = "";
    let nextId = 1000;
    const send = (m) => process.stdout.write(JSON.stringify(m) + "\\n");
    const notify = (method, params) => send({ jsonrpc: "2.0", method, params });
    const ask = (method, params) => send({ jsonrpc: "2.0", id: nextId++, method, params });
    const record = (m) => fs.appendFileSync(LOG, JSON.stringify(m) + "\\n");
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      buf += chunk;
      let i;
      while ((i = buf.indexOf("\\n")) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 1);
        if (!line) continue;
        const msg = JSON.parse(line);
        record(msg);
        if (msg.method === "initialize") {
          send({ jsonrpc: "2.0", id: msg.id, result: { protocolVersion: 1, agentCapabilities: { _meta: ${JSON.stringify(agentMeta)} } } });
          ${afterInit}
          continue;
        }
        ${onMessage}
        if (msg.id !== undefined) send({ jsonrpc: "2.0", id: msg.id, result: {} });
      }
    });
    process.stdin.on("end", () => process.exit(0));
  `);
  if (process.platform === "win32") {
    fs.writeFileSync(shim, `@echo off\r\n"${process.execPath}" "${js}"\r\n`);
  } else {
    fs.writeFileSync(shim, `#!/bin/sh\nexec ${JSON.stringify(process.execPath)} ${JSON.stringify(js)}\n`);
    fs.chmodSync(shim, 0o755);
  }
  return { cli: shim, log };
}

function seen(log) {
  if (!fs.existsSync(log)) return [];
  return fs.readFileSync(log, "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l));
}

function start(cli, options = {}) {
  const client = new AcpClient({ cliPath: cli, cwd: TMP, env: process.env, ...options });
  client.start();
  return client;
}

// Wait for the fake agent's log to satisfy a predicate, so tests do not race the
// pipe. Returns the matching entry, or undefined once the budget is spent.
async function waitFor(log, match, ms = 3000) {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    const hit = seen(log).find(match);
    if (hit) return hit;
    await new Promise((r) => setTimeout(r, 25));
  }
  return undefined;
}

async function settle(ms = 150) {
  await new Promise((r) => setTimeout(r, ms));
}

test("concurrent login shell lookups share one process", { skip: process.platform === "win32" }, async () => {
  const shell = path.join(TMP, "slow-shell.sh");
  const log = path.join(TMP, "shell.log");
  fs.writeFileSync(shell, `#!/bin/sh\nprintf x >> "$LOGIN_LOG"\nsleep 0.1\nprintf '__PATH__=%s' "$PATH"\n`);
  fs.chmodSync(shell, 0o755);
  const previousShell = process.env.SHELL;
  const previousLog = process.env.LOGIN_LOG;
  process.env.SHELL = shell;
  process.env.LOGIN_LOG = log;
  try {
    await Promise.all([loginShellEnv(), loginShellEnv()]);
    assert.strictEqual(fs.readFileSync(log, "utf8"), "x");
  } finally {
    if (previousShell === undefined) delete process.env.SHELL;
    else process.env.SHELL = previousShell;
    if (previousLog === undefined) delete process.env.LOGIN_LOG;
    else process.env.LOGIN_LOG = previousLog;
  }
});

test("concurrent health checks share version and authentication probes", { skip: process.platform === "win32" }, async () => {
  const cli = path.join(TMP, "health-cli.sh");
  const log = path.join(TMP, "health.log");
  fs.writeFileSync(cli, `#!/bin/sh\nprintf x >> "$HEALTH_LOG"\nsleep 0.1\ncase "$1" in\n  --version) echo "devin 1.0.0" ;;\n  auth) echo "Logged in." ;;\nesac\n`);
  fs.chmodSync(cli, 0o755);
  const previousLog = process.env.HEALTH_LOG;
  process.env.HEALTH_LOG = log;
  try {
    await Promise.all([checkHealth(cli), checkHealth(cli)]);
    assert.strictEqual(fs.readFileSync(log, "utf8"), "xx");
  } finally {
    if (previousLog === undefined) delete process.env.HEALTH_LOG;
    else process.env.HEALTH_LOG = previousLog;
  }
});

test("a call that must not hang gives up, and one that may run long is left alone", async () => {
  // A reply is otherwise settled only by the agent answering or its process
  // closing, so an agent that is alive and silent (a blocking MCP server, a token
  // refresh) leaves the caller waiting for the rest of the window: the panel sits
  // on "starting" and New chat never works again.
  const { spawn } = require("child_process");
  const child = spawn(process.execPath, ["-e", "process.stdin.resume()"], { stdio: ["pipe", "pipe", "pipe"] });
  const logs = [];
  const conn = new JsonRpcConnection(child, async () => ({}), () => {}, (l) => logs.push(l));
  try {
    const started = Date.now();
    await assert.rejects(
      () => conn.request("initialize", {}, 250),
      /did not answer within/,
      "a bounded call has to reject rather than wait for ever"
    );
    assert.ok(Date.now() - started < 3000, "and it rejects when it said it would");
    assert.ok(logs.some((l) => l.includes("[rpc-timeout]")), "with a line saying which call it was");

    // A prompt is a whole turn and can legitimately take many minutes, so no
    // timeout is passed for it and none is imposed.
    let settled = false;
    void conn.request("session/prompt", {}).then(() => { settled = true; }, () => { settled = true; });
    await new Promise((r) => setTimeout(r, 400));
    assert.strictEqual(settled, false, "an unbounded call is still waiting");
  } finally {
    conn.dispose();
    child.kill("SIGKILL");
  }
});

test("ACP stderr keeps actionable lines without credentials", async () => {
  const { spawn } = require("child_process");
  const child = spawn(process.execPath, ["-e", "process.stderr.write('INFO startup Authorization:secret\\nWARN connection failed Authorization:secret\\n')"], { stdio: ["pipe", "pipe", "pipe"] });
  const logs = [];
  const conn = new JsonRpcConnection(child, async () => ({}), () => {}, (line) => logs.push(line));
  try {
    await settle();
    assert.deepStrictEqual(logs, ["WARN connection failed Authorization:[redacted]"]);
  } finally {
    conn.dispose();
    child.kill("SIGKILL");
  }
});

test("ACP stdout drops an oversized unterminated frame", async () => {
  const { spawn } = require("child_process");
  const child = spawn(process.execPath, ["-e", "process.stdout.write('x'.repeat(8 * 1024 * 1024 + 1)); setTimeout(() => {}, 1000)"], { stdio: ["pipe", "pipe", "pipe"] });
  const logs = [];
  const conn = new JsonRpcConnection(child, async () => ({}), () => {}, (line) => logs.push(line));
  try {
    await settle(300);
    assert.strictEqual(conn.buffer, "");
    assert.ok(logs.some((line) => line.includes("frame exceeded")));
  } finally {
    conn.dispose();
    child.kill("SIGKILL");
  }
});

test("ACP stdout drops an oversized complete frame", async () => {
  const { spawn } = require("child_process");
  const child = spawn(process.execPath, ["-e", "process.stdout.write('x'.repeat(8 * 1024 * 1024 + 1) + '\\n'); setTimeout(() => {}, 1000)"], { stdio: ["pipe", "pipe", "pipe"] });
  const logs = [];
  const conn = new JsonRpcConnection(child, async () => ({}), () => {}, (line) => logs.push(line));
  try {
    await settle(300);
    assert.ok(logs.some((line) => line.includes("frame exceeded")));
  } finally {
    conn.dispose();
    child.kill("SIGKILL");
  }
});

test("ACP stderr drops an oversized unterminated line", async () => {
  const { spawn } = require("child_process");
  const child = spawn(process.execPath, ["-e", "process.stderr.write('x'.repeat(64 * 1024 + 1)); setTimeout(() => {}, 1000)"], { stdio: ["pipe", "pipe", "pipe"] });
  const logs = [];
  const conn = new JsonRpcConnection(child, async () => ({}), () => {}, (line) => logs.push(line));
  try {
    await settle(150);
    assert.strictEqual(conn.stderr, "");
    assert.ok(logs.some((line) => line.includes("stderr line exceeded")));
  } finally {
    conn.dispose();
    child.kill("SIGKILL");
  }
});

test("every call but a turn is bounded, so no feature can wait for ever", () => {
  // The rule the connection layer states: the handshake, opening a session and the
  // short queries are bounded, and only a prompt is not, because it is a whole turn.
  // Read from the source because that is where the rule can be broken: a method added
  // without a timeout looks exactly like one with it until an agent goes quiet, and
  // then whatever waited on it waits for the rest of the window. The settings panel
  // did, on the two calls that ask what is loaded, and it also leaked the agent it
  // had opened to ask, because the shutdown in its `finally` was never reached.
  const src = fs.readFileSync(path.join(ROOT, "src/acp/client.ts"), "utf8");
  const unbounded = [];
  for (let i = src.indexOf("this.rpc"); i !== -1; i = src.indexOf("this.rpc", i + 1)) {
    const open = src.indexOf("(", i);
    let depth = 0;
    let end = open;
    for (; end < src.length; end++) {
      if (src[end] === "(") depth++;
      else if (src[end] === ")" && --depth === 0) break;
    }
    const call = src.slice(open, end);
    if (!/TIMEOUT_MS/.test(call)) {
      unbounded.push((call.match(/"([^"]+)"/) || [])[1] || call.slice(0, 40));
    }
  }
  assert.deepStrictEqual(unbounded, ["session/prompt"], "only a turn may be unbounded");
});

test("initialize promises only the capabilities we actually serve", async () => {
  const { cli, log } = fakeAgent("caps", {});
  const client = start(cli, { diagnostics: true });
  await client.initialize();
  const init = seen(log).find((m) => m.method === "initialize");
  const declared = Object.keys(init.params.clientCapabilities._meta);

  // Declaring one of these is a promise to serve it, and the agent never echoes
  // our declarations back, so dropping one here turns a feature off in silence.
  for (const key of [
    "cognition.ai/revert",
    "cognition.ai/subagentSupport",
    "cognition.ai/subagentControl",
    "cognition.ai/requestDiagnostics",
    "cognition.ai/documentLifecycle"
  ]) {
    assert.ok(declared.includes(key), `expected to declare ${key}, declared: ${declared.join(", ")}`);
  }
  assert.strictEqual(init.params.clientCapabilities.fs.readTextFile, true);
  assert.strictEqual(init.params.clientCapabilities.terminal, true);
  await client.shutdown();
});

test("initialize omits automatic diagnostics when disabled", async () => {
  const { cli, log } = fakeAgent("caps-off", {});
  const client = start(cli, { diagnostics: false });
  await client.initialize();
  const init = seen(log).find((m) => m.method === "initialize");
  assert.ok(
    !Object.hasOwn(init.params.clientCapabilities._meta, "cognition.ai/requestDiagnostics"),
    "disabled diagnostics must not be advertised to the agent"
  );
  await client.shutdown();
});

test("a diagnostics pull is answered in the shape the agent demands", async () => {
  // The real agent pulls on its own schedule and rejects anything that is not a
  // RequestDiagnosticsResult: a bare null fails with "invalid type: null", and an
  // item without `uri` fails with "missing field `uri`".
  const { cli, log } = fakeAgent("diag", {}, {
    afterInit: `ask("_cognition.ai/request_diagnostics", {});`
  });
  const client = start(cli, { diagnostics: true });
  client.setHost({
    requestDiagnostics: () => ({
      items: [{
        uri: "file:///work/a.ts",
        id: "ts2304",
        message: "Cannot find name 'zzyzx'",
        severity: "error",
        source: "ts",
        range: { start: { line: 41, character: 6 }, end: { line: 41, character: 11 } }
      }]
    })
  });
  await client.initialize();

  const reply = await waitFor(log, (m) => m.result && Array.isArray(m.result.items));
  assert.ok(reply, "the client never answered the diagnostics pull");
  const item = reply.result.items[0];
  assert.strictEqual(item.uri, "file:///work/a.ts", "uri is required: without it the agent rejects the whole reply");
  assert.strictEqual(item.severity, "error");
  assert.strictEqual(item.range.start.line, 41, "ranges stay zero based, the agent renders them one based itself");
  await client.shutdown();
});

test("session listing accepts a caller supplied timeout", async () => {
  const client = new AcpClient({ cliPath: "devin", cwd: TMP });
  let timeout;
  client.rpc = async (_method, _params, value) => {
    timeout = value;
    return { sessions: [] };
  };

  assert.deepStrictEqual(await client.listSessions(125), []);
  assert.strictEqual(timeout, 125);
});

test("document lifecycle is only sent to an agent that supports it", async () => {
  const off = fakeAgent("doc-off", {});
  const client = start(off.cli);
  await client.initialize();
  client.documentEvent("didOpen", { sessionId: "s1", uri: "file:///a.ts", languageId: "typescript" });
  await settle();
  assert.ok(
    !seen(off.log).some((m) => String(m.method || "").includes("document/")),
    "an agent that does not advertise documentLifecycle logs a parse failure for every event it is sent"
  );
  await client.shutdown();

  const on = fakeAgent("doc-on", { "cognition.ai/documentLifecycle": true });
  const client2 = start(on.cli);
  await client2.initialize();
  client2.documentEvent("didFocus", { sessionId: "s1", uri: "file:///a.ts", languageId: "typescript" });
  const note = await waitFor(on.log, (m) => String(m.method || "").includes("document/didFocus"));
  assert.ok(note, "expected the didFocus notification to reach the agent");
  assert.strictEqual(note.method, "_cognition.ai/document/didFocus");
  assert.strictEqual(note.id, undefined, "it is a notification, so it must carry no id");
  // A plain path here instead of a uri is rejected by the real agent.
  assert.strictEqual(note.params.uri, "file:///a.ts");
  assert.strictEqual(note.params.sessionId, "s1");
  await client2.shutdown();
});

test("the revert head comes from the step list, not from parsing an error", async () => {
  const { cli, log } = fakeAgent("steps", { "cognition.ai/revert": true }, {
    onMessage: `if (msg.method === "_cognition.ai/revert/listSteps") {
      send({ jsonrpc: "2.0", id: msg.id, result: { steps: [
        { stepNumber: 1, kind: "prompt", revertTargetNodeId: 1, forkTargetNodeId: 2, summary: "first" },
        { stepNumber: 2, kind: "prompt", revertTargetNodeId: 7, forkTargetNodeId: 8, summary: "second" }
      ] } });
      continue;
    }`
  });
  const client = start(cli);
  await client.initialize();
  assert.strictEqual(client.supportsRevert(), true);

  const steps = await client.listRevertSteps("s1");
  assert.strictEqual(steps.length, 2);
  assert.strictEqual(steps[1].forkTargetNodeId, 8, "fork targets differ from revert targets, which is what makes forking possible");

  // The head is the newest step's FORK target, not its revert target. The two are
  // a turn apart: a step's revert target is the node before it ran. Reading the
  // revert target here rewinds every checkpoint one turn too far, which is a real
  // bug this test was written to allow before the agent was asked what it means.
  assert.strictEqual(await client.currentHead("s1"), 8);
  assert.ok(
    !seen(log).some((m) => m.method === "_cognition.ai/revert/preview"),
    "the head must come from listSteps, never from a preview probed for its error text"
  );
  await client.shutdown();
});

test("a step list with no fork target still yields a head", async () => {
  // Older agents, and a step the agent reports without one, must not leave the
  // checkpoint row with nothing to rewind to.
  const { cli } = fakeAgent("steps-legacy", { "cognition.ai/revert": true }, {
    onMessage: `if (msg.method === "_cognition.ai/revert/listSteps") {
      send({ jsonrpc: "2.0", id: msg.id, result: { steps: [{ stepNumber: 1, revertTargetNodeId: 5 }] } });
      continue;
    }`
  });
  const client = start(cli);
  await client.initialize();
  assert.strictEqual(await client.currentHead("s1"), 5);
  await client.shutdown();
});

test("a session with no history yet has no head", async () => {
  const { cli } = fakeAgent("steps-empty", { "cognition.ai/revert": true }, {
    onMessage: `if (msg.method === "_cognition.ai/revert/listSteps") {
      send({ jsonrpc: "2.0", id: msg.id, result: { steps: [] } });
      continue;
    }`
  });
  const client = start(cli);
  await client.initialize();
  assert.strictEqual(await client.currentHead("s1"), null, "a fresh session has nothing to revert to");
  await client.shutdown();
});

test("forking asks for the fork target and reports the new session", async () => {
  // Verified against the agent: the param is `targetNodeId` (not `stepNumber`),
  // and the agent copies the conversation into a NEW session rather than rewinding
  // this one, so the id it returns is the chat to open.
  const { cli, log } = fakeAgent("fork", { "cognition.ai/revert": true }, {
    onMessage: `if (msg.method === "_cognition.ai/revert/forkFromStep") {
      send({ jsonrpc: "2.0", id: msg.id, result: { forkedSessionId: "living-advantage" } });
      continue;
    }`
  });
  const client = start(cli);
  await client.initialize();
  const forked = await client.revertForkFromStep("s1", 31);
  assert.strictEqual(forked, "living-advantage");
  const call = seen(log).find((m) => m.method === "_cognition.ai/revert/forkFromStep");
  assert.deepStrictEqual(call.params, { sessionId: "s1", targetNodeId: 31 });
  await client.shutdown();
});

test("pushed notifications become events, and MCP churn is noted once", async () => {
  // Both of these arrive unasked from the real agent. The MCP one fires dozens of
  // times per turn with an empty payload, which used to flood the output channel.
  const { cli } = fakeAgent("push", {}, {
    afterInit: `
      notify("_cognition.ai/revert/stepsUpdated", { sessionId: "s1", steps: [{ stepNumber: 1, revertTargetNodeId: 3 }] });
      for (let i = 0; i < 6; i++) notify("_cognition.ai/mcp/serversChanged", {});
    `
  });
  const client = start(cli);
  const pushes = [];
  const logs = [];
  client.on("revertSteps", (u) => pushes.push(u));
  client.on("log", (l) => logs.push(l));
  await client.initialize();
  await settle(400);

  assert.strictEqual(pushes.length, 1, "the step list push should surface as one event");
  assert.strictEqual(pushes[0].steps[0].revertTargetNodeId, 3);
  assert.strictEqual(
    logs.filter((l) => l.includes("[mcp]")).length,
    1,
    "six notifications carrying nothing new should be noted once"
  );
  assert.strictEqual(
    logs.filter((l) => l.includes("[notify]")).length,
    0,
    "neither notification should fall through to the raw notify log"
  );
  await client.shutdown();
});

// --- The diagnostics the agent is handed -----------------------------------
// A filter, not a dump: what is left out matters as much as what goes in, since
// everything here costs the agent context it could have spent on the actual task.

test("only real defects are sent, as uris the agent will accept", () => {
  const items = diagnosticItems([
    [vscode.Uri.file(f("a.ts")), [
      diag("broken", ERROR, { code: "ts2304", source: "ts" }),
      diag("untidy", WARNING, { code: "no-unused-vars", source: "eslint" }),
      diag("just so you know", INFO),
      diag("could be nicer", HINT)
    ]]
  ], { roots: [W] });

  assert.deepStrictEqual(items.map((i) => i.severity), ["error", "warning"], "hints and information are editor UI, not defects");
  assert.match(items[0].uri, /^file:\/\/\S*a\.ts$/, "a path instead of a uri makes the agent reject the whole reply");
  assert.strictEqual(items[0].id, "ts2304");
  assert.strictEqual(items[1].id, "no-unused-vars");
  assert.strictEqual(items[0].range.start.line, 0, "ranges stay zero based");
});

test("an unlabelled diagnostic still gets a stable id", () => {
  const [withSource, bare] = diagnosticItems([
    [vscode.Uri.file(f("a.ts")), [diag("no code", ERROR, { source: "ts" })]],
    [vscode.Uri.file(f("b.ts")), [diag("nothing at all", ERROR)]]
  ], { roots: [W] });
  assert.strictEqual(withSource.id, "ts");
  assert.strictEqual(bare.id, "diagnostic");
});

test("a rule that carries a documentation link still reduces to a string", () => {
  // VS Code allows code to be an object with a link to the rule's docs.
  const [item] = diagnosticItems([
    [vscode.Uri.file(f("a.ts")), [diag("linked", ERROR, { code: { value: "ts6133", target: "https://x/y" } })]]
  ], { roots: [W] });
  assert.strictEqual(item.id, "ts6133");
});

test("diagnostics outside the workspace are dropped, and non-file schemes with them", () => {
  const items = diagnosticItems([
    [vscode.Uri.file(f("mine.ts")), [diag("mine", ERROR)]],
    [vscode.Uri.file(abs("elsewhere", "theirs.ts")), [diag("not actionable", ERROR)]],
    [{ scheme: "untitled", fsPath: f("scratch"), toString: () => "untitled:/w/scratch" }, [diag("unsaved", ERROR)]]
  ], { roots: [W] });
  assert.deepStrictEqual(items.map((i) => i.message), ["mine"]);
});

test("a path that merely starts with the root is not inside it", () => {
  const items = diagnosticItems([
    [vscode.Uri.file(abs("work-other", "a.ts")), [diag("different project", ERROR)]]
  ], { roots: [abs("work")] });
  assert.deepStrictEqual(items, [], "/work-other is not under /work");
});

test("a folder that is itself a drive root still contains its files", () => {
  // "/" is a folder a user can open, and so is "C:\\". The root already ends in a
  // separator there, and adding another made nothing match, so the agent was told
  // the code had no problems at all.
  const items = diagnosticItems([
    [vscode.Uri.file(abs("anywhere", "a.ts")), [diag("a real error", ERROR)]]
  ], { roots: [path.parse(W).root] });
  assert.deepStrictEqual(items.map((i) => i.message), ["a real error"]);
});

test("with no workspace open, nothing is filtered by location", () => {
  const items = diagnosticItems([
    [vscode.Uri.file(abs("anywhere", "a.ts")), [diag("still worth knowing", ERROR)]]
  ], { roots: [] });
  assert.strictEqual(items.length, 1);
});

test("files the agent just edited come first, then errors before warnings", () => {
  const items = diagnosticItems([
    [vscode.Uri.file(f("untouched.ts")), [diag("old error", ERROR)]],
    [vscode.Uri.file(f("edited.ts")), [diag("its warning", WARNING), diag("its error", ERROR)]]
  ], { roots: [W], touched: new Set([f("edited.ts")]) });

  assert.deepStrictEqual(
    items.map((i) => i.message),
    ["its error", "its warning", "old error"],
    "what the agent just broke is what it needs to see first"
  );
});

test("the list is capped, so one broken file cannot eat the whole context", () => {
  const many = Array.from({ length: MAX_DIAGNOSTICS + 50 }, (_, i) => diag("e" + i, ERROR, { line: i }));
  const items = diagnosticItems([[vscode.Uri.file(f("a.ts")), many]], { roots: [W] });
  assert.strictEqual(items.length, MAX_DIAGNOSTICS);
});

test("a touched file still wins after the cap is applied", () => {
  const noise = Array.from({ length: MAX_DIAGNOSTICS + 10 }, (_, i) => diag("noise" + i, ERROR, { line: i }));
  const items = diagnosticItems([
    [vscode.Uri.file(f("noisy.ts")), noise],
    [vscode.Uri.file(f("edited.ts")), [diag("the one that matters", ERROR)]]
  ], { roots: [W], touched: new Set([f("edited.ts")]) });

  assert.strictEqual(items.length, MAX_DIAGNOSTICS);
  assert.strictEqual(items[0].message, "the one that matters", "the cap must not throw away the relevant one");
});

test("query sessions do not start after shutdown begins", async () => {
  const { cli, log } = fakeAgent("query-shutdown", {});
  await shutdownQueryAgents();

  const result = await withQuerySession(cli, TMP, process.env, async () => "started");

  assert.strictEqual(result, undefined);
  assert.strictEqual(seen(log).some((m) => m.method === "initialize"), false);
});

test.after(() => {
  fs.rmSync(TMP, { recursive: true, force: true });
});
