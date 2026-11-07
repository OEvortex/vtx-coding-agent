// ACP probe: drive a real `devin acp` session end to end and log every message
// so we can see the true payload shapes the extension has to render.
//
// It speaks the same newline-delimited JSON-RPC the extension does (see
// src/acp/connection.ts), performs the initialize -> session/new -> session/prompt
// handshake, switches to Bypass mode and auto-approves anything so tools actually
// run, and records every session/update notification and agent->client request to
// a JSONL file. Use it whenever you add rendering for a new tool and need to know
// the exact `kind`, `_meta` (cognition.ai/inferenceToolName, toolName, eventType),
// `rawInput` and `content` a given tool emits.
//
// It also answers the three questions that come up whenever Devin's ACP surface
// moves: which client capabilities does this build look for, which custom methods
// does it actually implement, and does it consume a given notification. Those are
// only discoverable by asking a real agent, so `--caps`, `--methods` and `--notify`
// exist to keep the answers reproducible after a CLI upgrade rather than
// rediscovered by hand. See AGENTS.md for the matrix this produced.
//
// Usage:
//   node scripts/acp-probe.js --prompt "search the web for the node lts version"
//   node scripts/acp-probe.js -p "call the time MCP tool for UTC" --out /tmp/mcp.jsonl
//   node scripts/acp-probe.js -p "fetch https://example.com" --summary
//   node scripts/acp-probe.js --caps all --caps-report --no-prompt
//   node scripts/acp-probe.js --methods _cognition.ai/rules/list,session/list --no-prompt
//   node scripts/acp-probe.js --caps all --notify _cognition.ai/document/didOpen \
//     --notify-params '{"path":"/tmp/a.ts"}' -p "which files do I have open?"
//
// Flags (all optional):
//   -p, --prompt <text>   prompt to send            (default: "Say hi in one word.")
//   -o, --out <file>      JSONL transcript path      (default: scripts/.probe/last.jsonl)
//   -c, --cwd <dir>       session working directory  (default: process.cwd())
//       --cli <path>      devin binary               (default: $DEVIN_CLI or `devin`)
//       --mcp <json>      mcpServers array for session/new (default: [])
//       --watchdog <ms>   hard kill timeout          (default: 180000)
//       --summary         also print a tool-call summary to stdout at the end
//       --quiet           do not tail updates to stdout while running
//       --caps <list>     client capabilities to declare: short or full names,
//                         `all` for every key this build is known to look for,
//                         `none` for bare ACP  (default: what the extension sends)
//       --caps-report     print declared vs echoed agent capabilities and exit code 1
//                         if a declared key is not echoed back
//       --methods <list>  call each method and report whether it exists (-32601 = no).
//                         Runs before the prompt, and without a session when possible
//       --notify <m>      send one notification after the session opens
//       --notify-params <json>  params for it (sessionId is merged in)
//       --no-prompt       skip the prompt: capability and method probing only
//       --keep-session    do not delete the probe session on the way out
//       --mode <id|keep>  session mode to use. `keep` leaves it as opened, which is
//                         how to provoke a permission prompt (default: bypass, so
//                         tools run unattended and nothing asks for approval)
//
// Env fallbacks mirror the flags: DEVIN_CLI, PROBE_PROMPT, PROBE_OUT, PROBE_CWD,
// PROBE_MCP, PROBE_WATCHDOG.

const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

// Every client capability key this build of `devin acp` is known to look for,
// read out of the binary's own capability list. Declaring one is a promise to
// serve it, so this is a probe aid, not a list to copy into the extension.
const KNOWN_CAPS = [
  "revert",
  "subagentSupport",
  "subagentControl",
  "partialContent",
  "groupedSessionConfigOptions",
  "stopOnReject",
  "mcp",
  "plugins",
  "workspaceDirCommands",
  "clipboardWrite",
  "windsurfConfigBridge",
  "requestDiagnostics",
  "editorContext",
  "terminalContext",
  "fastContext",
  "browserPreview",
  "browserPreviewOpen",
  "messageGrouping",
  "documentLifecycle",
  "userEdits",
  "terminalLifecycle",
  "editableCommands",
  "commandRevision",
  "sessionShare",
  "ruleMentions",
  "multiRootWorkspace"
];

// What the extension itself declares today (src/acp/client.ts), so a probe with
// no --caps reproduces the extension's own view of the agent. Keep this in step
// with `initialize` there, or the probe stops telling you about the real thing.
const EXTENSION_CAPS = [
  "revert",
  "subagentSupport",
  "subagentControl",
  "groupedSessionConfigOptions",
  "multiRootWorkspace",
  "requestDiagnostics",
  "documentLifecycle",
  "stopOnReject",
  "partialContent"
];

