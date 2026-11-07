// Types for the Agent Client Protocol (ACP) as spoken by `vtx acp`.
// Only the subset we use is modelled here, from the spec at agentclientprotocol.com.

export type ContentBlock =
  | { type: "text"; text: string }
  | { type: "image"; mimeType: string; data: string }
  | { type: "resource"; resource: { uri: string; mimeType?: string; text?: string } }
  | { type: "resource_link"; uri: string; name?: string };

export interface InitializeResult {
  protocolVersion: number;
  agentCapabilities?: {
    loadSession?: boolean;
    promptCapabilities?: { image?: boolean; audio?: boolean; embeddedContext?: boolean };
    sessionCapabilities?: Record<string, unknown>;
    [k: string]: unknown;
  };
  agentInfo?: { name?: string; title?: string; version?: string };
  _meta?: Record<string, unknown>;
}

export interface NewSessionResult {
  sessionId: string;
  modes?: SessionModeState;
  configOptions?: ConfigOption[];
  _meta?: Record<string, unknown>;
}

export interface SessionModeState {
  currentModeId?: string;
  availableModes?: { id: string; name: string; description?: string }[];
}

// A generic session config option (Vtx exposes `mode` and `model` this way).
export interface ConfigOption {
  id: string;
  name?: string;
  description?: string;
  category?: string;
  type?: string;
  currentValue?: string | boolean;
  options?: (ConfigOptionChoice | ConfigOptionGroup)[];
}

export interface ConfigOptionChoice {
  value: string;
  name?: string;
  description?: string;
  _meta?: Record<string, unknown>;
}

export interface ConfigOptionGroup {
  group: string;
  name?: string;
  options: ConfigOptionChoice[];
  _meta?: Record<string, unknown>;
}

export type StopReason =
  | "end_turn"
  | "max_tokens"
  | "max_turn_requests"
  | "refusal"
  | "cancelled";

export interface PromptResult {
  stopReason: StopReason;
}

// session/update notification payloads (discriminated by `sessionUpdate`).
// `_meta` carries Vtx's extensions, including the subagent tags below.
export type SessionUpdate =
  | { sessionUpdate: "agent_message_chunk"; messageId?: string; content: ContentBlock; _meta?: UpdateMeta }
  | { sessionUpdate: "agent_thought_chunk"; messageId?: string; content: ContentBlock; _meta?: UpdateMeta }
  | { sessionUpdate: "user_message_chunk"; messageId?: string; content: ContentBlock }
  | { sessionUpdate: "plan"; entries: PlanEntry[] }
  | { sessionUpdate: "tool_call"; toolCallId: string; name?: string; title?: string; kind?: string; status?: ToolCallStatus; content?: ToolCallContent[]; rawInput?: unknown; locations?: { path: string; line?: number }[]; _meta?: UpdateMeta }
  | { sessionUpdate: "tool_call_update"; toolCallId: string; name?: string; title?: string; kind?: string; status?: ToolCallStatus; content?: ToolCallContent[]; rawInput?: unknown; locations?: { path: string; line?: number }[]; _meta?: UpdateMeta }
  | { sessionUpdate: "usage_update"; used: number; size: number; cost?: { amount: number; currency: string } }
  | { sessionUpdate: "available_commands_update"; availableCommands: AvailableCommand[] }
  | { sessionUpdate: "current_mode_update"; currentModeId: string }
  | { sessionUpdate: string; [k: string]: unknown };

export type ToolCallStatus = "pending" | "in_progress" | "completed" | "failed" | "cancelled";

// Vtx's `_meta` on a session update. `inferenceToolName`/`toolName`/`eventType`
// identify the real tool behind the coarse ACP `kind`; the subagent keys track
// delegated work (unlocked by clientCapabilities._meta["cognition.ai/subagentSupport"],
// without which only the subagent's opening tool_call leaks through, never its
// updates, leaving those rows stuck at pending).
export interface UpdateMeta {
  "cognition.ai/inferenceToolName"?: string;
  "cognition.ai/toolName"?: string;
  "cognition.ai/eventType"?: string;
  // On a tool_call_update whose toolCallId is the subagent's own agentId.
  "cognition.ai/subagent_started"?: SubagentStarted;
  "cognition.ai/subagent_completed"?: SubagentCompleted;
  // On every update the subagent itself produces (tool calls, message and
  // thought chunks), naming the subagent that produced it.
  "cognition.ai/subagent_context"?: { parentAgentId?: string };
  [k: string]: unknown;
}

