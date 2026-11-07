import * as vscode from "vscode";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as crypto from "crypto";
import {
  ConfigScope,
  readConfig,
  stripJsonComments,
  userConfigDir,
  userConfigPath,
  userMcpConfigPath,
  userHooksConfigPath,
  projectHooksConfigPath,
  userSkillsDir,
  vtxSkillsDir,
  projectSkillsDir,
  globalAgentDir,
  projectAgentDir,
  globalExtensionsDir,
  projectExtensionsDir,
  projectMcpConfigPath,
  writeMcpServer
} from "./configService";
import { CliContext, configList, configSet, isPlainCliName, listPlugins, NamedItem, pluginVerb } from "./vtxConfigCli";
import { withQuerySession } from "../acp/queryClient";
import { LoadedHook, LoadedRule } from "../acp/types";
import { checkHealth, loginShellEnv } from "../cli/locate";
import { listModelFamilies } from "../cli/models";

// This extension's id, used to filter VS Code's Settings editor when linking out
// to the settings that control the extension rather than the Vtx CLI.
const EXTENSION_ID = "OEvortex.vtx-vscode";

// The Vtx config keys the settings surface renders, with the default Vtx
// applies when the key is absent from ~/.vtx/config.yml (src/vtx/core/
// defaults/config.yml). This one table drives the controls, the "set here"
// markers, and Reset to defaults, so those can never disagree about what a
// default is. There is no project config.yml in Vtx, so these are global only.
const ROW_DEFAULTS: Record<string, unknown> = {
  "llm.default_provider": "openai-codex",
  "llm.default_model": "gpt-5.5",
  "llm.default_base_url": "",
  "llm.default_thinking_level": "low",
  "llm.tool_call_idle_timeout_seconds": 180,
  "llm.request_timeout_seconds": 600,
  "llm.auth.openai_compat": "auto",
  "llm.auth.anthropic_compat": "auto",
  "llm.tls.insecure_skip_verify": false,
  "llm.system_prompt.git_context": false,
  "llm.system_prompt.ponytail": false,
  "llm.system_prompt.todo_context": false,
  "ui.theme": "gruvbox-dark",
  "ui.collapse_thinking": true,
  "ui.thinking_lines": "1",
  "ui.colored_tool_badge": true,
  "ui.show_welcome_shortcuts": true,
  "ui.hidden_models": [],
  "ui.merged_turn_summary": true,
  "ui.collapse_after_turn": true,
  "ui.model_provider_filter": "",
  "compaction.on_overflow": "continue",
  "compaction.threshold_percent": 80,
  "agent.max_turns": 500,
  "agent.default_context_window": 200000,
  "permissions.mode": "prompt",
  "notifications.enabled": false,
  "notifications.volume": 0.5,
  "recap.enabled": true,
  "recap.idle_seconds": 60,
  "agents.default": "",
  "agents.switch_mode": "lock",
  "agents.files": [],
  "task.max_concurrent": 4
};
const ROW_KEYS = Object.keys(ROW_DEFAULTS);

// Messages that write to disk. Each one refreshes the panel itself, so the file
// watcher can ignore the change it just caused.
const MUTATING = new Set([
  "settings:setPath", "settings:resetSection", "settings:addHook", "settings:permission",
  "settings:createFile", "settings:deletePath", "settings:removeHook", "settings:createSkill",
  "settings:mcpAdd", "settings:mcpVerb", "settings:pluginVerb", "settings:clearExtensionModel"
]);

// Of those, the writes that can change which rules and hooks are in force, which
// is the only thing worth asking the agent again about. Asking means starting one,
// and starting one starts every MCP server: dropping the answer after a toggle
// that cannot possibly change it left that control disabled and spinning for as
// long as that took.
const RELOADS_AGENT = new Set([
  "settings:addHook", "settings:removeHook", "settings:createSkill", "settings:createFile",
  "settings:deletePath", "settings:resetSection", "settings:pluginVerb"
]);

// The Vtx customizations / settings surface: a webview editor panel with a
// section sidebar (General, Instructions, Skills, Plugins, MCP, Hooks,
// Permissions, Advanced) and a scope picker (Global, plus each workspace folder),
// exposing the full Vtx CLI config surface. Settings that control this
// extension live in VS Code settings and are only linked to from here.
export class SettingsPanel {
  private static current?: SettingsPanel;
  private readonly panel: vscode.WebviewPanel;
  private readonly disposables: vscode.Disposable[] = [];
  private watchers: fs.FSWatcher[] = [];
  private watchedDirs = "";
  private watchTimer?: NodeJS.Timeout;
  private cli: CliContext = { cliPath: "vtx" };
  private cliResolved = false;
  // A resolve in flight, so two refreshes at once do not both spawn a health check and a
  // login shell, and a counter so one that read the old setting cannot finish last and
  // win: it would latch, since only another change to those settings clears the flag.
  private cliPending?: Promise<void>;
  private cliGeneration = 0;
  private disposed = false;
  // When the config changes while the panel is hidden, refresh on reveal instead.
  private stale = false;
  private selfWriteAt = 0;

  static readonly viewType = "vtx.settings";

  static disposeCurrent(): void {
    const current = SettingsPanel.current;
    if (!current) {
      return;
    }
    current.panel.dispose();
    current.dispose();
  }

  static show(context: vscode.ExtensionContext): void {
    if (SettingsPanel.current) {
      SettingsPanel.current.panel.reveal();
      return;
    }
    const panel = vscode.window.createWebviewPanel(
      SettingsPanel.viewType,
      "Vtx Settings",
      vscode.ViewColumn.Active,
      { enableScripts: true, retainContextWhenHidden: true, localResourceRoots: [context.extensionUri] }
    );
    SettingsPanel.adopt(context, panel);
  }