function parseArgs(argv) {
  const out = {};
  const alias = { p: "prompt", o: "out", c: "cwd" };
  const flags = new Set(["summary", "quiet", "caps-report", "no-prompt", "keep-session"]);
  for (let i = 0; i < argv.length; i++) {
    let a = argv[i];
    if (!a.startsWith("-")) continue;
    a = a.replace(/^--?/, "");
    a = alias[a] || a;
    if (flags.has(a)) { out[a] = true; continue; }
    out[a] = argv[++i];
  }
  return out;
}

// "all" | "none" | a comma list of short ("editorContext") or full
// ("cognition.ai/editorContext") names, to the _meta object initialize wants.
function capsMeta(spec) {
  const names = spec === undefined
    ? EXTENSION_CAPS
    : spec === "all" ? KNOWN_CAPS
    : spec === "none" ? []
    : String(spec).split(",").map((s) => s.trim()).filter(Boolean);
  const meta = {};
  for (const n of names) {
    meta[n.startsWith("cognition.ai/") ? n : `cognition.ai/${n}`] = true;
  }
  return meta;
}

const args = parseArgs(process.argv.slice(2));
const CLI = args.cli || process.env.DEVIN_CLI || "devin";
const CWD = args.cwd || process.env.PROBE_CWD || process.cwd();
const PROMPT = args.prompt || process.env.PROBE_PROMPT || "Say hi in one word.";
const OUT = path.resolve(args.out || process.env.PROBE_OUT || path.join(__dirname, ".probe", "last.jsonl"));
const MCP = args.mcp ? JSON.parse(args.mcp) : (process.env.PROBE_MCP ? JSON.parse(process.env.PROBE_MCP) : []);
const WATCHDOG = Number(args.watchdog || process.env.PROBE_WATCHDOG || 180000);
const QUIET = !!args.quiet;
const SUMMARY = !!args.summary;
const CAPS = capsMeta(args.caps);
const CAPS_REPORT = !!args["caps-report"];
const METHODS = args.methods ? String(args.methods).split(",").map((s) => s.trim()).filter(Boolean) : [];
const NOTIFY = args.notify;
const NOTIFY_PARAMS = args["notify-params"] ? JSON.parse(args["notify-params"]) : {};
const NO_PROMPT = !!args["no-prompt"];
const KEEP_SESSION = !!args["keep-session"];
// Session mode: an id to set, or `keep` to leave the session in the mode it opened
// in. Default hunts for a bypass-style mode so tools run without approval.
const MODE = args.mode;
// The file the synthetic diagnostic is reported against. Defaults to the uri the
// --notify params name, so `didOpen` and the diagnostic agree on the document.
const DIAG_FIXTURE = String(NOTIFY_PARAMS.uri || "").replace(/^file:\/\//, "")
  || path.join(CWD, "src", "probe-fixture.ts");

fs.mkdirSync(path.dirname(OUT), { recursive: true });
const log = fs.createWriteStream(OUT, { flags: "w" });
const records = [];
function rec(kind, obj) {
  const entry = { t: Date.now(), kind, ...obj };
  records.push(entry);
  log.write(JSON.stringify(entry) + "\n");
}
function tail(line) { if (!QUIET) process.stderr.write(line + "\n"); }

const child = spawn(CLI, ["acp"], { cwd: CWD, env: process.env, stdio: ["pipe", "pipe", "pipe"] });
setTimeout(() => { try { child.kill("SIGKILL"); } catch {} try { log.end(); } catch {} process.exit(2); }, WATCHDOG).unref();

let buf = "";
let nextId = 1;
const pending = new Map();

child.stdout.setEncoding("utf8");
child.stdout.on("data", (chunk) => {
  buf += chunk;
  let i;
  while ((i = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, i).trim();
    buf = buf.slice(i + 1);
    if (line) dispatch(line);
  }
});
child.stderr.setEncoding("utf8");
child.stderr.on("data", (c) => rec("stderr", { text: c.toString() }));
child.on("close", (code) => { rec("exit", { code }); log.end(); });

function send(msg) { child.stdin.write(JSON.stringify(msg) + "\n"); }
function request(method, params) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    send({ jsonrpc: "2.0", id, method, params });
  });
}

function dispatch(line) {
  let msg;
  try { msg = JSON.parse(line); } catch { rec("nonjson", { line }); return; }
  if (msg.id !== undefined && msg.method !== undefined) { handleReq(msg); return; }
  if (msg.id !== undefined) {
    const p = pending.get(msg.id);
    if (!p) return;
    pending.delete(msg.id);
    if (msg.error) p.reject(msg.error); else p.resolve(msg.result);
    return;
  }
  if (msg.method !== undefined) {
    if (msg.method === "session/update") {
      const u = msg.params.update;
      rec("update", { update: u });
      tail("[update] " + (u.sessionUpdate || "?") + (u.title ? " | " + u.title : ""));
    } else {
      rec("notify", { method: msg.method, params: msg.params });
    }
  }
}

