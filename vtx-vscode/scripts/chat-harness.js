// Headless harness for the host side of a chat (src/chat/chatViewProvider.ts).
//
// This is the piece the test suite was missing. Everything the chat controller does
// happens across an await: spawning an agent, opening a session, replaying history,
// waking a chat that had exited. The bugs that keep being found in it are all the
// same shape, a chat finishing that work into a panel that has moved on, and none of
// them could be tested, so each was found by someone noticing a symptom and each fix
// was made by reading. This runs the real controller, against a real `devin acp`
// conversation, with the timing under the test's control.
//
// It is the real thing on both sides of the seam:
//   - the real ChatController, ChangeTracker, SessionStore, AcpClient and
//     TerminalManager, compiled out of src/ with `vscode` aliased to vscode-stub.js
//   - a fake agent that is a genuine ACP peer over stdio, and answers the CLI's
//     `--version` and `auth status` too, so even the health check is the real path
//
// The agent's delays are what makes the interleavings reachable: `newDelay` and
// `loadDelay` hold session/new and session/load open for as long as a test needs to
// do something else, which is the window every one of these bugs lives in.

const fs = require("fs");
const os = require("os");
const path = require("path");
const esbuild = require("esbuild");

const ROOT = path.resolve(__dirname, "..");
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "devin-chat-"));

// One bundle for the whole run: esbuild is the slow part, and every harness shares
// the same module instances, which is also what makes `globalThis.__dvVscode` the
// stub the controller is really using.
let loaded;
function load() {
  if (loaded) {
    return loaded;
  }
  const outfile = path.join(TMP, "host.js");
  esbuild.buildSync({
    entryPoints: [path.join(__dirname, "chat-entry.ts")],
    outfile,
    bundle: true,
    platform: "node",
    format: "cjs",
    logLevel: "error",
    alias: { vscode: path.join(__dirname, "vscode-stub.js") }
  });
  loaded = require(outfile);
  return loaded;
}

// A fake agent, wrapped so `<script> acp` starts it and `<script> --version` and
// `<script> auth status` answer the health check the way the real CLI does.
function writeAgent() {
  const js = path.join(TMP, "agent.js");
  const sh = path.join(TMP, "agent.sh");
  fs.writeFileSync(js, AGENT_SOURCE);
  fs.writeFileSync(
    sh,
    `#!/bin/sh
case "$1" in
  --version) echo "devin 3000.0.0-fake"; exit 0 ;;
  auth) echo "Logged in (via Devin)."; echo "  Name: Test User"; exit 0 ;;
  # Returning to the sessions list falls back to the CLI when no agent is live. Left to
  # reach the ACP loop below, it would sit on a stdin nobody writes to until the CLI's
  # own timeout killed it, tracked by nothing that could stop it.
  list) echo '[]'; exit 0 ;;
  models) echo '{"families":[]}'; exit 0 ;;
esac
exec ${JSON.stringify(process.execPath)} ${JSON.stringify(js)}
`
  );
  fs.chmodSync(sh, 0o755);
  return sh;
}