  // VS Code keeps the tab across a window reload and hands it back here. Without
  // a serializer it came back as a dead tab that nothing could revive, and
  // opening the settings again put a second one beside it. Nothing needs
  // restoring: the panel reads everything from disk.
  static register(context: vscode.ExtensionContext): vscode.Disposable {
    return vscode.window.registerWebviewPanelSerializer(SettingsPanel.viewType, {
      async deserializeWebviewPanel(panel: vscode.WebviewPanel): Promise<void> {
        if (SettingsPanel.current) {
          panel.dispose();
          return;
        }
        panel.webview.options = { enableScripts: true, localResourceRoots: [context.extensionUri] };
        SettingsPanel.adopt(context, panel);
      }
    });
  }

  private static adopt(context: vscode.ExtensionContext, panel: vscode.WebviewPanel): void {
    panel.iconPath = vscode.Uri.joinPath(context.extensionUri, "resources", "icon.png");
    SettingsPanel.current = new SettingsPanel(context, panel);
  }

  constructor(private readonly context: vscode.ExtensionContext, panel: vscode.WebviewPanel) {
    this.panel = panel;
    panel.webview.html = this.getHtml(panel.webview);
    panel.webview.onDidReceiveMessage((msg) => void this.onMessage(msg), undefined, this.disposables);
    // The panel mirrors files on disk, so keep it live: watch the config
    // directories, and pick up changes to the extension settings it discloses.
    this.disposables.push(
      panel.onDidChangeViewState(() => {
        if (this.panel.visible && this.stale) {
          this.stale = false;
          void this.sendData();
        }
      }),
      vscode.workspace.onDidChangeConfiguration((e) => {
        if (!e.affectsConfiguration("vtx")) {
          return;
        }
        // Fixing the path to the binary, or the environment it runs with, has to take
        // effect on the refresh it triggers rather than on the next panel.
        if (e.affectsConfiguration("vtx.cliPath") || e.affectsConfiguration("vtx.env")) {
          this.cliResolved = false;
          this.cliPending = undefined;
          this.cliGeneration++;
        }
        this.queueRefresh();
      }),
      // A folder added or removed changes the scope tabs, so never skip it.
      vscode.workspace.onDidChangeWorkspaceFolders(() => this.queueRefresh(true))
    );
    panel.onDidDispose(() => this.dispose());
  }

  private dispose(): void {
    if (this.disposed) {
      return;
    }
    this.disposed = true;
    this.stopWatching();
    for (const d of this.disposables) d.dispose();
    this.disposables.length = 0;
    if (SettingsPanel.current === this) {
      SettingsPanel.current = undefined;
    }
  }

  // The active workspace folder for project-scoped reads/writes, following the
  // scope tab. Defaults to the first folder.
  private selectedRoot?: string;

  private folders(): { name: string; path: string }[] {
    return (vscode.workspace.workspaceFolders || []).map((f) => ({ name: f.name, path: f.uri.fsPath }));
  }

  private root(): string | undefined {
    const all = this.folders();
    if (this.selectedRoot && all.some((f) => f.path === this.selectedRoot)) {
      return this.selectedRoot;
    }
    return all[0]?.path;
  }

  // Which binary to run and what to run it with. Resolving it means a health check
  // and reading a login shell's environment, so it is done once and kept, and dropped
  // when the settings it comes from change: it was only ever resolved when the panel
  // opened, so a wrong `vtx.cliPath` stayed wrong for the life of the tab. Every
  // list would be empty and every MCP or plugin action would fail, and correcting the
  // setting changed nothing, since only closing and reopening the tab could.
  private ensureCli(): Promise<void> {
    // The directory moves on its own: the scope the panel is pointed at, and the
    // folders in the workspace, both change without these settings changing.
    this.cli.cwd = this.root();
    if (this.cliResolved) {
      return Promise.resolve();
    }
    if (!this.cliPending) {
      const generation = this.cliGeneration;
      this.cliPending = (async () => {
        const setting = vscode.workspace.getConfiguration("vtx").get<string>("cliPath", "vtx") || "vtx";
        const [health, env] = await Promise.all([checkHealth(setting), loginShellEnv()]);
        if (generation !== this.cliGeneration) {
          // The setting changed while this was running, so it answers for a path the user
          // has already corrected. Another resolve is on its way.
          return;
        }
        const extra = vscode.workspace.getConfiguration("vtx").get<Record<string, string>>("env", {}) || {};
        this.cli = { cliPath: health.path || "vtx", env: { ...env, ...extra }, cwd: this.root() };
        this.cliResolved = true;
      })();
      const mine = this.cliPending;
      void this.cliPending.finally(() => {
        // Only if it is still ours: a settings change clears this and starts
        // another resolve, and clearing it again threw away the handle to that
        // one, so the next refresh paid for a second health check and a second
        // login shell read for nothing.
        if (this.cliPending === mine) {
          this.cliPending = undefined;
        }
      });
    }
    return this.cliPending;
  }

  private post(message: unknown): void {
    this.panel.webview.postMessage(message).then(undefined, () => {});
  }

  // --- Live refresh ---------------------------------------------------------

  // Watch the directories holding the config files this panel edits, so an edit
  // made in a text editor or terminal shows up without pressing Refresh. Called
  // on every refresh, and re-attaches only when the set of directories that
  // exist has changed (a .vtx folder may be created while the panel is open).
  private startWatching(): void {
    // The OAuth token directory is watched too: a login happens in a terminal and
    // reports back through nothing, so without this a server the user had just
    // signed in to kept offering to log in until they changed section.
    const dirs = [userConfigDir(), windsurfDir(), mcpOauthDir(), ...this.folders().map((f) => path.join(f.path, ".vtx"))]
      .filter((d) => {
        try { return fs.existsSync(d); } catch { return false; }
      });
    const signature = dirs.join("\n");
    if (signature === this.watchedDirs && this.watchers.length) return;
    this.stopWatching();
    this.watchedDirs = signature;
    for (const dir of dirs) {
      try {
        this.watchers.push(fs.watch(dir, () => this.queueRefresh()));
      } catch {
        // Watching is a nicety. A platform that refuses is not an error.
      }
    }
  }

