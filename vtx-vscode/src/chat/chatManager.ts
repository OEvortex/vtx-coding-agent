import * as vscode from "vscode";
import * as fs from "fs";
import * as os from "os";
import { ChatController, SurfaceHost } from "./chatViewProvider";
import { cliDataDir } from "../cli/sessionLocks";
import { ChangeTracker } from "../diff/changeTracker";
import { SessionStore } from "../session/sessionStore";
import { StatusBar } from "../ui/statusBar";
import { checkHealth, loginShellEnv } from "../cli/locate";

// Owns the chat surfaces: the sidebar view (one ChatController) and any number
// of editor/window chat panels (one ChatController each). Shared singletons
// (SessionStore, ChangeTracker, StatusBar) live here and are passed into each
// controller. The runtime pool is per controller, so each surface runs its own
// `vtx acp` sessions independently.
export class ChatManager implements vscode.WebviewViewProvider, vscode.WebviewPanelSerializer, SurfaceHost {
  public static readonly viewType = "vtx.chatView";
  public static readonly editorViewType = "vtx.chatEditor";

  private sidebar?: ChatController;
  private readonly panels = new Map<ChatController, vscode.WebviewPanel>();
  // The chat tab the user is in, for the commands that act on a tab (rename).
  private activePanel?: ChatController;
  // Controllers whose tab has closed and whose fate the user is being asked about.
  // They are out of `panels` by then, so shutdown has to find them here.
  private readonly deciding = new Set<ChatController>();
  // Sessions being moved between surfaces, so a tab that is closed as part of a
  // move does not treat its own closure as the user abandoning the chat.
  private readonly moving = new Set<string>();

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly store: SessionStore,
    private readonly changes: ChangeTracker,
    private readonly statusBar: StatusBar,
    private readonly output: vscode.OutputChannel
  ) {}

  // --- Sidebar view ---------------------------------------------------------

  resolveWebviewView(view: vscode.WebviewView): void {
    if (!this.sidebar) {
      this.sidebar = new ChatController(this.context, this.store, this.changes, this.statusBar, this.output, "view", this);
    }
    const sidebar = this.sidebar;
    sidebar.bind(view.webview, () => void vscode.commands.executeCommand("vtx.chatView.focus"));
    // A collapsed panel keeps its page, so it has to be told it is off screen: its
    // session list should not be re-listed while nobody can see it.
    sidebar.setSurfaceVisible(view.visible);
    view.onDidChangeVisibility(() => sidebar.setSurfaceVisible(view.visible));
  }

  // --- Moving a session between surfaces (SurfaceHost) ----------------------

  owner(id: string, except?: ChatController): ChatController | undefined {
    if (!id) {
      return undefined;
    }
    for (const c of this.controllers()) {
      if (c !== except && c.ownsSession(id)) {
        return c;
      }
    }
    return undefined;
  }

  label(controller: ChatController): string {
    return controller === this.sidebar ? "the side panel" : "an editor tab";
  }

  reveal(controller: ChatController): void {
    controller.focus();
  }

  // A question's half given answers, flushed as its widget is torn down, which is
  // also how a session leaves a surface: find whoever holds the request now.
  saveAnswerDraft(requestId: string, state: unknown, except: ChatController): void {
    for (const c of this.controllers()) {
      if (c !== except && c.storeAnswerDraft(requestId, state)) {
        return;
      }
    }
  }

  sessionsChanged(except: ChatController): void {
    for (const c of this.controllers()) {
      if (c !== except) {
        c.surfacesChanged();
      }
    }
  }

  // --- Keeping the session list live ----------------------------------------

  // The CLI offers no notification that its sessions changed, and a session can
  // be started, renamed or deleted by anything: another window, or `vtx` in a
  // terminal. What it does do is keep them all in `sessions.db`, so watching that
  // file is the change feed. Writes land constantly while an agent works, so this
  // is throttled, and it only re-lists while a list is actually on screen: every
  // listing runs `vtx list`, and nobody is watching an unopened list.
  private storeWatcher?: fs.FSWatcher;
  private relistTimer?: NodeJS.Timeout;
  private static readonly RELIST_THROTTLE = 3000;

  watchSessionStore(): void {
    try {
      this.storeWatcher = fs.watch(cliDataDir(), { persistent: false }, (_event, name) => {
        if (name && name.startsWith("sessions.db")) {
          this.scheduleRelist();
        }
      });
    } catch {
      // No CLI data directory yet. The list still refreshes on everything this
      // window does itself, and whenever it is opened.
    }
  }

  private scheduleRelist(): void {
    if (this.relistTimer || this.stopped) {
      return;
    }
    this.relistTimer = setTimeout(() => {
      this.relistTimer = undefined;
      for (const c of this.controllers()) {
        c.relistIfWatched();
      }
    }, ChatManager.RELIST_THROTTLE);
    this.relistTimer.unref?.();
  }

  // An editor tab is named after the chat it holds, like any other editor. A tab
  // with no chat yet (or one whose name the CLI has not reported) keeps the plain
  // "Vtx" it opened with, rather than flickering back to it mid handover.
  titlesChanged(): void {
    const titles = this.store.titles();
    for (const [controller, panel] of this.panels) {
      const id = controller.visibleSession();
      const name = id ? titles[id] : undefined;
      if (name && panel.title !== name) {
        panel.title = name;
      }
    }
  }

  elsewhere(except: ChatController): string[] {
    const ids = new Set<string>();
    for (const c of this.controllers()) {
      if (c === except) {
        continue;
      }
      for (const id of c.liveSessions()) {
        ids.add(id);
      }
    }
    return [...ids];
  }

  statuses(except: ChatController) {
    return Object.assign({}, ...this.controllers()
      .filter((controller) => controller !== except)
      .map((controller) => controller.liveSessionStatuses()));
  }

  sessionListClient(except: ChatController) {
    for (const controller of this.controllers()) {
      if (controller !== except) {
        const client = controller.sessionListClient();
        if (client) {
          return client;
        }
      }
    }
    return undefined;
  }

  // Move a session into a brand new editor tab, beside the current one.
  async detach(id: string): Promise<void> {
    if (!id) {
      return;
    }
    const from = this.owner(id);
    const to = this.openPanel(vscode.ViewColumn.Active, false);
    await to.whenReady();
    if (!from) {
      // Not running anywhere (a dead session): the tab just opens it itself.
      await to.openSession(id);
      this.titlesChanged();
      return;
    }
    await this.move(from, to, id);
  }

  // Move a session back into the side panel, opening the panel if need be.
  async attach(id: string): Promise<void> {
    const to = await this.ensureSidebar();
    if (id && to) {
      await this.moveHere(to, id);
    }
  }

  async moveHere(to: ChatController, id: string): Promise<void> {
    // A chat with no live agent left (terminated, idle-exited) is not running
    // anywhere, but the surface showing it still has to let go of it.
    const from = this.owner(id, to) || this.showing(id, to);
    if (!from) {
      await to.openSession(id);
      return;
    }
    await this.move(from, to, id);
    if (from !== this.sidebar && !from.liveSessions().length) {
      this.closePanelFor(from);
    }
  }

  // Which surface is showing a chat, live or not.
  private showing(id: string, except?: ChatController): ChatController | undefined {
    for (const c of this.controllers()) {
      if (c !== except && c.visibleSession() === id) {
        return c;
      }
    }
    return undefined;
  }

  // The handover itself: the live agent, its terminals and anything it is waiting
  // on leave one surface and are adopted by the other. Nothing restarts.
  private async move(from: ChatController, to: ChatController, id: string): Promise<void> {
    // Wait for the destination, then for any source replay, before export and import
    // run back to back. The second destination check catches a tab closed meanwhile.
    await to.whenReady();
    await from.waitForSessionLoad(id);
    await to.whenReady();
    // What only the old page knows (the draft being typed, a question's half given
    // answers) is written back before the chat leaves, so the new page has it.
    await from.flushSurfaceState();
    const transfer = from.exportRuntime(id);
    if (!transfer) {
      // Nothing live to hand over: the destination opens it from the CLI, and the
      // surface it was on stops showing it.
      from.releaseSession(id);
      await to.openSession(id);
      return;
    }
    this.moving.add(id);
    try {
      await to.importRuntime(transfer);
      this.titlesChanged();
    } catch (err) {
      // The destination could not take it: hand it back rather than leave a live
      // agent owned by nobody, holding the session lock until the window closes.
      try {
        await from.importRuntime(transfer);
      } catch {
        // Both surfaces refused it, so stop the runtime rather than leave its lock alive.
        transfer.rt.terminals.disposeAll();
        await transfer.rt.client.shutdown().catch(() => undefined);
      }
      throw err;
    } finally {
      this.moving.delete(id);
    }
  }

  // The sidebar controller only exists once its view has been resolved, which the
  // focus command triggers, so give VS Code a moment to do it.
  private async ensureSidebar(): Promise<ChatController | undefined> {
    await vscode.commands.executeCommand("vtx.chatView.focus");
    for (let i = 0; i < 50 && !this.sidebar; i++) {
      await new Promise((r) => setTimeout(r, 40));
    }
    return this.sidebar;
  }

  private controllers(): ChatController[] {
    return [this.sidebar, ...this.panels.keys(), ...this.deciding].filter(Boolean) as ChatController[];
  }

  private closePanelFor(controller: ChatController): void {
    const panel = this.panels.get(controller);
    if (panel) {
      panel.dispose();
    }
  }

  // --- Commands proxied to the sidebar surface ------------------------------

  focus(): void {
    if (this.sidebar) {
      this.sidebar.focus();
    } else {
      void vscode.commands.executeCommand("vtx.chatView.focus");
    }
  }
  newSession(): void {
    this.focus();
    void this.sidebar?.newSession();
  }
  // Through `ensureSidebar`, because the extension has no activation event of its
  // own for most of these: the command being run is what starts it, so on the
  // first press of the shortcut (or the first click of the status bar) there is
  // no sidebar controller yet, and a bare `this.sidebar?.` did nothing at all.
  async showSessions(): Promise<void> {
    const chat = await this.ensureSidebar();
    await chat?.showSessionsView();
  }

  // --- Entry points from the editor ----------------------------------------
  // These are invoked from the editor and the explorer, where there may be no
  // chat open at all, so the side panel is brought up first and given the work.
  // A chat already open in an editor tab is left alone: the side panel is the one
  // surface that is always reachable from a right click anywhere.

  async explainSelection(): Promise<void> {
    const chat = await this.ensureSidebar();
    await chat?.explainSelection();
  }

  async fixProblemsHere(): Promise<void> {
    const chat = await this.ensureSidebar();
    await chat?.fixProblemsHere();
  }

  // From the explorer's context menu, which passes the file that was clicked, and
  // from the command palette, which passes nothing and means the active editor.
  async addFileToChat(uri?: vscode.Uri): Promise<void> {
    const target = uri || vscode.window.activeTextEditor?.document.uri;
    if (!target) {
      return;
    }
    const chat = await this.ensureSidebar();
    await chat?.attachUri(target);
  }
  // Ctrl/Cmd+1..9. The panel numbers its own rows, so it is the one that knows
  // which chat a number stands for.
  switchSession(index: number): void {
    // The chat the shortcut was pressed in, which is not always the side panel's.
    (ChatController.focusedSurface() ?? this.sidebar)?.pickSession(index);
  }
  // The chat the user is actually in, like `switchSession`: with the only running
  // turn in an editor tab, cancelling the side panel's chat stopped nothing, or
  // stopped an unrelated turn it happened to be showing.
  cancel(): void {
    (ChatController.focusedSurface() ?? this.sidebar)?.cancel();
  }
  async runSetup(): Promise<void> {
    const chat = await this.ensureSidebar();
    await chat?.runSetup();
  }
  async showInfo(): Promise<void> {
    const chat = ChatController.focusedSurface() ?? (await this.ensureSidebar());
    await chat?.showInfo();
  }

  // Rename the chat in the editor tab this was invoked on (its context menu). VS
  // Code only offers a tab's context menu items for the active tab, so that is
  // the one it applies to.
  renameTabSession(): void {
    void this.activePanel?.renameVisibleSession();
  }

  // --- New session in the editor area / a new window ------------------------

  newSessionEditor(): void {
    this.openPanel(vscode.ViewColumn.Active, true);
  }

  async newSessionWindow(): Promise<void> {
    // No stable API creates a webview directly in an auxiliary window, so open
    // it in the active group, then move that editor into a new window.
    this.openPanel(vscode.ViewColumn.Active, true);
    try {
      await vscode.commands.executeCommand("workbench.action.moveEditorToNewWindow");
    } catch {
      // Fall back to leaving it in the editor area if the move is unavailable.
    }
  }

  private openPanel(column: vscode.ViewColumn, autoNewSession: boolean): ChatController {
    const panel = vscode.window.createWebviewPanel(
      ChatManager.editorViewType,
      "Vtx",
      column,
      { enableScripts: true, retainContextWhenHidden: true, localResourceRoots: [this.context.extensionUri] }
    );
    panel.iconPath = vscode.Uri.joinPath(this.context.extensionUri, "resources", "icon.png");
    this.adoptPanel(panel, autoNewSession);
    return this.controllerForPanel(panel)!;
  }

  private controllerForPanel(panel: vscode.WebviewPanel): ChatController | undefined {
    for (const [controller, p] of this.panels) {
      if (p === panel) {
        return controller;
      }
    }
    return undefined;
  }

  private adoptPanel(panel: vscode.WebviewPanel, autoNewSession: boolean): void {
    const controller = new ChatController(this.context, this.store, this.changes, undefined, this.output, "editor", this);
    controller.autoNewSession = autoNewSession;
    this.panels.set(controller, panel);
    controller.bind(panel.webview, () => panel.reveal());
    // Which tab a tab-scoped command (rename) applies to.
    if (panel.active) {
      this.activePanel = controller;
    }
    controller.setSurfaceVisible(panel.visible);
    panel.onDidChangeViewState(() => {
      controller.setSurfaceVisible(panel.visible);
      if (panel.active) {
        this.activePanel = controller;
      }
    });
    panel.onDidDispose(() => {
      // Every chat this tab was running, not just the one on screen: it keeps its
      // own session list, so others can be working in the background.
      const live = controller.liveSessions().filter((id) => !this.moving.has(id));
      controller.markClosed();
      this.panels.delete(controller);
      if (this.activePanel === controller) {
        this.activePanel = undefined;
      }
      // Closing a tab must never quietly kill a session that is still working.
      // VS Code cannot veto an editor closing, so the choice is offered after the
      // fact, and "Keep it open" reopens the tab it was just in.
      if (live.length && !this.stopped) {
        this.deciding.add(controller);
        void this.decideClosedTab(controller, live);
        return;
      }
      controller.dispose();
    });
  }

  // A detached chat's tab was closed while its sessions were still alive.
  private async decideClosedTab(controller: ChatController, ids: string[]): Promise<void> {
    const titles = this.store.titles();
    const named = ids.map((id) => titles[id] || id);
    const subject = ids.length === 1 ? `The chat "${named[0]}" is` : `${ids.length} chats are`;
    const detail = ids.length === 1
      ? "Stop it, carry on with it in the side panel, or cancel to put the tab back."
      : `${named.join(", ")}. Stop them, carry on in the side panel, or cancel to put the tab back.`;
    try {
      // VS Code cannot veto an editor closing, so the tab has already gone by the
      // time we are asked. Cancel is therefore "put it back": the agent never
      // stopped, so it costs only a repainted transcript, and dismissing the dialog
      // is the safe answer rather than the one that kills the work.
      const choice = await vscode.window.showWarningMessage(
        `${subject} still running.`,
        { modal: true, detail },
        "Terminate",
        "Move to Side Panel"
      );
      if (choice === "Move to Side Panel") {
        const to = await this.ensureSidebar();
        for (const id of to ? ids : []) {
          await this.move(controller, to!, id);
        }
      } else if (choice !== "Terminate") {
        const to = this.openPanel(vscode.ViewColumn.Active, false);
        for (const id of ids) {
          await this.move(controller, to, id);
        }
      }
    } finally {
      this.deciding.delete(controller);
      controller.dispose();
    }
  }

  // Restore editor/window chats after a reload. A `vtx acp` agent cannot outlive
  // the extension host, so the chat is reopened from the CLI rather than resumed:
  // the tab remembers which chat it held (the page records it), so it comes back
  // to the same one instead of an empty tab.
  async deserializeWebviewPanel(panel: vscode.WebviewPanel, state: unknown): Promise<void> {
    panel.webview.options = { enableScripts: true, localResourceRoots: [this.context.extensionUri] };
    this.adoptPanel(panel, false);
    const controller = this.controllerForPanel(panel);
    if (controller) {
      controller.openOnReady = (state as { sessionId?: string } | undefined)?.sessionId;
    }
  }

  // --- New Vtx CLI session in a terminal ----------------------------------

  async newSessionTerminal(): Promise<void> {
    const setting = vscode.workspace.getConfiguration("vtx").get<string>("cliPath", "vtx") || "vtx";
    const [health, env] = await Promise.all([checkHealth(setting), loginShellEnv()]);
    const bin = health.path || "vtx";
    const extra = vscode.workspace.getConfiguration("vtx").get<Record<string, string>>("env", {}) || {};
    const term = vscode.window.createTerminal({
      name: "Vtx CLI",
      cwd: this.terminalCwd(),
      env: { ...env, ...extra }
    });
    term.show(true);
    term.sendText(quoteArg(bin));
  }

  private terminalCwd(): string {
    const active = vscode.window.activeTextEditor?.document.uri;
    if (active) {
      const folder = vscode.workspace.getWorkspaceFolder(active);
      if (folder) {
        return folder.uri.fsPath;
      }
    }
    return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || os.homedir();
  }

  // Whichever stop path runs first wins, so the fallback dispose after an awaited
  // shutdown is a no-op rather than a second round of signals.
  private stopped = false;

  // Stop every surface for good and resolve once every agent has really exited.
  // Called from `deactivate`, which VS Code awaits, so a window reload leaves no
  // stranded agents, MCP servers or running commands behind.
  async shutdown(): Promise<void> {
    if (this.stopped) {
      return;
    }
    this.stopped = true;
    this.stopWatching();
    // `deciding` included: a tab closed with a live chat still holds its agent
    // while the user answers the dialog, and that agent has to stop with the rest.
    await Promise.all(this.controllers().map((c) => c.shutdown()));
    this.panels.clear();
    this.deciding.clear();
  }

  dispose(): void {
    if (this.stopped) {
      return;
    }
    this.stopped = true;
    this.stopWatching();
    for (const controller of this.controllers()) {
      controller.dispose();
    }
    this.panels.clear();
    this.deciding.clear();
  }

  private stopWatching(): void {
    this.storeWatcher?.close();
    this.storeWatcher = undefined;
    if (this.relistTimer) {
      clearTimeout(this.relistTimer);
      this.relistTimer = undefined;
    }
  }
}

// Quote a binary path for the user's shell (POSIX single-quote / Windows
// double-quote), matching the escaping used elsewhere for the auth command.
function quoteArg(p: string): string {
  if (process.platform === "win32") {
    return /\s/.test(p) ? `"${p}"` : p;
  }
  return `'${p.replace(/'/g, `'\\''`)}'`;
}
