import { spawn, execFile, ChildProcessWithoutNullStreams } from "child_process";
import { EventEmitter } from "events";
import { cliCommand } from "../cli/locate";
import { JsonRpcConnection } from "./connection";
import {
  AcpSessionRow,
  AgentStopped,
  CliOutput,
  ConfigOption,
  ContentBlock,
  CreateTerminalParams,
  DocumentParams,
  headOf,
  LoadedHook,
  LoadedRule,
  InitializeResult,
  NewSessionResult,
  PromptResult,
  ReadTextFileParams,
  RequestDiagnosticsParams,
  RequestDiagnosticsResult,
  RequestPermissionParams,
  RequestPermissionResult,
  RevertPreviewResult,
  RevertStep,
  RevertStepsUpdate,
  SessionUpdateNotification,
  TerminalExitStatus,
  TerminalRef,
  WriteTextFileParams
} from "./types";

export interface AcpClientOptions {
  cliPath: string;
  cwd: string;
  env?: NodeJS.ProcessEnv;
  extraArgs?: string[];
  diagnostics?: boolean;
}

// Callbacks the host (extension) provides so the client can serve the
// agent's client-side requests (permissions, file reads/writes, questions).
export interface AcpHost {
  requestPermission(params: RequestPermissionParams): Promise<RequestPermissionResult>;
  requestDiagnostics(params: RequestDiagnosticsParams): RequestDiagnosticsResult;
  readTextFile(params: ReadTextFileParams): Promise<{ content: string }>;
  writeTextFile(params: WriteTextFileParams): Promise<Record<string, never>>;
  createElicitation(params: unknown): Promise<unknown>;
  createTerminal(params: CreateTerminalParams): { terminalId: string };
  terminalOutput(params: TerminalRef): { output: string; truncated: boolean; exitStatus: TerminalExitStatus | null };
  waitForTerminalExit(params: TerminalRef): Promise<TerminalExitStatus>;
  killTerminal(params: TerminalRef): Record<string, never>;
  releaseTerminal(params: TerminalRef): Record<string, never>;
}

// Emitted events:
//  "update"  -> SessionUpdateNotification
//  "log"     -> string
//  "exit"    -> void
// The handshake is the agent saying hello, so it should be quick. Generous rather
// than tight: what matters is that it finishes at all, since a call with no bound
// leaves the panel waiting for the rest of the window.
const HANDSHAKE_TIMEOUT_MS = 60_000;

// Opening a session starts every configured MCP server, which can mean pulling a
// docker image on a cold machine, so this is deliberately long.
const OPEN_TIMEOUT_MS = 180_000;

// The short queries: a question the agent answers out of what it already knows.
// These are what the settings panel and the session list wait on, and an agent that
// is alive and silent on one of them used to leave the caller waiting for the rest of
// the window. Long enough not to fire on a busy machine, since the cost of firing
// early is only a fallback.
const QUERY_TIMEOUT_MS = 30_000;

export class AcpClient extends EventEmitter {
  private child?: ChildProcessWithoutNullStreams;
  private conn?: JsonRpcConnection;
  private host?: AcpHost;
  private exited = false;
  private mcpChangeSeen = false;
  initializeResult?: InitializeResult;

  constructor(private readonly options: AcpClientOptions) {
    super();
  }

  setHost(host: AcpHost): void {
    this.host = host;
  }

  start(): void {
    const cmd = cliCommand(this.options.cliPath, ["acp", ...(this.options.extraArgs || [])]);
    const child = spawn(cmd.file, cmd.args, {
      cwd: this.options.cwd,
      env: this.options.env ? { ...this.options.env } : { ...process.env },
      stdio: ["pipe", "pipe", "pipe"],
      shell: cmd.shell,
      windowsHide: true,
      // Own process group so we can signal the whole tree (the agent spawns
      // MCP servers as children that do NOT die when only the agent is killed).
      detached: process.platform !== "win32"
    }) as ChildProcessWithoutNullStreams;

    child.on("error", (err) => this.emit("log", `[spawn-error] ${err.message}`));
    const gone = (code: number | null, from: string) => {
      if (this.exited) {
        return;
      }
      this.exited = true;
      this.emit("log", `[acp-${from}] code=${code}`);
      // Reap any MCP children that outlived the agent.
      this.killTree("SIGKILL");
      this.emit("exit");
    };
    child.on("close", (code) => gone(code, "exit"));
    // The agent's own end, which is not the same moment: `close` also waits for
    // the pipes, and an MCP server it started holds them open. Waiting only for
    // `close` left a chat looking alive after its agent had died.
    child.on("exit", (code) => gone(code, "exited"));

    this.child = child;
    this.conn = new JsonRpcConnection(
      child,
      (method, params) => this.handleRequest(method, params),
      (method, params) => this.handleNotification(method, params),
      (line) => this.emit("log", line)
    );
  }