  private stopWatching(): void {
    if (this.watchTimer) {
      clearTimeout(this.watchTimer);
      this.watchTimer = undefined;
    }
    for (const w of this.watchers) {
      try { w.close(); } catch { /* already closed */ }
    }
    this.watchers = [];
    this.watchedDirs = "";
  }

  private queueRefresh(force?: boolean): void {
    if (this.disposed) return;
    if (!this.panel.visible) {
      this.stale = true;
      return;
    }
    // Every write from this panel already re-renders, so drop the file watcher's
    // echo of it: rendering twice would steal focus from whatever the user
    // clicked next. Only our own writes are dropped, never an outside edit,
    // because there is no manual Refresh to fall back on.
    if (!force && Date.now() - this.selfWriteAt < 1500) return;
    if (this.watchTimer) clearTimeout(this.watchTimer);
    this.watchTimer = setTimeout(() => {
      this.watchTimer = undefined;
      // A config file changed underneath us, which is the one thing that can make
      // the agent's answer about rules and hooks wrong.
      this.loaded = undefined;
      void this.sendData();
    }, 300);
  }

  private async onMessage(msg: any): Promise<void> {
    const type = String(msg?.type);
    const mutating = MUTATING.has(type);
    try {
      if (mutating) this.selfWriteAt = Date.now();
      // Only a write that can change which rules and hooks are in force drops the
      // cached answer. Which config files are read is one of those, so a change to
      // a `read_config_from` row counts even though the rest of that section does
      // not. Moving between sections never does.
      if (RELOADS_AGENT.has(type) || (type === "settings:setPath" && String(msg.path || "").startsWith("read_config_from"))) {
        this.loaded = undefined;
      }
      switch (msg?.type) {
        case "settings:load":
          await this.sendData();
          return;
        case "settings:reload":
          // Sent when moving between sections, so arriving at one always reflects
          // what is on disk now rather than what was there when the panel opened.
          await this.sendData();
          return;
        case "settings:setPath":
          await this.setValue(scopeOf(msg.scope), String(msg.path), msg.value, msg.root ? String(msg.root) : this.root());
          return;
        case "settings:setRoot":
          // Follows the scope tab, so project-scoped CLI verbs run in that folder. The
          // directory itself is brought up to date by the refresh below.
          this.selectedRoot = String(msg.path || "") || undefined;
          await this.sendData();
          return;
        case "settings:resetSection":
          await this.resetSection(scopeOf(msg.scope), Array.isArray(msg.keys) ? msg.keys.map(String) : [], String(msg.label || "this section"), msg.root ? String(msg.root) : this.root());
          return;
        case "settings:openExtensionSettings":
          await this.openExtensionSettings(msg.query ? String(msg.query) : "");
          return;
        case "settings:clearExtensionModel":
          await this.clearExtensionModel();
          return;
        case "settings:openFile":
          await this.openFile(String(msg.path || ""));
          return;
        case "settings:createFile":
          await this.createFile(String(msg.path || ""), String(msg.template || ""));
          return;
        case "settings:deletePath":
          await this.deletePath(String(msg.path || ""), !!msg.isDir, String(msg.label || ""));
          return;
        case "settings:removeHook":
          await this.removeHook(msg);
          return;
        case "settings:createSkill":
          await this.createSkill(String(msg.name || ""), scopeOf(msg.scope), msg.root ? String(msg.root) : undefined);
          return;
        case "settings:addHook":
          this.addHook(msg);
          await this.sendData();
          return;
        case "settings:pluginVerb": {
          const r = await pluginVerb(this.cli, msg.verb, msg.arg ? String(msg.arg) : undefined);
          if (!r.ok) {
            void vscode.window.showErrorMessage(`Plugin ${msg.verb} failed: ` + (r.err || "unknown error"));
          }
          await this.sendData();
          return;
        }
        case "settings:permission":
          this.editPermission(scopeOf(msg.scope), String(msg.bucket), String(msg.value || ""), !!msg.remove, msg.root ? String(msg.root) : undefined);
          await this.sendData();
          return;
        case "settings:mcpAdd": {
          // A server that belongs to another tool is written straight into that
          // tool's file: `vtx mcp` only ever edits Vtx's own.
          if (msg.source === "windsurf") {
            this.writeWindsurf(msg.options as McpAddOptions);
            await this.sendData();
            return;
          }
          const r = await mcpAdd(this.ctxFor(msg.root), msg.options as McpAddOptions);
          if (!r.ok) {
            void vscode.window.showErrorMessage("Add MCP server failed: " + (r.err || "unknown error"));
          }
          await this.sendData();
          return;
        }
        case "settings:mcpVerb": {
          if (msg.source === "windsurf" && msg.verb !== "login" && msg.verb !== "logout") {
            this.windsurfVerb(String(msg.verb), String(msg.name));
            await this.sendData();
            return;
          }
          const r = await mcpVerb(this.ctxFor(msg.root), msg.verb, String(msg.name), msg.scope ? scopeOf(msg.scope) : undefined);
          if (!r.ok) {
            void vscode.window.showErrorMessage(`MCP ${msg.verb} failed: ` + (r.err || "unknown error"));
          }
          await this.sendData();
          return;
        }
        case "settings:mcpLogin":
          // OAuth login is interactive (it prints a code and waits for the
          // browser), so run it in a terminal the user can see and complete.
          this.mcpLoginTerminal(String(msg.name || ""), msg.root ? String(msg.root) : undefined);
          return;
        default:
          return;
      }
    } catch (err) {
      this.post({ type: "settings:error", text: err instanceof Error ? err.message : String(err) });
    } finally {
      // Every message is answered, so the control that started it always stops
      // showing itself as running. Most paths already answered with fresh data, and
      // this covers the ones that changed nothing: a confirmation the user
      // declined, or an action that does its work elsewhere, like opening a login
      // terminal. Answering only for writes left that one spinning until a timer
      // gave up on it, because the panel counts it as work and the host did not.
      this.post({ type: "settings:idle" });
    }
  }