// Serve the agent's client-side requests: approve everything, satisfy file reads,
// and no-op terminals so tools run to completion without a real UI.
async function handleReq(msg) {
  rec("client_request", { method: msg.method, params: msg.params });
  let result = null;
  if (msg.method === "session/request_permission") {
    const opts = msg.params.options || [];
    const allow = opts.find((o) => (o.kind || "").startsWith("allow")) || opts[0];
    result = allow ? { outcome: { outcome: "selected", optionId: allow.optionId } } : { outcome: { outcome: "cancelled" } };
  } else if (msg.method === "fs/read_text_file") {
    try { result = { content: fs.readFileSync(msg.params.path, "utf8") }; } catch { result = { content: "" }; }
  } else if (msg.method === "fs/write_text_file") {
    result = null;
  } else if (msg.method === "terminal/create") {
    result = { terminalId: "term-noop" };
  } else if (msg.method === "terminal/output") {
    result = { output: "", truncated: false, exitStatus: { exitCode: 0, signal: null } };
  } else if (msg.method === "terminal/wait_for_exit") {
    result = { exitCode: 0, signal: null };
  } else if (msg.method === "_cognition.ai/request_diagnostics") {
    // The agent pulls these once `requestDiagnostics` is declared, and rejects a
    // null ("expected struct RequestDiagnosticsResult"). One synthetic item, so a
    // prompt can prove whether the agent really read it.
    // Points at a real file so a prompt can be asked about it: the agent only
    // surfaces diagnostics for documents it has been told are open, so pair this
    // with --notify _cognition.ai/document/didOpen for the same uri.
    const fixture = DIAG_FIXTURE;
    result = {
      items: [
        {
          uri: "file://" + fixture,
          id: "ts2304",
          message: "Probe fixture diagnostic: cannot find name 'zzyzx'",
          severity: "error",
          source: "ts",
          range: { start: { line: 41, character: 6 }, end: { line: 41, character: 11 } }
        }
      ]
    };
  }
  send({ jsonrpc: "2.0", id: msg.id, result: result ?? null });
}

function printSummary() {
  const byTool = new Map();
  for (const r of records) {
    if (r.kind !== "update") continue;
    const u = r.update;
    if (u.sessionUpdate !== "tool_call" && u.sessionUpdate !== "tool_call_update") continue;
    const id = u.toolCallId;
    const cur = byTool.get(id) || { id };
    if (u.title) cur.title = u.title;
    if (u.kind) cur.kind = u.kind;
    if (u.status) cur.status = u.status;
    if (u.rawInput !== undefined) cur.rawInput = u.rawInput;
    if (u._meta) cur.meta = Object.assign(cur.meta || {}, u._meta);
    if (Array.isArray(u.content) && u.content.length) cur.content = u.content;
    byTool.set(id, cur);
  }
  process.stdout.write("\n=== tool calls ===\n");
  for (const t of byTool.values()) {
    process.stdout.write(`- ${t.title || "(untitled)"}  [kind=${t.kind || "-"} status=${t.status || "-"}]\n`);
    if (t.meta) process.stdout.write(`    meta: ${JSON.stringify(t.meta)}\n`);
    if (t.rawInput !== undefined) process.stdout.write(`    rawInput: ${JSON.stringify(t.rawInput)}\n`);
    if (t.content) process.stdout.write(`    content: ${JSON.stringify(t.content).slice(0, 400)}\n`);
  }
}

// Which capabilities the agent echoed back, against what we declared. The agent
// echoes some keys unconditionally and gates others (`mcp` and `plugins` only
// appear once asked for), so the interesting column is what a declaration unlocks.
function printCapsReport(init) {
  const echoed = (init && init.agentCapabilities && init.agentCapabilities._meta) || {};
  const declared = Object.keys(CAPS).sort();
  const back = Object.keys(echoed).filter((k) => echoed[k] === true).sort();
  const missing = declared.filter((k) => !back.includes(k));
  process.stdout.write("\n=== capabilities ===\n");
  process.stdout.write(`declared (${declared.length}): ${declared.join(", ") || "(none)"}\n`);
  process.stdout.write(`echoed   (${back.length}): ${back.join(", ") || "(none)"}\n`);
  process.stdout.write(`echoed but not declared: ${back.filter((k) => !declared.includes(k)).join(", ") || "(none)"}\n`);
  process.stdout.write(`declared but NOT echoed: ${missing.join(", ") || "(none)"}\n`);
  const p = (init && init.agentCapabilities && init.agentCapabilities.promptCapabilities) || {};
  const s = (init && init.agentCapabilities && init.agentCapabilities.sessionCapabilities) || {};
  process.stdout.write(`prompt: ${JSON.stringify(p)}  session: ${JSON.stringify(s)}\n`);
  return missing;
}