  private async handleRequest(method: string, params: unknown): Promise<unknown> {
    if (!this.host) {
      throw new Error("No ACP host registered");
    }
    switch (method) {
      case "session/request_permission":
        return this.host.requestPermission(params as RequestPermissionParams);
      // The agent pulls diagnostics on its own schedule once
      // `cognition.ai/requestDiagnostics` is declared; it is not tied to a turn.
      case "_cognition.ai/request_diagnostics":
        return this.host.requestDiagnostics(params as RequestDiagnosticsParams);
      case "fs/read_text_file":
        return this.host.readTextFile(params as ReadTextFileParams);
      case "fs/write_text_file":
        return this.host.writeTextFile(params as WriteTextFileParams);
      // Vtx sends the Cognition-custom `_session/elicitation`; keep the
      // standard MCP `elicitation/create` name working too.
      case "_session/elicitation":
      case "elicitation/create":
        return this.host.createElicitation(params);
      case "terminal/create":
        return this.host.createTerminal(params as CreateTerminalParams);
      case "terminal/output":
        return this.host.terminalOutput(params as TerminalRef);
      case "terminal/wait_for_exit":
        return this.host.waitForTerminalExit(params as TerminalRef);
      case "terminal/kill":
        return this.host.killTerminal(params as TerminalRef);
      case "terminal/release":
        return this.host.releaseTerminal(params as TerminalRef);
      default: {
        // Not something this client serves. An empty object told the agent it had
        // worked, so it carried on believing a write had landed or a clipboard had
        // been set; -32601 is the answer the protocol has for this, and the one
        // the agent itself gives.
        this.emit("log", `[unhandled-request] ${method}`);
        const err = new Error(`Method not found: ${method}`) as Error & { code?: number };
        err.code = -32601;
        throw err;
      }
    }
  }

  private handleNotification(method: string, params: unknown): void {
    if (method === "session/update") {
      this.emit("update", params as SessionUpdateNotification);
      return;
    }
    // Vtx's own notifications. `output` carries the CLI's log stream, which is
    // where MCP tells you a server would not start, and `agent_stopped` carries
    // the numbers behind a finished turn. Both are worth showing, so they are
    // events rather than lines in the output channel.
    if (method === "_cognition.ai/output") {
      this.emit("output", params as CliOutput);
      return;
    }
    if (method === "_cognition.ai/agent_stopped") {
      this.emit("stopped", params as AgentStopped);
      return;
    }
    // The revertible step list, pushed after every turn. This is where node ids
    // come from, so it is worth having even though nothing asked for it.
    if (method === "_cognition.ai/revert/stepsUpdated") {
      this.emit("revertSteps", params as RevertStepsUpdate);
      return;
    }
    // Fires whenever the agent's MCP server set changes, which is constantly (50
    // times in a single trivial turn) and always with an empty payload, so every
    // one after the first says nothing the first did not. Noted once, then
    // dropped, rather than flooding the output channel with `[notify]` lines.
    if (method === "_cognition.ai/mcp/serversChanged") {
      if (!this.mcpChangeSeen) {
        this.mcpChangeSeen = true;
        this.emit("log", "[mcp] server set changed (further changes not logged)");
      }
      return;
    }
    // Anything else: log a small payload preview so its shape can be inspected in
    // the Output channel before deciding whether to surface it.
    let preview = "";
    try {
      const s = JSON.stringify(params);
      if (s) {
        preview = " " + (s.length > 300 ? s.slice(0, 300) + "\u2026" : s);
      }
    } catch {
      // ignore non-serialisable payloads
    }
    this.emit("log", `[notify] ${method}${preview}`);
  }