const AGENT_SOURCE = `
// A minimal but honest ACP agent. Line delimited JSON-RPC on stdio, the same as the
// real one, with its delays read from the environment so a test can hold a call open.
const delay = (ms) => new Promise((r) => setTimeout(r, ms));
const num = (k) => Number(process.env[k] || 0);
// Every chat gets its own agent process, so the id has to be unique across them:
// counting from one in each made every chat "s-1", which silently makes two chats
// look like the same chat to anything keyed on the id.
let seq = 0;
const idBase = "s" + process.pid;
const send = (o) => process.stdout.write(JSON.stringify(o) + "\\n");
let currentModel = "fusion-claude-opus-5-high-sidekick-swe-2-medium";
let currentThoughtLevel = "high";
const configOptions = () => [
  {
    id: "mode",
    name: "Session Mode",
    category: "mode",
    type: "select",
    currentValue: process.env.DV_MODE || "default",
    options: [{ value: "default", name: "Default" }, { value: "plan", name: "Plan" }]
  },
  {
    id: "model",
    name: "Model",
    category: "model",
    type: "select",
    currentValue: currentModel,
    options: [{
      group: "fusion",
      name: "Fusion",
      options: [
        { value: "fusion-claude-opus-5-high-sidekick-swe-2-medium", name: "Fusion (Claude Opus 5 High + SWE-2 Medium)" },
        { value: "fusion-gpt-6-astra-high-sidekick-swe-2-high", name: "Fusion (GPT-6 Astra High Thinking + SWE-2 High)" }
      ]
    }]
  },
  {
    id: "thought_level",
    name: "Thinking Level",
    category: "thought_level",
    type: "select",
    currentValue: currentThoughtLevel,
    options: [{ value: "low", name: "Low" }, { value: "high", name: "High" }]
  }
];
let buf = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", async (chunk) => {
  buf += chunk;
  let i;
  while ((i = buf.indexOf("\\n")) !== -1) {
    const line = buf.slice(0, i).trim();
    buf = buf.slice(i + 1);
    if (!line) continue;
    let msg;
    try { msg = JSON.parse(line); } catch { continue; }
    // Every request, in order, so a test can assert what the agent was really asked:
    // which prompt carried which attachment, and how many prompts arrived at all.
    if (process.env.DV_LOG) {
      try { require("fs").appendFileSync(process.env.DV_LOG, JSON.stringify({ ...msg, _agent: process.pid }) + "\\n"); } catch {}
    }
    if (msg.id === undefined) continue;
    const reply = (result) => send({ jsonrpc: "2.0", id: msg.id, result });
    switch (msg.method) {
      case "initialize":
        reply({
          protocolVersion: 1,
          agentCapabilities: {
            loadSession: true,
            promptCapabilities: { image: true, embeddedContext: true },
            _meta: { "cognition.ai/revert": true, "cognition.ai/sessionShare": true, ...(process.env.DV_DOCUMENT_LIFECYCLE === "1" ? { "cognition.ai/documentLifecycle": true } : {}) }
          }
        });
        break;
      case "session/new": {
        const newId = idBase + "-" + ++seq;
        // The real agent announces the session's mode before it answers, so this
        // update arrives while its runtime is not yet in the pool to be found by
        // id. Anything that answers such an update with "whatever is on screen"
        // paints one chat's settings into another's.
        send({ jsonrpc: "2.0", method: "session/update", params: { sessionId: newId, update: { sessionUpdate: "current_mode_update", currentModeId: process.env.DV_MODE || "default" } } });
        await delay(num("DV_NEW_DELAY"));
        reply({ sessionId: newId, modes: { currentModeId: process.env.DV_MODE || "default", availableModes: [{ id: "default", name: "Default" }, { id: "plan", name: "Plan" }] }, configOptions: configOptions() });
        break;
      }
      case "session/set_config_option":
        if (msg.params.configId === "model") currentModel = msg.params.value;
        if (msg.params.configId === "thought_level") currentThoughtLevel = msg.params.value;
        reply({ configOptions: configOptions() });
        break;
      case "session/load":
        await delay(num("DV_LOAD_DELAY"));
        reply({});
        break;
      case "session/prompt":
        // Long by default: a turn is running until the test says otherwise, which is
        // what makes "busy" states reachable.
        await delay(num("DV_PROMPT_DELAY") || 60000);
        reply({ stopReason: "end_turn" });
        break;
      case "_cognition.ai/revert/listSteps": await delay(num("DV_STEPS_DELAY")); reply({ steps: [] }); break;
      case "session/list": reply({ sessions: [] }); break;
      default: reply({});
    }
  }
});
process.stdin.on("end", () => process.exit(0));
`;

let agentPath;
let harnessSeq = 0;

// A Memento over a plain object, which is all SessionStore asks for.
function memento(seed) {
  const map = new Map(Object.entries(seed || {}));
  return {
    get: (k, fallback) => (map.has(k) ? map.get(k) : fallback),
    update: async (k, v) => { map.set(k, v); },
    keys: () => [...map.keys()],
    __map: map
  };
}