export interface SubagentStarted {
  agentId: string;
  title?: string;
  task?: string;
  // Display name of the profile, e.g. "Explore".
  profile?: string;
  depth?: number;
  isBackground?: boolean;
}

export interface SubagentCompleted {
  agentId: string;
  success?: boolean;
  // The subagent's final report, which the parent reads to continue.
  summary?: string;
}

export type ToolCallContent =
  | { type: "content"; content: ContentBlock }
  | { type: "diff"; path: string; oldText?: string | null; newText: string }
  | { type: string; [k: string]: unknown };

export interface PlanEntry {
  content: string;
  priority?: "high" | "medium" | "low";
  // ACP's core statuses are pending/in_progress/completed. The agent may also
  // mark an entry it decided not to do as skipped (or cancelled); both render as
  // a struck-through, dimmed row.
  status?: "pending" | "in_progress" | "completed" | "skipped" | "cancelled";
}

export interface AvailableCommand {
  name: string;
  description?: string;
  input?: unknown;
}

export interface SessionUpdateNotification {
  sessionId: string;
  update: SessionUpdate;
}

// session/request_permission
export interface RequestPermissionParams {
  sessionId: string;
  toolCall: {
    toolCallId: string;
    title?: string;
    kind?: string;
    content?: ToolCallContent[];
    locations?: { path: string; line?: number }[];
    // Vtx sends no title for a command it wants to run, only the command
    // itself, under `cognition.ai/editableCommand`.
    _meta?: Record<string, unknown>;
  };
  options: PermissionOption[];
}

export interface PermissionOption {
  optionId: string;
  name: string;
  kind?: "allow_once" | "allow_always" | "reject_once" | "reject_always" | string;
}

export type RequestPermissionResult =
  | { outcome: { outcome: "selected"; optionId: string } }
  | { outcome: { outcome: "cancelled" } };

// terminal/* client methods
export interface CreateTerminalParams {
  sessionId: string;
  command: string;
  args?: string[];
  env?: { name: string; value: string }[];
  cwd?: string;
  outputByteLimit?: number;
}

export interface TerminalRef {
  sessionId: string;
  terminalId: string;
}

export interface TerminalExitStatus {
  exitCode: number | null;
  signal: string | null;
}

// _cognition.ai/revert/* (verified extension; unlocked by advertising
// clientCapabilities._meta["cognition.ai/revert"] = true).
export interface RevertParams {
  sessionId: string;
  targetNodeId: number;
  force?: boolean;
  skipFileUndo?: boolean;
}

export interface RevertFileAction {
  path?: string;
  // "restore" | "delete" | "recreate" and similar
  action?: string;
  additions?: number;
  deletions?: number;
  [k: string]: unknown;
}

export interface RevertPreviewResult {
  fileActions: RevertFileAction[];
  irreversibleWarnings: { toolName?: string; description?: string }[];
  conflicts: unknown[];
}

// One revertible point in the conversation, from `_cognition.ai/revert/listSteps`
// and from the `revert/stepsUpdated` notification the agent pushes after every
// turn. This is the authoritative source of node ids: they are never in the
// stream, and the alternative (parsing them out of a preview error) breaks on any
// change to the agent's wording.
//
// `revertTargetNodeId` rewinds to the step, discarding what came after.
// `forkTargetNodeId` branches from it instead, leaving the later turns alone.
export interface RevertStep {
  stepNumber: number;
  // "prompt" for a user request; the agent also reports question and tool steps.
  kind?: string;
  userMessageId?: string;
  toolCallId?: string;
  questionNodeId?: number;
  revertTargetNodeId: number;
  forkTargetNodeId?: number;
  summary?: string;
  reason?: string;
}

export interface RevertStepsUpdate {
  sessionId?: string;
  steps: RevertStep[];
}

// The conversation's head from a step list: the newest step's fork target, which
// is the node after it finished. Its revert target is a turn earlier, so reading
// that instead rewinds one turn too far.
export function headOf(steps: RevertStep[]): number | null {
  const last = steps[steps.length - 1];
  if (!last) {
    return null;
  }
  return last.forkTargetNodeId ?? last.revertTargetNodeId ?? null;
}

// `_cognition.ai/rules/list`: every always-on context file the agent actually
// loaded, from wherever it came (AGENTS.md, CLAUDE.md, Cursor, Windsurf, a plugin).
// Scanning for these by filename cannot see the plugin ones, and cannot tell which
// of the files it found the CLI is honouring.
export interface LoadedRule {
  name: string;
  // Absolute path, so a row can open the file it is describing.
  path?: string;
  // "agents_standard" | "windsurf" | "cursor" | "claude" | ...
  provider?: string;
  // Display form of the provider, e.g. "AGENTS.md", ".windsurf".
  providerLabel?: string;
  // "always_on" for a rule; skills that the user can invoke report otherwise.
  trigger?: string;
  userInvocable?: boolean;
  // "global" | "workspace"
  scope?: string;
}