  async initialize(): Promise<InitializeResult> {
    const result = await this.rpc<InitializeResult>("initialize", {
      protocolVersion: 1,
      clientCapabilities: {
        fs: { readTextFile: true, writeTextFile: true },
        terminal: true,
        elicitation: { form: {}, url: {} },
        // Vtx's own extensions, none of them documented. Declaring one is a
        // promise to serve it, so each key here has a handler in this client or
        // in the chat controller. Note the agent does NOT echo these back: what
        // comes back in agentCapabilities._meta is what the AGENT offers, so a
        // capability we serve must never be gated on seeing it there.
        _meta: {
          // Unlocks the _cognition.ai/revert/* methods (conversation rewind +
          // file undo). Verified against vtx acp.
          "cognition.ai/revert": true,
          // Streams a subagent's own tool calls, messages and thoughts tagged
          // with `subagent_context`, plus the `subagent_started` /
          // `subagent_completed` lifecycle. Without it only the subagent's
          // opening tool_call arrives, so its rows never leave pending.
          "cognition.ai/subagentSupport": true,
          // Unlocks _cognition.ai/subagent/{background,foreground}.
          "cognition.ai/subagentControl": true,
          "cognition.ai/groupedSessionConfigOptions": true,
          "cognition.ai/multiRootWorkspace": true,
          // The agent pulls the editor's diagnostics instead of spawning tsc or
          // eslint to find out what the editor already knows. Pairs with
          // documentLifecycle: the agent only reports diagnostics for documents it
          // has been told are open.
          ...(this.options.diagnostics ? { "cognition.ai/requestDiagnostics": true } : {}),
          // Puts the files the user has open, focused and unsaved into the agent's
          // context, so it stops guessing at what "this file" means and knows when
          // a file it read has unsaved changes.
          "cognition.ai/documentLifecycle": true,
          // Rejecting a permission stops the turn rather than letting the agent
          // carry on down a path the user just refused.
          "cognition.ai/stopOnReject": true,
          // A cancelled tool keeps the output it had already produced.
          "cognition.ai/partialContent": true
        }
      }
    }, HANDSHAKE_TIMEOUT_MS);
    this.initializeResult = result;
    return result;
  }

  // True when the agent acknowledged the revert capability.
  supportsRevert(): boolean {
    return this.agentCapability("cognition.ai/revert");
  }

  // True when the agent accepts _cognition.ai/subagent/{background,foreground}.
  supportsSubagentControl(): boolean {
    return this.agentCapability("cognition.ai/subagentControl");
  }

  private agentCapability(key: string): boolean {
    const meta = this.initializeResult?.agentCapabilities?._meta as Record<string, unknown> | undefined;
    return meta?.[key] === true;
  }

  newSession(cwd: string, additionalDirectories: string[] = [], mcpServers: unknown[] = []): Promise<NewSessionResult> {
    return this.rpc<NewSessionResult>("session/new", {
      cwd: cwd || this.options.cwd,
      mcpServers,
      ...(additionalDirectories.length ? { additionalDirectories } : {})
    }, OPEN_TIMEOUT_MS);
  }

  // True when the agent can replay a stored conversation. Standard ACP capability:
  // an agent without it cannot reopen a chat at all, only start a new one.
  supportsLoadSession(): boolean {
    return this.initializeResult?.agentCapabilities?.loadSession !== false;
  }

  loadSession(
    sessionId: string,
    cwd: string,
    additionalDirectories: string[] = [],
    mcpServers: unknown[] = []
  ): Promise<unknown> {
    return this.rpc("session/load", {
      sessionId,
      cwd: cwd || this.options.cwd,
      mcpServers,
      ...(additionalDirectories.length ? { additionalDirectories } : {})
    }, OPEN_TIMEOUT_MS);
  }

  prompt(sessionId: string, blocks: ContentBlock[]): Promise<PromptResult> {
    return this.rpc<PromptResult>("session/prompt", { sessionId, prompt: blocks });
  }

  cancel(sessionId: string): void {
    this.conn?.notify("session/cancel", { sessionId });
  }

  renameSession(sessionId: string, title: string): Promise<unknown> {
    return this.rpc("session/rename", { sessionId, title }, QUERY_TIMEOUT_MS);
  }

  deleteSession(sessionId: string): Promise<unknown> {
    return this.rpc("session/delete", { sessionId }, QUERY_TIMEOUT_MS);
  }

  // Every session the CLI knows about, from any directory. Standard ACP (the
  // agent advertises it under sessionCapabilities.list) and it needs no session of
  // its own, so any live agent can answer it. Unlike `vtx list`, which is exact
  // match on cwd, this is not scoped: a session created in a subdirectory of the
  // workspace comes back too.
  async listSessions(timeoutMs = QUERY_TIMEOUT_MS): Promise<AcpSessionRow[]> {
    const res = await this.rpc<{ sessions?: AcpSessionRow[] }>("session/list", {}, timeoutMs || QUERY_TIMEOUT_MS);
    return res?.sessions || [];
  }

  // Vtx exposes both `mode` and `model` as config options set through this
  // custom method: { sessionId, configId, value }.
  setConfigOption(sessionId: string, configId: string, value: string | boolean): Promise<{ configOptions?: ConfigOption[] }> {
    return this.rpc("session/set_config_option", { sessionId, configId, value }, QUERY_TIMEOUT_MS);
  }

