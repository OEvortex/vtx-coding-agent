import * as vscode from "vscode";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as crypto from "crypto";
import { AcpClient, AcpHost } from "../acp/client";
import {
  AgentStopped,
  CliOutput,
  ConfigOption,
  ContentBlock,
  PromptResult,
  CreateTerminalParams,
  NewSessionResult,
  ReadTextFileParams,
  RequestDiagnosticsParams,
  RequestDiagnosticsResult,
  RequestPermissionParams,
  RequestPermissionResult,
  ResponseDimension,
  RevertPreviewResult,
  RevertStep,
  RevertStepsUpdate,
  headOf,
  SessionUpdateNotification,
  SubagentCompleted,
  SubagentStarted,
  TerminalExitStatus,
  TerminalRef,
  WriteTextFileParams
} from "../acp/types";
import { diagnosticItems } from "../acp/diagnostics";
import { TerminalManager } from "../acp/terminal";
import { VsCodeTerminalRunner } from "../acp/vscodeTerminal";
import { userConfigDir } from "../settings/configService";
import { VtxSession, ListResult, fromAcpRows, listSessions } from "../session/sessionList";
import { SessionStore } from "../session/sessionStore";
import { ChangeTracker } from "../diff/changeTracker";
import { diffStat } from "../diff/diffStat";
import { paintedReplay, recordPainted } from "./transcriptLog";
import { StatusBar } from "../ui/statusBar";
import { checkHealth, CliHealth, loginShellEnv } from "../cli/locate";
import {
  cachedFamilies,
  cachedProviderFilter,
  cachedProviders,
  familyOf,
  invalidateModelFamilies,
  listModelFamilies,
  ModelFamily,
  ModelProvider
} from "../cli/models";
import { lockOwner, removeLock } from "../cli/sessionLocks";

// One live session: its own `vtx acp` process and terminal manager, plus the
// per-session state that used to be flat on the provider. Several of these can
// be alive at once (one acp each), which is what lets a session keep running
// in the background while you look at another.
interface Runtime {
  id: string; // ACP session id
  cwd: string;
  client: AcpClient;
  terminals: TerminalManager;
  initialized: boolean;
  busy: boolean; // a turn is in flight
  awaiting: number; // pending permission/elicitation requests (needs the user)
  replaying: boolean; // a session/load replay is in progress
  // A silent background wake is in flight (the webview already shows the cached
  // transcript, so the replay is not streamed to it). Resolves when interactive.
  waking?: Promise<void>;
  // While true, session/update notifications are consumed but NOT streamed to the
  // webview (used during a silent wake so the cached transcript is not disturbed).
  silentReplay?: boolean;
  lastActivityAt: number; // for idle auto-exit
  mode?: string;
  model?: string;
  configOptions?: ConfigOption[];
  // Permission/elicitation requests from a background session, re-surfaced to
  // the webview when the session is next opened. Several can be outstanding at
  // once, so this is a queue rather than a single slot.
  pending: { requestId: string; payload: Record<string, unknown> }[];
  // A "needs your input" notification has already been shown for the current
  // attention episode, so we don't fire one per tool call. Reset when the
  // session is opened or its requests are all answered.
  attentionNotified?: boolean;
  // The prompt in flight, if any. A session can change surface mid turn, and the
  // surface it lands on has to be the one that ends it.
  turn?: Promise<PromptResult>;
  // How many prompts this session has run, which is what an edit row belongs to:
  // one row per file per turn, and the row's diff is that turn's work on it.
  turnCount: number;
  // Set when a session arrived on this surface mid turn and its own record of the
  // transcript was incomplete, so the rest is fetched from the CLI once the turn
  // ends and the channel is free.
  needsReplay?: boolean;
  // Everything this session has painted into a transcript, in order, so a chat
  // that moves surface can be rebuilt on the new one. The CLI cannot be asked for
  // it mid turn: `session/load` over a live channel kills the running prompt.
  log: Record<string, unknown>[];
  replay: Record<string, unknown>[];
  // False once the oldest entries were dropped, which makes a rebuild partial.
  logFull: boolean;
  // Messages the user submitted while a turn was in flight. The implicit context
  // is snapshotted at queue time and the attachments travel with the message, so
  // editing one can hand them back to the composer and take them again. The host
  // sends them in order as the session frees up (VS Code's chat queue).
  queued: { id: string; text: string; implicit: ContentBlock[]; attachments: Staged[] }[];
  // Webview messages a background turn produced while this session was not the
  // visible one. Replayed when the session is reopened so its progress is shown
  // even for a turn that is still running (capped so it can't grow unbounded).
  bgBuffer: Record<string, unknown>[];
  // MCP servers this agent could not reach, by name. The CLI only says so on its
  // output stream, so without this a broken server is invisible in the panel and
  // an aborted session load can only guess at the reason.
  mcpProblems: Map<string, string>;
  // Terminals we have started but not yet matched to the tool call that asked for
  // them, newest last.
  pendingTerminals: { id: string; command: string }[];
  // Subagent bookkeeping. A subagent is announced twice: first as the parent's
  // `run_subagent` tool call (which owns the rendered block) and then under its
  // own agentId, which is what all its later work is tagged with. These map one
  // to the other so the webview only ever sees the block's id.
  subagentSpawns: { id: string; title: string }[];
  subagentIds: Map<string, string>;
  // Every revertible point in this conversation, as the agent last reported it.
  // Pushed after each turn, so this is the authoritative source of the node ids
  // checkpoints and edit-and-resubmit rewind to.
  steps: RevertStep[];
  stepsKnown: boolean;
  openDocuments: Map<string, { uri: string; languageId: string; isDirty: boolean }>;
}

// Cap on a backgrounded session's replay buffer (oldest dropped past this).
const BG_BUFFER_MAX = 6000;
const REPLAY_BATCH_SIZE = 100;

// Shown in place of a transcript that could not be opened. The reason itself goes
// to the output channel, which the notification points at.
const OPEN_FAILED = "Couldn't open this chat. See the Vtx output for details.";

// A file or image attached to a message: staged in the composer, then travelling
// with the message it was attached to.
export interface Staged {
  id: string;
  label: string;
  type: string;
  block: ContentBlock;
}

// A live session handed from one chat surface to another (the side panel to an
// editor tab, or back). The `vtx acp` process, its terminals and any request it
// is still waiting on all travel together, so the agent never restarts, keeps the
// CLI's session lock, and an in flight turn is not interrupted.
export interface RuntimeTransfer {
  rt: Runtime;
  // Files and images staged in the composer but not sent yet.
  attachments: Staged[];
  permissions: [string, { resolve: (res: RequestPermissionResult) => void; rid: string }][];
  elicitations: [string, { resolve: (res: unknown) => void; rid: string }][];
  // Which models cannot read an image, which the agent only reports when it lists
  // its options. The arriving surface never asks again, and an image sent to a
  // model that cannot decode it breaks the session for good, so it travels.
  modelImages: [string, boolean][];
  from: string; // where it came from, for the log line the handover leaves
}

// What a controller needs from the surface that owns it: which other surface has
// a session, how to move one, and how to bring one to the front. Implemented by
// the ChatManager, which is the only thing that knows about all the surfaces.
export interface SurfaceHost {
  // Move a session to a new editor tab, or back to the side panel.
  detach(id: string): Promise<void>;
  attach(id: string): Promise<void>;
  // Move it to a specific surface, whichever one has it now.
  moveHere(to: ChatController, id: string): Promise<void>;
  owner(id: string, except?: ChatController): ChatController | undefined;
  label(controller: ChatController): string;
  reveal(controller: ChatController): void;
  // Live session ids held by every surface other than this one.
  elsewhere(except: ChatController): string[];
  statuses(except: ChatController): Record<string, SessionStatus>;
  sessionListClient(except: ChatController): AcpClient | undefined;
  // Half given answers for a request that has moved to another surface.
  saveAnswerDraft(requestId: string, state: unknown, except: ChatController): void;
  // One surface's live session state changed, so every other surface's list,
  // status dots and "running elsewhere" badges are now out of date.
  sessionsChanged(except: ChatController): void;
  // A session was renamed, or the CLI finally reported its name, so any editor
  // tab showing it needs its own title updating.
  titlesChanged(): void;
}

// Image file extensions we attach inline (as base64): the formats the agent can
// decode. Anything else is attached as text, including an image it cannot read
// (svg, heic, avif), which as an image block gets the whole request rejected.
const IMAGE_MIME_BY_EXT: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  bmp: "image/bmp",
  tif: "image/tiff",
  tiff: "image/tiff",
  ico: "image/x-icon"
};

// Match VS Code chat's cap on an attached image (it refuses larger ones).
const MAX_IMAGE_BYTES = 30 * 1024 * 1024;

// How much of an attached file's text is sent. A dropped file says so when it is
// cut short, since its bytes are all the agent gets: it has no path to read more.
const MAX_ATTACH_CHARS = 200000;

// The mode a chat starts in when nothing else says otherwise. "default" is
// the id vtx's own session/new advertises; the richer lists the pickers show
// arrive from the agent's config options.
const DEFAULT_MODE = "default";

// How long typing settles before the agent is told a file has unsaved edits. It
// only needs to know that the file is dirty, so one message per pause is enough.
const DIRTY_DEBOUNCE_MS = 1500;

// How many problems "Fix Problems Here" hands over. A file with hundreds is a
// build that is broken everywhere, not a question about a few lines.
const MAX_ATTACHED_PROBLEMS = 40;

// Below this many filename matches, the query is probably naming a symbol rather
// than a file, so the language servers are asked too.
const SYMBOL_FALLBACK_AT = 5;

// How long a "take over this session?" question waits for an answer before it is
// treated as declined. Long enough to walk away from and come back to, short of
// leaving the session unopenable for the rest of the window.
const TAKEOVER_TIMEOUT_MS = 3 * 60_000;
const SESSION_LIST_ACP_TIMEOUT_MS = 8_000;
const SESSION_LIST_CLI_TIMEOUT_MS = 12_000;

// A real file the user is working in, worth telling the agent about. The diff
// views git opens are a second scheme, but git also opens true files inside
// .git (a commit message), and those are plumbing, not the user's work. It has
// to be a path segment: `.github/workflows` and `.gitignore` are ordinary files.
function isEditorDocument(doc: vscode.TextDocument): boolean {
  return doc.uri.scheme === "file" && !/(^|[/\\])\.git([/\\]|$)/.test(doc.uri.fsPath);
}

// The few symbol kinds worth naming in a suggestion row. Anything else is left as
// "symbol" rather than spelling out every kind VS Code knows.
function symbolKindLabel(kind: vscode.SymbolKind): string {
  switch (kind) {
    case vscode.SymbolKind.Class: return "class";
    case vscode.SymbolKind.Interface: return "interface";
    case vscode.SymbolKind.Function: return "function";
    case vscode.SymbolKind.Method: return "method";
    case vscode.SymbolKind.Enum: return "enum";
    case vscode.SymbolKind.Constant: return "constant";
    case vscode.SymbolKind.Variable: return "variable";
    case vscode.SymbolKind.Struct: return "struct";
    default: return "symbol";
  }
}

function severityLabel(s: vscode.DiagnosticSeverity): string {
  if (s === vscode.DiagnosticSeverity.Error) return "error";
  if (s === vscode.DiagnosticSeverity.Warning) return "warning";
  if (s === vscode.DiagnosticSeverity.Information) return "info";
  return "hint";
}

// Shown until a session reports the real list. Deliberately the modes every
// supported CLI has: anything newer (`smart`, and whatever comes next) arrives
// from the agent instead of being hardcoded here.
const FALLBACK_MODES = [
  { value: DEFAULT_MODE, name: "Code", icon: "codicon-code" },
  { value: "ask", name: "Ask", icon: "codicon-comment-discussion" },
  { value: "plan", name: "Plan", icon: "codicon-checklist" },
  { value: "bypass", name: "Bypass", icon: "codicon-unlock" }
];

// Vtx names its mode icons after its own icon set, so map the ones it sends to
// the nearest codicon. An unknown name falls back to a neutral one rather than
// rendering an empty square.
const MODE_ICONS: Record<string, string> = {
  code: "codicon-code",
  sparkles: "codicon-sparkle",
  "message-circle": "codicon-comment-discussion",
  "file-text": "codicon-checklist",
  "shield-off": "codicon-unlock",
  shield: "codicon-shield",
  zap: "codicon-zap"
};

// The mode picker's options, from the agent's own `mode` config option. Vtx also
// sends a description for each ("Auto-approve actions the model judges safe"),
// which the picker has nowhere to show yet, so it is left out rather than passed
// through unused.
function configChoices(opt?: ConfigOption): { value: string; name?: string; description?: string; _meta?: Record<string, unknown> }[] {
  return (opt?.options || []).flatMap((choice) => "group" in choice ? choice.options : [choice]);
}

function groupedModelChoices(options?: ConfigOption[]): Record<string, { value: string; name?: string; description?: string }[]> {
  const model = options?.find((option) => option.id === "model" || option.category === "model");
  const groups: Record<string, { value: string; name?: string; description?: string }[]> = {};
  for (const choice of model?.options || []) {
    if ("group" in choice) {
      groups[choice.group] = choice.options;
    }
  }
  return groups;
}

function thoughtOption(options?: ConfigOption[]): ConfigOption | undefined {
  // Vtx reports the effort picker as `thinking`; `thought_level` is the id the
  // fake agent and older CLIs used. Match both and send back the option's own id.
  return options?.find((option) =>
    ["thought_level", "thinking"].includes(option.id) || ["thought_level", "thinking"].includes(option.category || "")
  );
}

function thoughtWireId(options?: ConfigOption[]): string {
  return thoughtOption(options)?.id || "thought_level";
}

// `vtx models list` exposes per-effort uids the session's `model` option does
// not accept: a session takes one uid per family, with the effort set through
// `thought_level`, and only the "high" lead-effort Fusion pairs. A picked uid
// therefore has to be translated to the uid this session advertises, with the
// stripped effort carried over for `thought_level`.
const EFFORT_SUFFIX = /-(none|low|medium|high|xhigh)$/;
function effortOf(uid: string): string | undefined {
  return EFFORT_SUFFIX.exec(uid)?.[1];
}
// Fusion uids carry the lead effort (`fusion-<lead>-<effort>-sidekick-<s>`),
// which the session does not. Two uids describe the same pair once it is gone.
function fusionKey(uid: string): string | undefined {
  if (!uid.startsWith("fusion-")) return undefined;
  const [lead = "", sidekick = ""] = uid.slice("fusion-".length).split("-sidekick-");
  return `${lead.replace(EFFORT_SUFFIX, "")}+${sidekick}`;
}
function acceptedModelUid(options: ConfigOption[] | undefined, requested: string): { model: string; effort?: string } {
  const accepted = new Set(
    configChoices(options?.find((o) => o.id === "model" || o.category === "model")).map((c) => c.value)
  );
  if (!accepted.size || accepted.has(requested)) return { model: requested };
  const fusion = fusionKey(requested);
  if (fusion) {
    const lead = requested.slice("fusion-".length).split("-sidekick-")[0];
    for (const value of accepted) {
      if (fusionKey(value) === fusion) return { model: value, effort: effortOf(lead) };
    }
    return { model: requested };
  }
  const base = requested.replace(EFFORT_SUFFIX, "");
  for (const value of accepted) {
    if (value.replace(EFFORT_SUFFIX, "") === base) return { model: value, effort: effortOf(requested) };
  }
  return { model: requested };
}

function modeChoices(opt?: ConfigOption): { value: string; name: string; icon: string }[] {
  return configChoices(opt).map((c) => ({
    value: c.value,
    name: c.name || c.value,
    icon: MODE_ICONS[String(c._meta?.["cognition.ai/icon"] || "")] || "codicon-circle-outline"
  }));
}

// The dot shown next to a session in the list. "attention" means a backgrounded
// session raised a permission/question and is now blocked waiting on the user.
type SessionStatus = "running" | "idle" | "starting" | "attention";

// One chat surface. The sidebar view and each editor/window panel is its own
// ChatController bound to a single `vscode.Webview`, with its own visible
// session and its own runtime pool. Shared singletons (SessionStore,
// ChangeTracker, StatusBar) are passed in by the ChatManager.
export class ChatController implements AcpHost {
  private webview?: vscode.Webview;
  // How to bring this surface to the front (focus the view, or reveal the panel).
  private reveal?: () => void;
  // When set (a freshly created editor/window surface), start a new session as
  // soon as the webview is ready instead of showing the session list.
  autoNewSession = false;
  // The chat an editor tab was holding before a window reload, reopened as soon as
  // the restored page is ready so the tab comes back to it rather than empty.
  openOnReady?: string;
  private readonly ownDisposables: vscode.Disposable[] = [];

  // Live runtimes keyed by session id. Absent = dead (gray) history.
  private readonly runtimes = new Map<string, Runtime>();
  // The session currently shown in the webview (the interactive one).
  private activeId?: string;
  // A runtime "starting" label per id (e.g. waking / creating), for the dots.
  private readonly starting = new Set<string>();
  // In-flight load/wake per session id. `onDidReceiveMessage` is fire-and-forget,
  // so two quick opens of the same session would otherwise both spawn a
  // `vtx acp` (the second orphaning the first and holding its lock). Sharing
  // the promise keeps it to one process.
  private readonly loading = new Map<string, Promise<void>>();
  // A brand-new session being created (id not known until session/new returns).
  private startingNew?: Promise<Runtime>;
  private idleTimer?: NodeJS.Timeout;

  private health?: CliHealth;
  private resolvedCli = "vtx";
  private env?: NodeJS.ProcessEnv;
  private currentMode?: string;
  private currentModel?: string;
  private currentThoughtLevel?: string;
  // Bumped every time a model change is queued or starts. After each await the
  // caller checks whether the generation moved, and bails if a newer call has
  // taken over, so only the latest model change publishes options.
  private modelGen = 0;
  private modelConfigTail: Promise<void> = Promise.resolve();
  private pendingModelConfig?: {
    rt: Runtime | undefined;
    model: string;
    thoughtLevel?: string;
    requestId?: number;
  };

  private readonly permissionResolvers = new Map<string, { resolve: (res: RequestPermissionResult) => void; rid: string }>();
  // Shared across surfaces: a request id has to stay unique when a session (and
  // the requests it is waiting on) moves from one surface to another.
  private static permissionSeq = 0;

  private attachSeq = 0;