// `_cognition.ai/hooks/list`: the hooks in force, including ones from a plugin or
// from another tool's config, which the panel's own file reading cannot see.
export interface LoadedHook {
  id?: string;
  name?: string;
  // "permission_request" | "post_tool" | ...
  events?: string[];
  sourcePath?: string;
  provider?: string;
  providerLabel?: string;
  scope?: string;
  // "claude" for a hook written in the Claude format the CLI also accepts.
  format?: string;
}

// A row from `session/list`. `_meta["cognition.ai/isLocked"]` says another client
// holds the session, but not which one: reclaiming a stale lock still needs the
// owning pid from the CLI's own lock files (see cli/sessionLocks.ts).
export interface AcpSessionRow {
  sessionId: string;
  cwd?: string;
  title?: string;
  // ISO 8601.
  updatedAt?: string;
  _meta?: Record<string, unknown>;
}

// `_cognition.ai/request_diagnostics`: the agent asking the editor what is wrong
// with the code, rather than spawning a compiler or a linter to find out what the
// editor already knows. Unlocked by advertising
// clientCapabilities._meta["cognition.ai/requestDiagnostics"], after which the
// agent pulls on its own schedule rather than per turn.
//
// The reply must be `RequestDiagnosticsResult` exactly: a null or a bare object is
// rejected ("invalid type: null, expected struct RequestDiagnosticsResult").
export interface RequestDiagnosticsParams {
  sessionId?: string;
  // Present when the agent wants one file rather than the whole workspace.
  path?: string;
}

export interface DiagnosticPosition {
  line: number;
  character: number;
}

export interface DiagnosticRange {
  start: DiagnosticPosition;
  end: DiagnosticPosition;
}

export interface DiagnosticItem {
  // File URI, required: the agent rejects the reply without it ("missing field
  // `uri`") and uses it to link the problem to a file.
  uri: string;
  // The rule or code that produced it, e.g. "ts2304" or an ESLint rule name.
  id: string;
  message: string;
  // Zero based, as VS Code reports it. The agent renders it one based itself.
  range: DiagnosticRange;
  // "error" | "warning" | "information" | "hint"
  severity: string;
  // Who reported it, e.g. "ts", "eslint".
  source?: string;
}

export interface RequestDiagnosticsResult {
  items: DiagnosticItem[];
}

// `_cognition.ai/document/{didOpen,didClose,didChangeDirty,didFocus}`: what the
// user has open, focused and unsaved. Sent as notifications (there is no reply)
// and unlocked by advertising clientCapabilities._meta["cognition.ai/documentLifecycle"].
//
// This is what puts an "open documents" list in the agent's context, and the agent
// only surfaces diagnostics for documents it knows about, so the two go together:
// without this, the diagnostics above have nothing to attach to.
export interface DocumentParams {
  sessionId: string;
  // File URI, not a path: a path is rejected as a parse failure.
  uri: string;
  languageId?: string;
  isDirty?: boolean;
}

// fs/* client methods
export interface ReadTextFileParams {
  sessionId: string;
  path: string;
  line?: number | null;
  limit?: number | null;
}
export interface WriteTextFileParams {
  sessionId: string;
  path: string;
  content: string;
}

// `_cognition.ai/output`: the CLI's own log stream. The MCP channel is the only
// place it says a configured server would not start, which is otherwise invisible.
export interface CliOutput {
  channel?: string;
  level?: string; // info | warn | error
  message?: string;
  sessionId?: string;
}

// `_cognition.ai/agent_stopped`: what a finished turn cost. `responseDimensions`
// is the CLI's own display contract for it (a label and a value, ready to show),
// so the panel renders those rather than inventing its own wording.
export interface ResponseDimension {
  uid?: string;
  groupTitle?: string;
  label?: string;
  kind?: { type?: string; value?: number | string; prefix?: string; tail?: string; pluralTail?: string };
}

export interface AgentStopped {
  cause?: string;
  sessionId?: string;
  stats?: {
    toolCalls?: number;
    filesChanged?: number;
    commandsRun?: number;
    totalTimeMs?: number;
    modelLabel?: string;
    responseDimensions?: ResponseDimension[];
  };
}