  // --- What the agent has actually loaded ----------------------------------
  // Rules and hooks come from more places than any one client can be expected to
  // know: AGENTS.md and CLAUDE.md, Cursor and Windsurf files, plugins, and whatever
  // a later CLI adds. Asking the agent is the only way to report what is really in
  // force, and each entry names the file it came from so it can still be opened.

  async listRules(sessionId: string): Promise<LoadedRule[]> {
    const res = await this.rpc<{ rules?: LoadedRule[] }>("_vtx.ai/rules/list", { sessionId }, QUERY_TIMEOUT_MS);
    return res?.rules || [];
  }

  async listHooks(sessionId: string): Promise<LoadedHook[]> {
    const res = await this.rpc<{ hooks?: LoadedHook[] }>("_vtx.ai/hooks/list", { sessionId }, QUERY_TIMEOUT_MS);
    return res?.hooks || [];
  }

  // Publish the conversation and get a link to it. Rejects with "Nothing to share
  // yet" until the session has content, which is a state to report rather than an
  // error to swallow.
  shareSession(sessionId: string): Promise<{ url?: string } | undefined> {
    return this.rpc<{ url?: string }>("_cognition.ai/session/share", { sessionId }, QUERY_TIMEOUT_MS);
  }

  supportsSessionShare(): boolean {
    return this.agentCapability("cognition.ai/sessionShare");
  }

  // --- Document lifecycle --------------------------------------------------
  // What the user has open, focused and unsaved. Notifications, so there is no
  // reply and nothing to await: a stale one is only ever a wrong "open documents"
  // list, never a hung call. Gated on the agent advertising the capability,
  // because an agent that does not will log a parse failure for every one.
  documentEvent(kind: "didOpen" | "didClose" | "didChangeDirty" | "didFocus", params: DocumentParams): void {
    if (!this.supportsDocumentLifecycle()) {
      return;
    }
    this.conn?.notify(`_cognition.ai/document/${kind}`, params);
  }

  supportsDocumentLifecycle(): boolean {
    return this.agentCapability("cognition.ai/documentLifecycle");
  }

  // --- Subagents -----------------------------------------------------------
  // Move a running subagent between foreground (the parent waits, and tool
  // calls prompt for approval) and background (the parent carries on, and
  // unapproved tools are denied). There is no ACP method to cancel one.
  subagentBackground(sessionId: string, agentId: string): Promise<unknown> {
    return this.rpc("_cognition.ai/subagent/background", { sessionId, agentId }, QUERY_TIMEOUT_MS);
  }

  subagentForeground(sessionId: string, agentId: string): Promise<unknown> {
    return this.rpc("_cognition.ai/subagent/foreground", { sessionId, agentId }, QUERY_TIMEOUT_MS);
  }

  // --- Revert (conversation rewind + file undo) ---------------------------
  // Preview what reverting to `targetNodeId` would do, without mutating.
  revertPreview(sessionId: string, targetNodeId: number, opts?: { force?: boolean; skipFileUndo?: boolean }): Promise<RevertPreviewResult> {
    return this.rpc<RevertPreviewResult>("_cognition.ai/revert/preview", {
      sessionId,
      targetNodeId,
      force: opts?.force ?? false,
      skipFileUndo: opts?.skipFileUndo ?? false
    }, QUERY_TIMEOUT_MS);
  }

  // Execute the rewind: truncate the conversation back to `targetNodeId` and
  // undo the file edits made from that node onward (unless skipFileUndo).
  revertExecute(sessionId: string, targetNodeId: number, opts?: { force?: boolean; skipFileUndo?: boolean }): Promise<unknown> {
    return this.rpc("_cognition.ai/revert/execute", {
      sessionId,
      targetNodeId,
      force: opts?.force ?? true,
      skipFileUndo: opts?.skipFileUndo ?? false
    }, QUERY_TIMEOUT_MS);
  }

  // Branch from a step rather than rewinding to it. Nothing is discarded and no
  // file is touched: the agent copies the conversation up to that point into a
  // BRAND NEW session and returns its id, leaving this one exactly as it was.
  // `targetNodeId` is the step's `forkTargetNodeId`, which is not its
  // `revertTargetNodeId` (verified: passing the wrong one forks the wrong turn).
  async revertForkFromStep(sessionId: string, targetNodeId: number): Promise<string | undefined> {
    const res = await this.rpc<{ forkedSessionId?: string }>("_cognition.ai/revert/forkFromStep", {
      sessionId,
      targetNodeId
    }, QUERY_TIMEOUT_MS);
    return res?.forkedSessionId;
  }