  // A CLI context whose cwd is a specific folder (for project-scoped verbs), or
  // the active folder by default.
  private ctxFor(root?: unknown): CliContext {
    return { ...this.cli, cwd: (typeof root === "string" && root) ? root : this.root() };
  }

  // The value a key would have if the user config did not set it: Vtx's
  // documented default from src/vtx/core/defaults/config.yml.
  private fallbackValue(_scope: ConfigScope, _root: string | undefined, key: string): unknown {
    return ROW_DEFAULTS[key];
  }

  // Write a value setting. Writing the value that already applies would only add
  // a redundant key and leave the row looking changed, so the key is removed
  // instead, the way VS Code drops a setting you return to its default.
  private async setValue(scope: ConfigScope, key: string, value: unknown, root?: string): Promise<void> {
    let next = value;
    if (next !== undefined && key in ROW_DEFAULTS &&
        JSON.stringify(next) === JSON.stringify(this.fallbackValue(scope, root, key))) {
      next = undefined;
    }
    if (scope === "user") {
      // `vtx config set` treats JSON null as a delete, which is how a reset
      // reaches the YAML file without the panel ever writing it directly.
      const r = await configSet(this.cli, key, next === undefined ? null : next);
      if (!r.ok) {
        void vscode.window.showErrorMessage(`Could not set ${key}: ${r.err || "unknown error"}`);
      }
    } else {
      // Vtx has no project config.yml; project scope is only for files such as
      // .vtx/mcp.json and .vtx/hooks.yml, which are edited by their own verbs.
      void vscode.window.showErrorMessage(`${key} is global only in Vtx.`);
    }
    await this.sendData();
  }

  // Open VS Code's Settings editor filtered to this extension, optionally
  // narrowed further so a link lands on the settings it names.
  private async openExtensionSettings(query: string): Promise<void> {
    const filter = `@ext:${EXTENSION_ID}` + (query ? ` ${query}` : "");
    await vscode.commands.executeCommand("workbench.action.openSettings", filter);
  }

  // Clear the extension's model override so chats started here fall back to the
  // Vtx CLI's own `agent.model`. Both targets are cleared, since the chat
  // panel's model picker writes the Workspace one.
  private async clearExtensionModel(): Promise<void> {
    const cfg = vscode.workspace.getConfiguration("vtx");
    for (const target of [vscode.ConfigurationTarget.Workspace, vscode.ConfigurationTarget.Global]) {
      try {
        await cfg.update("defaultModel", undefined, target);
      } catch {
        // Workspace target throws with no workspace open. Global still applies.
      }
    }
    await this.sendData();
  }

  // Reset a section: remove its keys from the user config so they fall back to
  // Vtx's defaults. Confirmed first. There is no project config.yml to reset.
  private async resetSection(scope: ConfigScope, keys: string[], label: string, root?: string): Promise<void> {
    if (!keys.length) return;
    if (scope !== "user") {
      void vscode.window.showErrorMessage(`${label} is global only in Vtx.`);
      return;
    }
    const choice = await vscode.window.showWarningMessage(
      `Reset ${label} to defaults in Global? This removes those settings from the config.`,
      { modal: true },
      "Reset"
    );
    if (choice !== "Reset") return;
    for (const k of keys) {
      const r = await configSet(this.cli, k, null);
      if (!r.ok) {
        void vscode.window.showErrorMessage(`Could not reset ${k}: ${r.err || "unknown error"}`);
      }
    }
    await this.sendData();
  }

  // Remove, or turn off without losing, a server in Windsurf's config. Windsurf
  // and the CLI both read `disabled`, so turning one off here turns it off in
  // both, which is the point: it is one list of servers, not two.
  private windsurfVerb(verb: string, name: string): void {
    const file = windsurfMcpConfigPath();
    const servers = readConfig(file).mcpServers as Record<string, unknown> | undefined;
    const def = servers && typeof servers === "object" ? servers[name] : undefined;
    if (!def || typeof def !== "object") {
      return;
    }
    if (verb === "remove") {
      writeMcpServer(file, name, null);
      return;
    }
    const next = { ...(def as Record<string, unknown>) };
    if (verb === "disable") {
      next.disabled = true;
    } else {
      delete next.disabled;
      delete next.enabled;
    }
    writeMcpServer(file, name, next);
  }

  // Add a server to Windsurf's config, in the shape Windsurf itself writes.
  private writeWindsurf(o: McpAddOptions): void {
    const def: Record<string, unknown> = o.url
      ? { serverUrl: o.url }
      : { command: o.command || "", args: o.args || [] };
    if (o.env && Object.keys(o.env).length) {
      def.env = o.env;
    }
    writeMcpServer(windsurfMcpConfigPath(), o.name, def);
  }

  private mcpLoginTerminal(name: string, root?: string): void {
    if (!name) return;
    // The name comes from a config file, which may be a cloned repository's own, and
    // this line is typed into whichever shell the user has.
    if (!isPlainCliName(name)) {
      void vscode.window.showErrorMessage(
        `"${name}" is not a name this can log in for. Run vtx mcp login yourself for that server.`
      );
      return;
    }
    const term = vscode.window.createTerminal({ name: "Vtx MCP login", env: this.cli.env, cwd: root || this.root() });
    term.show(true);
    term.sendText(`${quoteArg(this.cli.cliPath)} mcp login ${quoteArg(name)}`);
  }