  // Whether the active editor file is sent as implicit context (VS Code's
  // current-file behaviour). Mirrored to the composer as a pill.
  private implicitEnabled = true;
  private implicitTimer?: NodeJS.Timeout;
  private changeListSub?: vscode.Disposable;
  private changeResolveSub?: vscode.Disposable;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly store: SessionStore,
    private readonly changes: ChangeTracker,
    private readonly statusBar: StatusBar | undefined,
    private readonly output: vscode.OutputChannel,
    private readonly kind: "view" | "editor" = "view",
    // The surface that owns this controller, so a session can be moved between
    // surfaces and one already open elsewhere is revealed rather than fought over.
    private readonly surfaces?: SurfaceHost
  ) {
    this.changeListSub = this.changes.onDidChangeList(() => this.postWorkingSet());
    // Keeping or undoing a change is answered by the tracker, not by the button
    // that asked: "Keep all" and the Source Control view resolve files nobody
    // clicked, and the edit rows for those have to say so too.
    this.changeResolveSub = this.changes.onDidResolve((e) =>
      this.post({ type: "changesResolved", paths: e.paths, action: e.action })
    );
    this.implicitEnabled = this.cfg().get<boolean>("implicitContext.enabled", true);
    this.sessionsCache = this.store.sessionList();
    // Keep the implicit "current file" pill in sync with the active editor and
    // its selection (the latter debounced, since selection changes fire often).
    // Stored per controller so an editor/window surface cleans them up on close.
    this.ownDisposables.push(
      vscode.window.onDidChangeActiveTextEditor((e) => {
        this.postImplicitContext();
        // Which file the user is looking at, so "this file" means something and the
        // agent stops having to ask or guess.
        if (e) this.sendDocumentEvent("didFocus", e.document);
      }),
      vscode.window.onDidChangeTextEditorSelection((e) => {
        if (e.textEditor === vscode.window.activeTextEditor) this.scheduleImplicitPost();
      }),
      vscode.workspace.onDidOpenTextDocument((d) => this.sendDocumentEvent("didOpen", d)),
      vscode.workspace.onDidCloseTextDocument((d) => this.sendDocumentEvent("didClose", d)),
      // A file the agent read and the user has since edited without saving is the
      // stale read it cannot otherwise know about.
      vscode.workspace.onDidSaveTextDocument((d) => this.sendDocumentEvent("didChangeDirty", d)),
      vscode.workspace.onDidChangeTextDocument((e) => {
        if (e.document.isDirty) this.scheduleDirtyEvent(e.document);
      }),
      // Every panel preference rides on the capabilities message, so a setting
      // changed in the Settings editor applies to an open chat straight away
      // instead of waiting for the next session event.
      vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration("vtx")) this.postCapabilities();
      })
    );
  }

  private scheduleImplicitPost(): void {
    if (this.implicitTimer) {
      clearTimeout(this.implicitTimer);
    }
    this.implicitTimer = setTimeout(() => this.postImplicitContext(), 150);
  }

  // Tell the webview about the active editor file (and selection range) so it
  // can render the implicit-context pill.
  private postImplicitContext(): void {
    const ed = vscode.window.activeTextEditor;
    let file: { path: string; name: string; line1?: number; line2?: number } | null = null;
    if (ed && ed.document.uri.scheme === "file") {
      const doc = ed.document;
      const sel = ed.selection;
      const hasSel = !!sel && !sel.isEmpty;
      file = {
        path: doc.uri.fsPath,
        name: path.basename(doc.uri.fsPath),
        line1: hasSel ? sel.start.line + 1 : undefined,
        line2: hasSel ? sel.end.line + 1 : undefined
      };
    }
    this.post({ type: "implicitContext", file, enabled: this.implicitEnabled });
  }

  // Tell the live agent what the user is doing in the editor. This is the half of
  // the editor context the extension can give that the CLI in a terminal cannot,
  // and the agent needs it for diagnostics too: it only reports problems for
  // documents it has been told are open.
  //
  // Only the visible session is told. A background agent is working on its own
  // turn and the user's editor is not part of it, and telling every runtime would
  // multiply every keystroke by the number of open chats.
  private sendDocumentEvent(kind: "didOpen" | "didClose" | "didChangeDirty" | "didFocus", doc: vscode.TextDocument): void {
    const rt = this.active();
    if (!this.surfaceVisible || !rt || !rt.initialized || !rt.client.supportsDocumentLifecycle() || !isEditorDocument(doc)) {
      return;
    }
    // A URI, not a path: a path is rejected outright by the agent.
    const state = { uri: doc.uri.toString(), languageId: doc.languageId, isDirty: doc.isDirty };
    rt.client.documentEvent(kind, { sessionId: rt.id, ...state });
    if (kind === "didClose") {
      rt.openDocuments.delete(state.uri);
    } else if (kind !== "didFocus") {
      rt.openDocuments.set(state.uri, state);
    }
  }

  // Typing fires a change event per keystroke, so the dirty signal is coalesced:
  // the agent only needs to know a file has unsaved edits, not how many.
  private scheduleDirtyEvent(doc: vscode.TextDocument): void {
    const key = doc.uri.toString();
    if (this.dirtyTimers.has(key)) {
      return;
    }
    const t = setTimeout(() => {
      this.dirtyTimers.delete(key);
      this.sendDocumentEvent("didChangeDirty", doc);
    }, DIRTY_DEBOUNCE_MS);
    t.unref?.();
    this.dirtyTimers.set(key, t);
  }

  private readonly dirtyTimers = new Map<string, NodeJS.Timeout>();

  // Everything the user has open right now, sent to a session that has just
  // started so its context is not empty until the next editor event.
  private sendOpenDocuments(rt: Runtime): void {
    if (!this.surfaceVisible || this.activeId !== rt.id || !rt.client.supportsDocumentLifecycle()) {
      return;
    }
    const documents = new Map<string, { uri: string; languageId: string; isDirty: boolean }>();
    for (const doc of vscode.workspace.textDocuments) {
      if (isEditorDocument(doc)) {
        const state = { uri: doc.uri.toString(), languageId: doc.languageId, isDirty: doc.isDirty };
        documents.set(state.uri, state);
      }
    }
    for (const [uri, state] of rt.openDocuments) {
      if (!documents.has(uri)) {
        rt.client.documentEvent("didClose", { sessionId: rt.id, ...state });
      }
    }
    for (const [uri, state] of documents) {
      const previous = rt.openDocuments.get(uri);
      if (!previous) {
        rt.client.documentEvent("didOpen", { sessionId: rt.id, ...state });
      } else if (previous.languageId !== state.languageId || previous.isDirty !== state.isDirty) {
        rt.client.documentEvent("didChangeDirty", { sessionId: rt.id, ...state });
      }
    }
    rt.openDocuments = documents;
    const active = vscode.window.activeTextEditor?.document;
    if (active && isEditorDocument(active)) {
      rt.client.documentEvent("didFocus", {
        sessionId: rt.id,
        uri: active.uri.toString(),
        languageId: active.languageId,
        isDirty: active.isDirty
      });
    }
  }

  // The active editor as implicit context: the selection when there is one,
  // otherwise a lightweight resource link the agent can open.
  private buildImplicitBlocks(): ContentBlock[] {
    if (!this.implicitEnabled) {
      return [];
    }
    const ed = vscode.window.activeTextEditor;
    if (!ed || ed.document.uri.scheme !== "file") {
      return [];
    }
    const doc = ed.document;
    const uri = doc.uri;
    const sel = ed.selection;
    const rel = vscode.workspace.asRelativePath(uri);
    if (sel && !sel.isEmpty) {
      const body = doc.getText(sel).slice(0, 20000);
      return [{
        type: "text",
        text: `Current selection from ${rel} lines ${sel.start.line + 1}-${sel.end.line + 1}:\n\n\`\`\`${doc.languageId}\n${body}\n\`\`\``
      }];
    }
    return [{ type: "resource_link", uri: uri.toString(), name: path.basename(uri.fsPath) }];
  }

  // Hand the webview the unsent text stored for whichever composer it is showing
  // (a session's own, or the "new chat" box when there is none). The id travels
  // with it so a late arrival for a session already left is ignored.
  private postDraft(): void {
    this.post({ type: "draft", id: this.activeId || null, text: this.store.draft(this.activeId) });
  }

  // Part way through answering a question, remembered against the request so it
  // survives leaving the session and coming back. It lives on the pending payload
  // the host re-posts, so it is restored by the same path that re-shows the
  // question, and is dropped with it once the question is answered.
  private saveAnswerDraft(requestId: string, state: unknown): void {
    // The widget flushes its answers as it is torn down, which is also how a
    // session leaves a surface, so by now the request may belong to another one.
    if (!this.storeAnswerDraft(requestId, state)) {
      this.surfaces?.saveAnswerDraft(requestId, state, this);
    }
  }

  storeAnswerDraft(requestId: string, state: unknown): boolean {
    for (const rt of this.runtimes.values()) {
      const p = rt.pending.find((x) => x.requestId === requestId);
      if (p) {
        p.payload.answers = state;
        return true;
      }
    }
    return false;
  }

  // The working set is what the visible session changed. Other sessions keep
  // their own edits tracked (reopening one gets its files back), and the Source
  // Control view still lists everything.
  private postWorkingSet(): void {
    this.post({
      type: "workingSet",
      // The counts travel with the files, so a working set restored after a reload
      // arrives complete instead of waiting for each edit to be reported again.
      files: this.changes.changesFor(this.activeId).map((c) => ({
        path: c.path,
        name: path.basename(c.path),
        added: c.added,
        removed: c.removed
      }))
    });
  }

  // --- Webview lifecycle ---------------------------------------------------

  // Bind this controller to a webview (from a WebviewView or a WebviewPanel).
  // `reveal` brings the surface to the front when focus() is called.
  bind(webview: vscode.Webview, reveal?: () => void): void {
    this.webview = webview;
    this.reveal = reveal;
    // A re-bind is a brand new page with an empty transcript, and nothing painted
    // into it yet.
    this.webviewReady = false;
    this.painted = false;
    webview.options = {
      enableScripts: true,
      localResourceRoots: [this.context.extensionUri]
    };
    webview.html = this.getHtml(webview);
    webview.onDidReceiveMessage((msg) => this.onMessage(msg));
  }

  // Resolves once the webview is listening, so a session handed to a freshly
  // created surface is not posted into a page that cannot hear it yet.
  private webviewReady = false;
  // Set when the page this controller was bound to has gone for good (its editor
  // tab closed), so nothing waits on a reply that can never come.
  private webviewGone = false;
  private closed = false;
  // Whether a chat has been painted into the current page. The readiness chain
  // restores the last chat only when nothing has, so a session handed to this
  // surface is not immediately reloaded on top of itself.
  private painted = false;
  private readyWaiters: { resolve: () => void; reject: (reason: Error) => void }[] = [];
  whenReady(): Promise<void> {
    if (this.closed) {
      return Promise.reject(new Error("Chat surface is closed"));
    }
    if (this.webviewReady) {
      return Promise.resolve();
    }
    return new Promise<void>((resolve, reject) => this.readyWaiters.push({ resolve, reject }));
  }

  private releaseReadyWaiters(error?: Error): void {
    const waiters = this.readyWaiters;
    this.readyWaiters = [];
    for (const waiter of waiters) {
      if (error) {
        waiter.reject(error);
      } else {
        waiter.resolve();
      }
    }
  }

  markClosed(): void {
    this.webviewGone = true;
    this.closed = true;
    this.releaseReadyWaiters(new Error("Chat surface is closed"));
  }

  // What the composer holds is only known to the page: the draft is debounced and
  // a question's answers are half given. A handover is instant, so the page is
  // asked to write both back BEFORE the chat leaves, and waited on, or a prompt
  // someone was part way through writing arrives on the new surface empty.
  private flushWaiters: (() => void)[] = [];
  async flushSurfaceState(): Promise<void> {
    if (!this.webviewReady || this.webviewGone) {
      return;
    }
    this.post({ type: "flushState" });
    await new Promise<void>((resolve) => {
      const timer = setTimeout(() => {
        this.flushWaiters = this.flushWaiters.filter((w) => w !== done);
        resolve();
      }, 250);
      const done = () => {
        clearTimeout(timer);
        resolve();
      };
      this.flushWaiters.push(done);
    });
  }

  private onStateFlushed(): void {
    const waiters = this.flushWaiters;
    this.flushWaiters = [];
    for (const w of waiters) {
      w();
    }
  }

  // Open a session on this surface (used when a move has nothing live to carry).
  async openSession(id: string): Promise<void> {
    if (!id) {
      return;
    }
    this.post({ type: "body", body: "thread" });
    await this.loadSession(id);
    this.focus();
  }

  focus(): void {
    if (this.reveal) {
      this.reveal();
    } else {
      void vscode.commands.executeCommand("vtx.chatView.focus");
    }
  }

  // Kill every live ACP process (and its terminals) so a window reload or
  // extension deactivate does not leave stranded `vtx acp` agents (and their
  // MCP servers). This is item 6: stop all sessions on exit.
  //
  // Synchronous, so the escalation to SIGKILL is left on a timer. Prefer
  // `shutdown()` when the extension host itself is going away, since that timer
  // will not fire once it has exited.
  dispose(): void {
    this.closed = true;
    this.releaseReadyWaiters(new Error("Chat surface is closed"));
    this.stopLocalState();
    // A surface that has gone cannot be the one holding the keyboard.
    this.setFocused(false);
    for (const rt of new Set([...this.runtimes.values(), ...this.spawnedRuntimes])) {
      // The same as terminating the chat, which this is one of the ways of doing:
      // closing a tab mid turn offers to terminate, and the prompt promises the
      // conversation is kept, not that the writing behind it is thrown away.
      this.keepQueued(rt);
      this.destroyRuntime(rt);
    }
    this.runtimes.clear();
    this.spawnedRuntimes.clear();
    this.starting.clear();
    this.activeId = undefined;
  }

  // Stop everything for good on the way out (window reload, extension
  // deactivate), and resolve only once every agent is really gone.
  //
  // A `vtx acp` agent cannot be handed over to the next extension host: it
  // runs its shell commands, file writes and permission prompts through us, over
  // our stdio, so leaving it alive would strand an agent that can do nothing
  // while still holding the CLI's session lock. Instead each one is stopped
  // deterministically here, and any session with a turn in flight is recorded so
  // the next window can say the turn was interrupted rather than losing it
  // silently.
  async shutdown(): Promise<void> {
    this.closed = true;
    this.releaseReadyWaiters(new Error("Chat surface is closed"));
    // Everything with a process, not just everything with a session id: a chat
    // still waiting on session/new is not in the pool yet, and it is exactly as
    // unable to outlive us as the rest.
    const live = [...new Set([...this.runtimes.values(), ...this.spawnedRuntimes])];
    const interrupted = live.filter((rt) => rt.id && (rt.busy || rt.awaiting > 0)).map((rt) => rt.id);
    // Anything still waiting behind a turn is the user's own writing, and it only
    // ever lived on the runtime we are about to stop.
    const unsent = live.flatMap((rt) => rt.queued.map((q) => ({ id: rt.id, text: q.text })));
    this.stopLocalState();
    this.runtimes.clear();
    this.spawnedRuntimes.clear();
    this.starting.clear();
    this.activeId = undefined;
    // Record before killing: the write has to be in flight while the host is
    // still alive to flush it.
    const recorded = Promise.all([
      this.store.markInterrupted(interrupted),
      this.store.queuedBackToDrafts(unsent)
    ]);
    await Promise.all(live.map((rt) => this.stopRuntime(rt)));
    try {
      await recorded;
    } catch {
      // workspaceState is best effort; losing the note is not worth failing on
    }
  }

  // Stop one runtime in order: cancel the turn so the agent can stop cleanly,
  // stop its commands, wait for the agent to exit, then force anything left.
  private async stopRuntime(rt: Runtime): Promise<void> {
    if (rt.busy && rt.id) {
      try {
        rt.client.cancel(rt.id);
      } catch {
        // the pipe may already be gone
      }
    }
    try {
      rt.terminals.requestStopAll();
    } catch {
      // ignore
    }
    try {
      await rt.client.shutdown();
    } catch {
      // ignore
    }
    try {
      rt.terminals.forceStopAll();
    } catch {
      // ignore
    }
  }

  // Everything except the runtimes: timers, in-flight agent requests, and our
  // own subscriptions. Shared by both stop paths.
  private stopLocalState(): void {
    if (this.idleTimer) {
      clearInterval(this.idleTimer);
      this.idleTimer = undefined;
    }
    if (this.implicitTimer) {
      clearTimeout(this.implicitTimer);
      this.implicitTimer = undefined;
    }
    // Settle every in-flight agent request so its client-side call never hangs.
    for (const [, e] of this.permissionResolvers) {
      e.resolve({ outcome: { outcome: "cancelled" } });
    }
    this.permissionResolvers.clear();
    for (const [, e] of this.elicitationResolvers) {
      e.resolve({ action: "cancel" });
    }
    this.elicitationResolvers.clear();
    for (const [requestId] of [...this.takeoverResolvers]) {
      this.resolveTakeover(requestId, "cancel");
    }
    this.loading.clear();
    this.changeListSub?.dispose();
    this.changeListSub = undefined;
    this.changeResolveSub?.dispose();
    this.changeResolveSub = undefined;
    for (const [, t] of this.dirtyTimers) {
      clearTimeout(t);
    }
    this.dirtyTimers.clear();
    for (const d of this.ownDisposables) {
      try { d.dispose(); } catch { /* ignore */ }
    }
    this.ownDisposables.length = 0;
  }

  // --- Moving a live session between surfaces ------------------------------

  // Record the session this surface is showing. Only the side panel is restored
  // after a reload, so only it writes the marker.
  private markVisible(id: string): void {
    this.store.setActive(id);
    if (this.kind === "view") {
      this.store.setViewing(id);
    }
  }

  // Which sessions this surface is running, and whether it is showing one.
  ownsSession(id: string): boolean {
    return this.runtimes.has(id);
  }
  visibleSession(): string | undefined {
    return this.activeId;
  }
  // Every session this surface is running, including ones working in the
  // background while its list or another chat is on screen.
  liveSessions(): string[] {
    return [...this.runtimes.keys()].filter(Boolean);
  }
  liveSessionStatuses(): Record<string, SessionStatus> {
    return this.statusMap();
  }
  async waitForSessionLoad(id: string): Promise<void> {
    await this.loading.get(id);
  }
  sessionListClient(): AcpClient | undefined {
    return this.anyLiveClient();
  }

  // The chat is being opened on another surface and has no live agent to hand
  // over, so this surface simply stops showing it.
  releaseSession(id: string): void {
    if (this.activeId !== id) {
      return;
    }
    this.activeId = undefined;
    if (this.kind === "view") {
      this.store.setViewing(undefined);
      this.post({ type: "body", body: "list" });
    }
  }

  // Hand a live session out: it leaves this surface's pool with its process, its
  // terminals and anything it is still waiting on, and this surface stops showing
  // it. The agent is untouched, so nothing restarts and no lock changes hands.
  exportRuntime(id: string): RuntimeTransfer | undefined {
    const rt = this.runtimes.get(id);
    if (!rt || rt.replaying) {
      return undefined;
    }
    this.runtimes.delete(id);
    this.starting.delete(id);
    // Drop this controller's listeners: the new one attaches its own.
    rt.client.removeAllListeners();
    // The arriving surface owns the process from here, so this one must stop
    // counting it among the agents it is responsible for stopping.
    this.spawnedRuntimes.delete(rt);
    const take = <T>(map: Map<string, T & { rid: string }>): [string, T][] => {
      const taken: [string, T][] = [];
      for (const [key, entry] of [...map]) {
        if (entry.rid === id) {
          taken.push([key, entry]);
          map.delete(key);
        }
      }
      return taken;
    };
    // What was staged for this chat travels with it, whichever chat is on screen.
    const visible = this.activeId === id;
    const transfer: RuntimeTransfer = {
      rt,
      attachments: this.stagedFor(id),
      permissions: take(this.permissionResolvers),
      elicitations: take(this.elicitationResolvers),
      modelImages: [...this.modelImageSupport],
      from: this.kind === "view" ? "the side panel" : "an editor tab"
    };
    // Held by the surface it is going to now. The file on disk stays: it is that chat's,
    // and whichever surface shows it next reads it back the same way a reload does, which
    // is why the post below must not put an empty entry back in its place.
    this.staged.delete(this.stagedKey(id));
    if (visible) {
      this.postStaged();
    }
    // A lock takeover question belongs to a load this surface is abandoning, and
    // its widget goes with the transcript, so settle it rather than leave the load
    // waiting on an answer that can never arrive.
    for (const [rid, entry] of [...this.takeoverResolvers]) {
      if (entry.rid === id) {
        this.resolveTakeover(rid, "cancel");
      }
    }
    if (visible) {
      // This surface has nothing to show any more, so it goes back to its list.
      // An editor tab has no list: it is one chat, and the manager closes it.
      this.activeId = undefined;
      if (this.kind === "view") {
        this.store.setViewing(undefined);
        this.post({ type: "busy", value: false });
        this.post({ type: "body", body: "list" });
      }
    }
    this.broadcastStatuses();
    void this.refreshSessionsIfVisible();
    return transfer;
  }

  // Take a live session over: adopt its process and outstanding requests, then
  // show it. An idle session repaints its transcript from the agent it already
  // has (about a second, no new process); one mid turn cannot be reloaded over
  // its live channel, so it picks up from here and says so.
  async importRuntime(transfer: RuntimeTransfer): Promise<void> {
    if (this.closed) {
      throw new Error("Chat surface is closed");
    }
    const rt = transfer.rt;
    this.runtimes.set(rt.id, rt);
    rt.client.setHost(this);
    rt.client.on("log", (line: string) => this.log(line));
    rt.client.on("update", (n: SessionUpdateNotification) => this.onUpdate(n, rt));
    rt.client.on("output", (o: CliOutput) => this.onCliOutput(rt, o));
    rt.client.on("stopped", (s: AgentStopped) => this.onAgentStopped(rt, s));
    rt.client.on("revertSteps", (s: RevertStepsUpdate) => this.onRevertSteps(rt, s));
    rt.client.on("exit", () => this.onRuntimeExit(rt));
    rt.terminals.retarget(
      (terminalId, output, exitStatus) => {
        if (this.activeId === rt.id) {
          this.postTerminal(rt.terminals, terminalId, output, exitStatus);
        }
      },
      (line) => this.log(line)
    );
    for (const [key, entry] of transfer.permissions) {
      this.permissionResolvers.set(key, entry);
    }
    for (const [key, entry] of transfer.elicitations) {
      this.elicitationResolvers.set(key, entry);
    }
    for (const [model, supported] of transfer.modelImages) {
      this.modelImageSupport.set(model, supported);
    }
    // This surface is responsible for stopping it now.
    this.spawnedRuntimes.add(rt);
    this.activeId = rt.id;
    this.rememberSession(rt.id, rt.cwd);
    this.markVisible(rt.id);
    this.ensureIdleTimer();
    // The agent can die during the handover, when its exit has no listener and is
    // never emitted again, so the arriving surface has to ask.
    if (rt.client.hasExited()) {
      this.onRuntimeExit(rt);
      return;
    }
    // A turn that was already running is finished by this surface from now on.
    if (rt.busy && rt.turn) {
      this.adoptTurn(rt, rt.turn);
    }
    this.post({ type: "body", body: "thread" });
    if (rt.busy) {
      // A running turn cannot be replayed by the agent (loading over a live
      // channel kills the prompt), so the chat brings its own transcript with it.
      if (rt.log.length) {
        this.replayLog(rt);
      } else {
        this.post({ type: "clear" });
      }
      this.log(
        `[move] ${rt.id} arrived from ${transfer.from} mid turn: rebuilt ${rt.log.length} ` +
        `transcript ${rt.log.length === 1 ? "part" : "parts"}${rt.logFull ? "" : " (partial, the rest follows this turn)"}`
      );
      // Only what the record could not hold is worth asking the CLI for later.
      rt.needsReplay = !rt.logFull;
      await this.activateSession(rt.id);
    } else if (rt.logFull) {
      this.replayLog(rt);
      await this.activateSession(rt.id);
      this.log(`[move] ${rt.id} arrived from ${transfer.from} idle: restored from the local transcript`);
    } else {
      await this.doLoadSession(rt.id);
      this.log(`[move] ${rt.id} arrived from ${transfer.from} idle: reloaded from the agent`);
    }
    if (this.runtimes.get(rt.id) === rt && this.activeId === rt.id) {
      for (const terminal of rt.terminals.snapshots()) {
        this.postTerminal(rt.terminals, terminal.terminalId, terminal.output, terminal.exitStatus);
      }
    }
    if (transfer.attachments.length) {
      this.staged.set(this.stagedKey(rt.id), transfer.attachments);
      this.stagedChanged(rt.id);
    }
    // A brand new surface announces its own readiness while this is running, so
    // say once more where it should be: showing the chat, not its session list.
    this.post({ type: "body", body: "thread" });
    this.focus();
  }

  // --- Runtime pool --------------------------------------------------------

  private active(): Runtime | undefined {
    return this.activeId ? this.runtimes.get(this.activeId) : undefined;
  }

  private destroyRuntime(rt: Runtime): void {
    this.spawnedRuntimes.delete(rt);
    try {
      // Stop listening before stopping the process: everything this runtime could
      // still say is about a session we have finished with, and its exit is about
      // to arrive late enough to be mistaken for a live one's.
      rt.client.removeAllListeners();
      rt.client.dispose();
    } catch {
      // ignore
    }
    try {
      rt.terminals.disposeAll();
    } catch {
      // ignore
    }
  }

  // Every runtime whose process is alive, whether or not it has a session id yet.
  // The pool is keyed by session id, so a runtime waiting on session/new is not in
  // it, and a reload during those seconds left the agent running: it survived the
  // host that spawned it, still holding a lock, and on Windows nothing reaps it.
  private readonly spawnedRuntimes = new Set<Runtime>();

  // Spawn a fresh `vtx acp` process and wire its events. The runtime is not
  // yet in the pool: its session id is unknown until session/new or /load.
  private spawnRuntime(cwd: string): Runtime {
    if (this.closed) {
      throw new Error("Chat surface is closed");
    }
    const client = new AcpClient({
      cliPath: this.resolvedCli || "vtx",
      cwd,
      env: this.clientEnv(),
      extraArgs: this.extraArgs(),
      diagnostics: this.cfg().get<boolean>("editorContext.diagnostics", true)
    });
    let ref: Runtime | undefined;
    const terminals = new TerminalManager(
      this.clientEnv(),
      cwd,
      (terminalId, output, exitStatus) => {
        // Only the visible session streams terminal output to the webview.
        if (ref && this.activeId === ref.id) {
          this.postTerminal(terminals, terminalId, output, exitStatus);
        }
      },
      (line) => this.log(line),
      new VsCodeTerminalRunner(cwd, this.configuredEnv(), (line) => this.log(line))
    );
    const rt: Runtime = {
      id: "",
      cwd,
      client,
      terminals,
      initialized: false,
      busy: false,
      awaiting: 0,
      replaying: false,
      turnCount: 0,
      lastActivityAt: Date.now(),
      pending: [],
      queued: [],
      bgBuffer: [],
      log: [],
      replay: [],
      logFull: true,
      mcpProblems: new Map(),
      steps: [],
      stepsKnown: false,
      openDocuments: new Map(),
      pendingTerminals: [],
      subagentSpawns: [],
      subagentIds: new Map()
    };
    ref = rt;
    this.spawnedRuntimes.add(rt);
    client.setHost(this);
    client.on("log", (line: string) => this.log(line));
    client.on("update", (n: SessionUpdateNotification) => this.onUpdate(n, rt));
    client.on("output", (o: CliOutput) => this.onCliOutput(rt, o));
    client.on("stopped", (s: AgentStopped) => this.onAgentStopped(rt, s));
    client.on("revertSteps", (s: RevertStepsUpdate) => this.onRevertSteps(rt, s));
    client.on("exit", () => this.onRuntimeExit(rt));
    client.start();
    return rt;
  }

  // The CLI's own output stream. Its MCP channel is the only place a server that
  // would not start is reported: the tool call simply never happens, and a session
  // load the agent aborts over it says nothing about why.
  private onCliOutput(rt: Runtime, out: CliOutput): void {
    this.log(`[${out.channel || "cli"}] ${out.message || ""}`);
    if (out.channel !== "MCP" || (out.level !== "warn" && out.level !== "error")) {
      return;
    }
    const name = /'([^']+)'/.exec(out.message || "")?.[1];
    if (name) {
      rt.mcpProblems.set(name, (out.message || "").trim());
      this.postMcpProblems(rt);
    }
  }

  // Which chat surfaces currently hold the keyboard. A keybinding cannot ask
  // whether focus is inside a webview (`focusedView` is the view pane, and it is
  // not the pane that has focus once the iframe takes it, nor is there a view at
  // all for a chat in an editor tab), so the panel says so itself and this
  // publishes it as a context key for the `when` clauses to use.
  private static readonly focused = new Set<ChatController>();

  // The chat the keyboard is in, for a shortcut that should act on the chat the
  // user is looking at rather than always on the side panel's.
  static focusedSurface(): ChatController | undefined {
    return [...ChatController.focused][0];
  }

  private setFocused(has: boolean): void {
    const before = ChatController.focused.size;
    if (has) {
      ChatController.focused.add(this);
    } else {
      ChatController.focused.delete(this);
    }
    const now = ChatController.focused.size;
    if (!before !== !now) {
      void vscode.commands.executeCommand("setContext", "vtx.chatFocused", now > 0);
    }
  }

  // Muted for this window only ("not again now"): a restart is a fresh start, so
  // a server that is still broken says so again. Turning it off for good is the
  // setting, which this reads too.
  private static mcpMutedHere = false;

  private muteMcpWarnings(scope: string): void {
    if (scope === "always") {
      void this.cfg().update("showMcpWarnings", false, vscode.ConfigurationTarget.Global);
    } else {
      ChatController.mcpMutedHere = true;
    }
  }

  // A command's output, with what can be done about it: opened in the terminal
  // it is really running in, and left running so the agent stops waiting on it.
  private postTerminal(
    terminals: TerminalManager,
    terminalId: string,
    output: string,
    exitStatus: TerminalExitStatus | null
  ): void {
    this.post({
      type: "terminalOutput",
      terminalId,
      output,
      exitStatus,
      integrated: terminals.isIntegrated(terminalId),
      skipped: terminals.isSkipped(terminalId),
      revealed: terminals.isRevealed(terminalId)
    });
  }

  // The skill behind an invocation, opened where it is written. The CLI looks in
  // the workspace first and then the user's own directories, and takes either
  // spelling of the folder, so this looks in the same places in the same order.
  private async openSkill(name: string): Promise<void> {
    if (!name || /[\\/]/.test(name)) {
      return;
    }
    const roots = [
      ...(vscode.workspace.workspaceFolders || []).map((f) => f.uri.fsPath),
      userConfigDir(),
      os.homedir()
    ];
    for (const root of roots) {
      for (const dir of [".vtx", ".agents", ""]) {
        const file = path.join(root, dir, "skills", name, "SKILL.md");
        if (fs.existsSync(file)) {
          await vscode.window.showTextDocument(vscode.Uri.file(file), { preview: true });
          return;
        }
      }
    }
    void vscode.window.showWarningMessage(`Couldn't find the skill "${name}" on this machine.`);
  }

  // Always posts for the visible chat, including an empty list: the card lives above
  // the composer rather than in the transcript, so it is not swapped out with the
  // thread, and staying silent left the last chat's failures sitting over the next
  // one, telling the user it was missing tools it actually had.
  private postMcpProblems(rt: Runtime): void {
    if (this.activeId !== rt.id) {
      return;
    }
    const muted = ChatController.mcpMutedHere || !this.cfg().get<boolean>("showMcpWarnings", true);
    this.post({
      type: "mcpProblems",
      servers: muted ? [] : [...rt.mcpProblems].map(([name, message]) => ({ name, message }))
    });
  }

  // What a finished turn cost. The CLI hands over its own labelled figures
  // (`responseDimensions`), so the footer shows those rather than inventing names
  // for them, alongside the wall clock time it reports.
  private onAgentStopped(rt: Runtime, stopped: AgentStopped): void {
    const stats = stopped.stats;
    if (!stats) {
      return;
    }
    this.emit(rt, {
      type: "turnStats",
      model: stats.modelLabel,
      totalTimeMs: stats.totalTimeMs,
      dimensions: (stats.responseDimensions || []).map((d) => ({
        label: d.label,
        value: dimensionText(d)
      })).filter((d) => d.label && d.value)
    });
  }

  // A runtime's `vtx acp` exited (crash, kill, or idle exit). Drop it from
  // the pool and, if it was the visible one, reflect the disconnected state.
  private onRuntimeExit(rt: Runtime): void {
    // A disposed agent is SIGKILLed on a timer, so its exit can arrive up to a
    // second and a half after we stopped caring, by which time the user may have
    // reopened the session and a NEW runtime holds this id. Deleting by id alone
    // removed that one instead: the live agent kept the CLI's session lock while
    // being invisible to the pool, so the row went gray, Terminate disappeared,
    // the next send met its own lock, and shutdown never stopped it.
    const current = rt.id ? this.runtimes.get(rt.id) : undefined;
    if (current && current !== rt) {
      this.log(`[exit] a replaced agent for ${rt.id} exited, keeping the live one`);
      // Still stop counting the dead one among the agents to stop, or its transcript
      // and buffers are held for the life of the window.
      this.spawnedRuntimes.delete(rt);
      return;
    }
    this.spawnedRuntimes.delete(rt);
    if (rt.id) {
      // Settle any prompt the agent was still waiting on so its client-side
      // call does not hang and no dead widget is left on screen.
      this.settleRequestsFor(rt.id);
      // An agent can also go without us asking: a crash, an external kill, the CLI
      // giving up. Every teardown we start keeps the queued messages, and this is the
      // one where the user is least expecting to lose anything.
      this.keepQueued(rt);
      this.runtimes.delete(rt.id);
      this.starting.delete(rt.id);
    }
    if (this.activeId === rt.id) {
      this.setBusy(false);
      this.statusBar?.set({ connected: false });
      // The side panel says this in its list (the dot goes gray). An editor tab
      // has no list, so the chat itself has to explain why it went quiet.
      if (this.kind === "editor") {
        this.post({ type: "sessionEnded" });
      }
    }
    this.broadcastStatuses();
  }

  private runtimeBySessionId(sessionId?: string, owner?: Runtime): Runtime | undefined {
    if (sessionId && this.runtimes.has(sessionId)) {
      return this.runtimes.get(sessionId);
    }
    // The agent it came from, whenever the caller knows which that is. A session's
    // first updates arrive before `session/new` returns, so its runtime is not in
    // the pool to be found by id, and answering with whatever is on screen painted
    // a chat starting in the background into the chat the user was reading.
    if (owner) {
      return owner;
    }
    // Otherwise the active runtime (e.g. a request that arrives before the
    // session id is stamped, or a client that only serves one session).
    return this.active();
  }

  // --- Status dots ---------------------------------------------------------

  // What this surface is running, and which of them it is showing. Every other
  // surface lists these statuses too, so a change here has to reach them: without
  // it a chat started in a tab is missing from the side panel's list until it is
  // refreshed by hand.
  private ownership = "";

  private broadcastStatuses(): void {
    const own = this.statusMap();
    this.post({ type: "sessionStatuses", statuses: { ...this.surfaces?.statuses(this), ...own }, activeId: this.activeId, elsewhere: this.elsewhere() });
    const owned = `${Object.keys(own).sort().map((id) => `${id}:${own[id]}`).join(",")}|${this.activeId || ""}`;
    if (owned !== this.ownership) {
      this.ownership = owned;
      this.surfaces?.sessionsChanged(this);
    }
    this.statusBar?.set({ connected: this.isReady(), mode: this.currentMode, model: this.currentModel });
  }

  // Auto-exit idle (amber) runtimes that have been waiting longer than the
  // keep-alive window. Running sessions are never touched. Item 3.
  private ensureIdleTimer(): void {
    if (this.idleTimer) {
      return;
    }
    this.idleTimer = setInterval(() => this.reapIdleRuntimes(), 30000);
    this.idleTimer.unref?.();
  }

  private reapIdleRuntimes(): void {
    const minutes = this.cfg().get<number>("idleSessionKeepAliveMinutes", 60);
    const limit = Math.floor(this.cfg().get<number>("maxIdleSessions", 3));
    const now = Date.now();
    const idle = [...this.runtimes.values()].filter((rt) => !rt.replaying && !rt.waking && !rt.busy && rt.awaiting === 0 && !rt.terminals.hasRunning());
    const reaped = new Set<Runtime>();
    if (minutes > 0) {
      const maxIdleMs = minutes * 60000;
      for (const rt of idle) {
        if (now - rt.lastActivityAt > maxIdleMs) {
          this.log(`[idle-exit] session ${rt.id} exceeded ${minutes}m idle; exiting`);
          reaped.add(rt);
        }
      }
    }
    if (limit > 0) {
      const remaining = idle.filter((rt) => !reaped.has(rt));
      const excess = remaining.length - limit;
      if (excess > 0) {
        for (const rt of remaining
          .filter((rt) => rt.id !== this.activeId)
          .sort((a, b) => a.lastActivityAt - b.lastActivityAt)
          .slice(0, excess)) {
          this.log(`[idle-exit] session ${rt.id} exceeded the idle session limit; exiting`);
          reaped.add(rt);
        }
      }
    }
    for (const rt of reaped) {
      this.settleRequestsFor(rt.id);
      this.keepQueued(rt);
      this.destroyRuntime(rt);
      this.runtimes.delete(rt.id);
      this.starting.delete(rt.id);
      if (this.activeId === rt.id) {
        // The visible session died; it stays on screen as history and will be
        // re-woken on the next send.
        this.setBusy(false);
      }
    }
    if (reaped.size) {
      this.broadcastStatuses();
    }
  }

  private post(message: unknown): void {
    // postMessage rejects if the webview is being disposed; swallow it so it
    // never surfaces as an unhandled rejection in the extension host.
    this.webview?.postMessage(message).then(undefined, () => {});
  }

  private log(line: string): void {
    this.output.appendLine(line);
  }

  // A session/load the agent aborted (most often because a configured MCP server
  // failed to start, which it treats as fatal). Point the user at the output for
  // the underlying reason rather than showing the opaque agent message.
  private reportLoadFailure(message: string, rt?: Runtime): void {
    this.log(`[load-failed] ${message}`);
    const show = "Show Output";
    // The agent treats an MCP server that will not start as fatal, and it is by
    // far the most common reason a load is aborted. When its output stream named
    // the servers, say which, instead of leaving the user to guess.
    const blamed = rt && rt.mcpProblems.size ? [...rt.mcpProblems.keys()].join(", ") : "";
    void vscode.window
      .showErrorMessage(
        blamed
          ? `Couldn't open this session. These MCP servers failed to start: ${blamed}. See the Vtx output for details.`
          : "Couldn't open this session. A configured MCP server may have failed to start, or the session is unavailable. See the Vtx output for details.",
        show
      )
      .then((choice) => {
        if (choice === show) {
          this.output.show(true);
        }
      });
  }

  // --- Message handling from the webview ----------------------------------

  private async onMessage(msg: any): Promise<void> {
    try {
      switch (msg?.type) {
        case "ready":
          await this.onWebviewReady();
          return;
        case "send":
          await this.handleSend(String(msg.text || ""), !!msg.newSession, !!msg.preserveTranscript);
          return;
        case "stopAndSend":
          this.stopAndSend(String(msg.text || ""));
          return;
        case "removeQueued":
          this.removeQueued(String(msg.id || ""));
          return;
        case "editQueued":
          this.editQueued(String(msg.id || ""), String(msg.text || ""));
          return;
        case "sendQueuedNow":
          this.sendQueuedNow(String(msg.id || ""));
          return;
        case "queueEditing":
          // Which queued message (if any) the composer is editing. Changing it can
          // unblock a flush a completed turn deferred, so re-run the drain.
          this.setQueueEditing(msg.id ? String(msg.id) : undefined);
          return;
        case "cancel":
          this.cancel();
          return;
        case "webviewError":
          this.log(`[webview-error] in "${msg.where}": ${msg.message}`);
          return;
        case "revertPreview":
          await this.handleRevertPreview(Number(msg.head), msg.token);
          return;
        case "revertFork":
          await this.handleRevertFork(msg.target);
          return;
        case "shareSession":
          await this.shareSession();
          return;
        case "revertExecute":
          await this.handleRevertExecute(Number(msg.head), msg.resendText, !!msg.newSession);
          return;
        case "newSession":
          await this.newSession();
          return;
        case "newSessionAt":
          await this.newSessionAt(String(msg.target || "view"));
          return;
        case "openSettings":
          await vscode.commands.executeCommand("vtx.openSettings");
          return;
        case "loadSession":
          await this.loadSession(String(msg.id || ""));
          return;
        case "activateSession":
          await this.activateSession(String(msg.id || ""));
          return;
        case "wakeSession":
          await this.wakeSession(String(msg.id || ""));
          return;
        case "renameSession":
          await this.renameSession(String(msg.id || ""), msg.title);
          return;
        case "deleteSession":
          await this.deleteSession(String(msg.id || ""), msg.title);
          return;
        case "refreshSessions":
          // Only the explicit Refresh button forces a re-listing; going back to
          // the list serves the cache and revalidates behind it.
          if (msg.force) {
            await this.refreshSessions(true);
          } else {
            await this.refreshSessionsFast();
          }
          return;
        case "leaveToList":
          this.leaveToList();
          return;
        case "listVisible": {
          const visible = msg.value === true;
          if (this.listVisible !== visible) {
            this.listVisible = visible;
            if (visible && this.surfaceVisible) {
              void this.refreshSessionsFast();
            }
          }
          return;
        }
        case "detachSession":
          await this.surfaces?.detach(String(msg.id || this.activeId || ""));
          return;
        case "attachSession":
          await this.surfaces?.attach(String(msg.id || this.activeId || ""));
          return;
        case "moveHere":
          await this.surfaces?.moveHere(this, String(msg.id || ""));
          return;
        case "revealSession":
          this.revealOwner(String(msg.id || ""));
          return;
        case "stateFlushed":
          this.onStateFlushed();
          return;
        case "terminateSession":
          await this.terminateSession(String(msg.id || ""), msg.title, !!msg.returnToList);
          return;
        case "takeoverDecision":
          this.resolveTakeover(String(msg.requestId || ""), String(msg.decision || "cancel"));
          return;
        case "setMode":
          await this.setMode(String(msg.mode || DEFAULT_MODE));
          return;
        case "setModel":
          await this.queueModelConfig(String(msg.model || ""), undefined, msg.requestId);
          return;
        case "setFusionModel":
          await this.queueModelConfig(String(msg.model || ""), String(msg.thoughtLevel || ""), msg.requestId);
          return;
        case "setConfigOption":
          if (msg.configId === "thought_level" && typeof msg.value === "string") {
            await this.queueModelConfig(String(msg.model || this.currentModel || ""), msg.value, msg.requestId);
            return;
          }
          await this.setSessionConfig(String(msg.configId || ""), msg.value);
          return;
        case "permission":
          this.resolvePermission(String(msg.requestId), msg.optionId);
          return;
        case "subagentMode":
          await this.setSubagentMode(String(msg.id || ""), msg.background === true);
          return;
        case "chatFocus":
          this.setFocused(msg.value === true);
          return;
        case "openSkill":
          await this.openSkill(String(msg.name || ""));
          return;
        case "showTerminal": {
          // Reveal and focus the terminal a command is running in, and say so:
          // the action is named for whether it is hidden.
          const rt = this.active();
          const id = String(msg.terminalId || "");
          if (rt) {
            rt.terminals.show(id);
            this.postTerminal(rt.terminals, id, rt.terminals.output(id).output, rt.terminals.output(id).exitStatus);
          }
          return;
        }
        case "continueInBackground": {
          // The command keeps running; the agent stops waiting for it. Its row
          // says so as soon as the host confirms.
          const rt = this.active();
          const id = String(msg.terminalId || "");
          if (rt && rt.terminals.skip(id)) {
            this.postTerminal(rt.terminals, id, rt.terminals.output(id).output, null);
          }
          return;
        }
        case "muteMcpWarnings":
          this.muteMcpWarnings(String(msg.scope || "window"));
          return;
        case "openDiff":
          // An edit row opens what that edit did; the working set opens what the
          // file is still holding, which is every edit not yet reviewed.
          if (msg.editId) {
            await this.changes.openEdit(String(msg.editId), String(msg.path || ""));
          } else {
            await this.changes.openDiff(String(msg.path || ""));
          }
          return;
        case "openFile":
          await this.openFile(String(msg.path || ""), typeof msg.line === "number" ? msg.line : undefined);
          return;
        case "copyText":
          await vscode.env.clipboard.writeText(String(msg.text || ""));
          return;
        case "insertAtCursor":
          await this.insertAtCursor(String(msg.text || ""));
          return;
        case "applyToFile":
          await this.applyToFile(String(msg.text || ""));
          return;
        case "runInTerminal":
          this.runInTerminal(String(msg.text || ""));
          return;
        case "openExternal":
          if (msg.url) {
            await vscode.env.openExternal(vscode.Uri.parse(String(msg.url)));
          }
          return;
        case "openAllDiffs":
          await this.openAllDiffs();
          return;
        case "acceptFile":
          this.changes.accept(String(msg.path || ""));
          return;
        case "rejectFile":
          await this.changes.reject(String(msg.path || ""));
          return;
        // From the chat's own changed files tray, so scoped to that chat. The
        // Source Control title buttons stay unscoped: they speak for the view they
        // are in, which lists every chat's files.
        case "acceptAll":
          this.changes.acceptAll(this.activeId);
          return;
        case "rejectAll":
          await this.changes.rejectAll(this.activeId);
          return;
        case "addContext":
          await this.addContext();
          return;
        case "setImplicit":
          this.implicitEnabled = !!msg.enabled;
          await this.cfg().update("implicitContext.enabled", this.implicitEnabled, vscode.ConfigurationTarget.Workspace);
          this.postImplicitContext();
          return;
        case "attachImage":
          this.attachImage(msg.name, msg.mime, msg.data);
          return;
        case "attachDroppedText":
          this.attachDroppedText(msg.name, msg.text);
          return;
        case "attachDroppedFolder":
          this.attachDroppedFolder(msg.name, msg.entries);
          return;
        case "removeAttachment":
          this.removeAttachment(String(msg.id || ""));
          return;
        case "queryFiles":
          await this.queryFiles(String(msg.query || ""));
          return;
        case "addMention":
          await this.addFile(String(msg.path || ""));
          return;
        case "elicitationResponse":
          this.resolveElicitation(String(msg.requestId), String(msg.action || "cancel"), msg.content);
          return;
        case "browseCli":
          await this.browseCli();
          return;
        case "recheck":
          await this.runHealthCheck();
          await this.pushReadiness();
          await this.restoreAfterReady();
          return;
        case "authenticate":
          await this.authenticate();
          return;
        case "setConfig":
          await this.setConfig(msg.key, msg.value);
          return;
        case "draft":
          this.store.setDraft(typeof msg.id === "string" && msg.id ? msg.id : undefined, String(msg.text ?? ""));
          return;
        case "answerDraft":
          this.saveAnswerDraft(String(msg.requestId || ""), msg.state);
          return;
        case "finishSetup":
          this.post({ type: "ready" });
          return;
        default:
          return;
      }
    } catch (err) {
      this.log(`[error] ${err instanceof Error ? err.stack || err.message : String(err)}`);
      this.post({ type: "error", text: err instanceof Error ? err.message : String(err) });
      this.setBusy(false);
    }
  }

  private async onWebviewReady(): Promise<void> {
    this.webviewReady = true;
    this.releaseReadyWaiters();
    this.post({ type: "workspace", name: this.workspaceName() });
    // Which surface this is decides the whole chrome (an editor tab is one chat,
    // with no session list and no back button), so it is settled before anything
    // is drawn rather than waiting for the first session to load.
    this.postCapabilities();
    this.postImplicitContext();
    // A prompt left half written before the window closed is put straight back.
    this.postDraft();
    // The CLI health check spawns a login shell and calls the CLI, which can
    // take several seconds. Paint the chat shell immediately using the last
    // known readiness so the sidebar is never blank while it runs; the check
    // below then reconciles (switching to setup only if the CLI is missing or
    // signed out).
    if (this.context.globalState.get<boolean>(ChatController.READY_HINT, false)) {
      this.post({ type: "ready" });
      void this.publishInitialOptions();
    }
    await this.runHealthCheck();
    if (this.closed) {
      return;
    }
    await this.pushReadiness();
    await this.restoreAfterReady();
  }

  private async restoreAfterReady(): Promise<void> {
    if (this.closed || !this.isReady() || this.activeId) {
      return;
    }
    // A freshly opened editor/window surface starts a new session immediately
    // instead of landing on the session list, and one restored after a reload
    // reopens the chat it was holding.
    if (this.autoNewSession) {
      this.autoNewSession = false;
      this.post({ type: "body", body: "thread" });
      await this.newSession();
      return;
    }
    if (this.openOnReady) {
      const id = this.openOnReady;
      this.openOnReady = undefined;
      await this.openSession(id);
      return;
    }
    // Auto-resume only applies to the sidebar surface. Editor/window surfaces
    // that were freshly opened start a new session instead (see onWebviewReady).
    // A window reload builds a brand new webview with an empty transcript, so
    // the session the panel was showing is reopened: without that, the chat you
    // were reading comes back blank until you go to the list and pick it again.
    const viewing = this.store.viewing();
    const last = viewing || this.store.activeId();
    // Always reopen a session whose turn we had to kill on the way out, even
    // when auto-resume is off: landing on the session list with no word of the
    // interruption is the one case where it is genuinely confusing.
    const wasInterrupted = !!last && this.store.interrupted().includes(last);
    const resume = !!viewing || wasInterrupted || this.cfg().get<boolean>("autoResumeLast", false);
    // Another surface may already be running it (an editor tab that outlived a
    // reload), in which case it belongs there and this panel stays on the list.
    const heldElsewhere = !!last && !!this.surfaces?.owner(last, this);
    if (this.kind === "view" && resume && last && !heldElsewhere && !this.painted) {
      this.post({ type: "body", body: "thread" });
      await this.loadSession(last);
    }
  }

  private static readonly READY_HINT = "vtx.readyHint.v1";

  async runSetup(): Promise<void> {
    this.focus();
    await this.runHealthCheck();
    this.post({ type: "setup", health: this.publicHealth() });
  }

  // Decides whether the webview shows the setup panel or the chat.
  private async pushReadiness(): Promise<void> {
    // Remember readiness so the next launch can paint the chat shell instantly.
    void this.context.globalState.update(ChatController.READY_HINT, this.isReady());
    if (this.isReady()) {
      this.post({ type: "ready" });
      void this.publishInitialOptions();
      void this.refreshSessionsIfVisible();
    } else if (this.runtimes.size === 0) {
      this.post({ type: "setup", health: this.publicHealth() });
    }
  }

  // --- Config + workspace helpers -----------------------------------------

  private cfg(): vscode.WorkspaceConfiguration {
    return vscode.workspace.getConfiguration("vtx");
  }

  private folders(): string[] {
    return (vscode.workspace.workspaceFolders || []).map((f) => f.uri.fsPath);
  }

  private cwd(): string {
    // Never process.cwd(): for the extension host that is the VS Code install
    // directory, which nothing may write to.
    return this.folders()[0] || os.homedir();
  }

  // A multi-root workspace has no single root, so a new session belongs to the
  // folder the user is actually working in: the active editor's workspace
  // folder, falling back to the first folder (then HOME).
  private resolveNewSessionCwd(): string {
    const active = vscode.window.activeTextEditor?.document.uri;
    if (active) {
      const folder = vscode.workspace.getWorkspaceFolder(active);
      if (folder) {
        return folder.uri.fsPath;
      }
    }
    return this.cwd();
  }

  // All workspace folders except the session's own cwd, passed as extra
  // context so the agent can still reach the rest of the workspace.
  private additionalDirs(cwd: string): string[] {
    return this.folders().filter((f) => f !== cwd);
  }

  private workspaceName(): string {
    if (vscode.workspace.workspaceFile) {
      return path.basename(vscode.workspace.workspaceFile.fsPath).replace(/\.code-workspace$/, "");
    }
    return vscode.workspace.workspaceFolders?.[0]?.name || "no folder open";
  }

  // --- CLI health + setup --------------------------------------------------

  private isReady(): boolean {
    return !!this.health?.found && this.health?.loggedIn !== false;
  }

  private publicHealth() {
    return {
      found: !!this.health?.found,
      loggedIn: this.health?.loggedIn,
      version: this.health?.version,
      path: this.health?.path,
      error: this.health?.error
    };
  }

  private async runHealthCheck(): Promise<void> {
    const startedAt = Date.now();
    const setting = this.cfg().get<string>("cliPath", "vtx") || "vtx";
    this.health = await checkHealth(setting);
    this.resolvedCli = this.health.path || "vtx";
    this.env = await loginShellEnv();
    this.log(
      `[health] path=${this.health.path} found=${this.health.found} loggedIn=${this.health.loggedIn} version=${this.health.version || ""} ${this.health.error || ""}`
    );
    this.statusBar?.setInfo({ version: this.health.version, account: this.health.account });
    this.statusBar?.set({ connected: this.isReady(), mode: this.currentMode, model: this.currentModel });
    this.log(`[perf] health-check ${Date.now() - startedAt}ms`);
  }

  private async browseCli(): Promise<void> {
    const picked = await vscode.window.showOpenDialog({
      canSelectFiles: true,
      canSelectFolders: false,
      canSelectMany: false,
      openLabel: "Select the vtx executable"
    });
    if (!picked || !picked.length) {
      return;
    }
    await this.cfg().update("cliPath", picked[0].fsPath, vscode.ConfigurationTarget.Global);
    await this.runHealthCheck();
    await this.pushReadiness();
    await this.restoreAfterReady();
  }

  private async authenticate(): Promise<void> {
    const bin = this.resolvedCli || "vtx";
    const term = vscode.window.createTerminal({ name: "Vtx Login", env: this.env });
    term.show(true);
    term.sendText(`${quote(bin)} auth login`);
  }

  // Persist a UI preference the webview toggled (e.g. a "don't ask again"
  // checkbox). Allowlisted so the webview cannot write arbitrary settings.
  private static readonly WRITABLE_KEYS = new Set(["editing.confirmEditRequestRemoval", "sessionsPanel.side"]);
  private async setConfig(key: unknown, value: unknown): Promise<void> {
    if (typeof key !== "string" || !ChatController.WRITABLE_KEYS.has(key)) return;
    await this.cfg().update(key, value, vscode.ConfigurationTarget.Global);
  }

  // --- Session management --------------------------------------------------

  private extraArgs(): string[] {
    const v = this.cfg().get<string[]>("extraArgs", []);
    return Array.isArray(v) ? v.map(String) : [];
  }

  private clientEnv(): NodeJS.ProcessEnv {
    return { ...(this.env || process.env), ...this.configuredEnv() };
  }

  // What the user asked to be set, on top of whatever environment the thing
  // being started already has. The integrated terminal takes only this: it opens
  // its own login shell, so handing it a whole cached environment would put a
  // stale copy of that shell's own variables back over it.
  private configuredEnv(): Record<string, string> {
    const extra = this.cfg().get<Record<string, string>>("env", {}) || {};
    // `vtx acp` has no --sandbox flag: the env var is the only way in, which is
    // why the sandbox rows in the settings panel did nothing on their own. Set
    // before `vtx.env` so an explicit VTX_SANDBOX there still wins.
    const sandbox: Record<string, string> = this.cfg().get<boolean>("sandbox", false) ? { VTX_SANDBOX: "1" } : {};
    return { ...sandbox, ...extra };
  }

  private async ensureInitialized(rt: Runtime): Promise<void> {
    if (!rt.initialized) {
      await rt.client.initialize();
      rt.initialized = true;
    }
  }

  private async ensureReady(): Promise<boolean> {
    if (!this.health) {
      await this.runHealthCheck();
    }
    if (!this.isReady()) {
      this.post({ type: "setup", health: this.publicHealth() });
      return false;
    }
    return true;
  }

  // Create a brand-new session in its own `vtx acp` and make it active.
  private async createSession(): Promise<Runtime> {
    if (this.startingNew) {
      return this.startingNew;
    }
    this.startingNew = (async () => {
      const cwd = this.resolveNewSessionCwd();
      const rt = this.spawnRuntime(cwd);
      try {
        await this.ensureInitialized(rt);
        const res = await rt.client.newSession(cwd, this.additionalDirs(cwd));
        rt.id = res.sessionId;
        rt.lastActivityAt = Date.now();
        this.runtimes.set(rt.id, rt);
        this.rememberSession(rt.id, cwd);
        // Starting a chat takes seconds, and the user can open an existing one
        // while it does. It is a real session either way, so it stays in the pool
        // and runs in the background, but it must not take the panel back from
        // whatever they are looking at now.
        const claimed = this.activeId === undefined;
        if (claimed) {
          this.activeId = rt.id;
          this.currentMode = undefined;
          this.currentModel = undefined;
          this.currentThoughtLevel = undefined;
          this.markVisible(rt.id);
          this.painted = true;
          this.postCapabilities();
        } else {
          this.log(`[new-session] ${rt.id} arrived after another chat was opened, left in the background`);
        }
        this.publishOptions(rt, res.configOptions, res.modes?.currentModeId);
        await this.applyDefaults(rt, res);
        // Start it off knowing what is already open, rather than waiting for the
        // user to switch files before it has any editor context at all.
        this.sendOpenDocuments(rt);
        if (claimed) {
          // Whatever was in the "new chat" box has been carried into this session,
          // so it is no longer waiting in the list: the text, and the files staged
          // with it, belong to this chat now.
          this.store.setDraft(undefined, "");
          const carried = this.stagedFor(undefined);
          if (carried.length) {
            this.staged.set(this.stagedKey(rt.id), carried);
          }
          await this.dropStaged(undefined);
          this.stagedChanged(rt.id);
          this.post({ type: "sessionReady", sessionId: rt.id });
        }
        this.ensureIdleTimer();
        this.broadcastStatuses();
        void this.refreshSessionsIfVisible();
        return rt;
      } catch (err) {
        this.destroyRuntime(rt);
        if (rt.id) {
          this.runtimes.delete(rt.id);
        }
        throw err;
      }
    })();
    try {
      return await this.startingNew;
    } finally {
      this.startingNew = undefined;
    }
  }

  // Load a session into `rt`, taking over a lock when needed (item 5): a stale
  // lock (dead owner) is reclaimed automatically; a lock held by a live process
  // prompts the user, and force take-over removes it and loads anyway.
  private async loadWithTakeover(rt: Runtime, id: string, cwd: string): Promise<NewSessionResult | undefined> {
    if (!rt.client.supportsLoadSession()) {
      // Nothing to fall back to: without session/load an existing chat cannot be
      // reopened, and saying so beats a raw "method not found" from the agent.
      throw new Error("This Vtx CLI cannot reopen an existing chat. Start a new one instead.");
    }
    const attempt = () =>
      rt.client.loadSession(id, cwd, this.additionalDirs(cwd)) as Promise<NewSessionResult | undefined>;
    try {
      return await attempt();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (!/currently running|already running|another process|is locked|cannot be resumed/i.test(msg)) {
        throw err;
      }
      const owner = lockOwner(id);
      if (!owner.locked) {
        removeLock(id); // stale lock, dead owner: reclaim and retry
        return await attempt();
      }
      const decision = await this.askTakeover(id, owner.pid);
      if (decision !== "takeover") {
        throw new Error(`This session is open in another Vtx process (PID ${owner.pid}). Close it there, or take over.`);
      }
      removeLock(id);
      return await attempt();
    }
  }

  // Ask the webview whether to force take-over a session held by a live process.
  // `rid` is the session the question belongs to, so a chat that moves surface
  // takes only its own with it.
  private readonly takeoverResolvers = new Map<string, { resolve: (d: "takeover" | "cancel") => void; rid: string; timer?: NodeJS.Timeout }>();
  private takeoverSeq = 0;
  private askTakeover(id: string, pid?: number): Promise<"takeover" | "cancel"> {
    const requestId = `lock-${++this.takeoverSeq}`;
    this.post({ type: "lockConflict", requestId, id, pid });
    return new Promise((resolve) => {
      const entry: { resolve: (d: "takeover" | "cancel") => void; rid: string; timer?: NodeJS.Timeout } = { resolve, rid: id };
      this.takeoverResolvers.set(requestId, entry);
      // An unanswered question held the load open for ever, and a load that never
      // returns pins the session: every later attempt to open it waits on the same
      // promise, its row keeps a starting dot, and everything sent to it is queued
      // behind a replay that will not finish. Left long enough to be answered, but
      // not left for the life of the window.
      entry.timer = setTimeout(() => {
        if (!this.takeoverResolvers.has(requestId)) {
          return;
        }
        this.log(`[takeover] no answer for ${id}, treating it as cancelled`);
        this.resolveTakeover(requestId, "cancel");
      }, TAKEOVER_TIMEOUT_MS);
      entry.timer.unref?.();
    });
  }
  private resolveTakeover(requestId: string, decision: string): void {
    const entry = this.takeoverResolvers.get(requestId);
    if (!entry) {
      return;
    }
    this.takeoverResolvers.delete(requestId);
    if (entry.timer) {
      clearTimeout(entry.timer);
    }
    entry.resolve(decision === "takeover" ? "takeover" : "cancel");
  }

  // Borrow an initialized client for a session-agnostic call (rename/delete),
  // preferring a live runtime and otherwise spawning a short-lived one.
  private async withClient<T>(fn: (client: AcpClient) => Promise<T>): Promise<T> {
    for (const rt of this.runtimes.values()) {
      if (rt.initialized) {
        return fn(rt.client);
      }
    }
    const rt = this.spawnRuntime(this.cwd());
    try {
      await this.ensureInitialized(rt);
      return await fn(rt.client);
    } finally {
      this.destroyRuntime(rt);
    }
  }

  // Leaving the active session for the sessions list. The session keeps running
  // in the background (its runtime stays alive, shown green/amber in the list).
  // Detaching `activeId` is what makes "background" real: a turn still in flight
  // stops streaming into the now-hidden thread, and its completion/error is not
  // posted to the list view. Returning to the session re-attaches (activate).
  private leaveToList(): void {
    this.activeId = undefined;
    if (this.kind === "view") {
      this.store.setViewing(undefined);
    }
    // The composer is a "new chat" box again, so it gets that box's own draft and
    // staged files back, rather than being emptied.
    void this.loadStaged(undefined);
    this.postDraft();
  }

  // Terminate a session's live process (kill its acp + terminals), freeing its
  // lock and turning its dot gray, after a confirmation prompt. The conversation
  // is preserved and can be woken again later. Terminating the open session (or
  // any terminate that asks) returns to the sessions list.
  private async terminateSession(id: string, title?: string, returnToList?: boolean): Promise<void> {
    const rt = this.runtimes.get(id);
    if (!rt) {
      return;
    }
    const choice = await vscode.window.showWarningMessage(
      `Terminate the session "${title || id}"? This stops its running process. The conversation is kept and can be resumed later.`,
      { modal: true },
      "Terminate"
    );
    if (choice !== "Terminate") {
      return;
    }
    // The runtime may have changed while the modal was open; re-read it.
    const live = this.runtimes.get(id);
    if (!live) {
      return;
    }
    this.settleRequestsFor(id);
    this.keepQueued(live);
    this.destroyRuntime(live);
    this.runtimes.delete(id);
    this.starting.delete(id);
    const wasActive = this.activeId === id;
    if (wasActive) {
      this.setBusy(false);
    }
    this.broadcastStatuses();
    // Terminating the open session returns to the list; otherwise just refresh.
    if (returnToList || wasActive) {
      await this.closeOutSession();
    } else {
      void this.refreshSessionsIfVisible();
    }
  }

  // Nothing left to show for the chat this surface was on. The side panel falls
  // back to its session list. An editor tab is one chat, so it stays on it: the
  // transcript remains, a send wakes the agent again, and a chat that could not
  // be opened at all says so in place of the transcript.
  private async closeOutSession(reason?: string): Promise<void> {
    if (this.kind === "view") {
      await this.showSessionsView();
      return;
    }
    if (reason) {
      this.post({ type: "loaded" });
      this.post({ type: "error", text: reason });
    }
  }

  // Start a new session in a chosen location. "view" is this surface; the
  // others run the corresponding registered command (handled by ChatManager).
  private async newSessionAt(target: string): Promise<void> {
    switch (target) {
      case "editor":
        await vscode.commands.executeCommand("vtx.newSessionEditor");
        return;
      case "window":
        await vscode.commands.executeCommand("vtx.newSessionWindow");
        return;
      case "terminal":
        await vscode.commands.executeCommand("vtx.newSessionTerminal");
        return;
      default:
        await this.newSession();
        return;
    }
  }

  async newSession(): Promise<void> {
    if (!(await this.ensureReady())) {
      return;
    }
    // The previous session (if any) is left alive in the background and keeps its
    // own edits tracked; the clear below starts this chat with an empty one. What
    // was staged in the "new chat" box comes with it: that is what it was for.
    this.activeId = undefined;
    await this.loadStaged(undefined);
    this.focus();
    this.post({ type: "body", body: "thread" });
    // `reset` tells the webview this is a fresh session so it clears the title
    // (and code badge) instead of keeping the previous session's.
    this.post({ type: "clear", reset: true });
    try {
      await this.createSession();
    } catch (err) {
      this.post({ type: "error", text: err instanceof Error ? err.message : String(err) });
    }
  }

  // Tell the thread, once, that this session's turn was killed by a window reload
  // or an extension shutdown, so an interrupted turn is visible and re-sendable
  // rather than silently missing.
  private reportInterrupted(id: string): void {
    if (!this.store.interrupted().includes(id)) {
      return;
    }
    this.store.clearInterrupted(id);
    this.post({ type: "interrupted" });
  }

  // Open a session: reuse its live runtime if it is already alive, otherwise
  // wake it (spawn a fresh acp and load its history). Either way the session is
  // alive when this returns. Item 4.
  private async loadSession(id: string): Promise<void> {
    if (!id || !(await this.ensureReady())) {
      return;
    }
    if (this.openedElsewhere(id)) {
      return;
    }
    const inflight = this.loading.get(id);
    if (inflight) {
      // A load/wake for this session is already running; make it the active one
      // and share its result rather than spawning a second acp.
      this.activeId = id;
      return inflight;
    }
    const p = this.doLoadSession(id);
    this.loading.set(id, p);
    try {
      await p;
    } finally {
      this.loading.delete(id);
    }
  }

  private async doLoadSession(id: string): Promise<void> {
    const already = this.runtimes.get(id);
    // Never reload a session whose turn is in flight: issuing a loadSession over
    // its live channel aborts the running prompt ("channel closed"). Re-attach
    // to the running runtime instead (it keeps streaming).
    if (already && already.busy) {
      await this.activateSession(id);
      return;
    }
    const startedAt = Date.now();
    this.activeId = id;
    // A full reload rebuilds the whole transcript, so any buffered background
    // stream for this runtime is superseded, as is any replay it was owed.
    if (already) {
      already.bgBuffer = [];
      already.needsReplay = false;
      // The transcript is about to be rebuilt from the agent, so the record of
      // what is painted starts again with it.
      already.log = [];
      already.replay = [];
      already.logFull = true;
      already.steps = [];
      already.stepsKnown = false;
    }
    // "Waking session…" while a fresh acp spins up; a live one loads instantly.
    this.post({ type: "clear", loading: true, waking: !already });
    // Name the session before anything keyed on it: the stored draft is only
    // accepted for the session the composer belongs to.
    this.painted = true;
    this.post({ type: "sessionReady", sessionId: id, title: this.store.titles()[id] });
    // The clear empties the working set and the composer, so re-send this
    // session's own pending edits, unsent text and staged files: a reload throws
    // none of them away.
    this.postWorkingSet();
    this.postDraft();
    const staged = this.loadStaged(id);
    if (!already) {
      this.starting.add(id);
    }
    this.broadcastStatuses();

    const cwd = this.store.cwds()[id] || this.resolveNewSessionCwd();
    const rt = already ?? this.spawnRuntime(cwd);
    if (!already) {
      rt.id = id;
      this.runtimes.set(id, rt);
    }
    rt.replaying = true;
    let loadFailed = "";
    try {
      if (!already) {
        await this.ensureInitialized(rt);
      }
      this.postCapabilities();
      const res = await this.loadWithTakeover(rt, id, cwd);
      await staged;
      rt.lastActivityAt = Date.now();
      this.rememberSession(id, cwd);
      // Loading a chat means spawning an agent and replaying its history, which
      // takes seconds, and nothing stops the user picking a different chat while
      // it runs. Everything below speaks for the panel, so it only applies if this
      // is still the chat the panel is showing.
      if (this.activeId === id) {
        this.markVisible(id);
      }
      if (res && (res.configOptions || res.modes)) {
        this.publishOptions(rt, res.configOptions, res.modes?.currentModeId);
      } else {
        void this.publishInitialOptions();
      }
      // A woken session starts knowing nothing about the editor, same as a new one.
      this.sendOpenDocuments(rt);
      if (this.activeId === id) {
        this.post({ type: "assistantEnd" });
        this.post({ type: "sessionReady", sessionId: id });
      }
      this.ensureIdleTimer();
    } catch (err) {
      // Waking failed: drop the half-spawned runtime so the row goes gray again,
      // and stop pointing `activeId` at a runtime that no longer exists.
      if (!already) {
        this.destroyRuntime(rt);
        this.runtimes.delete(id);
        if (this.activeId === id) {
          this.activeId = undefined;
        }
      }
      loadFailed = err instanceof Error ? err.message : String(err);
    } finally {
      rt.replaying = false;
      if (loadFailed) {
        rt.replay = [];
      }
      this.starting.delete(id);
      if (loadFailed) {
        // The agent aborted the load (commonly because a configured MCP server
        // failed to initialise, which it treats as fatal, or the session no
        // longer exists). Don't strand the user on a broken empty thread: return
        // to the list with a clear message. Drop the cached listing first so a
        // session that has since been deleted elsewhere cannot be served back
        // from the cache and clicked again.
        this.sessionsCache = undefined;
        this.broadcastStatuses();
        // Only if the user is still on this chat. Closing out would otherwise send
        // them back to the list from a chat that opened perfectly well.
        if (this.activeId === id) {
          await this.closeOutSession(OPEN_FAILED);
          this.reportLoadFailure(loadFailed, rt);
        } else {
          this.log(`[load-failed] ${id} (no longer on screen): ${loadFailed}`);
        }
      } else {
        const updateHead = this.activeId === id;
        if (updateHead) {
          this.flushReplay(rt);
          this.post({ type: "loaded" });
          this.reportInterrupted(id);
        } else {
          rt.replay = [];
        }
        this.broadcastStatuses();
        // Re-surface permissions/questions this session raised while it was in
        // the background, now that it is visible again.
        const opened = this.runtimes.get(id);
        if (opened && this.activeId === id) {
          for (const p of opened.pending) {
            this.post(p.payload);
          }
          // Republish the queue so any messages still waiting on this session
          // reappear after a reload, not just after an instant re-attach.
          this.postQueued(opened);
          this.postMcpProblems(opened);
        }
        // Anything typed while it was opening has been waiting for the channel.
        if (opened) {
          this.flushQueue(opened);
        }
        if (updateHead && !opened?.busy) {
          // The head read right after a reload is NOT a reliable revert target: the
          // next prompt re-expands the conversation and orphans it.
          void this.postTurnHead(false);
        }
        void this.refreshSessionsIfVisible();
      }
      this.log(`[perf] session-load ${already ? "live" : "cold"} ${Date.now() - startedAt}ms`);
    }
  }

  // Re-show an already-alive session WITHOUT reloading its history: the webview
  // has kept its rendered transcript and restores it locally, so we only need to
  // re-point the active session and refresh the composer chrome. Falls back to a
  // full wake/load if the runtime is not alive. Item: switch without reload.
  private async activateSession(id: string): Promise<void> {
    const rt = this.runtimes.get(id);
    if (!rt) {
      await this.loadSession(id);
      return;
    }
    this.activeId = id;
    this.markVisible(id);
    this.sendOpenDocuments(rt);
    this.postWorkingSet();
    // The composer belongs to this chat now, so it shows this chat's own unsent
    // text and staged files, not the ones from wherever we just were.
    await this.loadStaged(id);
    this.postDraft();
    this.currentMode = rt.mode;
    this.currentModel = rt.model;
    this.currentThoughtLevel = thoughtOption(rt.configOptions)?.currentValue as string | undefined;
    this.postCapabilities();
    this.postModelOptions(rt.model || "", rt.configOptions);
    if (rt.mode) {
      this.post({ type: "mode", mode: rt.mode });
    }
    if (rt.model) {
      this.post({ type: "model", model: rt.model });
    }
    this.post({ type: "busy", value: rt.busy });
    // Which servers failed to start belongs to the agent behind this chat, so the
    // card above the composer is re-derived for the chat now on screen rather than
    // being left saying what was wrong with the last one.
    this.postMcpProblems(rt);
    // Name the session: a surface handed a chat never called into `switchToSession`,
    // so without this its header, terminate and move controls have no session.
    this.painted = true;
    this.post({ type: "sessionReady", sessionId: id, title: this.store.titles()[id] });
    // Replay anything the turn streamed while this session was in the background
    // (including a turn still running), so the restored transcript catches up
    // instead of showing the stale state from when the user left.
    this.flushBgBuffer(rt);
    this.broadcastStatuses();
    // An instant restore does not reload, so the current head stays valid. Skip
    // the head read while a turn is in flight so it never contends with the
    // running prompt on the channel; the next completion refreshes it.
    if (!rt.busy) {
      await this.postTurnHead(true);
    }
    // Re-surface any prompts this session raised while backgrounded.
    for (const p of rt.pending) {
      this.post(p.payload);
    }
    // Restore the composer's queued-message rows for this session.
    this.postQueued(rt);
    void this.refreshSessionsIfVisible();
  }

  // A session is already running on another surface. Two panels cannot share one
  // agent, so the chat opens as a placeholder saying where it is, with a button
  // that brings it here. Returns true when this surface should not open it itself.
  private openedElsewhere(id: string): boolean {
    const owner = this.surfaces?.owner(id, this);
    if (!owner) {
      return false;
    }
    this.post({
      type: "elsewhere",
      id,
      where: this.surfaces!.label(owner),
      here: this.kind === "view" ? "the side panel" : "this tab",
      title: this.store.titles()[id]
    });
    void this.refreshSessionsIfVisible();
    return true;
  }

  // Bring the surface running a chat to the front, for the placeholder's second
  // action ("show it where it is").
  private revealOwner(id: string): void {
    const owner = id ? this.surfaces?.owner(id, this) : undefined;
    if (owner) {
      this.surfaces!.reveal(owner);
    }
  }

  // Seamlessly bring a dead (terminated / idle-exited) session back to life when
  // the webview already shows its cached transcript: spawn a fresh acp and load
  // its history WITHOUT clearing the thread or streaming a replay ("Waking…"
  // spinner). The row shows a "starting" dot until it is interactive. A send
  // that races the wake waits on `rt.waking`.
  private async wakeSession(id: string): Promise<void> {
    if (!id || !(await this.ensureReady())) {
      return;
    }
    if (this.openedElsewhere(id)) {
      return;
    }
    if (this.runtimes.has(id)) {
      await this.activateSession(id);
      return;
    }
    const inflight = this.loading.get(id);
    if (inflight) {
      // A load/wake for this session is already spawning an acp; share it.
      this.activeId = id;
      return inflight;
    }
    const p = this.doWakeSession(id);
    this.loading.set(id, p);
    try {
      await p;
    } finally {
      this.loading.delete(id);
      // Anything typed while it was waking was queued rather than sent, because
      // the channel was busy. A load drains its queue on the way out and a wake
      // did not, so that message sat there until some later turn finished, out of
      // order, or until the user noticed and sent it by hand.
      const opened = this.runtimes.get(id);
      if (opened) {
        this.flushQueue(opened);
      }
    }
  }

  private async doWakeSession(id: string): Promise<void> {
    const startedAt = Date.now();
    this.activeId = id;
    this.postWorkingSet();
    this.postDraft();
    const staged = this.loadStaged(id);
    const cwd = this.store.cwds()[id] || this.resolveNewSessionCwd();
    const rt = this.spawnRuntime(cwd);
    rt.id = id;
    this.runtimes.set(id, rt);
    rt.replaying = true;
    rt.silentReplay = true;
    this.starting.add(id);
    this.broadcastStatuses();
    let done: () => void = () => {};
    let wakeFailed = "";
    rt.waking = new Promise<void>((resolve) => { done = resolve; });
    try {
      await this.ensureInitialized(rt);
      const res = await this.loadWithTakeover(rt, id, cwd);
      await staged;
      rt.lastActivityAt = Date.now();
      this.rememberSession(id, cwd);
      // A wake takes seconds too, so this only speaks for the panel while the panel
      // is still showing this chat, the same as loading one does. Recording it as
      // the chat being viewed regardless sent the next window reload to the chat the
      // user had left rather than the one they were reading.
      if (this.activeId === id) {
        this.markVisible(id);
      }
      if (res && (res.configOptions || res.modes)) {
        this.publishOptions(rt, res.configOptions, res.modes?.currentModeId);
      } else {
        void this.publishInitialOptions();
      }
      // A woken session starts knowing nothing about the editor, same as a new one.
      this.sendOpenDocuments(rt);
      this.postCapabilities();
      if (this.activeId === id) {
        this.post({ type: "sessionReady", sessionId: id });
        this.reportInterrupted(id);
      }
      this.ensureIdleTimer();
    } catch (err) {
      if (this.runtimes.get(id) === rt) {
        this.destroyRuntime(rt);
        this.runtimes.delete(id);
        if (this.activeId === id) {
          this.activeId = undefined;
        }
      }
      wakeFailed = err instanceof Error ? err.message : String(err);
    } finally {
      rt.replaying = false;
      rt.silentReplay = false;
      rt.waking = undefined;
      this.starting.delete(id);
      this.broadcastStatuses();
      if (wakeFailed) {
        // The agent could not reload this session: return to the list rather
        // than leaving a stale, non interactive transcript on screen. Only if the
        // user is still on it, though. A wake takes seconds, and closing out after
        // they have moved to another chat rips them out of the one they are reading
        // to report a failure in the one they left.
        this.sessionsCache = undefined;
        if (this.activeId === id) {
          await this.closeOutSession(OPEN_FAILED);
          this.reportLoadFailure(wakeFailed, rt);
        } else {
          this.log(`[wake-failed] ${id} (no longer on screen): ${wakeFailed}`);
        }
      }
      const updateHead = !wakeFailed && this.activeId === id;
      done();
      if (updateHead) {
        void this.postTurnHead(false);
      }
      this.log(`[perf] session-wake ${Date.now() - startedAt}ms`);
    }
  }

  private sessionsCache?: { at: number; sessions: VtxSession[] };
  private sessionsRefreshing?: Promise<void>;
  private sessionsRefreshingIds?: string;
  private sessionsRefreshingGeneration?: number;
  private sessionsForcePending = false;
  private sessionListGeneration = 0;

  // `force` bypasses the short TTL cache (used for explicit refresh/rename/delete);
  // implicit refreshes after a load/prompt reuse the cache to avoid respawning
  // `vtx list` repeatedly.
  // `staleOk` serves the cache at any age (used when returning to the list, so it
  // paints instantly) and is paired with a background revalidate.
  // List through a live agent when there is one, which costs no process at all,
  // rather than spawning `vtx list` once per directory. Returns undefined when
  // nothing is live, leaving the CLI path to answer a cold panel (measured at
  // roughly the same cost as spawning an agent purely to ask, so there is nothing
  // to gain by starting one).
  private listOverProtocol(): Promise<ListResult> | undefined {
    const client = this.anyLiveClient() || this.surfaces?.sessionListClient(this);
    if (!client) {
      return undefined;
    }
    const ids = this.store.ids();
    return client
      .listSessions(SESSION_LIST_ACP_TIMEOUT_MS)
      .then((rows) => {
        // No rows at all, from an agent that has a session of its own to report, is
        // not an answer about the user's sessions: it is the call not working (a
        // renamed field after a CLI upgrade, a reset store). Fall back rather than
        // paint an empty list.
        if (!rows.length && ids.length) {
          throw new Error("session/list answered with no sessions");
        }
        return fromAcpRows(rows, ids);
      })
      .catch((err) => {
        // An agent that cannot answer must not empty the list: fall back for this
        // refresh and let the next one try again.
        this.log(`[list-over-acp-failed] ${err instanceof Error ? err.message : String(err)}`);
        return listSessions({
          cliPath: this.resolvedCli || "vtx",
          env: this.clientEnv(),
          timeoutMs: SESSION_LIST_CLI_TIMEOUT_MS,
          folders: this.folders(),
          trackedIds: ids,
          cwdById: this.store.cwds()
        });
      });
  }

  // Any agent that has finished initialize, since `session/list` belongs to the
  // connection rather than to a session.
  private anyLiveClient(): AcpClient | undefined {
    for (const rt of this.runtimes.values()) {
      if (rt.initialized && !rt.client.hasExited()) {
        return rt.client;
      }
    }
    return undefined;
  }

  async refreshSessions(force = false, staleOk = false): Promise<void> {
    const inflight = this.sessionsRefreshing;
    if (inflight) {
      if (!force) {
        return inflight;
      }
      if (
        this.sessionsRefreshingIds !== this.store.ids().join("\u0000") ||
        this.sessionsRefreshingGeneration !== this.sessionListGeneration
      ) {
        this.sessionsForcePending = true;
      }
      await inflight;
      if (this.sessionsForcePending) {
        this.sessionsForcePending = false;
        await this.refreshSessions(true);
      }
      return;
    }
    const refresh = this.refreshSessionsNow(force, staleOk);
    this.sessionsRefreshing = refresh;
    this.sessionsRefreshingIds = this.store.ids().join("\u0000");
    this.sessionsRefreshingGeneration = this.sessionListGeneration;
    try {
      await refresh;
    } finally {
      if (this.sessionsRefreshing === refresh) {
        this.sessionsRefreshing = undefined;
        this.sessionsRefreshingIds = undefined;
        this.sessionsRefreshingGeneration = undefined;
      }
    }
  }

  private async refreshSessionsNow(force = false, staleOk = false): Promise<void> {
    if (!this.isReady()) {
      return;
    }
    const startedAt = Date.now();
    const folders = this.folders();
    const trackedAtStart = new Set(this.store.ids());
    let source = "cache";
    let sessions: VtxSession[] = [];
    if (!force && this.sessionsCache && (staleOk || Date.now() - this.sessionsCache.at < 4000)) {
      sessions = this.sessionsCache.sessions;
    } else {
      this.post({ type: "sessionsLoading" });
      // Never let a slow/failed `vtx list` leave the list stuck on its
      // spinner: cap the wait and fall back to the cache (or empty).
      try {
        const protocol = this.listOverProtocol();
        source = protocol ? "acp" : "cli";
        const listing = protocol || listSessions({
          cliPath: this.resolvedCli || "vtx",
          env: this.clientEnv(),
          timeoutMs: SESSION_LIST_CLI_TIMEOUT_MS,
          folders,
          trackedIds: this.store.ids(),
          cwdById: this.store.cwds()
        });
        const { sessions: live, prunedIds } = await listing;
        // Drop tracked ids Vtx no longer knows about so stale rows self-heal,
        // but NEVER prune a session we still hold a live runtime for (or are
        // starting): a freshly created session is not in `vtx list` until its
        // first turn persists, and pruning it here would make it vanish.
        for (const id of prunedIds) {
          const held = this.runtimes.has(id) || this.starting.has(id) || !!this.surfaces?.owner(id);
          if (!held) {
            this.store.remove(id);
            void this.dropStaged(id);
          }
        }
        sessions = live;
        this.sessionsCache = { at: Date.now(), sessions };
        this.store.cacheSessionList(this.sessionsCache);
      } catch (err) {
        this.log(`[list-failed] ${err instanceof Error ? err.message : String(err)}`);
        sessions = this.sessionsCache?.sessions ?? [];
      }
    }
    const trackedNow = new Set(this.store.ids());
    sessions = sessions.filter((session) => !trackedAtStart.has(session.id) || trackedNow.has(session.id));
    // List every live session even when `vtx list` has not caught up yet (a
    // brand-new session appears there only after its first turn persists), so a
    // chat started from the list shows up immediately. Sessions running on
    // another surface count: a chat started in an editor tab belongs in this list
    // too, badged as running elsewhere.
    const known = new Set(sessions.map((s) => s.id));
    const cwds = this.store.cwds();
    const titles = this.store.titles();
    const liveIds = new Set<string>([...this.starting, ...this.runtimes.keys(), ...this.elsewhere()]);
    const synthesized: VtxSession[] = [];
    for (const id of liveIds) {
      if (known.has(id)) {
        continue;
      }
      const cwd = cwds[id] || this.runtimes.get(id)?.cwd || this.cwd();
      synthesized.push({
        id,
        short_id: id,
        working_directory: cwd,
        title: titles[id],
        last_activity_at: Math.floor(Date.now() / 1000),
        tracked: true
      });
    }
    if (synthesized.length) {
      sessions = [...synthesized, ...sessions];
    }
    // Persist titles, and fill any tracked session whose title we only know
    // from the cache (e.g. its directory is not currently listed). A name we set
    // ourselves wins until the listing catches up with it, so a rename is not
    // undone a moment later by a `vtx list` taken before it landed.
    const cached = this.store.titles();
    const pinned = this.store.pinnedTitles();
    const freshTitles: Record<string, string> = {};
    for (const s of sessions) {
      if (s.title) {
        freshTitles[s.id] = s.title;
      }
      const own = pinned[s.id] || (s.title ? undefined : cached[s.id]);
      if (own) {
        s.title = own;
      }
    }
    this.store.cacheTitles(freshTitles);
    // An editor tab is named after the chat it holds, so a name the CLI has just
    // reported (or one that was changed) retitles the tab.
    this.surfaces?.titlesChanged();
    this.post({
      type: "sessions",
      sessions,
      activeId: this.activeId,
      statuses: this.allStatuses(),
      // Rows another surface is running, so they can say so before being clicked.
      elsewhere: this.elsewhere(),
      folders: folders.map((f) => ({ path: f, name: path.basename(f) }))
    });
    this.log(`[perf] session-list ${source} ${Date.now() - startedAt}ms`);
  }

  private elsewhere(): string[] {
    return this.surfaces?.elsewhere(this) || [];
  }

  private allStatuses(): Record<string, SessionStatus> {
    return { ...this.surfaces?.statuses(this), ...this.statusMap() };
  }

  // Another surface started, moved or stopped a chat: re-list, so this surface's
  // rows, dots and "running elsewhere" badges say what is actually true.
  surfacesChanged(): void {
    this.post({ type: "sessionStatuses", statuses: this.allStatuses(), activeId: this.activeId, elsewhere: this.elsewhere() });
    void this.refreshSessionsIfVisible();
  }

  // Whether a session list is on screen here (the full list, the docked panel, or
  // the switcher) and this surface is actually showing. Only then is it worth
  // re-listing on every change to the CLI's store: a list nobody can see is re-read
  // when it comes back, and every listing runs `vtx list`.
  private listVisible = false;
  private surfaceVisible = true;

  setSurfaceVisible(visible: boolean): void {
    const changed = this.surfaceVisible !== visible;
    this.surfaceVisible = visible;
    if (changed && visible) {
      const rt = this.active();
      if (rt) {
        this.sendOpenDocuments(rt);
      }
      void this.refreshSessionsIfVisible(true);
    }
  }

  private refreshSessionsIfVisible(force = false, staleOk = false): Promise<void> {
    if (this.closed || !this.listVisible || !this.surfaceVisible) {
      return Promise.resolve();
    }
    return this.refreshSessions(force, staleOk);
  }

  private invalidateSessionList(): void {
    this.sessionsCache = undefined;
    this.sessionListGeneration++;
  }

  relistIfWatched(): void {
    this.invalidateSessionList();
    void this.refreshSessionsIfVisible(true);
  }

  private statusMap(): Record<string, SessionStatus> {
    const statuses: Record<string, SessionStatus> = {};
    for (const [id, rt] of this.runtimes) {
      if (rt.awaiting > 0 && id !== this.activeId) {
        statuses[id] = "attention";
      } else {
        statuses[id] = rt.busy && rt.awaiting === 0 ? "running" : "idle";
      }
    }
    // Last, not first: a load and a wake both register the runtime before the replay
    // has finished, so reading the pool afterwards called every one of them "idle"
    // and the starting dot was never shown for either. Not over "attention", which
    // is a question waiting to be answered.
    for (const id of this.starting) {
      if (statuses[id] !== "attention") {
        statuses[id] = "starting";
      }
    }
    return statuses;
  }

  // Rename the chat this surface is showing (the editor tab's context menu, and
  // the side panel's header title).
  async renameVisibleSession(): Promise<void> {
    const id = this.activeId;
    if (id) {
      await this.renameSession(id, this.store.titles()[id]);
    }
  }

  private async renameSession(id: string, currentTitle?: string): Promise<void> {
    if (!id || !(await this.ensureReady())) {
      return;
    }
    const input = await vscode.window.showInputBox({
      title: "Rename session",
      value: currentTitle || "",
      prompt: "New session title"
    });
    const title = (input || "").trim();
    if (!title) {
      return;
    }
    try {
      // The agent holding this session has its own copy of the name, so it is the
      // one that has to be told: renaming through any other agent leaves the
      // holder with the old name, which it writes back a moment later.
      const holder = this.runtimes.has(id) ? this : this.surfaces?.owner(id, this);
      await (holder ? holder.renameOwned(id, title) : this.withClient((c) => c.renameSession(id, title)));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.log(`[rename-failed] ${message}`);
      // A rename that failed must not look like one that worked.
      void vscode.window.showErrorMessage(`Couldn't rename this session: ${message}`);
      return;
    }
    // Name it here rather than waiting for the CLI's next listing, so the header,
    // the lists and the editor tab all follow straight away.
    this.store.setTitle(id, title);
    this.invalidateSessionList();
    if (this.activeId === id) {
      this.post({ type: "sessionReady", sessionId: id, title });
    }
    this.surfaces?.titlesChanged();
    this.surfaces?.sessionsChanged(this);
    await this.refreshSessionsIfVisible(true);
  }

  // A name the agent chose itself, rather than one the user typed. Painted like a
  // rename, but nothing is sent back to the agent, since this came from it. The
  // pin `setTitle` leaves behind is wanted here too: it stops a `vtx list`
  // already in flight from putting the older name back.
  private applyAgentTitle(rt: Runtime, title: string): void {
    if (this.store.titles()[rt.id] === title) {
      return;
    }
    this.store.setTitle(rt.id, title);
    if (this.activeId === rt.id) {
      this.post({ type: "sessionReady", sessionId: rt.id, title });
    }
    this.surfaces?.titlesChanged();
    this.surfaces?.sessionsChanged(this);
    // Repaint the list from the cached listing rather than shelling out for a
    // fresh one: the only thing that changed is a name we already hold.
    void this.refreshSessionsIfVisible(false, true);
  }

  // Rename through the agent this surface holds the session in, so its own copy of
  // the name goes with it. Falls back to a throwaway agent if it has since gone.
  async renameOwned(id: string, title: string): Promise<void> {
    const rt = this.runtimes.get(id);
    await (rt?.initialized
      ? rt.client.renameSession(id, title)
      : this.withClient((c) => c.renameSession(id, title)));
  }

  private async deleteSession(id: string, title?: string): Promise<void> {
    if (!id || !(await this.ensureReady())) {
      return;
    }
    const choice = await vscode.window.showWarningMessage(
      `Delete the session "${title || id}"? This permanently removes it and cannot be undone.`,
      { modal: true },
      "Delete"
    );
    if (choice !== "Delete") {
      return;
    }
    // Kill its live runtime first (frees the lock), then delete server-side.
    const rt = this.runtimes.get(id);
    if (rt) {
      this.settleRequestsFor(id);
      this.destroyRuntime(rt);
      this.runtimes.delete(id);
      this.starting.delete(id);
    }
    try {
      await this.withClient((client) => client.deleteSession(id));
    } catch (err) {
      this.log(`[delete-failed] ${err instanceof Error ? err.message : String(err)}`);
    }
    this.store.remove(id);
    this.invalidateSessionList();
    // A deleted chat has nothing left to stage for, and what it was holding can be
    // images: kilobytes to megabytes of base64 that would otherwise sit in the
    // workspace's storage for good, since nothing else ever looks at that file again.
    void this.dropStaged(id);
    if (this.activeId === id) {
      this.activeId = undefined;
      this.post({ type: "clear" });
    }
    this.broadcastStatuses();
    await this.refreshSessionsIfVisible(true);
  }

  // --- Mode + model --------------------------------------------------------

  // What the pickers and the status bar are showing. A chat's own mode and model live
  // on its runtime, and this is only what is on screen, so every write that crossed an
  // await goes through here: a chat that is not the one being shown records its own
  // settings and may not paint them. The synchronous writers, taking a new chat and the
  // user's own pickers, set it directly, since the panel cannot have moved on under
  // them. The rule used to be written out again at each of these sites, and the one that
  // left it out flipped the pickers of the chat the user was reading to a background
  // chat's, which matters most on the mode, since it says whether permission is asked
  // for before anything runs. Answers whether the panel is really showing this chat, so
  // a caller can post the rest of what it was going to say.
  private showOptions(rt: Runtime | undefined, mode?: string, model?: string, thoughtLevel?: string): boolean {
    if (rt && this.activeId !== rt.id) {
      return false;
    }
    this.currentMode = mode || this.currentMode;
    this.currentModel = model || this.currentModel;
    // When thoughtLevel is undefined the caller did not supply one, so keep the
    // current value. An explicit empty string means "this model has no thought
    // level", which must clear the stale value from the previous model.
    this.currentThoughtLevel = thoughtLevel !== undefined ? thoughtLevel : this.currentThoughtLevel;
    this.statusBar?.set({ connected: this.isReady(), mode: this.currentMode, model: this.currentModel });
    return true;
  }

  // `rt` is the session the options came from: its mode and model are recorded on it so
  // switching back to it later restores the pickers to what that session is actually set
  // to, rather than to the last session's or to a default.
  private publishOptions(rt: Runtime | undefined, options: ConfigOption[] | undefined, currentModeId?: string): void {
    const activeOptions = options || rt?.configOptions;
    const byId = new Map((activeOptions || []).map((o) => [o.id, o]));
    const modeOpt = byId.get("mode");
    const modelOpt = byId.get("model");
    const thoughtOpt = thoughtOption(activeOptions);
    const mode = typeof modeOpt?.currentValue === "string" ? modeOpt.currentValue : currentModeId;
    const model = typeof modelOpt?.currentValue === "string" ? modelOpt.currentValue : undefined;
    // When the agent reports config options but thought_level is absent (the
    // model does not support it), pass "" so showOptions clears the stale value
    // left by a previous model. undefined means "not reported at all" (e.g. no
    // options yet), so the old value is preserved.
    const thoughtLevel = typeof thoughtOpt?.currentValue === "string"
      ? thoughtOpt.currentValue
      : activeOptions ? "" : undefined;
    if (rt) {
      rt.configOptions = activeOptions;
      rt.mode = mode || rt.mode;
      rt.model = model || rt.model;
    }
    // The agent reports the modes it actually has, with their names, descriptions
    // and icons. Remember them so the picker offers whatever this CLI supports
    // rather than a list compiled into the extension.
    const modes = modeChoices(modeOpt);
    if (modes.length) {
      this.modes = modes;
    }
    // Which models can read an image. Not every one can (Inkling cannot), and an
    // image block sent to one that cannot poisons the whole session, so this is
    // recorded whenever the agent reports it.
    for (const choice of configChoices(modelOpt)) {
      const supported = choice._meta?.["cognition.ai/supportsImages"];
      if (typeof supported === "boolean") {
        this.modelImageSupport.set(choice.value, supported);
      }
    }
    if (this.pendingModelConfig && this.pendingModelConfig.rt === rt) {
      return;
    }
    // A background session reports its own mode and model too, and they belong to it
    // alone: recorded on its runtime above, and not painted over the pickers.
    if (!this.showOptions(rt, mode || currentModeId, model, thoughtLevel)) {
      return;
    }
    this.postModelOptions(this.currentModel || "", activeOptions);
  }

  // The families the model picker offers: everything `vtx models list` returned,
  // narrowed to one provider while `ui.model_provider_filter` is set (the same
  // scope the TUI's /provider gives /model).
  private visibleModelFamilies(families: ModelFamily[]): ModelFamily[] {
    const filter = cachedProviderFilter();
    return filter ? families.filter((f) => f.provider === filter) : families;
  }

  // Posts the mode + model-family options. Model families come from
  // `vtx models list` (cached); if not fetched yet, fetch and re-post.
  private postModelOptions(currentModel: string, options = this.active()?.configOptions): void {
    const families = this.visibleModelFamilies(cachedFamilies());
    const thought = thoughtOption(options);
    const payload = {
      type: "options",
      modes: this.modes,
      currentMode: this.currentMode || DEFAULT_MODE,
      models: families,
      providers: cachedProviders(),
      currentModel,
      modelChoices: groupedModelChoices(options),
      thoughtLevels: configChoices(thought),
      currentThoughtLevel: typeof thought?.currentValue === "string" ? thought.currentValue : this.currentThoughtLevel || ""
    };
    if (families.length) {
      this.store.cacheOptions(payload);
    }
    this.post(payload);
    if (!families.length) {
      void listModelFamilies(this.resolvedCli || "vtx", this.env).then((f) => {
        if (f.length) {
          // Listing takes seconds, so re-read the mode: the visible session may
          // have changed by now and this post must not put the old one back.
          this.postModelOptions(this.active()?.model || this.currentModel || "");
        }
      });
    }
  }

  // Whether each model can read an image, as the agent reported it. A model that
  // is not in here has not said, and is given the benefit of the doubt.
  private readonly modelImageSupport = new Map<string, boolean>();

  // Can the model this chat is on read an image? `warn` tells the user why not,
  // for the paths where they just tried to attach one.
  private imagesSupported(warn = false): boolean {
    const model = this.active()?.model || this.currentModel;
    if (!model || this.modelImageSupport.get(model) !== false) {
      return true;
    }
    if (warn) {
      const name = familyOf(model)?.name || model;
      void vscode.window.showWarningMessage(
        `${name} cannot read images. Pick another model to attach one.`
      );
    }
    return false;
  }

  // The modes this CLI offers, as it reported them. Seeded with the ones every
  // supported CLI has so the picker is never empty before a session exists, then
  // replaced wholesale by the real list (which is where `smart` comes from, and
  // any mode a later CLI adds).
  private modes = FALLBACK_MODES;

  // Populate the dropdowns before any session exists so they are never empty.
  // Models come from `vtx models list` (no session needed, and the uids match
  // what the ACP model option accepts).
  private async publishInitialOptions(): Promise<void> {
    let families: ModelFamily[] = [];
    try {
      families = await listModelFamilies(this.resolvedCli || "vtx", this.env);
    } catch (err) {
      this.log(`[models-failed] ${err instanceof Error ? err.message : String(err)}`);
    }
    if (!families.length) {
      const cached = this.store.options() as { models?: ModelFamily[] } | undefined;
      families = cached?.models?.length
        ? cached.models
        : [];
    }
    // Listing the models spawns the CLI, so this often resolves after a session
    // has already been opened. Read the state only now, and let the visible
    // session's own mode and model win: posting the configured defaults over
    // them would show (say) Bypass on a session the agent is still asking
    // permission for.
    const live = this.active();
    const mode = live?.mode || this.currentMode || this.cfg().get<string>("defaultMode", DEFAULT_MODE);
    const model = live?.model || this.currentModel || this.cfg().get<string>("defaultModel", "");
    const options = live?.configOptions;
    const thought = thoughtOption(options);
    const payload = {
      type: "options",
      modes: this.modes,
      currentMode: mode || DEFAULT_MODE,
      models: this.visibleModelFamilies(families),
      currentModel: model || "",
      modelChoices: groupedModelChoices(options),
      thoughtLevels: configChoices(thought),
      currentThoughtLevel: typeof thought?.currentValue === "string" ? thought.currentValue : ""
    };
    this.store.cacheOptions(payload);
    this.post(payload);
  }

  private async applyDefaults(rt: Runtime, res: NewSessionResult): Promise<void> {
    const mode = this.cfg().get<string>("defaultMode", DEFAULT_MODE);
    const model = this.cfg().get<string>("defaultModel", "");
    const thoughtLevel = this.cfg().get<string>("defaultThoughtLevel", "");
    const currentMode = res.modes?.currentModeId;
    // Record the session's own mode first. The `current_mode_update` that
    // announces it arrives before session/new returns, so the runtime is not in
    // the pool yet to receive it, and a session that already starts in the
    // configured mode would otherwise have no mode recorded at all.
    rt.mode = currentMode || rt.mode;
    // Show it straight away, if this is the chat being shown at all.
    this.showOptions(rt, rt.mode);
    const gen = ++this.modelGen;
    try {
      if (mode && mode !== currentMode) {
        const result = await rt.client.setConfigOption(rt.id, "mode", mode);
        rt.mode = mode;
        this.publishOptions(rt, result.configOptions);
      }
      if (gen !== this.modelGen) return;
      // Only re-apply a remembered model if it's still an available model
      // (when we know the list); otherwise keep the session's own default. The
      // session accepts a narrower set than the catalog, so translate first.
      const resolved = acceptedModelUid(rt.configOptions, model);
      const modelKnown = resolved.model !== model || cachedFamilies().length === 0 || !!familyOf(model);
      if (model && modelKnown) {
        const result = await rt.client.setConfigOption(rt.id, "model", resolved.model);
        this.publishOptions(rt, result.configOptions);
        if (gen !== this.modelGen) return;
      }
      const rememberedLevel = thoughtLevel || resolved.effort;
      const thoughtOpt = thoughtOption(rt.configOptions);
      if (rememberedLevel && thoughtOpt) {
        const result = await rt.client.setConfigOption(rt.id, thoughtOpt.id, rememberedLevel);
        this.publishOptions(rt, result.configOptions);
        if (gen !== this.modelGen) return;
      }
      if (!this.showOptions(rt, rt.mode, rt.model)) {
        return;
      }
      this.post({ type: "mode", mode: this.currentMode });
      if (this.currentModel) {
        this.post({ type: "model", model: this.currentModel });
      }
    } catch (err) {
      this.log(`[apply-defaults-failed] ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  private async setMode(mode: string): Promise<void> {
    this.currentMode = mode;
    const rt = this.active();
    if (rt) {
      rt.mode = mode;
      try {
        const result = await rt.client.setConfigOption(rt.id, "mode", mode);
        this.publishOptions(rt, result.configOptions);
      } catch (err) {
        this.log(`[set-mode-failed] ${err instanceof Error ? err.message : String(err)}`);
      }
    }
    // Remember it for the next chat, after the session itself has it: a settings
    // write can fail (e.g. no workspace open) and that must never be what stops
    // the mode reaching the agent.
    try {
      await this.cfg().update("defaultMode", mode, vscode.ConfigurationTarget.Workspace);
    } catch (err) {
      this.log(`[set-mode-persist-failed] ${err instanceof Error ? err.message : String(err)}`);
    }
    this.statusBar?.set({ connected: this.isReady(), mode: this.currentMode, model: this.currentModel });
    this.post({ type: "mode", mode });
  }

  // Move a running subagent between foreground and background. The agent sends
  // no confirmation, so the webview flips its own state optimistically and we
  // only report a failure.
  private async setSubagentMode(agentId: string, background: boolean): Promise<void> {
    const rt = this.active();
    if (!agentId || !rt) {
      return;
    }
    try {
      if (background) await rt.client.subagentBackground(rt.id, agentId);
      else await rt.client.subagentForeground(rt.id, agentId);
    } catch (err) {
      this.log(`[subagent-mode-failed] ${err instanceof Error ? err.message : String(err)}`);
      this.post({ type: "subagentMode", id: agentId, background: !background });
    }
  }

  private async setSessionConfig(configId: string, value: unknown): Promise<void> {
    const rt = this.active();
    const option = rt?.configOptions?.find((item) => item.id === configId);
    if (!rt || !option || (typeof value !== "string" && typeof value !== "boolean")) {
      return;
    }
    if (option.type === "select" && (typeof value !== "string" || !configChoices(option).some((choice) => choice.value === value))) {
      return;
    }
    const thoughtId = thoughtOption(rt.configOptions)?.id;
    const isThought = configId === "thought_level" || configId === thoughtId;
    // Record the thought level immediately so a concurrent setFusionModel sees
    // the user's latest choice before its own ACP call returns.
    if (isThought && typeof value === "string") {
      this.currentThoughtLevel = value;
    }
    try {
      const result = await rt.client.setConfigOption(rt.id, configId, value);
      this.publishOptions(rt, result.configOptions);
      if (isThought && typeof value === "string") {
        await this.cfg().update("defaultThoughtLevel", value, vscode.ConfigurationTarget.Workspace);
      }
    } catch (err) {
      this.log(`[set-config-failed] ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  private queueModelConfig(model: string, thoughtLevel?: string, requestId?: number): Promise<void> {
    const request = { rt: this.active(), model, thoughtLevel, requestId };
    this.pendingModelConfig = request;
    ++this.modelGen;
    const barriers: (Promise<Runtime> | Promise<void> | undefined)[] = [
      this.startingNew,
      request.rt?.waking,
      this.activeId ? this.loading.get(this.activeId) : undefined
    ];
    const task = this.modelConfigTail.then(async () => {
      let started: Runtime | undefined;
      for (const barrier of barriers) {
        if (!barrier) continue;
        const result = await barrier.then((rt) => rt, () => undefined);
        if (result) started = result as Runtime;
      }
      if (this.pendingModelConfig !== request) return;
      const rt = request.rt || (started && this.active() === started ? started : undefined);
      request.rt = rt;
      if (this.active() !== rt) {
        this.pendingModelConfig = undefined;
        return;
      }
      const previousModel = this.currentModel;
      const previousThought = this.currentThoughtLevel;
      // The session's own option is the authority on the `model` values it
      // takes, which is narrower than the `vtx models list` catalog the
      // picker was built from. Translate before asking.
      const resolved = acceptedModelUid(rt?.configOptions, model);
      const level = thoughtLevel ?? resolved.effort;
      let failure: unknown;
      try {
        if (level === undefined) await this.setModel(resolved.model);
        else await this.setFusionModel(resolved.model, level);
      } catch (err) {
        failure = err;
      }
      if (this.pendingModelConfig !== request) return;
      this.pendingModelConfig = undefined;
      if (this.active() !== rt) return;
      const options = rt?.configOptions;
      const modelOpt = options?.find((option) => option.id === "model" || option.category === "model");
      const modeOpt = options?.find((option) => option.id === "mode" || option.category === "mode");
      const confirmedMode = typeof modeOpt?.currentValue === "string" ? modeOpt.currentValue : rt?.mode;
      const confirmedModel = typeof modelOpt?.currentValue === "string" ? modelOpt.currentValue : (failure ? previousModel : this.currentModel) || resolved.model;
      const confirmedThought = typeof thoughtOption(options)?.currentValue === "string"
        ? thoughtOption(options)?.currentValue as string
        : options ? "" : (failure ? previousThought : this.currentThoughtLevel);
      if (rt && options) {
        rt.mode = typeof modeOpt?.currentValue === "string" ? modeOpt.currentValue : rt.mode;
        rt.model = typeof modelOpt?.currentValue === "string" ? modelOpt.currentValue : rt.model;
      }
      if (failure) {
        const message = failure instanceof Error ? failure.message : String(failure);
        this.log(`[model-config-failed] model=${model} thoughtLevel=${thoughtLevel ?? ""} ${message}`);
        void vscode.window.showWarningMessage(`Could not update model configuration: ${message}`);
      }
      this.showOptions(rt, confirmedMode, confirmedModel, confirmedThought);
      this.postModelOptions(this.currentModel || "", options);
      this.post({ type: "modelConfigApplied", requestId, model: confirmedModel, thoughtLevel: confirmedThought });
    });
    this.modelConfigTail = task;
    return task;
  }

  private async setFusionModel(model: string, thoughtLevel: string): Promise<void> {
    const gen = ++this.modelGen;
    this.currentModel = model;
    this.currentThoughtLevel = thoughtLevel;
    const rt = this.active();
    if (rt) {
      try {
        // Apply model first, but do not publish options yet. The model change
        // may reset thought_level on the server, so the second call restores it.
        const confirmed = rt.configOptions?.find((option) => option.id === "model" || option.category === "model");
        let result = confirmed?.currentValue === model
          ? { configOptions: rt.configOptions }
          : await rt.client.setConfigOption(rt.id, "model", model);
        this.publishOptions(rt, result.configOptions);
        if (gen !== this.modelGen) return;
        // If the user changed the effort level while the model call was in
        // flight, use their latest choice rather than the stale captured value.
        const latestThought = this.pendingModelConfig?.thoughtLevel ?? thoughtLevel;
        const thoughtId = thoughtWireId(result.configOptions);
        if (latestThought && configChoices(thoughtOption(result.configOptions)).some((choice) => choice.value === latestThought)) {
          result = await rt.client.setConfigOption(rt.id, thoughtId, latestThought);
        }
        // Publish once with the final state after both values are applied.
        this.publishOptions(rt, result.configOptions);
        if (gen !== this.modelGen) return;
      } catch (err) {
        this.log(`[set-fusion-failed] ${err instanceof Error ? err.message : String(err)}`);
        throw err;
      }
    }
    if (gen !== this.modelGen) return;
    const appliedOptions = rt?.configOptions;
    const appliedModelOpt = appliedOptions?.find((option) => option.id === "model" || option.category === "model");
    const appliedThoughtOpt = thoughtOption(appliedOptions);
    const appliedModel = rt ? (typeof appliedModelOpt?.currentValue === "string" ? appliedModelOpt.currentValue : model) : model;
    const appliedThought = rt ? (typeof appliedThoughtOpt?.currentValue === "string" ? appliedThoughtOpt.currentValue : "") : thoughtLevel;
    try {
      await this.cfg().update("defaultModel", appliedModel, vscode.ConfigurationTarget.Workspace);
    } catch {}
    try {
      await this.cfg().update("defaultThoughtLevel", appliedThought, vscode.ConfigurationTarget.Workspace);
    } catch {}
    if (gen !== this.modelGen || (rt && this.active() !== rt)) return;
    this.statusBar?.set({ connected: this.isReady(), mode: this.currentMode, model: this.currentModel });
    this.post({ type: "model", model: appliedModel });
  }

  private async setModel(model: string): Promise<void> {
    const gen = ++this.modelGen;
    this.currentModel = model;
    const rt = this.active();
    if (rt) {
      try {
        const result = await rt.client.setConfigOption(rt.id, "model", model);
        this.publishOptions(rt, result.configOptions);
        if (gen !== this.modelGen) return;
      } catch (err) {
        this.log(`[set-model-failed] ${err instanceof Error ? err.message : String(err)}`);
        throw err;
      }
    }
    if (gen !== this.modelGen) return;
    try {
      await this.cfg().update("defaultModel", model, vscode.ConfigurationTarget.Workspace);
    } catch (err) {
      this.log(`[set-model-persist-failed] ${err instanceof Error ? err.message : String(err)}`);
    }
    if (gen !== this.modelGen || (rt && this.active() !== rt)) return;
    this.statusBar?.set({ connected: this.isReady(), mode: this.currentMode, model: this.currentModel });
    this.post({ type: "model", model });
  }

  // --- Context attachments -------------------------------------------------

  // Attachments in the shape the request itself shows them: a label, and for a
  // picture the picture, so the pill can be a thumbnail of it.
  private shownAttachments(list: Staged[]): { label: string; type: string; thumb?: string }[] {
    return list.map((a) => {
      const b = a.block as { type?: string; mimeType?: string; data?: string };
      const thumb = a.type === "image" && b?.type === "image" && b.data
        ? `data:${b.mimeType || "image/png"};base64,${b.data}`
        : undefined;
      return { label: a.label, type: a.type, thumb };
    });
  }

  // Files and images staged in the composer belong to the chat they were staged
  // for, and to a prompt that has not been sent yet, so they outlive leaving the
  // chat, moving it, and reloading the window. They live on disk rather than in
  // workspace state: an image is inlined as base64, which has no business in a
  // settings blob. A pasted screenshot has no file to point back to, so the block
  // itself is what has to be kept.
  private static readonly NEW_CHAT_ATTACHMENTS = "__new__";
  // Past this the staged files are kept for the session only: writing tens of
  // megabytes of base64 on every attach is not worth it.
  private static readonly MAX_ATTACHMENT_BYTES = 24 * 1024 * 1024;

  // Held per chat, not as one list belonging to whatever is on screen. The list used
  // to be swapped on every switch, and that swap is what made the late writes land on
  // the wrong chat: a send that had to wake a chat first read the composer again when
  // it finished, and by then it was another chat's, so its files went to an agent they
  // were never meant for and were then deleted, since saving was keyed on what was
  // being shown. Keyed by chat id, or by the "new chat" box, which is a real place of
  // its own: a screenshot can be attached from the sessions list before a chat exists.
  private readonly staged = new Map<string, Staged[]>();

  private stagedKey(id?: string): string {
    return id || ChatController.NEW_CHAT_ATTACHMENTS;
  }

  private rememberSession(id: string, cwd: string): void {
    for (const evicted of this.store.add(id, cwd)) {
      if (!this.runtimes.has(evicted) && !this.surfaces?.owner(evicted)) {
        void this.dropStaged(evicted);
      }
    }
  }

  // What is staged for one chat. The array is live: pushing to it stages a file.
  private stagedFor(id?: string): Staged[] {
    const key = this.stagedKey(id);
    const held = this.staged.get(key);
    if (held) {
      return held;
    }
    const list: Staged[] = [];
    this.staged.set(key, list);
    return list;
  }

  // Something staged for a chat changed: remember it, and show it if that chat is the
  // one on screen. One place decides what the composer shows and what is written down.
  private stagedChanged(id?: string): void {
    void this.saveStaged(id);
    if (this.stagedKey(id) === this.stagedKey(this.activeId)) {
      this.postStaged();
    }
  }

  // The files a message is being sent with. Taken rather than read, and taken before
  // anything is awaited: a second message written while this one is still waiting for
  // its chat to open would otherwise find the same files still staged and carry them
  // too, so one image went to the agent twice.
  private takeStaged(id?: string): Staged[] {
    const held = this.stagedFor(id);
    this.staged.set(this.stagedKey(id), []);
    if (held.length) {
      this.stagedChanged(id);
    }
    return held;
  }

  // Put them back, for a send that could not go through: the text is handed back to
  // the composer the same way, and the two belong together.
  private restoreStaged(id: string | undefined, items: Staged[]): void {
    if (items.length) {
      this.staged.set(this.stagedKey(id), items);
      this.stagedChanged(id);
    }
  }

  private postStaged(): void {
    this.post({
      type: "attachments",
      // Not `stagedFor`, which would insert an entry: a read must not create one, or a
      // chat handed to another surface gets an empty entry here that then shadows the
      // files still on disk, and they are never read back.
      items: (this.staged.get(this.stagedKey(this.activeId)) || []).map((a) => {
        const item: { id: string; label: string; type: string; thumb?: string } = { id: a.id, label: a.label, type: a.type };
        const b = a.block as { type?: string; mimeType?: string; data?: string };
        if (a.type === "image" && b && b.type === "image" && b.data) {
          item.thumb = `data:${b.mimeType || "image/png"};base64,${b.data}`;
        }
        return item;
      })
    });
  }

  private attachmentsFile(id?: string): vscode.Uri | undefined {
    const root = this.context.storageUri || this.context.globalStorageUri;
    const key = this.stagedKey(id).replace(/[^A-Za-z0-9._-]/g, "_");
    return root ? vscode.Uri.joinPath(root, "attachments", `${key}.json`) : undefined;
  }

  private async saveStaged(id?: string): Promise<void> {
    const file = this.attachmentsFile(id);
    if (!file) {
      return;
    }
    const items = this.stagedFor(id);
    try {
      if (!items.length) {
        await vscode.workspace.fs.delete(file);
        return;
      }
      const body = Buffer.from(JSON.stringify(items), "utf8");
      if (body.byteLength > ChatController.MAX_ATTACHMENT_BYTES) {
        this.log(`[attachments] ${body.byteLength} bytes staged, too much to keep past this window`);
        // And the older, smaller set that is still on disk is not the answer either: it
        // would come back after a reload as though it were what the composer holds.
        await vscode.workspace.fs.delete(file).then(undefined, () => {});
        return;
      }
      await vscode.workspace.fs.createDirectory(vscode.Uri.joinPath(file, ".."));
      await vscode.workspace.fs.writeFile(file, body);
    } catch {
      // Nothing staged to delete, or the storage is not writable: the composer is
      // still correct, it just will not survive a reload.
    }
  }

  // Forget what was staged for a chat, here and on disk: a chat that has been deleted, or
  // the "new chat" box once what was in it has been carried into a real chat.
  private async dropStaged(id?: string): Promise<void> {
    this.staged.delete(this.stagedKey(id));
    const file = this.attachmentsFile(id);
    if (file) {
      await vscode.workspace.fs.delete(file).then(undefined, () => {});
    }
  }

  // Read back what was staged for a chat before the window was reloaded. Only for a
  // chat this window has not staged anything for yet: what is held here is never
  // older than the file, and reading over it would undo an attach.
  private async loadStaged(id?: string): Promise<void> {
    const file = this.attachmentsFile(id);
    if (file && !this.staged.has(this.stagedKey(id))) {
      const list = this.stagedFor(id);
      try {
        const raw = Buffer.from(await vscode.workspace.fs.readFile(file)).toString("utf8");
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) {
          list.push(...parsed.filter((a) => a && a.id && a.block));
        }
      } catch {
        // Nothing staged for this chat.
      }
    }
    this.postStaged();
  }

  private removeAttachment(id: string): void {
    const list = this.stagedFor(this.activeId);
    const at = list.findIndex((a) => a.id === id);
    if (at !== -1) {
      list.splice(at, 1);
      this.stagedChanged(this.activeId);
    }
  }

  private async addContext(): Promise<void> {
    const uris = await vscode.workspace.findFiles(
      "**/*",
      "**/{node_modules,.git,dist,out,build,.venv,__pycache__,target}/**",
      1000
    );
    // findFiles never returns directories, so derive the folders under each
    // workspace root from the files, letting a folder be picked by name the way
    // VS Code's "Add Folder to Chat" does.
    const roots = (vscode.workspace.workspaceFolders || []).map((f) => f.uri.fsPath);
    const dirs = new Set<string>();
    for (const u of uris) {
      let d = path.dirname(u.fsPath);
      while (d && roots.some((r) => d !== r && d.startsWith(r + path.sep))) {
        dirs.add(d);
        d = path.dirname(d);
      }
    }
    const picks: (vscode.QuickPickItem & { id: string })[] = [
      { label: "$(list-selection) Current selection or file", id: "__sel__" },
      { label: "$(file) Browse files...", id: "__browse__" },
      { label: "$(folder-opened) Browse folders...", id: "__browseDir__" },
      ...[...dirs].sort().slice(0, 300).map((d) => ({
        label: "$(folder) " + vscode.workspace.asRelativePath(d) + "/",
        id: d
      })),
      ...uris.map((u) => ({ label: "$(file) " + vscode.workspace.asRelativePath(u), id: u.fsPath }))
    ];
    const chosen = await vscode.window.showQuickPick(picks, {
      placeHolder: "Add context for Vtx",
      matchOnDescription: true
    });
    if (!chosen) {
      return;
    }
    if (chosen.id === "__sel__") {
      await this.addSelection();
      return;
    }
    if (chosen.id === "__browse__" || chosen.id === "__browseDir__") {
      const dirs = chosen.id === "__browseDir__";
      const picked = await vscode.window.showOpenDialog({
        canSelectMany: true,
        canSelectFiles: !dirs,
        canSelectFolders: dirs,
        openLabel: "Add"
      });
      for (const p of picked || []) {
        await this.addFile(p.fsPath);
      }
      return;
    }
    await this.addFile(chosen.id);
  }

  private async openFile(fsPath: string, line?: number): Promise<void> {
    if (!fsPath) {
      return;
    }
    fsPath = fileFromUri(fsPath);
    if (!path.isAbsolute(fsPath)) {
      fsPath = path.join(this.active()?.cwd || this.cwd(), fsPath);
    }
    const uri = vscode.Uri.file(fsPath);
    try {
      const doc = await vscode.workspace.openTextDocument(uri);
      const options: vscode.TextDocumentShowOptions = {};
      if (typeof line === "number" && line > 0) {
        const pos = new vscode.Position(line - 1, 0);
        options.selection = new vscode.Range(pos, pos);
      }
      await vscode.window.showTextDocument(doc, options);
    } catch (err) {
      // Not openable as text (an image, a PDF, a binary): let VS Code pick the
      // editor for it rather than refusing to open a file the user clicked.
      try {
        await vscode.commands.executeCommand("vscode.open", uri);
      } catch {
        this.log(`[open-file-failed] ${err instanceof Error ? err.message : String(err)}`);
      }
    }
  }

  private async insertAtCursor(text: string): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      void vscode.window.showInformationMessage("Open a file to insert this code into.");
      return;
    }
    await editor.edit((b) => b.insert(editor.selection.active, text));
  }

  private async applyToFile(text: string): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      void vscode.window.showInformationMessage("Open a file to apply this code to.");
      return;
    }
    const doc = editor.document;
    await editor.edit((b) => {
      if (!editor.selection.isEmpty) {
        b.replace(editor.selection, text);
      } else {
        const full = new vscode.Range(doc.positionAt(0), doc.positionAt(doc.getText().length));
        b.replace(full, text);
      }
    });
  }

  private async openAllDiffs(): Promise<void> {
    await this.changes.openAll();
  }

  // Insert the command into a terminal without auto-running it, so the user
  // reviews it before pressing Enter.
  private runInTerminal(text: string): void {
    const existing = vscode.window.terminals.find((t) => t.name === "Vtx");
    const term = existing || vscode.window.createTerminal({ name: "Vtx", env: this.env });
    term.show(true);
    term.sendText(text, false);
  }

  // The path or, from a drop, the file URI that names it.
  private async addFile(dropped: string): Promise<void> {
    const fsPath = fileFromUri(dropped);
    const ext = path.extname(fsPath).toLowerCase().replace(/^\./, "");
    const imageMime = IMAGE_MIME_BY_EXT[ext];
    try {
      // A dropped folder attaches as a listing rather than failing to be read.
      const stat = await fs.promises.stat(fsPath);
      if (stat.isDirectory()) {
        await this.addDirectory(fsPath);
        return;
      }
      // An image file (dropped or @-mentioned) is attached inline as base64 so
      // the model can actually see it, rather than as unreadable text.
      if (imageMime) {
        if (stat.size > MAX_IMAGE_BYTES) {
          void vscode.window.showWarningMessage(`${path.basename(fsPath)} is too large to attach (over 30 MB).`);
          return;
        }
        if (!this.imagesSupported(true)) {
          return;
        }
        const buf = await fs.promises.readFile(fsPath);
        this.stagedFor(this.activeId).push({
          id: `att-${++this.attachSeq}`,
          label: path.basename(fsPath),
          type: "image",
          block: { type: "image", mimeType: imageMime, data: buf.toString("base64") }
        });
        this.stagedChanged(this.activeId);
        return;
      }
      const raw = await fs.promises.readFile(fsPath, "utf8");
      const text = raw.length > MAX_ATTACH_CHARS ? raw.slice(0, MAX_ATTACH_CHARS) : raw;
      this.stagedFor(this.activeId).push({
        id: `att-${++this.attachSeq}`,
        label: path.basename(fsPath),
        type: "file",
        block: {
          type: "resource",
          resource: { uri: vscode.Uri.file(fsPath).toString(), text }
        }
      });
      this.stagedChanged(this.activeId);
    } catch (err) {
      this.log(`[attach-file-failed] ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  // A dropped folder attaches as a listing of what it contains, so the agent
  // knows the shape of it and can read whichever files it needs.
  private async addDirectory(dirPath: string): Promise<void> {
    const entries = await fs.promises.readdir(dirPath, { withFileTypes: true });
    const lines = entries
      .filter((e) => !e.name.startsWith("."))
      .slice(0, 500)
      .map((e) => (e.isDirectory() ? `${e.name}/` : e.name))
      .sort();
    const label = path.basename(dirPath) || dirPath;
    this.stagedFor(this.activeId).push({
      id: `att-${++this.attachSeq}`,
      label,
      type: "directory",
      block: { type: "text", text: `Folder ${dirPath} contains:\n\n${lines.join("\n")}` }
    });
    this.stagedChanged(this.activeId);
  }

  // A folder dropped from outside VS Code. An OS drag carries no filesystem path,
  // so the webview reads the folder's top level and sends the names instead. The
  // block has to say so: worded like the path based listing above, a bare folder
  // name reads as a path relative to the agent's cwd, and it goes hunting for a
  // folder that is not there. The listing doubles as the fingerprint for finding
  // the real one.
  private attachDroppedFolder(name: unknown, entries: unknown): void {
    const label = typeof name === "string" && name ? name : "folder";
    const lines = (Array.isArray(entries) ? entries : []).filter((e): e is string => typeof e === "string");
    const text = [
      `A folder named "${label}" was dropped into the chat from outside VS Code, so no filesystem`,
      "path came with it and its location is unknown (it may or may not be inside the workspace).",
      "Its top level, which can be used to identify it on disk, is:",
      "",
      lines.join("\n"),
      "",
      `To read inside it, find the folder that matches this listing (or ask which "${label}" is meant)`,
      "rather than assuming a path."
    ].join("\n");
    this.stagedFor(this.activeId).push({
      id: `att-${++this.attachSeq}`,
      label,
      type: "directory",
      block: { type: "text", text }
    });
    this.stagedChanged(this.activeId);
  }

  // Attach the raw content of a file dropped from outside VS Code (an OS drag
  // gives us bytes but no path). Images arrive via attachImage instead. Same
  // caveat as a dropped folder: a bare name in the place a path usually goes
  // invites the agent to treat a same named workspace file as this one, so the
  // block says where it came from, and says when it was cut short.
  private attachDroppedText(name: unknown, text: unknown): void {
    if (typeof text !== "string" || !text.trim()) {
      return;
    }
    const label = typeof name === "string" && name ? name : "file";
    const body = text.slice(0, MAX_ATTACH_CHARS);
    const truncated = text.length > MAX_ATTACH_CHARS;
    const blockText = [
      `A file named "${label}" was dropped into the chat from outside VS Code, so no filesystem`,
      "path came with it and its location is unknown (it may or may not be inside the workspace).",
      truncated
        ? `Its first ${MAX_ATTACH_CHARS} characters are below, so treat these as the file rather than a`
        : "Its contents are below, so treat these as the file rather than a",
      "same named one in the workspace:",
      "",
      body
    ].join("\n");
    this.stagedFor(this.activeId).push({
      id: `att-${++this.attachSeq}`,
      label,
      type: "file",
      block: { type: "text", text: blockText }
    });
    this.stagedChanged(this.activeId);
  }

  // Reports whether there was anything to attach, so a caller that follows it with
  // a question does not ask one about nothing.
  private async addSelection(): Promise<boolean> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      return false;
    }
    const doc = editor.document;
    const sel = editor.selection;
    const hasSel = sel && !sel.isEmpty;
    const body = hasSel ? doc.getText(sel) : doc.getText();
    if (!body.trim()) {
      return false;
    }
    const rel = vscode.workspace.asRelativePath(doc.uri);
    const label = hasSel
      ? `${path.basename(doc.uri.fsPath)}:${sel.start.line + 1}-${sel.end.line + 1}`
      : path.basename(doc.uri.fsPath);
    const text = `From ${rel}${hasSel ? ` lines ${sel.start.line + 1}-${sel.end.line + 1}` : ""}:\n\n\`\`\`${doc.languageId}\n${body.slice(0, MAX_ATTACH_CHARS)}\n\`\`\``;
    this.stagedFor(this.activeId).push({
      id: `att-${++this.attachSeq}`,
      label,
      type: "selection",
      block: { type: "text", text }
    });
    this.stagedChanged(this.activeId);
    return true;
  }

  // --- Entry points from the editor ----------------------------------------
  // Attach what the user is pointing at and put a starting question in the
  // composer, without sending it. Same convention as "Run in terminal" on a code
  // block: the extension sets things up, the user decides to go ahead.

  // Reachable from the command palette with no editor in front, where there is no
  // code to explain and a question on its own would be nonsense.
  async explainSelection(): Promise<void> {
    if (await this.addSelection()) {
      this.prefill("Explain this code.");
    }
  }

  // The problems the editor reports where the cursor is, handed over explicitly.
  // The agent can pull diagnostics itself, but that is the whole workspace: this
  // is the user pointing at the ones they care about.
  async fixProblemsHere(): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      return;
    }
    const sel = editor.selection;
    const all = vscode.languages.getDiagnostics(editor.document.uri);
    // With a selection, the problems it covers. With just a cursor, the whole file,
    // since one line rarely holds the problem the user means.
    const here = sel.isEmpty ? all : all.filter((d) => !!d.range.intersection(sel));
    const rel = vscode.workspace.asRelativePath(editor.document.uri);
    if (!here.length) {
      void vscode.window.showInformationMessage(`No problems reported in ${rel}.`);
      return;
    }
    const lines = here
      .slice(0, MAX_ATTACHED_PROBLEMS)
      .map((d) => `- line ${d.range.start.line + 1}: ${severityLabel(d.severity)} ${d.message}${d.source ? ` (${d.source})` : ""}`);
    this.stagedFor(this.activeId).push({
      id: `att-${++this.attachSeq}`,
      label: `${path.basename(editor.document.uri.fsPath)}: ${here.length} problem${here.length === 1 ? "" : "s"}`,
      type: "selection",
      block: { type: "text", text: `Problems the editor reports in ${rel}:\n\n${lines.join("\n")}` }
    });
    await this.addSelection();
    this.prefill(here.length === 1 ? "Fix this problem." : "Fix these problems.");
  }

  async attachUri(uri: vscode.Uri): Promise<void> {
    await this.addFile(uri.fsPath);
  }

  // Put text in the composer, focused and ready to send. Whatever the user has
  // already typed wins: this never overwrites their own words.
  private prefill(text: string): void {
    this.store.setDraft(this.activeId, text);
    this.postDraft();
    this.post({ type: "focusInput" });
  }

  private fileCache?: { at: number; uris: vscode.Uri[] };

  private async queryFiles(query: string): Promise<void> {
    const now = Date.now();
    if (!this.fileCache || now - this.fileCache.at > 15000) {
      const uris = await vscode.workspace.findFiles(
        "**/*",
        "**/{node_modules,.git,dist,out,build,.venv,__pycache__,target}/**",
        3000
      );
      this.fileCache = { at: now, uris };
    }
    const q = query.toLowerCase();
    const files = this.fileCache.uris
      .map((u) => ({ u, rel: vscode.workspace.asRelativePath(u) }))
      .filter((x) => !q || x.rel.toLowerCase().includes(q))
      .slice(0, 20)
      .map((x) => ({ path: x.u.fsPath, label: path.basename(x.u.fsPath), detail: x.rel }));
    // Filenames are only one way to name the thing you mean. When few files match,
    // ask the language servers: "@ChatController" then finds the file that declares
    // it, which no amount of filename matching would.
    const items = files.length >= SYMBOL_FALLBACK_AT || q.length < 2
      ? files
      : [...files, ...await this.querySymbols(q, files.map((f) => f.path))];
    this.post({ type: "fileSuggestions", query, items });
  }

  // Workspace symbols matching the query, as suggestion rows naming the file that
  // holds them. Attaching one attaches its file, which is what the agent needs to
  // read it in context.
  private async querySymbols(query: string, already: string[]): Promise<{ path: string; label: string; detail: string }[]> {
    try {
      const found = await vscode.commands.executeCommand<vscode.SymbolInformation[]>(
        "vscode.executeWorkspaceSymbolProvider",
        query
      );
      const seen = new Set(already);
      const rows: { path: string; label: string; detail: string }[] = [];
      for (const s of found || []) {
        const uri = s.location?.uri;
        if (!uri || uri.scheme !== "file" || seen.has(uri.fsPath)) {
          continue;
        }
        seen.add(uri.fsPath);
        rows.push({
          path: uri.fsPath,
          label: s.name,
          detail: `${symbolKindLabel(s.kind)} in ${vscode.workspace.asRelativePath(uri)}`
        });
        if (rows.length >= 8) {
          break;
        }
      }
      return rows;
    } catch {
      // No symbol provider for this workspace, or none ready yet: files only.
      return [];
    }
  }

  private attachImage(name: unknown, mime: unknown, data: unknown): void {
    if (typeof data !== "string" || typeof mime !== "string" || !this.imagesSupported(true)) {
      return;
    }
    this.stagedFor(this.activeId).push({
      id: `att-${++this.attachSeq}`,
      label: typeof name === "string" && name ? name : "image",
      type: "image",
      block: { type: "image", mimeType: mime, data }
    });
    this.stagedChanged(this.activeId);
  }

  // --- Prompting -----------------------------------------------------------

  private async handleSend(text: string, startNew = false, preserveTranscript = false): Promise<void> {
    if (!text.trim()) {
      return;
    }
    // The text has left the composer, so it is no longer a draft. Every way out
    // of this method from here on either sends it, queues it, or hands it back:
    // clearing the draft and then returning is how a message disappears.
    const draftFor = startNew ? undefined : this.activeId;
    this.store.setDraft(draftFor, "");
    // Taken from the chat this message was written in, not from whatever is on screen
    // by the time it goes out: waking a chat and creating one both take seconds, and the
    // user can open another one while they run. Taken now rather than cleared later, so a
    // second message written in the meantime cannot pick up the same files.
    const staged = this.takeStaged(draftFor);
    const giveBack = (why?: string) => {
      this.store.setDraft(draftFor, text);
      this.restoreStaged(draftFor, staged);
      this.post({ type: "draft", id: draftFor || null, text });
      if (why) {
        this.post({ type: "error", text: why });
      }
    };
    // `/provider` with no argument is the TUI's picker command: the agent can
    // only answer it headlessly, so the picker lives here and the choice goes
    // back through the normal send path as `/provider <slug>`.
    if (/^\/provider\s*$/.test(text.trim())) {
      const slug = await this.pickProviderFilter();
      if (slug === null) {
        giveBack();
        return;
      }
      this.restoreStaged(draftFor, staged);
      await this.handleSend(slug ? `/provider ${slug}` : "/provider reset", startNew, preserveTranscript);
      return;
    }
    if (!(await this.ensureReady())) {
      giveBack();
      return;
    }
    // Everything from here on is about `target`, the chat this message was written
    // in, and never about whatever is on screen by the time it goes out. A wake
    // takes seconds, and a message written in one chat and sent to another is the
    // worst thing this method can do.
    const target = draftFor;
    // If that session is still coming back to life (a silent background wake),
    // wait for it so the prompt lands on a ready runtime.
    if (!startNew && target) {
      const waking = this.runtimes.get(target)?.waking;
      if (waking) {
        await waking;
      }
      // A session still opening has its channel busy replaying its history, and a
      // prompt sent into that is swallowed. Queue it: the load drains the queue
      // when it finishes, and it shows as pending in the meantime instead of
      // seeming to vanish.
      const rt = this.runtimes.get(target);
      if (rt && (rt.replaying || this.loading.has(target))) {
        this.enqueueMessage(rt, text, staged);
        return;
      }
    }
    // Starting a fresh chat leaves the previous session alive in the background. What was
    // staged in the sessions list was staged for THIS message, so it goes with it.
    if (startNew) {
      this.activeId = undefined;
      // `pendingSend` tells the webview not to show the welcome screen on this
      // clear, and we render the user's message right away, so a new chat
      // started from the list never flashes the welcome while the ACP session
      // spins up.
      this.post({ type: "clear", pendingSend: true });
      this.post({ type: "userMessage", text, attachments: this.shownAttachments(staged) });
    }

    let rt = startNew || !target ? undefined : this.runtimes.get(target);
    // One turn at a time within a session: a message sent while the visible
    // session is mid-turn is queued (and shown as a pending row) rather than
    // dropped, then auto-sent when the turn finishes.
    if (rt && rt.busy) {
      this.enqueueMessage(rt, text, staged);
      return;
    }
    if (!rt) {
      try {
        if (!startNew && target) {
          // The session this was written in was idle-exited: wake it, then send. The
          // wake takes seconds, so the message belongs to the session it was written
          // in, not to whichever one is on screen when the wake finishes.
          await (preserveTranscript ? this.wakeSession(target) : this.loadSession(target));
          rt = this.runtimes.get(target);
        } else {
          rt = await this.createSession();
        }
      } catch (err) {
        // Waking or creating the session failed, so there is nowhere to send it.
        giveBack(err instanceof Error ? err.message : String(err));
        return;
      }
    }
    if (!rt) {
      giveBack("Couldn't open a session to send that to. Your message is back in the box.");
      return;
    }
    // A turn can have started while the wake or the create was running: opening a
    // chat drains whatever was typed while it opened, and two sends from the new chat
    // box share one session/new and so arrive at one runtime. The busy check above
    // ran before all of that. ACP has no way to hand a prompt to a live one, so a
    // second here does not queue behind the first, it contends with it: the two
    // arrive out of order and the channel can be lost. It goes to the queue, which is
    // where a message sent during a turn goes.
    if (rt.busy) {
      this.enqueueMessage(rt, text, staged);
      return;
    }

    const sent = rt;
    // What was attached goes with the message, so the request keeps saying what it
    // was asked about instead of the context vanishing the moment it is sent.
    const attachments = this.shownAttachments(staged);
    this.record(sent, { type: "userMessage", text, attachments });
    // For a fresh chat the message was already rendered above; only echo it here
    // for a send within an existing (visible) session, and only while that session
    // is still the one on screen: a wake that finishes after the user has opened
    // another chat would otherwise paint this message into their transcript.
    if (!startNew && this.activeId === sent.id) {
      this.post({ type: "userMessage", text, attachments });
    }
    const blocks: ContentBlock[] = [...this.buildImplicitBlocks(), ...staged.map((a) => a.block), { type: "text", text }];
    await this.runPrompt(sent, blocks);
  }

  // The provider picker `/provider` opens: every configured provider plus the
  // "All providers" row that clears the filter, mirroring the TUI. Returns the
  // slug to send, "" for all providers, or null when the picker was dismissed.
  private async pickProviderFilter(): Promise<string | null> {
    if (!cachedFamilies().length && !cachedProviders().length) {
      await listModelFamilies(this.resolvedCli || "vtx", this.env);
    }
    let providers = cachedProviders();
    if (!providers.length) {
      providers = [...new Set(cachedFamilies().map((f) => f.provider).filter((p): p is string => !!p))]
        .sort()
        .map((slug) => ({ slug }));
    }
    if (!providers.length) {
      this.post({ type: "error", text: "No providers configured" });
      return null;
    }
    const items: (vscode.QuickPickItem & { slug: string })[] = [
      { label: "All providers", description: "show every provider in the model picker", slug: "" },
      ...providers.map((p: ModelProvider) => ({
        label: p.name && p.name !== p.slug ? `${p.name} (${p.slug})` : p.slug,
        description: p.slug,
        slug: p.slug
      }))
    ];
    const pick = await vscode.window.showQuickPick(items, {
      title: "Provider filter",
      placeHolder: "Scope the model picker to one provider"
    });
    return pick ? pick.slug : null;
  }

  // The model list is a cached CLI call, so a /provider turn that just changed
  // ui.model_provider_filter has to drop it before the picker is repainted.
  private async refreshModelFamilies(): Promise<void> {
    invalidateModelFamilies();
    const families = await listModelFamilies(this.resolvedCli || "vtx", this.env);
    if (!families.length) {
      return;
    }
    this.postModelOptions(this.active()?.model || this.currentModel || "");
  }

  // Run one prompt turn against a runtime and stream its outcome, rendering only
  // while that session is the visible one. On completion it flushes the next
  // queued message, so a session's queue drains itself turn by turn. Shared by a
  // live send and a queued flush.
  private async runPrompt(rt: Runtime, blocks: ContentBlock[]): Promise<void> {
    // Last stop before the wire. Attaching already refuses an image the model
    // cannot read, but the model can be changed after that (or while the message
    // sat in the queue), and an image block the model cannot decode does not fail
    // the turn, it breaks the session for good.
    blocks = this.withoutUnreadableImages(rt, blocks);
    rt.steps = [];
    rt.stepsKnown = false;
    this.setRuntimeBusy(rt, true);
    if (this.activeId === rt.id) {
      this.post({ type: "assistantStart" });
    }
    rt.turnCount++;
    const turn = rt.client.prompt(rt.id, blocks);
    rt.turn = turn;
    try {
      const result = await turn;
      // Only render the completion if this session is still the visible one.
      if (this.activeId === rt.id) {
        this.post({ type: "assistantEnd", stopReason: result.stopReason });
      }
    } catch (err) {
      if (this.activeId === rt.id) {
        this.post({ type: "error", text: err instanceof Error ? err.message : String(err) });
      }
    } finally {
      if (rt.turn === turn) {
        rt.turn = undefined;
      }
      this.endTurn(rt);
      // A /provider turn rewrites ui.model_provider_filter, which the cached
      // `vtx models list` answer knows nothing about: refetch before the next
      // options post, queued turn or not.
      const last = blocks[blocks.length - 1];
      if (last?.type === "text" && /^\/provider\b/.test(last.text.trim())) {
        void this.refreshModelFamilies();
      }
    }
  }

  // Close out a finished turn, but only for the surface that still holds the
  // session: one that moved away mid turn is finished by its new surface (see
  // `adoptTurn`), which owns the webview the result belongs to and its queue.
  private endTurn(rt: Runtime): void {
    if (this.runtimes.get(rt.id) !== rt) {
      rt.busy = false;
      return;
    }
    this.setRuntimeBusy(rt, false);
    // Send the next queued message first: the revert-head probe below is an
    // extra round trip on the same channel, and awaiting it here is what used
    // to leave a visible gap before a queued message went out.
    this.flushQueue(rt);
    void this.refreshSessionsIfVisible();
    // A chat moved here mid turn has no history on this surface, and now that the
    // channel is free it can be asked for. Only when the queue did not start
    // another turn, and it is still what the user is looking at.
    if (!rt.busy && rt.needsReplay && this.activeId === rt.id) {
      rt.needsReplay = false;
      void this.loadSession(rt.id);
      return;
    }
    // A live completion's head is on the current expansion: a reliable revert
    // target. Only read it when the session actually went idle, so it never
    // contends with a queued turn we just started.
    if (!rt.busy && this.activeId === rt.id) {
      void this.postTurnHead(true);
    }
  }

  // Take over a turn that was already running when the session arrived here, so
  // it ends on this surface: the reply settles, the working state clears, and the
  // queue drains here rather than on the surface it left.
  private adoptTurn(rt: Runtime, turn: Promise<PromptResult>): void {
    void turn
      .then(
        (result) => {
          if (this.activeId === rt.id) {
            this.post({ type: "assistantEnd", stopReason: result.stopReason });
          }
        },
        (err) => {
          if (this.activeId === rt.id) {
            this.post({ type: "error", text: err instanceof Error ? err.message : String(err) });
          }
        }
      )
      .then(() => this.endTurn(rt));
  }

  private queueSeq = 0;
  // The queued message currently being edited in the composer, if any. The queue
  // still drains past it: only that one message is held back when it reaches the
  // head, so earlier messages keep going and later ones stay behind it.
  private queueEditingId?: string;

  // Snapshot a message (implicit context + attachments + text) and add it to the
  // runtime's queue, then reflect the queue in the composer.
  // `staged` is passed rather than read from the composer: a message can be queued
  // after an await, by which time the composer belongs to whatever chat the user
  // opened in the meantime.
  private enqueueMessage(rt: Runtime, text: string, staged: Staged[], first = false): void {
    const message = { id: `q-${++this.queueSeq}`, text, implicit: this.buildImplicitBlocks(), attachments: staged };
    if (first) {
      rt.queued.unshift(message);
    } else {
      rt.queued.push(message);
    }
    this.postQueued(rt);
  }

  // Send a message into a turn that is already running. ACP has no way to hand one
  // to a live prompt (what VS Code calls steering, and what its own agents support
  // through their SDKs), so the nearest honest thing is to put it at the head of
  // the queue and end the turn: the queue drains the moment it settles, so this is
  // the next thing the agent sees, and nothing is lost that the turn had done.
  private stopAndSend(text: string): void {
    if (!text.trim()) {
      return;
    }
    const rt = this.active();
    if (!rt || !rt.busy) {
      void this.handleSend(text, false);
      return;
    }
    this.store.setDraft(this.activeId, "");
    this.enqueueMessage(rt, text, this.takeStaged(this.activeId), true);
    rt.client.cancel(rt.id);
  }

  // Send the next queued message once a runtime is free. Runs one at a time:
  // runPrompt calls back into this in its finally, so the queue drains in order.
  private flushQueue(rt: Runtime): void {
    if (rt.busy || rt.queued.length === 0) {
      return;
    }
    // A session still replaying its history has its channel busy, and a prompt
    // sent into that is swallowed: the queue is where a message typed during a
    // load waits, so it must not be drained back into the load. The load calls
    // this itself once the replay is over.
    if (rt.replaying) {
      return;
    }
    // Hold only the message being edited, and only once it reaches the head: the
    // ones before it still send, the ones after it wait behind it. Resumes when
    // the edit is committed or cancelled (see the queueEditing message).
    if (this.queueEditingId && rt.queued[0].id === this.queueEditingId) {
      return;
    }
    const next = rt.queued.shift()!;
    this.postQueued(rt);
    // Route the echo through emit so a flush that happens while the session is
    // backgrounded is buffered and replays (its user bubble shows on return),
    // instead of being dropped like the old active-only post. What was attached
    // goes with it, as it does for a message sent straight away.
    this.emit(rt, { type: "userMessage", text: next.text, attachments: this.shownAttachments(next.attachments) });
    void this.runPrompt(rt, this.messageBlocks(next));
  }

  // Start or stop editing a queued message. Its attachments are copied into the
  // composer the way its text is: there to be seen and changed while editing,
  // and still on the message, whose row goes on saying what it will send if the
  // edit is abandoned. Committing replaces them with whatever the composer ended
  // up holding (editQueued), and abandoning drops that copy.
  private setQueueEditing(id?: string): void {
    const rt = this.active();
    const was = this.queueEditingId;
    this.queueEditingId = id;
    if (was && was !== id && this.stagedFor(this.activeId).length) {
      // Abandoning an edit drops the copy of that message's files it was given.
      this.staged.set(this.stagedKey(this.activeId), []);
      this.stagedChanged(this.activeId);
    }
    const to = id && rt ? rt.queued.find((q) => q.id === id) : undefined;
    if (to) {
      this.staged.set(this.stagedKey(this.activeId), to.attachments.map((a) => ({ ...a })));
      this.stagedChanged(this.activeId);
    }
    if (rt) {
      this.flushQueue(rt);
    }
  }

  // Remove a queued message the user dropped or moved back to the composer.
  private removeQueued(id: string): void {
    const rt = this.active();
    if (!rt) {
      return;
    }
    const before = rt.queued.length;
    rt.queued = rt.queued.filter((q) => q.id !== id);
    if (rt.queued.length !== before) {
      this.postQueued(rt);
    }
  }

  // VS Code's "Send Immediately" on a pending request, which means now rather
  // than next: it moves the message to the front, cancels the request in flight
  // and lets the queue drain into the gap (chatServiceImpl's
  // sendPendingRequestImmediately). Moving it alone did nothing at all, since a
  // queue only exists while a turn is running and the drain waits for the turn.
  private sendQueuedNow(id: string): void {
    const rt = this.active();
    if (!rt) {
      return;
    }
    const at = rt.queued.findIndex((q) => q.id === id);
    if (at <= -1) {
      return;
    }
    if (at > 0) {
      rt.queued.unshift(rt.queued.splice(at, 1)[0]);
      this.postQueued(rt);
    }
    if (rt.busy) {
      // The turn ending drains the queue, and this message is now its head.
      rt.client.cancel(rt.id);
      return;
    }
    this.flushQueue(rt);
  }

  // What a queued message finally sends: the context it was queued with, then
  // whatever is attached to it, then the message itself.
  private messageBlocks(q: { text: string; implicit: ContentBlock[]; attachments: Staged[] }): ContentBlock[] {
    return [...q.implicit, ...q.attachments.map((a) => a.block), { type: "text", text: q.text }];
  }

  // Drop image blocks a model that cannot read them would choke on, and say so in
  // the chat: sending the rest of the message is far better than breaking the
  // session, but the user has to know their screenshot did not go.
  private withoutUnreadableImages(rt: Runtime, blocks: ContentBlock[]): ContentBlock[] {
    const model = rt.model || this.currentModel;
    if (!model || this.modelImageSupport.get(model) !== false || !blocks.some((b) => b.type === "image")) {
      return blocks;
    }
    const kept = blocks.filter((b) => b.type !== "image");
    const dropped = blocks.length - kept.length;
    const name = familyOf(model)?.name || model;
    this.emit(rt, {
      type: "error",
      text: `${name} cannot read images, so ${dropped === 1 ? "an image was" : `${dropped} images were`} left out of this request.`
    });
    return kept;
  }

  // Update a queued message's text in place, keeping its position in the queue
  // (editing must not move it to the end). Editing borrowed the composer, so
  // whatever is staged there is now this message's context: that is both what
  // the edit started with and anything attached while editing.
  private editQueued(id: string, text: string): void {
    const rt = this.active();
    if (!rt || !text.trim()) {
      return;
    }
    const q = rt.queued.find((x) => x.id === id);
    if (!q) {
      return;
    }
    q.text = text;
    q.attachments = this.takeStaged(this.activeId);
    this.postQueued(rt);
  }

  // Replay a backgrounded session's buffered stream into the (now visible)
  // transcript and clear it, so returning shows the progress the turn made while
  // it was not on screen.
  private record(rt: Runtime, payload: Record<string, unknown>): void {
    if (recordPainted(rt.log, payload)) {
      rt.logFull = false;
    }
  }

  private postReplay(items: Record<string, unknown>[]): void {
    for (let start = 0; start < items.length; start += REPLAY_BATCH_SIZE) {
      this.post({ type: "replay", items: items.slice(start, start + REPLAY_BATCH_SIZE) });
    }
  }

  private flushReplay(rt: Runtime): void {
    if (!rt.replay.length) {
      return;
    }
    const items = rt.replay;
    rt.replay = [];
    this.postReplay(items);
  }

  // Rebuild a transcript from what this session has already painted. This is how
  // a chat keeps its history when it moves surface mid turn, when asking the agent
  // for it would abort the turn.
  private replayLog(rt: Runtime): void {
    this.post({ type: "clear", loading: true });
    this.postReplay(paintedReplay(rt.log));
    this.post({ type: "loaded" });
    // The log is a superset of anything buffered while the session was hidden.
    rt.bgBuffer = [];
  }

  private flushBgBuffer(rt: Runtime): void {
    if (!rt.bgBuffer.length) {
      return;
    }
    const buffered = rt.bgBuffer;
    rt.bgBuffer = [];
    for (const payload of buffered) {
      // Reasoning that streamed while the session was in the background renders
      // in one go now, so it is timed no more usefully than a replay is.
      if (payload.type === "thoughtChunk") {
        payload.replayed = true;
      }
      this.post(payload);
    }
    // The turn-boundary markers (assistantStart/End) are only posted for the
    // visible session, so a background turn that has already finished never sent
    // its End. Settle the last replayed block if the turn is done.
    if (!rt.busy) {
      this.post({ type: "assistantEnd" });
    }
  }

  // A chat losing its agent, however it lost it. Anything waiting behind the turn is
  // the user's own writing and only ever lived on the runtime, so it goes back to
  // that chat's draft, where an unsent draft in the same box already survives. The
  // rows go with it: they offer Send now and Remove against a runtime that has gone,
  // and both are no ops once it has.
  private keepQueued(rt: Runtime): void {
    if (!rt.id || !rt.queued.length) {
      return;
    }
    void this.store.queuedBackToDrafts(rt.queued.map((q) => ({ id: rt.id, text: q.text })));
    rt.queued = [];
    this.postQueued(rt);
    if (this.activeId === rt.id) {
      this.postDraft();
    }
  }

  // Mirror a runtime's queue to the webview, but only while it is visible: the composer
  // only ever shows the active session's queue.
  private postQueued(rt: Runtime): void {
    if (this.activeId === rt.id) {
      this.post({
        type: "queued",
        items: rt.queued.map((q) => ({ id: q.id, text: q.text, attachments: this.shownAttachments(q.attachments) }))
      });
    }
  }

  // The agent pushes the whole revertible step list after every turn, so the
  // checkpoint target is handed to us rather than asked for. Recorded on the
  // runtime so a session switch can restore it without a round trip.
  private onRevertSteps(rt: Runtime, update: RevertStepsUpdate): void {
    rt.steps = Array.isArray(update.steps) ? update.steps : [];
    rt.stepsKnown = true;
    if (this.activeId !== rt.id || rt.silentReplay) {
      return;
    }
    const head = headOf(rt.steps);
    if (head != null) {
      // Pushed after the turn it belongs to, on the expansion it belongs to, so
      // unlike a head we read ourselves this is always a valid target.
      this.post({ type: "turnHead", head, reliable: true });
    }
  }

  // Publish this chat and put the link on the clipboard. The agent refuses until
  // the conversation has something in it, and says so, which is worth passing on
  // rather than reporting as a failure.
  private async shareSession(): Promise<void> {
    const rt = this.active();
    if (!rt || !rt.client.supportsSessionShare()) {
      return;
    }
    try {
      const res = await rt.client.shareSession(rt.id);
      const url = res?.url;
      if (!url) {
        void vscode.window.showInformationMessage("Vtx did not return a link for this chat.");
        return;
      }
      await vscode.env.clipboard.writeText(url);
      const open = await vscode.window.showInformationMessage("Link to this chat copied.", "Open");
      if (open === "Open") {
        await vscode.env.openExternal(vscode.Uri.parse(url));
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      void vscode.window.showWarningMessage(`Couldn't share this chat: ${message}`);
    }
  }

  // Fork: continue from an earlier turn in a new chat, leaving this one untouched.
  // The agent copies the conversation into a new session and hands back its id,
  // which is the chat to open. Nothing is rewound and no file is undone, so unlike
  // a restore there is nothing to confirm.
  private async handleRevertFork(target: unknown): Promise<void> {
    const rt = this.active();
    if (!rt || typeof target !== "number" || !Number.isFinite(target)) {
      return;
    }
    try {
      const forked = await rt.client.revertForkFromStep(rt.id, target);
      if (!forked) {
        this.post({ type: "error", text: "Couldn't fork this chat: the agent did not return a new session." });
        return;
      }
      // The fork is a separate session with its own agent, so it is opened the way
      // any other existing chat is.
      this.rememberSession(forked, rt.cwd);
      await this.loadSession(forked);
      await this.refreshSessionsIfVisible(true);
    } catch (err) {
      this.post({ type: "error", text: err instanceof Error ? err.message : String(err) });
    }
  }

  // After a turn completes, hand the webview the node id to pin a revert target
  // ("checkpoint") to the finished turn. The agent pushes this itself
  // (`onRevertSteps`), so this only covers the state a session starts in: a
  // freshly loaded conversation has steps but has had no push yet.
  private async postTurnHead(reliable = false): Promise<void> {
    const rt = this.active();
    if (!rt || !rt.client.supportsRevert()) {
      return;
    }
    const post = (steps: RevertStep[]): void => {
      const head = headOf(steps);
      if (head != null && this.activeId === rt.id) {
        this.post({ type: "turnHead", head, reliable });
      }
    };
    if (rt.stepsKnown) {
      post(rt.steps);
      return;
    }
    try {
      const steps = await rt.client.listRevertSteps(rt.id);
      rt.steps = steps;
      rt.stepsKnown = true;
      post(steps);
    } catch (err) {
      this.log(`[turn-head-failed] ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  // Preview what reverting to a node would undo (files + irreversible actions),
  // so the webview can render an inline confirmation before executing.
  private async handleRevertPreview(head: number, token?: unknown): Promise<void> {
    const rt = this.active();
    if (!rt || !Number.isFinite(head)) {
      return;
    }
    // The agent's plan is only half the answer: it reports no file actions for the
    // edits it made through us, so the count of this chat's unreviewed files is
    // what tells the user their edits are staying on disk.
    const pendingFiles = this.changes.changesFor(rt.id).length;
    try {
      const result = await rt.client.revertPreview(rt.id, head);
      this.post({ type: "revertPreview", head, token, result, pendingFiles });
    } catch (err) {
      this.post({ type: "revertPreview", head, token, pendingFiles, error: err instanceof Error ? err.message : String(err) });
    }
  }

  // Execute a revert (edit-in-place submit, restore checkpoint, or undo). When
  // `resendText` is given, resend it as the next prompt after the rewind.
  private async handleRevertExecute(head: number, resendText: unknown, startNew: boolean): Promise<void> {
    if (!(await this.ensureReady())) {
      return;
    }
    // Reverting the very first turn has no prior node: start fresh instead.
    if (startNew || !Number.isFinite(head)) {
      await this.newSession();
      if (typeof resendText === "string" && resendText.trim()) {
        await this.handleSend(resendText, false);
      }
      return;
    }
    const rt = this.active();
    if (!rt) {
      return;
    }
    try {
      // The agent truncates the conversation but does NOT touch the client's disk,
      // and it does not tell us what to put back either: probing a real agent, the
      // file plan is empty even for files it edited through us. So read the plan,
      // rewind, apply whatever it did ask for, and keep everything it did not.
      const preview = await rt.client.revertPreview(rt.id, head).catch(() => undefined);
      await rt.client.revertExecute(rt.id, head);
      const restored = await this.applyRevertFileUndo(preview, rt);
      // Only the files that were really put back leave the working set. The rest
      // still hold the agent's edits, so they stay listed and undoable rather than
      // being silently abandoned on disk with no way back.
      this.changes.clearFor(rt.id, restored);
      this.post({ type: "reverted", head, filesLeft: this.changes.changesFor(rt.id).length });
    } catch (err) {
      this.post({ type: "error", text: err instanceof Error ? err.message : String(err) });
      return;
    }
    if (typeof resendText === "string" && resendText.trim()) {
      await this.handleSend(resendText, false);
    }
  }

  // Apply the file undo the agent planned in revert/preview, and report which
  // files it actually put back.
  //
  // The agent's own content is preferred over the working set's, because they are
  // not the same text: the working set holds what the file was before this chat
  // FIRST touched it, which for a file edited in two turns is older than the
  // checkpoint being restored, so using it would discard the earlier turn's work
  // as well (and delete a file that turn had created).
  //
  // Probed against a real agent: `fileActions` comes back empty even for a file
  // the agent edited through our own fs/write_text_file, and `revert/execute`
  // leaves the disk alone (`outcomes: []`). So in practice this restores nothing,
  // which is why the caller must keep whatever is left reviewable.
  private async applyRevertFileUndo(preview: RevertPreviewResult | undefined, rt: Runtime): Promise<string[]> {
    const actions = preview?.fileActions;
    if (!Array.isArray(actions) || !actions.length) {
      return [];
    }
    const restored: string[] = [];
    for (const a of actions) {
      const p = typeof a.path === "string" ? this.resolvePath(a.path, rt.id) : undefined;
      if (!p) {
        continue;
      }
      const original = (a.kind as { originalContent?: string } | undefined)?.originalContent;
      try {
        if (typeof original === "string") {
          await fs.promises.writeFile(p, original, "utf8");
          restored.push(p);
        } else if (a.action === "delete" && this.changes.hasUnresolvedChange(p)) {
          // The agent asking for the file to go. The working set knows whether it
          // existed before this chat, and deletes it if not. Only counted as put
          // back when it really was: an undo that failed still holds the agent's
          // content, and forgetting it would take away the only way back.
          if (await this.changes.reject(p)) {
            restored.push(p);
          }
        } else {
          this.log(`[revert] no content for ${p}, left as it is`);
        }
      } catch (err) {
        // A failed restore must not abort the rewind, but it must not be silent
        // either: the file still holds the agent's content.
        this.log(`[revert-failed] ${p}: ${err instanceof Error ? err.message : String(err)}`);
      }
    }
    return restored;
  }

  // Settle every outstanding permission/elicitation request owned by a session
  // as cancelled, so the agent's client-side calls never hang and the resolver
  // maps do not leak. Used both by the user Stop button and by every teardown
  // path (terminate, delete, idle-exit, process exit), where these would
  // otherwise be left unresolved with a dead prompt still on screen.
  private settleRequestsFor(sessionId: string): void {
    if (!sessionId) {
      return;
    }
    for (const [rid, e] of [...this.permissionResolvers]) {
      if (e.rid === sessionId) {
        e.resolve({ outcome: { outcome: "cancelled" } });
        this.permissionResolvers.delete(rid);
      }
    }
    for (const [rid, e] of [...this.elicitationResolvers]) {
      if (e.rid === sessionId) {
        e.resolve({ action: "cancel" });
        this.elicitationResolvers.delete(rid);
      }
    }
    const rt = this.runtimes.get(sessionId);
    if (rt) {
      rt.awaiting = 0;
      // The questions/permissions are no longer outstanding, so drop the stored
      // payloads: they must not re-surface when the session is reopened.
      rt.pending = [];
    }
    // Close any open permission/question widgets for the visible session.
    if (this.activeId === sessionId) {
      this.post({ type: "cancelPrompts" });
    }
  }

  cancel(): void {
    const rt = this.active();
    if (rt) {
      // Stop interrupts only the current turn; any queued messages remain and
      // are sent once the interrupt settles (the user clears the queue by hand
      // if they meant to abandon it).
      rt.client.cancel(rt.id);
      this.settleRequestsFor(rt.id);
    }
    // Whatever the agent was asking is answered now, with "cancelled", so the
    // question goes with the turn it belonged to. Settling the requests only
    // tells the agent: the widgets are on the other side and were left on
    // screen, still offering to answer a question nobody was waiting on.
    this.post({ type: "cancelPrompts" });
    // The composer is free again, but the turn is not over: cancel is a
    // notification, and the prompt stays open until the agent answers it, which
    // can take as long as the command it is waiting on. Clearing `rt.busy` here
    // let the next message past the one gate there is and put a second prompt on
    // a channel that already had one. `endTurn` clears it when the turn really
    // ends, and drains anything queued in between.
    this.post({ type: "busy", value: false });
  }

  // Click-through popup for the status bar (the hover card can't be triggered
  // by click; VS Code has no API for that), mirroring the same info.
  async showInfo(): Promise<void> {
    type Item = vscode.QuickPickItem & { action?: "cloud" | "login" };
    const a = this.health?.account || {};
    const items: Item[] = [{ label: "$(link-external) Open Vtx Cloud", detail: "https://app.vtx.ai", action: "cloud" }];
    if (this.isReady()) {
      if (a.name || a.email) {
        items.push({ label: `$(account) ${a.name || a.email}`, description: a.name && a.email ? a.email : undefined });
      }
      const org = a.plan || a.tier;
      if (org) {
        items.push({ label: `$(organization) ${org}` });
      }
    } else {
      items.push({ label: "$(error) Not signed in", description: "Sign in", action: "login" });
    }
    const mm = [this.currentModel, this.currentMode].filter(Boolean).join("  /  ");
    if (mm) {
      items.push({ label: `$(sparkle) ${mm}` });
    }
    items.push({ label: `$(versions) CLI ${this.health?.version || "unknown"}` });
    const picked = await vscode.window.showQuickPick(items, { title: "Vtx" });
    if (picked?.action === "cloud") {
      await vscode.env.openExternal(vscode.Uri.parse("https://app.vtx.ai"));
    } else if (picked?.action === "login") {
      await this.authenticate();
    }
  }

  async showSessionsView(): Promise<void> {
    this.focus();
    this.leaveToList();
    this.post({ type: "body", body: "list" });
    await this.refreshSessionsFast();
  }

  // Open the chat wearing this number in the list (Ctrl/Cmd+1..9).
  pickSession(index: number): void {
    this.focus();
    this.post({ type: "pickSession", index });
  }

  // Returning to the list should be instant: paint the cached listing at any age,
  // then revalidate in the background so it is still correct. Only a cold cache
  // waits on `vtx list`.
  private async refreshSessionsFast(): Promise<void> {
    const warm = !!this.sessionsCache;
    await this.refreshSessions(false, true);
    if (warm) {
      void this.refreshSessions(true);
    }
  }

  // Set a runtime's busy state, mirroring it to the webview only when it is the
  // visible session, and refreshing the status dots.
  private setRuntimeBusy(rt: Runtime, value: boolean): void {
    rt.busy = value;
    if (!value) {
      rt.lastActivityAt = Date.now();
      // A finished turn is when the CLI names a new chat, so this is what puts
      // that name in the header and the lists without anyone asking for it.
      void this.refreshSessionsIfVisible(true);
    }
    if (this.activeId === rt.id) {
      this.post({ type: "busy", value });
    } else if (value) {
      // A backgrounded session started working, so the webview's saved
      // transcript for it is now stale and must be reloaded on return.
      this.post({ type: "sessionActivity", id: rt.id });
    }
    this.broadcastStatuses();
  }

  private setBusy(value: boolean): void {
    const rt = this.active();
    if (rt) {
      this.setRuntimeBusy(rt, value);
    } else {
      this.post({ type: "busy", value });
    }
  }

  // Tell the webview which optional features are available/enabled so it can
  // gate edit-in-place, checkpoints, and undo.
  private postCapabilities(): void {
    this.post({
      type: "capabilities",
      // So a list of results can name the folder each one is in without repeating
      // the whole absolute path on every row.
      root: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath,
      revert: !!this.active()?.client.supportsRevert(),
      subagentControl: !!this.active()?.client.supportsSubagentControl(),
      sessionShare: !!this.active()?.client.supportsSessionShare(),
      editRequests: this.cfg().get<string>("editRequests", "inline"),
      checkpoints: this.cfg().get<boolean>("checkpoints.enabled", true),
      showFileChanges: this.cfg().get<boolean>("checkpoints.showFileChanges", true),
      confirmRemoval: this.cfg().get<boolean>("editing.confirmEditRequestRemoval", true),
      verbose: this.cfg().get<boolean>("verbose", true),
      progressBorder: this.cfg().get<boolean>("progressBorder.enabled", true),
      contextUsage: this.cfg().get<boolean>("contextUsage.enabled", true),
      inlineReferencesStyle: this.cfg().get<string>("inlineReferences.style", "box"),
      thinkingStyle: this.cfg().get<string>("thinking.style", "fixedScrolling"),
      streamAnim: this.cfg().get<string>("incrementalRendering.animationStyle", "rise"),
      panelSide: this.cfg().get<string>("sessionsPanel.side", "right"),
      sendWhileWorking: this.cfg().get<string>("sendWhileWorking", "queue"),
      surface: this.kind
    });
  }

  // --- Incoming session/update notifications -------------------------------

  // Send a transcript message to the webview if its session is the visible one,
  // otherwise buffer it on the runtime so the missed stream can be replayed when
  // the user returns (background progress is never lost). History replays (a
  // session/load or a silent wake) are neither shown nor buffered here.
  private emit(rt: Runtime | undefined, payload: Record<string, unknown>): void {
    if (!rt) {
      return;
    }
    this.record(rt, payload);
    // The visible session streams straight into the transcript. This includes an
    // active session/load replay (rt.replaying), which is how a reload paints its
    // history; a silent background wake is suppressed (its cached view is shown).
    if (this.activeId === rt.id && !rt.silentReplay) {
      if (rt.replaying) {
        rt.replay.push(payload);
        if (rt.replay.length >= REPLAY_BATCH_SIZE) {
          this.flushReplay(rt);
        }
      } else {
        this.post(payload);
      }
      return;
    }
    // A live background turn (not a history replay): buffer so the user sees the
    // progress it made when they reopen the session.
    if (!rt.silentReplay && !rt.replaying) {
      rt.bgBuffer.push(payload);
      if (rt.bgBuffer.length > BG_BUFFER_MAX) {
        rt.bgBuffer.splice(0, rt.bgBuffer.length - BG_BUFFER_MAX);
      }
    }
  }

  private onUpdate(n: SessionUpdateNotification, owner?: Runtime): void {
    const u = n.update as any;
    const rt = this.runtimeBySessionId(n.sessionId, owner);
    // Delegated work arrives on the same stream as the parent's, tagged in
    // `_meta`. Lift the lifecycle out first, then hand the rest to the switch
    // with the owning subagent attached so the webview can nest it.
    if (this.onSubagentUpdate(u, rt)) {
      return;
    }
    const parentId = rt ? this.subagentBlockId(rt, subagentParent(u)) : undefined;
    switch (u.sessionUpdate) {
      case "agent_message_chunk": {
        const img = imageOf(u.content);
        if (parentId) {
          this.emit(rt, { type: "subagentChunk", parentId, stream: "message", text: textOf(u.content) });
        } else if (img) {
          this.emit(rt, { type: "assistantImage", mime: img.mimeType, data: img.data, messageId: u.messageId });
        } else {
          this.emit(rt, { type: "assistantChunk", text: textOf(u.content), messageId: u.messageId });
        }
        return;
      }
      case "user_message_chunk":
        // A replayed request carries what was attached to it as its own content
        // blocks. Reading only the text out of them lost the attachments on every
        // reload, so a request that had asked about a screenshot came back bare.
        this.emit(rt, {
          type: "userChunk",
          text: textOf(u.content),
          messageId: u.messageId,
          attachments: replayedAttachments(u.content)
        });
        return;
      case "agent_thought_chunk":
        if (this.cfg().get<boolean>("showThinking", true)) {
          if (parentId) this.emit(rt, { type: "subagentChunk", parentId, stream: "thought", text: textOf(u.content) });
          // A replayed thought is not being timed: it already happened, so the
          // panel must not present the replay's own elapsed time as how long
          // Vtx thought for. `cognition.ai/timestamp` cannot stand in for the
          // duration (it is per message node, so a thought and the tool call it
          // led to carry the same one), but it does say when it happened, which
          // the panel shows on hover instead.
          else this.emit(rt, {
            type: "thoughtChunk",
            text: textOf(u.content),
            messageId: u.messageId,
            replayed: rt?.replaying === true,
            at: metaTimestamp(u)
          });
        }
        return;
      case "plan":
        this.emit(rt, { type: "plan", entries: u.entries });
        return;
      case "tool_call":
        this.emit(rt, {
          type: "toolCall",
          id: u.toolCallId,
          parentId,
          title: u.title,
          kind: u.kind,
          meta: toolMeta(u),
          status: u.status || "pending",
          rawInput: u.rawInput,
          content: normalizeToolContent(u.content, rt ? (p) => this.editId(rt, p) : undefined),
          locations: normalizeLocations(u.locations),
          terminalId: claimTerminal(rt, u)
        });
        this.recordDiffs(u, rt);
        return;
      case "tool_call_update":
        this.emit(rt, {
          type: "toolCallUpdate",
          id: u.toolCallId,
          parentId,
          title: u.title,
          kind: u.kind,
          meta: toolMeta(u),
          status: u.status,
          rawInput: u.rawInput,
          content: normalizeToolContent(u.content, rt ? (p) => this.editId(rt, p) : undefined),
          locations: normalizeLocations(u.locations)
        });
        this.recordDiffs(u, rt);
        return;
      case "usage_update":
        this.emit(rt, { type: "usage", used: u.used, size: u.size, cost: u.cost });
        return;
      case "available_commands_update":
        this.emit(rt, { type: "commands", commands: u.availableCommands });
        return;
      case "current_mode_update":
        if (rt) rt.mode = u.currentModeId || rt.mode;
        if (rt && !rt.silentReplay && this.showOptions(rt, u.currentModeId)) {
          this.post({ type: "mode", mode: u.currentModeId });
        }
        return;
      // The mode or model changed somewhere other than our own pickers: a `/model`
      // slash command, or another client on the same session. Without this the
      // composer keeps showing the model the session is no longer using.
      case "config_option_update":
        this.publishOptions(rt, u.configOptions);
        return;
      // The agent names a session after its first request, and renames it as the
      // conversation moves on. This is the push feed for that, so the header, the
      // lists and the editor tab follow it without waiting for a `vtx list`.
      case "session_info_update":
        if (rt && typeof u.title === "string" && u.title.trim()) {
          this.applyAgentTitle(rt, u.title.trim());
        }
        return;
      default:
        return;
    }
  }

  // Handle the subagent lifecycle, returning true when the update was one and
  // needs no further rendering.
  //
  // The parent's `run_subagent` call is what owns the rendered block: it is the
  // only part of a subagent a reloaded session is guaranteed to get back (the
  // CLI replays a foreground subagent's whole transcript, but of a background one
  // keeps just this row), and its `rawInput` already holds the task, the prompt
  // and the mode. The `subagent_started` tag that follows carries the agentId
  // everything else is tagged with, so it is bound to the block rather than
  // rendered itself.
  private onSubagentUpdate(u: any, rt?: Runtime): boolean {
    if (!rt) {
      return false;
    }
    const started = subagentStarted(u);
    if (started) {
      const spawn = takeSubagentSpawn(rt, started.title);
      const id = spawn || started.agentId;
      rt.subagentIds.set(started.agentId, id);
      this.emit(rt, {
        type: "subagentStart",
        id,
        title: started.title,
        task: started.task,
        profile: started.profile,
        background: started.isBackground === true
      });
      return true;
    }
    const completed = subagentCompleted(u);
    if (completed) {
      const id = this.subagentBlockId(rt, completed.agentId);
      rt.subagentIds.delete(completed.agentId);
      this.emit(rt, {
        type: "subagentEnd",
        id,
        success: completed.success !== false,
        summary: completed.summary
      });
      return true;
    }
    if (u._meta?.["cognition.ai/inferenceToolName"] !== "run_subagent") {
      return false;
    }
    const raw = u.rawInput;
    if (raw && typeof raw === "object") {
      rt.subagentSpawns.push({ id: u.toolCallId, title: String(raw.title || "") });
      this.emit(rt, {
        type: "subagentStart",
        id: u.toolCallId,
        title: raw.title,
        task: raw.task,
        profile: profileName(raw.profile),
        background: raw.is_background === true
      });
      return true;
    }
    // A spawn that failed never produces a lifecycle of its own, so it has to
    // close its own block or it would shimmer forever. Drop it from the waiting
    // list too, or the next subagent to start could claim it as its own.
    if (u.status === "failed" || u.status === "cancelled") {
      rt.subagentSpawns = rt.subagentSpawns.filter((s) => s.id !== u.toolCallId);
      this.emit(rt, {
        type: "subagentEnd",
        id: u.toolCallId,
        success: false,
        summary: textOf(firstContent(u.content))
      });
    }
    return true;
  }

  // The block id a subagent's own agentId belongs to (itself, when the spawn
  // that created it was never seen).
  private subagentBlockId(rt: Runtime, agentId?: string): string | undefined {
    if (!agentId) {
      return undefined;
    }
    return rt.subagentIds.get(agentId) || agentId;
  }

  // The edit row a file's change belongs to. There is one row per file per turn,
  // so that is what the row's own diff covers: what this turn did to this file,
  // as against the working set, which is everything still awaiting review.
  private editId(rt: Runtime, fsPath: string): string {
    return `${rt.id}#${rt.turnCount}#${fsPath}`;
  }

  private recordDiffs(u: any, rt?: Runtime): void {
    // Historical diffs from a session/load replay are already resolved, so they
    // are not part of an actionable working set. A background session's edits
    // are tracked under that session and shown when it is next opened.
    if (!rt || rt.replaying) {
      return;
    }
    const content = Array.isArray(u.content) ? u.content : [];
    for (const c of content) {
      if (c && c.type === "diff" && typeof c.path === "string") {
        // Against the session's own directory, like every other agent supplied path:
        // resolving a relative one against the extension host's working directory
        // gives a path in the VS Code install, which is then what an undo writes to.
        const full = this.resolvePath(c.path, rt.id);
        const s = diffStat(c.oldText, c.newText);
        const editId = this.editId(rt, full);
        this.changes.recordEdit(editId, full, c.oldText ?? null, c.newText ?? "");
        // Post the per-file counts before recordDiff fires the working-set list,
        // so the list renders with the deltas already known.
        this.emit(rt, { type: "fileChange", path: full, added: s.added, removed: s.removed, created: c.oldText == null || c.oldText === "", editId });
        // `oldText` is optional on the wire, and a missing one must not become the
        // sentinel that makes Undo delete the file: only our own read of the disk
        // can say a file did not exist. Without a baseline there is nothing to
        // review against, so the row stands on its own and the file is left out of
        // the working set until the write itself records it.
        if (typeof c.oldText === "string") {
          this.changes.recordDiff(full, c.oldText, c.newText ?? "", rt.id, s);
        }
      }
    }
  }

  // --- AcpHost implementation (agent -> client requests) -------------------

  requestPermission(params: RequestPermissionParams): Promise<RequestPermissionResult> {
    const tc = params.toolCall || ({} as RequestPermissionParams["toolCall"]);
    // Vtx asks about a command without a title, putting the command itself in
    // `_meta`. Without it the widget could only say "a tool", which is not enough
    // to answer: the user has to see what would run.
    const command = typeof tc._meta?.["cognition.ai/editableCommand"] === "string"
      ? (tc._meta["cognition.ai/editableCommand"] as string).trim()
      : undefined;
    const rt = this.runtimeBySessionId(params.sessionId);
    if (command && rt?.mode === "bypass") {
      const allow = params.options.find((option) => option.optionId === "allow_once");
      if (allow) {
        return Promise.resolve({ outcome: { outcome: "selected", optionId: allow.optionId } });
      }
    }
    const requestId = `perm-${++ChatController.permissionSeq}`;
    const payload = {
      type: "permission",
      requestId,
      title: tc.title || (command ? "Vtx wants to run a command" : "Vtx wants to run a tool"),
      kind: tc.kind,
      command,
      toolCallId: tc.toolCallId,
      content: tc.content,
      locations: tc.locations,
      options: params.options
    };
    if (rt) {
      rt.awaiting++;
      rt.pending.push({ requestId, payload });
    }
    // Show now if it's the visible session; otherwise mark it "attention" and
    // re-surface when the session is opened, plus a one-off notification so the
    // user knows a background session is blocked waiting on them.
    if (!rt || this.activeId === rt.id) {
      this.post(payload);
    } else {
      this.notifyBackgroundAttention(rt);
    }
    this.broadcastStatuses();
    return new Promise<RequestPermissionResult>((resolve) => {
      this.permissionResolvers.set(requestId, { resolve, rid: rt?.id || this.activeId || "" });
    });
  }

  private resolvePermission(requestId: string, optionId: unknown): void {
    const e = this.permissionResolvers.get(requestId);
    if (!e) {
      return;
    }
    this.permissionResolvers.delete(requestId);
    this.clearAwaiting(e.rid, requestId);
    if (typeof optionId === "string" && optionId.length > 0) {
      e.resolve({ outcome: { outcome: "selected", optionId } });
    } else {
      e.resolve({ outcome: { outcome: "cancelled" } });
    }
  }

  // The agent asks the user a structured question (e.g. ask_user_question).
  private readonly elicitationResolvers = new Map<string, { resolve: (res: unknown) => void; rid: string }>();
  private static elicitationSeq = 0;

  createElicitation(params: any): Promise<unknown> {
    const rt = this.runtimeBySessionId(typeof params?.sessionId === "string" ? params.sessionId : undefined);
    const requestId = `elicit-${++ChatController.elicitationSeq}`;
    const payload = {
      type: "elicitation",
      requestId,
      mode: params?.mode || "form",
      message: params?.message || "",
      schema: params?.requestedSchema,
      allowOther: params?._meta?.["cognition.ai/allowOther"] === true,
      url: params?.url
    };
    if (rt) {
      rt.awaiting++;
      rt.pending.push({ requestId, payload });
    }
    if (!rt || this.activeId === rt.id) {
      this.post(payload);
    } else {
      this.notifyBackgroundAttention(rt);
    }
    this.broadcastStatuses();
    return new Promise((resolve) => {
      this.elicitationResolvers.set(requestId, { resolve, rid: rt?.id || this.activeId || "" });
    });
  }

  private resolveElicitation(requestId: string, action: string, content: unknown): void {
    const e = this.elicitationResolvers.get(requestId);
    if (!e) {
      return;
    }
    this.elicitationResolvers.delete(requestId);
    this.clearAwaiting(e.rid, requestId);
    if (action === "accept") {
      e.resolve({ action: "accept", content: content ?? null });
    } else {
      e.resolve({ action: action === "decline" ? "decline" : "cancel" });
    }
  }

  // Drop a resolved request from its runtime's awaiting count and remove the
  // stored pending payload for the request just answered.
  private clearAwaiting(rid: string, requestId: string): void {
    const rt = this.runtimes.get(rid);
    if (rt) {
      rt.awaiting = Math.max(0, rt.awaiting - 1);
      rt.pending = rt.pending.filter((p) => p.requestId !== requestId);
      // Attention episode is over once nothing is outstanding: allow the next
      // background block to notify again.
      if (rt.awaiting === 0) {
        rt.attentionNotified = false;
      }
    }
    this.broadcastStatuses();
  }

  // A backgrounded session is now blocked waiting on the user. Show one toast
  // per attention episode (not per tool call) with an Open action that brings
  // this surface forward and opens the session so its request can be answered.
  private notifyBackgroundAttention(rt: Runtime): void {
    if (rt.attentionNotified) {
      return;
    }
    rt.attentionNotified = true;
    const title = this.store.titles()[rt.id] || "a background session";
    void vscode.window.showInformationMessage(`Vtx needs your input in ${title}.`, "Open").then((choice) => {
      if (choice === "Open") {
        this.focus();
        this.post({ type: "body", body: "thread" });
        this.post({ type: "openSession", id: rt.id });
      }
    });
  }

  // Resolve an agent-supplied path against the session's cwd when it is
  // relative, so it never accidentally resolves against the extension host's
  // own working directory.
  private resolvePath(p: string, sessionId?: string): string {
    if (path.isAbsolute(p)) {
      return p;
    }
    const base = this.runtimeBySessionId(sessionId)?.cwd || this.cwd();
    return path.join(base, p);
  }

  async readTextFile(params: ReadTextFileParams): Promise<{ content: string }> {
    const full = this.resolvePath(params.path, params.sessionId);
    let content = await fs.promises.readFile(full, "utf8");
    if (params.line || params.limit) {
      const lines = content.split("\n");
      const start = Math.max(0, (params.line ?? 1) - 1);
      const end = params.limit ? start + params.limit : lines.length;
      content = lines.slice(start, end).join("\n");
    }
    return { content };
  }

  // Each runtime owns its own TerminalManager, so terminal client requests are
  // routed to the runtime that owns the session.
  createTerminal(params: CreateTerminalParams): { terminalId: string } {
    const rt = this.runtimeBySessionId(params.sessionId);
    if (!rt) {
      return { terminalId: "" };
    }
    const created = rt.terminals.create(params);
    // The tool call for this command lands a moment later and says nothing about
    // the terminal, so the row would have no way to show the output arriving. We
    // are the ones running it, so hold on to the command and hand the id to the
    // row that matches.
    rt.pendingTerminals.push({ id: created.terminalId, command: params.command });
    if (rt.pendingTerminals.length > 8) {
      rt.pendingTerminals.shift();
    }
    return created;
  }

  terminalOutput(params: TerminalRef): { output: string; truncated: boolean; exitStatus: TerminalExitStatus | null } {
    const rt = this.runtimeBySessionId(params.sessionId);
    return rt ? rt.terminals.output(params.terminalId) : { output: "", truncated: false, exitStatus: null };
  }

  waitForTerminalExit(params: TerminalRef): Promise<TerminalExitStatus> {
    const rt = this.runtimeBySessionId(params.sessionId);
    return rt ? rt.terminals.waitForExit(params.terminalId) : Promise.resolve({ exitCode: null, signal: null });
  }

  // Empty-object results, not null: the agent deserializes these responses into
  // structs and rejects a bare null with a "Parse error" (as fs/write_text_file
  // did). An empty object is the safe "void" response.
  killTerminal(params: TerminalRef): Record<string, never> {
    this.runtimeBySessionId(params.sessionId)?.terminals.kill(params.terminalId);
    return {};
  }

  releaseTerminal(params: TerminalRef): Record<string, never> {
    this.runtimeBySessionId(params.sessionId)?.terminals.release(params.terminalId);
    return {};
  }

  // What the editor already knows is wrong with a file the agent names. Unscoped
  // pulls receive nothing, so workspace wide editor noise never enters the chat.
  // Documents must also be open, which `sendDocumentEvent` covers.
  requestDiagnostics(params: RequestDiagnosticsParams): RequestDiagnosticsResult {
    if (!this.cfg().get<boolean>("editorContext.diagnostics", true) || !params.path) {
      return { items: [] };
    }
    const runtimes = [...new Set([...this.runtimes.values(), ...this.spawnedRuntimes])];
    const runtime = params.sessionId
      ? runtimes.find((candidate) => candidate.id === params.sessionId)
      : runtimes.length === 1
        ? runtimes[0]
        : undefined;
    if (!runtime?.busy) {
      return { items: [] };
    }
    const only = params.path ? vscode.Uri.file(this.resolvePath(params.path, runtime.id)) : undefined;
    const entries = only
      ? [[only, vscode.languages.getDiagnostics(only)] as [vscode.Uri, readonly vscode.Diagnostic[]]]
      : vscode.languages.getDiagnostics();
    return {
      items: diagnosticItems(entries, {
        touched: new Set(this.changes.pathsFor(runtime.id)),
        roots: (vscode.workspace.workspaceFolders || []).map((f) => f.uri.fsPath)
      })
    };
  }

  async writeTextFile(params: WriteTextFileParams): Promise<Record<string, never>> {
    const full = this.resolvePath(params.path, params.sessionId);
    let bytes: Buffer | undefined;
    try {
      bytes = await fs.promises.readFile(full);
    } catch (err) {
      // Only "it is not there" may become a null original: that is the sentinel for
      // a file this chat created, and it is what makes Undo DELETE rather than
      // restore. Folding a permission error, a lock, or running out of file handles
      // into it meant an edit to an existing file was recorded as a creation, and
      // undoing it deleted the user's file with nothing kept to put back.
      const code = (err as { code?: string }).code;
      if (code !== "ENOENT" && code !== "ENOTDIR") {
        throw err;
      }
      bytes = undefined;
    }
    const original = bytes ? bytes.toString("utf8") : null;
    // Reading a UTF-16 or Latin-1 file as text gives replacement characters, and
    // writing that back would destroy every byte they stood for. Refuse instead:
    // the agent is told why, which is better than a file it silently ruined.
    if (bytes && original !== null && !bytes.equals(Buffer.from(original, "utf8"))) {
      throw new Error(`${path.basename(full)} is not UTF-8 text, so it was left alone.`);
    }
    await fs.promises.mkdir(path.dirname(full), { recursive: true });
    // Keep the file's own line endings: the agent writes LF, and rewriting a
    // CRLF file with it would show every line as changed in git.
    let content = original && /\r\n/.test(original) && !/\r\n/.test(params.content)
      ? params.content.replace(/\n/g, "\r\n")
      : params.content;
    // And keep its byte order mark for the same reason: the agent's content will
    // not have one, and dropping it is a real change to a .csproj, a .ps1 or
    // anything else Windows authored, on a line the diff does not even show.
    if (original && original.startsWith("\uFEFF") && !content.startsWith("\uFEFF")) {
      content = "\uFEFF" + content;
    }
    await fs.promises.writeFile(full, content, "utf8");
    // The edit is tracked against the session that made it, so a background
    // session's working set is waiting for it when it is next opened.
    const rt = this.runtimeBySessionId(params.sessionId);
    if (rt) {
      const s = diffStat(original, content);
      const editId = this.editId(rt, full);
      this.changes.recordEdit(editId, full, original, content);
      this.emit(rt, { type: "fileChange", path: full, added: s.added, removed: s.removed, created: original == null, editId });
      this.changes.recordDiff(full, original, content, rt.id, s);
    }
    // The agent's fs/write_text_file expects an (empty) object result; returning
    // null makes it report a spurious "Parse error" even though the write landed.
    return {};
  }

  // --- HTML ---------------------------------------------------------------

  private getHtml(webview: vscode.Webview): string {
    const nonce = getNonce();
    const scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, "dist", "webview.js"));
    const mermaidUri = webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, "dist", "mermaid.js"));
    const styleUri = webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, "media", "main.css"));
    const codiconUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this.context.extensionUri, "media", "codicon", "codicon.css")
    );
    const logoUri = webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, "media", "vtx-logo.svg"));
    // The panel markup lives in a standalone file so the webview harness
    // (scripts/webview-harness.js) can mount the exact same DOM in tests.
    const appBody = fs.readFileSync(
      vscode.Uri.joinPath(this.context.extensionUri, "media", "webview-body.html").fsPath,
      "utf8"
    );
    const modelIcon = (f: string) =>
      webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, "media", "models", f)).toString();
    const modelIcons = JSON.stringify({
      claude: modelIcon("claude.svg"),
      openai: modelIcon("openai.svg"),
      grok: modelIcon("grok.svg")
    }).replace(/"/g, "&quot;");
    const csp = [
      `default-src 'none'`,
      `style-src ${webview.cspSource} 'unsafe-inline'`,
      `script-src 'nonce-${nonce}'`,
      `font-src ${webview.cspSource}`,
      `img-src ${webview.cspSource} https: data:`
    ].join("; ");

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="${csp}" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <link href="${codiconUri}" rel="stylesheet" />
  <link href="${styleUri}" rel="stylesheet" />
  <title>Vtx</title>
</head>
<body data-logo="${logoUri}" data-model-icons="${modelIcons}" data-mermaid-src="${mermaidUri}" data-nonce="${nonce}" data-surface="${this.kind}">
  ${appBody}
  <script nonce="${nonce}" src="${scriptUri}"></script>
</body>
</html>`;
  }
}

interface ToolContentItem {
  type: string;
  text?: string;
  path?: string;
  terminalId?: string;
  added?: number;
  removed?: number;
  created?: boolean;
  mime?: string;
  data?: string;
  // Which edit this diff belongs to, so its row can open just that edit.
  editId?: string;
  // A file a tool pointed at rather than quoted: rendered as a pill that opens it.
  link?: boolean;
}

// Flatten ACP tool-call content into renderable items for the webview.
function normalizeToolContent(content: any, editId?: (path: string) => string): ToolContentItem[] {
  if (!Array.isArray(content)) {
    return [];
  }
  const out: ToolContentItem[] = [];
  for (const c of content) {
    if (!c) {
      continue;
    }
    if (c.type === "diff" && typeof c.path === "string") {
      const s = diffStat(c.oldText, c.newText);
      // `created` so a reloaded transcript can say Created rather than Edited: it
      // has only the tool call to go on, never the live change event.
      out.push({
        type: "diff",
        path: c.path,
        added: s.added,
        removed: s.removed,
        created: c.oldText == null || c.oldText === "",
        editId: editId ? editId(c.path) : undefined
      });
    } else if (c.type === "terminal" && typeof c.terminalId === "string") {
      out.push({ type: "terminal", terminalId: c.terminalId });
    } else if (c.type === "content") {
      const img = imageOf(c.content);
      const link = c.content && c.content.type === "resource_link" && typeof c.content.uri === "string"
        ? { uri: c.content.uri as string, name: c.content.name as string | undefined }
        : undefined;
      if (img) {
        out.push({ type: "image", mime: img.mimeType, data: img.data });
      } else if (link) {
        // A file the tool is pointing at (a search hit, a listing). Dropped before
        // now, because only text and images were read out of a content block.
        out.push({ type: "link", path: fileFromUri(link.uri), text: link.name });
      } else {
        const text = textOf(c.content);
        if (text) {
          out.push({ type: "text", text });
        }
      }
    } else if (typeof c.text === "string") {
      out.push({ type: "text", text: c.text });
    }
  }
  return out;
}

// An image content block ({ type: "image", mimeType, data }), or null.
function imageOf(content: any): { mimeType?: string; data: string } | null {
  if (content && content.type === "image" && typeof content.data === "string" && content.data) {
    return { mimeType: content.mimeType, data: content.data };
  }
  return null;
}

// Vtx tags each tool with its real identity in `_meta` (the ACP `kind` is a
// coarse bucket, e.g. both web search and fetch report kind "fetch"). We surface
// these so the webview can render web search / fetch / MCP tools distinctly.
function toolMeta(u: any): { inferenceToolName?: string; toolName?: string; eventType?: string } | undefined {
  const m = u && u._meta;
  const out: { inferenceToolName?: string; toolName?: string; eventType?: string } = {};
  if (typeof u?.name === "string") out.inferenceToolName = u.name;
  if (m && typeof m === "object") {
    if (typeof m["cognition.ai/inferenceToolName"] === "string") out.inferenceToolName = m["cognition.ai/inferenceToolName"];
    if (typeof m["cognition.ai/toolName"] === "string") out.toolName = m["cognition.ai/toolName"];
    if (typeof m["cognition.ai/eventType"] === "string") out.eventType = m["cognition.ai/eventType"];
  }
  return Object.keys(out).length ? out : undefined;
}

// --- Subagent tags on a session update -----------------------------------
// A subagent announces itself on a tool_call_update whose toolCallId is its own
// agentId, and finishes the same way carrying its report. Everything it does in
// between is tagged with the agentId that produced it.
function subagentStarted(u: any): SubagentStarted | undefined {
  const s = u?._meta?.["cognition.ai/subagent_started"];
  return s && typeof s.agentId === "string" ? (s as SubagentStarted) : undefined;
}

function subagentCompleted(u: any): SubagentCompleted | undefined {
  const c = u?._meta?.["cognition.ai/subagent_completed"];
  return c && typeof c.agentId === "string" ? (c as SubagentCompleted) : undefined;
}

// When the agent produced this update, as the CLI records it on the message node
// it belongs to. Only useful on a replay: live content is happening now.
function metaTimestamp(u: any): string | undefined {
  const at = u?._meta?.["cognition.ai/timestamp"];
  return typeof at === "string" && at ? at : undefined;
}

function subagentParent(u: any): string | undefined {
  const id = u?._meta?.["cognition.ai/subagent_context"]?.parentAgentId;
  return typeof id === "string" && id ? id : undefined;
}

// Claim the spawn a starting subagent belongs to. Both sides carry the same
// title, which is what tells parallel spawns apart; without a match take the
// oldest still-unclaimed one.
function takeSubagentSpawn(rt: Runtime, title?: string): string | undefined {
  const i = rt.subagentSpawns.findIndex((s) => s.title === (title || ""));
  const [spawn] = rt.subagentSpawns.splice(i >= 0 ? i : 0, 1);
  return spawn?.id;
}

// "subagent_explore" is the profile as the tool takes it; the display name the
// lifecycle reports for the same profile is "Explore".
function profileName(profile: unknown): string | undefined {
  if (typeof profile !== "string" || !profile) {
    return undefined;
  }
  const name = profile.replace(/^subagent[_-]/, "").replace(/[_-]+/g, " ");
  return name.charAt(0).toUpperCase() + name.slice(1);
}

function firstContent(content: any): any {
  const first = Array.isArray(content) ? content[0] : undefined;
  return first && first.type === "content" ? first.content : first;
}

function normalizeLocations(locations: any): { path: string; line?: number }[] {
  if (!Array.isArray(locations)) {
    return [];
  }
  return locations
    .filter((l) => l && typeof l.path === "string")
    .map((l) => ({ path: l.path, line: typeof l.line === "number" ? l.line : undefined }));
}

function textOf(content: any): string {
  if (!content) {
    return "";
  }
  if (typeof content === "string") {
    return content;
  }
  if (content.type === "text") {
    return content.text || "";
  }
  // An embedded resource carries its own text (a file a tool is quoting back).
  if (content.type === "resource" && content.resource && typeof content.resource.text === "string") {
    return content.resource.text;
  }
  return "";
}

// What was attached to a request, read back out of the content blocks it was sent
// as: a file it pointed at, or a picture, which travels with its data so the pill
// can show it.
function replayedAttachments(content: unknown): { label: string; type: string; thumb?: string }[] {
  const blocks = Array.isArray(content) ? content : [content];
  const out: { label: string; type: string; thumb?: string }[] = [];
  for (const b of blocks) {
    const block = b as { type?: string; uri?: string; name?: string; mimeType?: string; data?: string };
    if (!block || typeof block !== "object") {
      continue;
    }
    if (block.type === "image" && block.data) {
      out.push({ label: "Image", type: "image", thumb: `data:${block.mimeType || "image/png"};base64,${block.data}` });
    } else if (block.type === "resource_link" && block.uri) {
      out.push({ label: block.name || path.basename(fileFromUri(block.uri) || block.uri), type: "file" });
    }
  }
  return out;
}

// The terminal this tool call is running in. The agent asks the client to create
// one, then only reads it once it has exited, so the call itself never mentions
// it: the command is the only thing they share. Matching on it lets the row show
// the output as it arrives, which is ours to show because we are running it.
function claimTerminal(rt: Runtime | undefined, u: { rawInput?: Record<string, unknown> }): string | undefined {
  const command = typeof u.rawInput?.command === "string" ? u.rawInput.command : "";
  if (!rt || !command) {
    return undefined;
  }
  const i = rt.pendingTerminals.findIndex((t) => t.command === command);
  if (i < 0) {
    return undefined;
  }
  return rt.pendingTerminals.splice(i, 1)[0].id;
}

// One of the CLI's own response figures as text: it supplies the number, the unit
// and both spellings of it, so the panel only has to pick the right one.
function dimensionText(d: ResponseDimension): string {
  const k = d.kind;
  if (!k || k.value === undefined || k.value === null) {
    return "";
  }
  if (typeof k.value === "string") {
    return k.value;
  }
  const rounded = Math.abs(k.value) >= 10 || Number.isInteger(k.value)
    ? Math.round(k.value).toLocaleString()
    : k.value.toFixed(2);
  const unit = Math.abs(k.value) === 1 ? k.tail : k.pluralTail || k.tail;
  return `${k.prefix || ""}${rounded}${unit || ""}`;
}

// The path inside a file URI, for a resource a tool points at.
function fileFromUri(uri: string): string {
  try {
    return /^file:/i.test(uri) ? vscode.Uri.parse(uri).fsPath : uri;
  } catch {
    return uri;
  }
}

// Quote a binary path for the user's shell before it is sent to a VS Code
// terminal. On POSIX, single-quote and escape any embedded single quotes so no
// path metacharacter ($, ;, spaces, quotes) can break out; on Windows, paths
// cannot contain `"`, so double-quoting when there is whitespace is enough.
function quote(p: string): string {
  if (process.platform === "win32") {
    return /\s/.test(p) ? `"${p}"` : p;
  }
  return `'${p.replace(/'/g, `'\\''`)}'`;
}

function getNonce(): string {
  // A CSP nonce must be unguessable, so use a CSPRNG rather than Math.random.
  return crypto.randomBytes(16).toString("hex");
}
