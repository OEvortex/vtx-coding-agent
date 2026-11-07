import * as vscode from "vscode";
import { CliAccount } from "../cli/locate";

interface State {
  connected: boolean;
  mode?: string;
  model?: string;
}

interface Info {
  version?: string;
  account?: CliAccount;
}

// Anything from the CLI or the agent, made safe for a trusted Markdown tooltip: the
// characters that could turn a name into a link, an image or a command, plus the
// newlines that could add a line of their own.
function safe(text?: string): string {
  return String(text ?? "").replace(/[\\`*_[\]()<>#|$]/g, "\\$&").replace(/\s+/g, " ").trim();
}

export class StatusBar {
  private readonly item: vscode.StatusBarItem;
  private state: State = { connected: false };
  private info: Info = {};

  constructor() {
    this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 90);
    this.item.command = "vtx.showInfo";
    this.render();
    this.item.show();
  }

  set(state: State): void {
    this.state = state;
    this.render();
  }

  // CLI version + signed-in account, shown in the hover popover.
  setInfo(info: Info): void {
    this.info = info || {};
    this.render();
  }

  private render(): void {
    this.item.text = this.state.connected ? "$(chat-sparkle)" : "$(debug-disconnect)";
    this.item.tooltip = this.buildTooltip();
  }

  // A rich Markdown tooltip acts as the designed popover anchored above the
  // status-bar icon (VS Code has no click-anchored popover API for it).
  //
  // It has to be trusted for the two links at the bottom to work, and trusted
  // markdown renders a `command:` link, so nothing that came from outside is put in
  // it raw: the account details are parsed out of the CLI's own output, and the model
  // and mode arrive from the agent.
  private buildTooltip(): vscode.MarkdownString {
    const md = new vscode.MarkdownString(undefined, true);
    md.isTrusted = true;
    md.supportThemeIcons = true;
    md.appendMarkdown("**$(chat-sparkle) Vtx**\n\n");

    if (!this.state.connected) {
      md.appendMarkdown("$(debug-disconnect) Not connected\n\n");
    } else {
      const a = this.info.account || {};
      if (a.name || a.email) {
        md.appendMarkdown(`$(account) **${safe(a.name || a.email)}**${a.name && a.email ? `  \n${safe(a.email)}` : ""}\n\n`);
      }
      const org = a.plan || a.tier;
      if (org) {
        md.appendMarkdown(`$(organization) ${safe(org)}\n\n`);
      }
      const mm = [this.state.model, this.state.mode].filter(Boolean).map(safe).join("  /  ");
      if (mm) {
        md.appendMarkdown(`$(sparkle) ${mm}\n\n`);
      }
      if (this.info.version) {
        md.appendMarkdown(`$(versions) CLI ${safe(this.info.version)}\n\n`);
      }
    }

    md.appendMarkdown("\n---\n\n");
    md.appendMarkdown("[$(link-external) Open Vtx Cloud](https://app.vtx.ai) &nbsp; ");
    md.appendMarkdown("[$(comment-discussion) Open chat](command:vtx.focusChat)");
    return md;
  }

  dispose(): void {
    this.item.dispose();
  }
}
