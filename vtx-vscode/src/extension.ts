import * as vscode from "vscode";
import { ChatManager } from "./chat/chatManager";
import { ChangeTracker } from "./diff/changeTracker";
import { SessionStore } from "./session/sessionStore";
import { StatusBar } from "./ui/statusBar";
import { reapOrphanedAgents } from "./cli/reaper";
import { sweepStaleLocks } from "./cli/sessionLocks";
import { SettingsPanel } from "./settings/settingsPanel";
import { shutdownQueryAgents } from "./acp/queryClient";

// Held at module scope so `deactivate` can await the shutdown. A `vtx acp`
// agent runs its commands, file writes and permission prompts through this
// extension host over our stdio, so it cannot be handed to the next one: the
// only safe move on the way out is to stop every agent deterministically.
let manager: ChatManager | undefined;
// Held here for the same reason: its last write is debounced, and `deactivate` is
// the only place left that can wait for it.
let working: ChangeTracker | undefined;

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel("Vtx");
  // Clean up anything left stranded by a previous crash or force-quit before we
  // start fresh: orphaned `vtx acp` agents (ppid == 1), and session lock
  // files whose owning PID is dead. Deferred so activation never blocks on the
  // lock-dir scan. Live owners are left untouched.
  reapOrphanedAgents((line) => output.appendLine(line));
  setTimeout(() => {
    try {
      sweepStaleLocks((line) => output.appendLine(line));
    } catch {
      // best effort
    }
  }, 0).unref?.();
  const changes = new ChangeTracker();
  working = changes;
  const store = new SessionStore(context.workspaceState);
  const statusBar = new StatusBar();
  context.subscriptions.push(output, changes.register(), changes, statusBar);
  // A change waiting to be reviewed has nothing to do with the agent that made it,
  // so it survives a window reload: without this the diffs and their Keep and Undo
  // were forgotten, and the next edit looked like a brand new set of changes.
  // Only with a folder or workspace open. A folderless window has no storage of
  // its own, and the global directory is shared with every other folderless
  // window, so one window's save replaced another window's whole working set.
  if (context.storageUri) {
    void changes.useStore(context.storageUri);
  }

  manager = new ChatManager(context, store, changes, statusBar, output);
  const chat = manager;
  // Keep the session list live off the CLI's own store instead of a refresh button.
  chat.watchSessionStore();
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(ChatManager.viewType, chat, {
      webviewOptions: { retainContextWhenHidden: true }
    }),
    vscode.window.registerWebviewPanelSerializer(ChatManager.editorViewType, chat),
    SettingsPanel.register(context),
    // A backstop for any path that disposes the extension without calling
    // `deactivate`. After an awaited shutdown this is a no-op.
    { dispose: () => chat.dispose() }
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("vtx.focusChat", () => chat.focus()),
    vscode.commands.registerCommand("vtx.newSession", () => chat.newSession()),
    vscode.commands.registerCommand("vtx.newSessionEditor", () => chat.newSessionEditor()),
    vscode.commands.registerCommand("vtx.newSessionWindow", () => chat.newSessionWindow()),
    vscode.commands.registerCommand("vtx.newSessionTerminal", () => chat.newSessionTerminal()),
    vscode.commands.registerCommand("vtx.renameTabSession", () => chat.renameTabSession()),
    vscode.commands.registerCommand("vtx.showSessions", () => chat.showSessions()),
    vscode.commands.registerCommand("vtx.switchSession", (index: number) => chat.switchSession(index)),
    vscode.commands.registerCommand("vtx.cancel", () => chat.cancel()),
    vscode.commands.registerCommand("vtx.runSetup", () => chat.runSetup()),
    vscode.commands.registerCommand("vtx.showInfo", () => chat.showInfo()),
    vscode.commands.registerCommand("vtx.openSettings", () => SettingsPanel.show(context)),
    // Reachable from the editor and the explorer, so a question about the code in
    // front of you does not start with finding the panel and describing where you are.
    vscode.commands.registerCommand("vtx.explainSelection", () => chat.explainSelection()),
    vscode.commands.registerCommand("vtx.fixProblemsHere", () => chat.fixProblemsHere()),
    vscode.commands.registerCommand("vtx.addFileToChat", (uri?: vscode.Uri) => chat.addFileToChat(uri))
  );
}

// VS Code awaits this (with a timeout) before tearing the extension host down,
// which is the only point where we can wait for the agents to actually exit. The
// synchronous dispose path cannot: its escalation to SIGKILL sits on a timer that
// never fires once the host has gone, which is how an agent that ignores SIGTERM
// used to survive as an orphan until the next window reaped it.
export async function deactivate(): Promise<void> {
  const chat = manager;
  const changes = working;
  manager = undefined;
  working = undefined;
  SettingsPanel.disposeCurrent();
  // The chats, and the agent the settings panel opens to ask what is loaded, which
  // belongs to no chat and so is in no pool for `shutdown` to find. Plus the
  // working set, whose last Keep or Undo is still sitting behind a debounce.
  await Promise.all([chat?.shutdown(), shutdownQueryAgents(), changes?.flush()]);
}