  // Every revertible point in the conversation. The agent also pushes this list
  // unprompted after each turn (`revertSteps`), so this is mostly for the state
  // a freshly loaded session starts in, before the first push arrives.
  async listRevertSteps(sessionId: string): Promise<RevertStep[]> {
    const res = await this.rpc<{ steps?: RevertStep[] }>("_cognition.ai/revert/listSteps", { sessionId }, QUERY_TIMEOUT_MS);
    return res?.steps || [];
  }

  // The conversation's current head, or null when it has no revertible history.
  //
  // This is the newest step's `forkTargetNodeId`, NOT its `revertTargetNodeId`.
  // The two are a turn apart: a step's revert target is the node BEFORE it ran
  // (rewinding there discards it), and its fork target is the node after it
  // finished, which is the head. Verified against the agent: with two turns the
  // steps report rev=27/31 and fork=31/34 while the head is 31 then 34.
  async currentHead(sessionId: string): Promise<number | null> {
    const steps = await this.listRevertSteps(sessionId);
    return headOf(steps);
  }

  private rpc<T>(method: string, params?: unknown, timeoutMs?: number): Promise<T> {
    if (!this.conn) {
      return Promise.reject(new Error("ACP connection not started"));
    }
    return this.conn.request<T>(method, params, timeoutMs);
  }

  // Stop the agent while this extension host stays alive (terminating one
  // session, a failed start). Fire and forget: the follow-up SIGKILL runs on a
  // timer, which is only reliable because we are still here to fire it. Use
  // `shutdown()` on the way out instead.
  dispose(): void {
    this.conn?.dispose();
    // SIGTERM the whole group (lets the agent + docker-based MCP shut down
    // cleanly), then SIGKILL any stragglers shortly after.
    this.killTree("SIGTERM");
    setTimeout(() => this.killTree("SIGKILL"), 1500).unref?.();
  }

  // Stop the agent for good and resolve only once it is really gone, so a caller
  // that is shutting the extension down can await it rather than racing the
  // host's exit (a `setTimeout` escalation never fires once the host is gone).
  //
  // Escalates within `timeoutMs`: closing stdin is a clean EOF most stdio agents
  // exit on, then SIGTERM the process group so docker-backed MCP servers get to
  // tidy up, then SIGKILL. Any turn should be cancelled by the caller first.
  async shutdown(timeoutMs = 800): Promise<void> {
    if (!this.child || this.exited) {
      this.conn?.dispose();
      return;
    }
    const deadline = Date.now() + timeoutMs;
    this.conn?.dispose();
    if (await this.waitForExit(Math.round(timeoutMs * 0.5))) {
      return;
    }
    this.killTree("SIGTERM");
    if (await this.waitForExit(deadline - Date.now())) {
      return;
    }
    this.emit("log", "[acp-shutdown] agent ignored SIGTERM, killing");
    this.killTree("SIGKILL");
    await this.waitForExit(150);
  }

  // Whether the agent process has already gone. A surface being handed a session
  // has to ask: the exit it missed is never emitted again.
  hasExited(): boolean {
    return this.exited;
  }

  // Resolves true once the process has closed, false if `ms` runs out first.
  private waitForExit(ms: number): Promise<boolean> {
    if (this.exited) {
      return Promise.resolve(true);
    }
    if (ms <= 0) {
      return Promise.resolve(false);
    }
    return new Promise((resolve) => {
      const done = (): void => {
        clearTimeout(timer);
        resolve(true);
      };
      const timer = setTimeout(() => {
        this.off("exit", done);
        resolve(this.exited);
      }, ms);
      this.once("exit", done);
    });
  }

  // Signal the agent's entire process tree: its process group on POSIX, or
  // taskkill on Windows, which has no groups (so a plain child.kill there would
  // strand the MCP servers the agent spawned).
  private killTree(signal: NodeJS.Signals): void {
    const pid = this.child?.pid;
    if (!pid) {
      return;
    }
    if (process.platform === "win32") {
      // Windows has no signals: `taskkill /T` asks the tree to close, and only
      // `/F` forces it, so the escalation is the flag rather than the signal.
      const force = signal === "SIGKILL" ? ["/F"] : [];
      try {
        execFile("taskkill", ["/PID", String(pid), "/T", ...force], { windowsHide: true }, () => {});
      } catch {
        // taskkill missing; fall through to the direct kill below
      }
      if (force.length) {
        try {
          this.child?.kill();
        } catch {
          // already gone
        }
      }
      return;
    }
    try {
      process.kill(-pid, signal); // negative pid => process group
    } catch {
      try {
        this.child?.kill(signal);
      } catch {
        // ignore
      }
    }
  }
}