// The page, as far as the host can tell: it records what it was told, and lets the
// test say what the user did.
function webviewDouble(posted) {
  return {
    options: {},
    html: "",
    cspSource: "vscode-webview:",
    asWebviewUri: (uri) => uri,
    postMessage: (m) => { posted.push(JSON.parse(JSON.stringify(m))); return Promise.resolve(true); },
    onDidReceiveMessage: (fn) => { webviewDouble.last = fn; return { dispose() {} }; }
  };
}

/**
 * Build a chat surface with an agent behind it.
 *
 * `opts.newDelay` / `opts.loadDelay` / `opts.promptDelay` are the agent's delays in ms,
 * which is how a test gets inside the window between asking for something and it
 * arriving. `opts.storage` and `opts.cwd` reuse another harness's directories, which is
 * how a reload is written. `opts.config` overrides the settings the controller reads.
 */
function createChat(opts = {}) {
  const { ChatController, SessionStore, ChangeTracker } = load();
  const vscode = globalThis.__dvVscode;
  agentPath = agentPath || writeAgent();

  const dir = opts.cwd || fs.mkdtempSync(path.join(TMP, "ws-"));
  // Pass another harness's `storage` to stand in for a window reload, or for a second
  // surface: the extension's storage directory is per workspace, not per window, and it
  // is where anything that outlives an agent is kept.
  const storage = opts.storage || fs.mkdtempSync(path.join(TMP, "store-"));
  // Per harness, not per directory: two harnesses can share a workspace now (that is
  // what a reload is), and a shared log would mix one controller's requests into the
  // other's, which is silent and would make `agentSaw` quietly wrong.
  const agentLog = path.join(storage, `agent-requests.${++harnessSeq}.jsonl`);
  globalThis.__dvFolders = [{ name: path.basename(dir), uri: vscode.Uri.file(dir), index: 0 }];
  // How the agent is told what to do. It goes through the `devin.env` setting rather
  // than this process's environment because the controller reads that on every spawn,
  // while the environment it inherits is read from a login shell once and cached for
  // the run: a delay set through that would only ever reach the first agent.
  const agentEnv = {
    DV_LOG: agentLog,
    DV_NEW_DELAY: String(opts.newDelay || 0),
    DV_LOAD_DELAY: String(opts.loadDelay || 0),
    DV_PROMPT_DELAY: String(opts.promptDelay || 0),
    DV_STEPS_DELAY: String(opts.stepsDelay || 0),
    DV_DOCUMENT_LIFECYCLE: opts.documentLifecycle ? "1" : ""
  };
  globalThis.__dvConfig = Object.assign(
    {
      cliPath: agentPath,
      env: agentEnv,
      // Turned off rather than defaulted: the editor context and a real terminal are
      // their own subjects, and an idle exit firing mid test would look like a bug in
      // whatever was being tested. The key has to be the one the code reads.
      "implicitContext.enabled": false,
      useIntegratedTerminal: false,
      idleSessionKeepAliveMinutes: 0
    },
    opts.config
  );

  const posted = [];
  const logs = [];
  const state = memento(opts.state);
  const store = new SessionStore(state);
  const changes = new ChangeTracker();
  const context = {
    extensionUri: vscode.Uri.file(ROOT),
    storageUri: vscode.Uri.file(storage),
    globalStorageUri: vscode.Uri.file(storage),
    subscriptions: [],
    workspaceState: state,
    globalState: state
  };
  const output = { appendLine: (l) => logs.push(l), dispose() {} };
  const controller = new ChatController(context, store, changes, undefined, output, opts.kind || "view", opts.surfaceHost);
  const webview = webviewDouble(posted);
  controller.bind(webview, () => {});
  const toHost = webviewDouble.last;

  const api = {
    controller,
    store,
    changes,
    state,
    posted,
    logs,
    webview,
    cwd: dir,
    // Where anything that outlives the agent is kept. Hand it to another `createChat` to
    // write what a window reload does.
    storage,
    // What the user did. The real page posts these, and the host cannot tell the
    // difference: this is the same handler VS Code would call.
    send: (msg) => { void toHost(msg); return api; },
    // Wait, in the same units the agent's delays are in.
    settle: (ms = 50) => new Promise((r) => setTimeout(r, ms)),
    // Wait for something to become true rather than for a guessed number of
    // milliseconds: the first chat of a run also pays for the health check and a login
    // shell read, so a fixed wait is either flaky or slow.
    async until(predicate, timeout = 10000) {
      const deadline = Date.now() + timeout;
      while (Date.now() < deadline) {
        if (predicate()) {
          return true;
        }
        await api.settle(25);
      }
      return false;
    },
    // The delays the NEXT agent starts with. Each chat spawns its own, so a test can
    // let the first one open instantly and hold the second one open.
    // The mode the NEXT chat's agent reports itself to be in, so a test can tell one
    // chat's mode from another's and catch a background chat setting the panel's.
    setAgentMode(mode) {
      agentEnv.DV_MODE = String(mode);
      return api;
    },
    setDelays({ newDelay, loadDelay, promptDelay }) {
      if (newDelay !== undefined) agentEnv.DV_NEW_DELAY = String(newDelay);
      if (loadDelay !== undefined) agentEnv.DV_LOAD_DELAY = String(loadDelay);
      if (promptDelay !== undefined) agentEnv.DV_PROMPT_DELAY = String(promptDelay);
      return api;
    },
    // Everything of one type the host posted, oldest first.
    postsOf: (type) => posted.filter((m) => m && m.type === type),
    // What the agent was really asked, in order. `method` filters it.
    agentSaw: (method) => {
      const lines = fs.existsSync(agentLog) ? fs.readFileSync(agentLog, "utf8").trim().split("\n") : [];
      return lines.filter(Boolean).map((l) => JSON.parse(l)).filter((m) => !method || m.method === method);
    },
    last: (type) => [...posted].reverse().find((m) => !type || m.type === type),
    // The chat the panel is showing, which is the thing most of these bugs get wrong.
    activeId: () => controller.activeId,
    // How many chats are really open. A chat is only in the pool once its session/new
    // has been answered, so this is the signal that a chat started in the background has
    // finished starting, which a test has to wait for or it asserts nothing.
    liveChats: () => controller.runtimes.size,
    // What the user picks in a modal (Terminate, Discard). The controller asks through
    // window.showWarningMessage, so this is the answer it gets.
    answerWith: (choice) => { vscode.window.answer = choice; return api; },
    // Announce the page is listening, which is what unblocks anything waiting on it.
    async ready() {
      await toHost({ type: "ready" });
      return api;
    },
    // Start a chat and wait until it is really open, the common setup.
    async startChat(text = "hello") {
      const before = controller.activeId;
      const promptsBefore = api.agentSaw("session/prompt").length;
      api.send({ type: "send", text, newSession: true });
      const opened = await api.until(() => controller.activeId && controller.activeId !== before);
      if (!opened) {
        throw new Error("the chat never opened. Agent log: " + logs.join(" | "));
      }
      const prompted = await api.until(() => api.agentSaw("session/prompt").length > promptsBefore);
      if (!prompted) {
        throw new Error("the chat opened without sending its prompt. Agent log: " + logs.join(" | "));
      }
      return controller.activeId;
    },
    async dispose() {
      built.delete(api);
      // An answer outlives the harness otherwise, and the stub hands it to every dialog,
      // so a later test's rename prompt would be answered "Terminate".
      vscode.window.answer = undefined;
      await controller.shutdown().catch(() => {});
      changes.dispose?.();
    }
  };
  built.add(api);
  return api;
}

// Every harness built in this run. A test that fails throws before its own dispose,
// and an agent left alive keeps the test runner's event loop alive with it, which
// shows up as the whole suite hanging with no output rather than as a failure. So the
// teardown is not the test's responsibility.
const built = new Set();

async function cleanup() {
  await Promise.all([...built].map((h) => h.dispose().catch(() => {})));
  built.clear();
  fs.rmSync(TMP, { recursive: true, force: true });
}

module.exports = { createChat, cleanup };