  private async openFile(p: string): Promise<void> {
    if (!p) return;
    if (p.startsWith("~/") || p.startsWith("~\\")) p = path.join(os.homedir(), p.slice(2));
    try {
      // Open the file as a tab in the settings panel's own editor group (next to
      // the settings tab), not as a split pane or a separate window.
      const col = this.panel.viewColumn ?? vscode.ViewColumn.Active;
      const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(p));
      await vscode.window.showTextDocument(doc, { viewColumn: col, preview: false });
    } catch (err) {
      void vscode.window.showErrorMessage("Could not open " + p + ": " + (err instanceof Error ? err.message : String(err)));
    }
  }

  // Delete a file or directory after a modal confirmation, then refresh.
  private async deletePath(p: string, isDir: boolean, label: string): Promise<void> {
    if (!p || !fs.existsSync(p)) return;
    const choice = await vscode.window.showWarningMessage(
      `Delete ${label || p}? This cannot be undone.`,
      { modal: true, detail: p },
      "Delete"
    );
    if (choice !== "Delete") return;
    try {
      fs.rmSync(p, { recursive: isDir, force: true });
    } catch (err) {
      void vscode.window.showErrorMessage("Could not delete " + p + ": " + (err instanceof Error ? err.message : String(err)));
    }
    await this.sendData();
  }

  // Remove a single hook entry from its source JSON file (matched by event,
  // matcher, and command/prompt), pruning now-empty groups.
  private async removeHook(msg: any): Promise<void> {
    const src = String(msg.source || "");
    if (!src || !fs.existsSync(src)) return;
    const choice = await vscode.window.showWarningMessage(
      "Remove this hook?",
      { modal: true, detail: `${msg.event || ""} ${msg.command || msg.prompt || ""}`.trim() },
      "Remove"
    );
    if (choice !== "Remove") return;
    try {
      // Same hazard as every other write: an unparseable file reads as {}, and
      // writing that back would replace it with just this hook's leftovers.
      refuseIfUnparseable(src);
      const root = readConfig(src);
      const hooksObj = (src.endsWith("hooks.v1.json") ? root : (root.hooks as Record<string, unknown>)) || {};
      const groups = (hooksObj as Record<string, unknown>)[String(msg.event)] as any[];
      if (Array.isArray(groups)) {
        // The first match only. Two hooks in one group can carry the same command
        // (nothing stops that, and the Add form does not check), and filtering by
        // command removed both from a button that offered to remove this hook.
        let removed = false;
        for (const g of groups) {
          if (removed || !g || !Array.isArray(g.hooks)) continue;
          if (msg.matcher !== undefined && (g.matcher || "") !== msg.matcher) continue;
          const at = g.hooks.findIndex((h: any) => (h?.command ?? h?.prompt) === (msg.command ?? msg.prompt));
          if (at !== -1) {
            g.hooks.splice(at, 1);
            removed = true;
          }
        }
        // Drop the groups this emptied, and the event with them when it has none
        // left. The old test asked whether the array existed, which it always does,
        // and whether the pre-filter list was empty, which it never is by then, so
        // removing the last hook for an event left `"PreToolUse": []` behind.
        const kept = groups.filter((g) => Array.isArray(g.hooks) && g.hooks.length);
        if (kept.length) {
          (hooksObj as Record<string, unknown>)[String(msg.event)] = kept;
        } else {
          delete (hooksObj as Record<string, unknown>)[String(msg.event)];
        }
      }
      writeFileAtomic(src, JSON.stringify(root, null, 2) + "\n");
    } catch (err) {
      void vscode.window.showErrorMessage("Could not remove hook: " + (err instanceof Error ? err.message : String(err)));
    }
    await this.sendData();
  }

  // Scaffold a new skill (a directory with a SKILL.md front-matter template) at
  // user or project scope, then open it for editing.
  private async createSkill(name: string, scope: ConfigScope, root?: string): Promise<void> {
    const safe = name.trim().replace(/[^a-zA-Z0-9._-]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase();
    if (!safe) return;
    const folder = root || this.root();
    const base = scope !== "user" && folder
      ? path.join(folder, ".vtx", "skills")
      : path.join(userConfigDir(), "skills");
    const file = path.join(base, safe, "SKILL.md");
    if (!fs.existsSync(file)) {
      const template = `---\nname: ${safe}\ndescription: One line on what this skill does and when Vtx should use it.\n---\n\n# ${safe}\n\nDescribe the skill here. Explain when to use it and how it behaves.\n`;
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, template, "utf8");
    }
    await this.openFile(file);
    await this.sendData();
  }

  // Append a hook to a scope's config.json under hooks.<event>, grouped by
  // matcher (Vtx reads hooks from config.json).
  private addHook(msg: any): void {
    const scope = scopeOf(msg.scope);
    const event = String(msg.event || "").trim();
    const value = String(msg.value || "").trim();
    if (!event || !value) return;
    const type = msg.hookType === "prompt" ? "prompt" : "command";
    const hook: Record<string, unknown> = { type };
    hook[type] = value;
    if (msg.timeout && Number(msg.timeout) > 0) hook.timeout = Number(msg.timeout);
    const matcher = String(msg.matcher || "");
    const root = msg.root ? String(msg.root) : this.root();
    const file = loadConfigFile(scope, root);
    const hooks = (file.data.hooks && typeof file.data.hooks === "object" ? file.data.hooks : {}) as Record<string, unknown[]>;
    const groups = Array.isArray(hooks[event]) ? (hooks[event] as any[]) : [];
    let group = groups.find((g) => (g.matcher || "") === matcher);
    if (!group) { group = { matcher, hooks: [] }; groups.push(group); }
    group.hooks = Array.isArray(group.hooks) ? group.hooks : [];
    group.hooks.push(hook);
    hooks[event] = groups;
    setConfigPath(scope, "hooks", hooks, root);
  }

  private async createFile(p: string, template: string): Promise<void> {
    if (!p) return;
    try {
      fs.mkdirSync(path.dirname(p), { recursive: true });
      if (!fs.existsSync(p)) {
        fs.writeFileSync(p, template, "utf8");
      }
      await this.openFile(p);
      await this.sendData();
    } catch (err) {
      void vscode.window.showErrorMessage("Could not create " + p + ": " + (err instanceof Error ? err.message : String(err)));
    }
  }

  // Add or remove a value from a permissions bucket (allow/deny/ask) in a scope
  // (and folder, for project scope).
  private editPermission(scope: ConfigScope, bucket: string, value: string, remove: boolean, root?: string): void {
    if (!["allow", "deny", "ask"].includes(bucket) || (!value && !remove)) return;
    const target = root || this.root();
    const file = loadConfigFile(scope, target);
    const perms = (file.data.permissions && typeof file.data.permissions === "object"
      ? (file.data.permissions as Record<string, unknown>)
      : {}) as Record<string, string[]>;
    const list = Array.isArray(perms[bucket]) ? (perms[bucket] as string[]).slice() : [];
    const idx = list.indexOf(value);
    if (remove) {
      if (idx >= 0) list.splice(idx, 1);
    } else if (idx < 0) {
      list.push(value);
    }
    // An empty bucket is not a permission rule, and `setConfigPath` only prunes
    // empty objects, so removing the last one left `"deny": []` in the file for
    // ever. Same reasoning as the hook groups above.
    if (list.length) {
      perms[bucket] = list;
    } else {
      delete perms[bucket];
    }
    setConfigPath(scope, "permissions", Object.keys(perms).length ? perms : undefined, target);
  }

  // --- Gather section data --------------------------------------------------

  // Refreshes are not serialised (a message handler is fire and forget), and the
  // scope tab can move while one is in flight. Only the newest may answer: a
  // slower one landing last showed one folder's rules under another folder's tab,
  // and left the cache agreeing with it.
  private dataGeneration = 0;
  private painted = false;
  // The persisted ~/.vtx/config.yml as `vtx config list` reports it. Vtx has
  // no project config.yml, so this is the only source for value rows.
  private userConfig: Record<string, unknown> = {};

  private superseded(generation: number): boolean {
    return this.disposed || generation !== this.dataGeneration;
  }

  private async sendData(): Promise<void> {
    if (this.disposed) return;
    const generation = ++this.dataGeneration;
    // What is on disk, straight away and on its own. It was read in microseconds,
    // and holding it back until a CLI listing and an agent handshake had finished
    // left the panel saying "Loading Vtx settings…" for as long as starting an
    // agent takes, which is every configured MCP server coming up.
    if (!this.painted) {
      this.painted = true;
      this.post({ type: "settings:data", data: this.buildData() });
    }
    // Every list below is a CLI call, so this is where the binary has to be current.
    // A no op unless the settings it comes from changed, or the folder moved.
    await this.ensureCli();
    if (this.superseded(generation)) return;
    this.startWatching();
    const [userConfig, skills, plugins, loaded] = await Promise.all([
      configList(this.cli).catch(() => undefined),
      listSkills(this.cli).catch(() => []),
      listPlugins(this.cli).catch(() => []),
      // Asking the agent means starting one, which costs about as much as the two
      // listings above, so it runs alongside them rather than after them.
      this.loadFromAgent()
    ]);
    if (this.superseded(generation)) return;
    this.userConfig = userConfig || {};
    let families: unknown[] = [];
    try { families = await listModelFamilies(this.cli.cliPath, this.cli.env); } catch { /* ignore */ }
    if (this.superseded(generation)) return;
    this.post({ type: "settings:data", data: this.buildData({ skills, plugins, families, loaded }) });
  }

  // Everything the panel renders. The parts that need a process are optional, so
  // the first paint can go out with only what the files say.
  private buildData(fromCli?: {
    skills: NamedItem[];
    plugins: NamedItem[];
    families: unknown[];
    loaded?: { rules: LoadedRule[]; hooks: LoadedHook[] };
  }): unknown {
    const skills = fromCli?.skills || [];
    const plugins = fromCli?.plugins || [];
    const families = fromCli?.families || [];
    const loaded = fromCli?.loaded;
    const groups = this.scopeGroups();
    // Vtx has no project config.yml, so value rows are global only. Project
    // scope remains for the file-backed sections (instructions, skills, MCP,
    // hooks), which have real per-folder files.
    const userSkills = this.scanSkillDir(userSkillsDir()).concat(this.scanSkillDir(vtxSkillsDir()));

    const data = {
      folders: this.folders(),
      models: { families },
      valuesByScope: [this.valueGroup({ scope: "user", title: "Global" })],
      folderOverrides: {},
      // Settings owned by this extension, disclosed here but edited in VS Code.
      extension: {
        defaultModel: vscode.workspace.getConfiguration("vtx").get<string>("defaultModel", "") || ""
      },
      instructions: {
        byScope: groups.map((g) => ({ ...g, file: this.ruleFileForDir(g.scope === "user" ? userConfigDir() : g.root!) })),
        // What the agent has really loaded, which is more than the one file per
        // scope this panel lets you edit: rules also come from Cursor and Windsurf
        // files, from another tool's config, and from plugins. Undefined when the
        // CLI could not be asked, and the section then says only what it can.
        loaded: loaded?.rules
      },
      skills: {
        byScope: groups.map((g) => ({ ...g, list: g.scope === "user" ? userSkills : this.scanProjectSkills(g.root!) }))
      },
      mcp: { byScope: groups.map((g) => ({ ...g, servers: this.mcpServersForScope(g.scope, g.root) })) },
      hooks: {
        byScope: groups.map((g) => ({ ...g, entries: this.hooksForScope(g.scope, g.root) })),
        loaded: loaded?.hooks
      },
      plugins: { list: plugins },
      permissions: { byScope: [{ scope: "user", title: "Global", mode: defaulted(this.userConfig, "permissions.mode") }] }
    };
    return data;
  }

  // Rules and hooks as the agent reports them. Both need a session, so one query
  // agent answers both and is then closed. Cached until a file changes, which is
  // the only thing that can change the answer: a write here, or an outside edit
  // the watcher picks up.
  // Held against the folder it was asked in: which rules and hooks are in force
  // depends on where the agent is looking, so a multi root workspace has a
  // different answer per folder, and switching folder has to ask again.
  private loaded?: { root: string; rules: LoadedRule[]; hooks: LoadedHook[] };

  private async loadFromAgent(): Promise<{ rules: LoadedRule[]; hooks: LoadedHook[] } | undefined> {
    // The folder the panel is pointed at, not simply the first one: the sections
    // that show this are per scope, so with two folders open the second one was
    // being told the first one's rules governed it.
    const root = this.root() || userConfigDir();
    if (this.loaded && this.loaded.root === root) {
      return this.loaded;
    }
    const result = await withQuerySession(this.cli.cliPath, root, this.cli.env, async (client, sessionId) => ({
      rules: await client.listRules(sessionId),
      hooks: await client.listHooks(sessionId)
    }));
    this.loaded = result ? { root, ...result } : undefined;
    return result;
  }

  // The scope groups every section renders: Global plus one per workspace folder.
  private scopeGroups(): { scope: ConfigScope; root?: string; title: string }[] {
    const folders = this.folders();
    const single = folders.length === 1;
    const groups: { scope: ConfigScope; root?: string; title: string }[] = [{ scope: "user", title: "Global" }];
    for (const f of folders) {
      groups.push({ scope: "project", root: f.path, title: single ? "Workspace" : "Workspace · " + f.name });
    }
    return groups;
  }

  // The effective config is the persisted ~/.vtx/config.yml from `vtx config
  // list`, with Vtx's defaults filled in for display. There is no project
  // config.yml to merge.
  private effectiveData(): Record<string, unknown> {
    return this.userConfig;
  }

  // The one value group: Global. `setKeys` is what config.yml actually sets,
  // which is what separates "unset" from "set to the default".
  private valueGroup(g: { scope: ConfigScope; root?: string; title: string }): unknown {
    const eff = this.effectiveData();
    const values: Record<string, unknown> = {};
    for (const k of ROW_KEYS) {
      values[k] = defaulted(eff, k);
    }
    return {
      scope: g.scope,
      root: g.root,
      title: g.title,
      path: userConfigPath(),
      exists: fs.existsSync(userConfigPath()),
      values,
      setKeys: ROW_KEYS.filter((k) => pick(this.userConfig, k) !== undefined)
    };
  }

  // The instruction entry for a directory. Vtx reads AGENTS.md; CLAUDE.md is
  // only a fallback (used when AGENTS.md is absent), so we never offer creating
  // it, and "Create" always creates AGENTS.md.
  private ruleFileForDir(dir: string): { path: string; exists: boolean; kind: string } {
    const agents = path.join(dir, "AGENTS.md");
    const claude = path.join(dir, "CLAUDE.md");
    if (fs.existsSync(agents)) return { path: agents, exists: true, kind: "AGENTS.md" };
    if (fs.existsSync(claude)) return { path: claude, exists: true, kind: "CLAUDE.md" };
    return { path: agents, exists: false, kind: "AGENTS.md" };
  }

  // Project skills for a folder, scanned from its .agents/skills directory
  // (src/vtx/agent/context/skills.py discovers <cwd-or-ancestor>/.agents/skills).
  private scanProjectSkills(folder: string): unknown[] {
    return this.scanSkillDir(projectSkillsDir(folder));
  }

  private scanSkillDir(base: string): unknown[] {
    if (!fs.existsSync(base)) return [];
    const out: unknown[] = [];
    for (const name of fs.readdirSync(base)) {
      const skillMd = path.join(base, name, "SKILL.md");
      if (!fs.existsSync(skillMd)) continue;
      out.push({ name, description: this.skillDescription(skillMd), dir: path.join(base, name), path: skillMd, scope: "project" });
    }
    return out;
  }

  // The `description:` from a SKILL.md front-matter block (first line only).
  private skillDescription(file: string): string {
    try {
      const raw = fs.readFileSync(file, "utf8");
      const fm = raw.match(/^---\s*[\r\n]([\s\S]*?)[\r\n]---/);
      const m = (fm ? fm[1] : raw).match(/^description:\s*(.+)$/m);
      return m ? m[1].trim().replace(/^["']|["']$/g, "") : "";
    } catch {
      return "";
    }
  }

  // Resolve each skill's SKILL.md path and directory (for open/remove). The CLI
  // list gives a directory relative to home (user skills) or the project root
  // (.vtx/skills); resolve both.
  private resolveSkills(skills: { name: string; description?: string; path?: string; source?: string }[], root?: string): unknown[] {
    const home = os.homedir();
    return skills.map((s) => {
      let dir = s.path || "";
      if (dir === "~") {
        dir = home;
      } else if (dir.startsWith("~/") || dir.startsWith("~\\")) {
        dir = path.join(home, dir.slice(2));
      } else if (dir && !path.isAbsolute(dir)) {
        // Windows separators first, so the test below reads one kind of path.
        const rel = dir.replace(/\\/g, "/").replace(/^\.\//, "");
        // .vtx/skills is project-relative; .vtx/skills and
        // .agents/skills are home-relative.
        dir = rel.startsWith(".vtx/") && root ? path.join(root, rel) : path.join(home, rel);
      }
      const scope = (s.source || "").includes("project") ? "project" : "user";
      return { name: s.name, description: s.description, trigger: s.source, dir, path: dir ? path.join(dir, "SKILL.md") : "", scope };
    });
  }

  // The set of MCP servers with a stored OAuth token (logged in), keyed by both
  // server name and url so we can match either.
  private mcpLoggedIn(): { names: Set<string>; urls: Set<string> } {
    const names = new Set<string>();
    const urls = new Set<string>();
    try {
      const dir = mcpOauthDir();
      for (const f of fs.readdirSync(dir)) {
        if (!f.endsWith(".json")) continue;
        try {
          const t = JSON.parse(fs.readFileSync(path.join(dir, f), "utf8"));
          if (t && t.access_token) {
            if (t.server_name) names.add(String(t.server_name));
            if (t.url) urls.add(String(t.url));
          }
        } catch { /* ignore a bad token file */ }
      }
    } catch { /* no oauth dir yet */ }
    return { names, urls };
  }

  // MCP servers for one scope: the global files for User, or a folder's
  // .vtx/mcp.json for a project folder.
  private mcpServersForScope(scope: ConfigScope, root?: string): unknown[] {
    const out: unknown[] = [];
    const seen = new Set<string>();
    const auth = this.mcpLoggedIn();
    const addFrom = (file: string, source = "vtx") => {
      if (!fs.existsSync(file)) return;
      const servers = readConfig(file).mcpServers;
      if (!servers || typeof servers !== "object") return;
      for (const [name, def] of Object.entries(servers as Record<string, unknown>)) {
        if (seen.has(name)) continue;
        seen.add(name);
        const d = (def || {}) as Record<string, unknown>;
        // Windsurf writes an http server's address as `serverUrl`, so a server
        // imported from it is not stdio just because it has no `url`.
        const url = typeof d.url === "string" ? d.url : (typeof d.serverUrl === "string" ? d.serverUrl : "");
        const transport = typeof d.type === "string" ? String(d.type) : (url ? "http" : "stdio");
        const loggedIn = (url && auth.urls.has(url)) || auth.names.has(name);
        out.push({
          name, scope, transport, file, source,
          // A safe, secret-free summary. Never expose command args or headers:
          // stdio args and tokens can contain secrets (see mcp get).
          detail: safeMcpDetail(url, d.command),
          disabled: d.disabled === true || d.enabled === false,
          loggedIn: !!loggedIn,
          oauthCapable: transport !== "stdio",
          envKeys: d.env && typeof d.env === "object" ? Object.keys(d.env as object) : [],
          headerKeys: d.headers && typeof d.headers === "object" ? Object.keys(d.headers as object) : []
        });
      }
    };
    if (scope === "user") {
      addFrom(userMcpConfigPath());
      addFrom(userConfigPath());
      // The CLI imports these, so this agent has them whether or not Vtx was
      // told about them. Showing them is the only way to know what is loaded.
      addFrom(windsurfMcpConfigPath(), "windsurf");
    } else if (root) {
      addFrom(path.join(root, ".vtx", "mcp.json"));
    }
    return out;
  }

  // Hooks for one scope: the User config, or a folder's .vtx config + hooks.v1.
  private hooksForScope(scope: ConfigScope, root?: string): unknown[] {
    const out: unknown[] = [];
    const addFrom = (hooksObj: unknown, source: string) => {
      if (!hooksObj || typeof hooksObj !== "object") return;
      for (const [event, groups] of Object.entries(hooksObj as Record<string, unknown>)) {
        if (!Array.isArray(groups)) continue;
        for (const g of groups) {
          const matcher = (g && typeof g === "object" ? (g as Record<string, unknown>).matcher : "") || "";
          const hooks = (g && typeof g === "object" ? (g as Record<string, unknown>).hooks : []) as unknown[];
          for (const h of Array.isArray(hooks) ? hooks : []) {
            const ho = (h || {}) as Record<string, unknown>;
            out.push({ event, matcher, type: ho.type, command: ho.command, prompt: ho.prompt, timeout: ho.timeout, source });
          }
        }
      }
    };
    const file = loadConfigFile(scope, root);
    addFrom(file.data.hooks, file.path);
    if (scope !== "user" && root) {
      const p = path.join(root, ".vtx", "hooks.v1.json");
      if (fs.existsSync(p)) {
        try { addFrom(JSON.parse(stripJsonComments(fs.readFileSync(p, "utf8"))), p); } catch { /* ignore */ }
      }
    }
    return out;
  }

  private permissionsForScope(scope: ConfigScope, root?: string): { allow: unknown[]; deny: unknown[]; ask: unknown[] } {
    const p = (loadConfigFile(scope, root).data.permissions || {}) as Record<string, unknown>;
    return {
      allow: Array.isArray(p.allow) ? p.allow : [],
      deny: Array.isArray(p.deny) ? p.deny : [],
      ask: Array.isArray(p.ask) ? p.ask : []
    };
  }

  private getHtml(webview: vscode.Webview): string {
    const nonce = getNonce();
    const scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, "dist", "settings.js"));
    const styleUri = webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, "media", "main.css"));
    const codiconUri = webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, "media", "codicon", "codicon.css"));
    const body = fs.readFileSync(vscode.Uri.joinPath(this.context.extensionUri, "media", "settings-body.html").fsPath, "utf8");
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
  <title>Vtx Settings</title>
