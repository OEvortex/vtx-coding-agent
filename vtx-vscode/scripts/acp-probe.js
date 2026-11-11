// ACP probe: drive a real `vtx acp` session end to end and log every message
// so we can see the true payload shapes the extension has to render.
//
// It speaks the same newline-delimited JSON-RPC the extension does (see
// src/acp/connection.ts), performs the initialize -> session/new -> session/prompt
// handshake, auto-approves anything so tools actually run, and records every
// session/update notification and agent->client request to a JSONL file. Use it
// whenever you add rendering for a new tool and need to know the exact `kind`,
// `_meta`, `rawInput` and `content` a given tool emits.
//
// It also answers the questions that come up whenever Vtx's ACP surface moves:
// which methods does it actually implement, and does it consume a given
// notification. Vtx stores clientCapabilities but never reads them, so there is
// no capability matrix to probe; `--methods` and `--notify` keep the answers
// reproducible after a CLI upgrade rather than rediscovered by hand.
//
// Usage:
//   node scripts/acp-probe.js --prompt "search the web for the node lts version"
//   node scripts/acp-probe.js -p "call the time MCP tool for UTC" --out /tmp/mcp.jsonl
//   node scripts/acp-probe.js -p "fetch https://example.com" --summary
//   node scripts/acp-probe.js --methods _vtx.ai/rules/list,session/list --no-prompt
//   node scripts/acp-probe.js --notify session/update --no-prompt
//
// Flags (all optional):
//   -p, --prompt <text>   prompt to send            (default: "Say hi in one word.")
//   -o, --out <file>      JSONL transcript path      (default: scripts/.probe/last.jsonl)
//   -c, --cwd <dir>       session working directory  (default: process.cwd())
//       --cli <path>      vtx binary                 (default: $VTX_CLI or `vtx`)
//       --mcp <json>      mcpServers array for session/new (default: [])
//       --watchdog <ms>   hard kill timeout          (default: 180000)
//       --summary         also print a tool-call summary to stdout at the end
//       --quiet           do not tail updates to stdout while running
//       --methods <list>  call each method and report whether it exists (-32601 = no).
//                         Runs before the prompt, and without a session when possible
//       --notify <m>      send one notification after the session opens
//       --notify-params <json>  params for it (sessionId is merged in)
//       --no-prompt       skip the prompt: method probing only
//       --keep-session    do not delete the probe session on the way out
//       --mode <id|keep>  session mode to use. `keep` leaves it as opened
//                         (default: leave as opened; Vtx ships a single `default` mode)
//
// Env fallbacks mirror the flags: VTX_CLI, PROBE_PROMPT, PROBE_OUT, PROBE_CWD,
// PROBE_MCP, PROBE_WATCHDOG.

const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

function parseArgs(argv) {
  const out = {};
  const alias = { p: "prompt", o: "out", c: "cwd" };
  const flags = new Set(["summary", "quiet", "no-prompt", "keep-session"]);
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

const args = parseArgs(process.argv.slice(2));
const CLI = args.cli || process.env.VTX_CLI || "vtx";
const CWD = args.cwd || process.env.PROBE_CWD || process.cwd();
const PROMPT = args.prompt || process.env.PROBE_PROMPT || "Say hi in one word.";
const OUT = path.resolve(args.out || process.env.PROBE_OUT || path.join(__dirname, ".probe", "last.jsonl"));
const MCP = args.mcp ? JSON.parse(args.mcp) : (process.env.PROBE_MCP ? JSON.parse(process.env.PROBE_MCP) : []);
const WATCHDOG = Number(args.watchdog || process.env.PROBE_WATCHDOG || 180000);
const QUIET = !!args.quiet;
const SUMMARY = !!args.summary;
const METHODS = args.methods ? String(args.methods).split(",").map((s) => s.trim()).filter(Boolean) : [];
const NOTIFY = args.notify;
const NOTIFY_PARAMS = args["notify-params"] ? JSON.parse(args["notify-params"]) : {};
const NO_PROMPT = !!args["no-prompt"];
const KEEP_SESSION = !!args["keep-session"];
// Session mode: an id to set, or `keep` to leave the session in the mode it opened
// in. Vtx ships a single `default` mode, so this only matters against other agents.
const MODE = args.mode;

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

// Serve the agent's client-side requests: approve everything and satisfy the
// standard ACP file and terminal methods so tools run to completion.
// Vtx itself only ever calls session/request_permission.
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
      elicitation: { form: {}, url: {} }
    }
  });
  rec("initialize_result", { result: init });
  tail("[initialize] " + JSON.stringify(init.agentInfo || {}));

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

  // Set a mode if asked. Vtx ships only `default`, so this normally does nothing.
  try {
    const modes = (sess.modes && sess.modes.availableModes) || [];
    const wanted = MODE && MODE !== "keep" ? modes.find((m) => m.id === MODE) : undefined;
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
  setTimeout(() => { try { child.kill("SIGTERM"); } catch {} log.end(); process.exit(0); }, 800);
})().catch((e) => { rec("fatal", { err: String(e) }); log.end(); process.exit(1); });