// Does a method exist? -32601 is the only reliable "no": every other error means
// the method is routed and only the params were wrong. Notifications cannot be
// probed this way (they are not routed as requests), which is what --notify is for.
async function probeMethods(sessionId) {
  process.stdout.write("\n=== methods ===\n");
  for (const m of METHODS) {
    const params = sessionId ? { sessionId } : {};
    let verdict;
    try {
      const result = await request(m, params);
      verdict = "EXISTS -> " + JSON.stringify(result).slice(0, 200);
    } catch (err) {
      verdict = err && err.code === -32601
        ? "NO (method not found)"
        : `EXISTS (${err && err.code}: ${String(err && err.message).slice(0, 80)})`;
    }
    rec("method_probe", { method: m, verdict });
    process.stdout.write(`${m.padEnd(44)} ${verdict}\n`);
  }
}

(async () => {
  const init = await request("initialize", {
    protocolVersion: 1,
    clientCapabilities: {
      fs: { readTextFile: true, writeTextFile: true },
      terminal: true,
      elicitation: { form: {}, url: {} },
      _meta: CAPS
    }
  });
  rec("initialize_result", { result: init });
  let notEchoed = [];
  if (CAPS_REPORT) notEchoed = printCapsReport(init);

  // Probe first without a session: anything that works here (session/list does)
  // needs no session, which is worth knowing before building on it.
  if (METHODS.length) {
    await probeMethods(undefined);
  }

  const sess = await request("session/new", { cwd: CWD, mcpServers: MCP });
  rec("new_session_result", { result: sess });
  const sessionId = sess.sessionId;
  tail("[session] " + sessionId);

  if (METHODS.length) {
    process.stdout.write("\n(re-probing the same methods with a session)\n");
    await probeMethods(sessionId);
  }

  // Switch to a bypass-style mode so tools auto-run (we still auto-approve as a
  // fallback for agents that do not expose one). `--mode` overrides that, which is
  // how a permission prompt is provoked: in bypass there is nothing to approve, so
  // `session/request_permission` never arrives to be inspected.
  try {
    const modes = (sess.modes && sess.modes.availableModes) || [];
    const wanted = MODE && MODE !== "keep"
      ? modes.find((m) => m.id === MODE)
      : modes.find((m) => /bypass|yolo|auto/i.test(m.id + " " + (m.name || "")));
    if (MODE === "keep") {
      rec("set_mode", { modeId: sess.modes && sess.modes.currentModeId, kept: true });
    } else if (wanted) {
      await request("session/set_mode", { sessionId, modeId: wanted.id });
      rec("set_mode", { modeId: wanted.id });
    }
  } catch (e) { rec("set_mode_err", { err: String(e) }); }

  // A notification gets no reply, so the only evidence it landed is what the agent
  // does next: watch the stderr records for a parse complaint, then ask it something
  // that can only be answered if it consumed this.
  if (NOTIFY) {
    send({ jsonrpc: "2.0", method: NOTIFY, params: { sessionId, ...NOTIFY_PARAMS } });
    rec("notify_sent", { method: NOTIFY, params: { sessionId, ...NOTIFY_PARAMS } });
    tail("[notify->] " + NOTIFY);
    await new Promise((r) => setTimeout(r, 300));
  }

  if (!NO_PROMPT) {
    rec("prompt_send", { prompt: PROMPT });
    tail("[prompt] " + PROMPT);
    try {
      const res = await request("session/prompt", { sessionId, prompt: [{ type: "text", text: PROMPT }] });
      rec("prompt_result", { result: res });
    } catch (e) {
      rec("prompt_error", { err: e });
    }
  }

  if (SUMMARY) printSummary();
  // A probe session is listed like any other the moment it is created, so clean it
  // up rather than leaving debris in the user's session list.
  if (!KEEP_SESSION) {
    try { await request("session/delete", { sessionId }); rec("session_deleted", { sessionId }); } catch (e) { rec("session_delete_err", { err: String(e) }); }
  }
  process.stderr.write("\nTranscript: " + OUT + "\n");
  const code = notEchoed.length ? 1 : 0;
  setTimeout(() => { try { child.kill("SIGTERM"); } catch {} log.end(); process.exit(code); }, 800);
})().catch((e) => { rec("fatal", { err: String(e) }); log.end(); process.exit(1); });