</head>
<body class="settings-body" data-nonce="${nonce}">
  ${body}
  <script nonce="${nonce}" src="${scriptUri}"></script>
</body>
</html>`;
  }
}

function scopeOf(s: unknown): ConfigScope {
  return s === "project" ? "project" : "user";
}

// A safe, secret-free one-line summary of an MCP server for display: the URL
// (host + path, query stripped) for remote servers, or just the command binary
// name for stdio servers (never its args, which can contain tokens).
function safeMcpDetail(url: string, command: unknown): string {
  if (url) {
    try { const u = new URL(url); return u.origin + u.pathname; } catch { return url.split("?")[0]; }
  }
  if (typeof command === "string" && command) {
    return "stdio · " + (path.basename(command) || command);
  }
  return "";
}

function quoteArg(p: string): string {
  if (process.platform === "win32") return /\s/.test(p) ? `"${p}"` : p;
  return /[^A-Za-z0-9_./-]/.test(p) ? `'${p.replace(/'/g, `'\\''`)}'` : p;
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

// Layer one config file over another the way Vtx merges them: later keys win,
// and object values (agent, proxy, sandbox, read_config_from) merge one level so
// a folder can override a single sub-key.
function mergeConfig(base: Record<string, unknown>, over: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = { ...base };
  for (const [k, v] of Object.entries(over)) {
    const b = out[k];
    out[k] = isPlainObject(b) && isPlainObject(v) ? { ...b, ...v } : v;
  }
  return out;
}

// A row's value in a config object, falling back to Vtx's documented default.
function defaulted(d: Record<string, unknown>, key: string): unknown {
  const v = pick(d, key);
  return v === undefined ? ROW_DEFAULTS[key] : v;
}

// Read a dotted path from a config object.
function pick(obj: Record<string, unknown>, dotted: string): unknown {
  let node: unknown = obj;
  for (const k of dotted.split(".")) {
    if (!node || typeof node !== "object") return undefined;
    node = (node as Record<string, unknown>)[k];
  }
  return node;
}

function getNonce(): string {
  return crypto.randomBytes(16).toString("hex");
}
