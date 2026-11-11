// Tests for the host half of the settings panel (src/settings/settingsPanel.ts):
// the writes it makes to Vtx's own files.
//
// The panel's own rendering is covered by settings.test.js through the jsdom
// harness. What is left here is what ends up on disk: mcp.json server edits and
// hooks.yml entries, where the risk is leaving the file saying something the
// user did not ask for.

const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const esbuild = require("esbuild");

const ROOT = path.resolve(__dirname, "..");
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "vtx-settings-host-"));

const outfile = path.join(TMP, "settingsPanel.js");
esbuild.buildSync({
  entryPoints: [path.join(ROOT, "src/settings/settingsPanel.ts")],
  outfile,
  bundle: true,
  platform: "node",
  format: "cjs",
  logLevel: "error",
  alias: { vscode: path.join(__dirname, "vscode-stub.js") }
});
const { SettingsPanel } = require(outfile);
const vscode = globalThis.__dvVscode;

function panelDouble() {
  return {
    visible: true,
    iconPath: undefined,
    webview: {
      html: "",
      cspSource: "vscode-webview:",
      asWebviewUri: (uri) => uri,
      postMessage: () => Promise.resolve(true),
      onDidReceiveMessage: () => ({ dispose() {} })
    },
    onDidChangeViewState: () => ({ dispose() {} }),
    onDidDispose: () => ({ dispose() {} }),
    reveal() {},
    dispose() {}
  };
}

function makePanel(root) {
  globalThis.__dvFolders = [{ name: path.basename(root), uri: vscode.Uri.file(root), index: 0 }];
  const context = { extensionUri: vscode.Uri.file(ROOT), subscriptions: [] };
  const panel = new SettingsPanel(context, panelDouble());
  panel.sendData = async () => {};
  return panel;
}

const yaml = require("js-yaml");
const hooksOf = (file) => yaml.load(fs.readFileSync(file, "utf8")) || {};

test("addHook writes the Vtx hooks.yml entry shape", () => {
  const root = fs.mkdtempSync(path.join(TMP, "ws-"));
  const panel = makePanel(root);
  const file = path.join(root, ".vtx", "hooks.yml");

  panel.addHook({
    scope: "project", root, event: "PreToolUse", hookType: "command",
    matcher: "exec", value: "npm run lint", timeout: "30"
  });

  const hooks = hooksOf(file);
  assert.deepStrictEqual(hooks.pre_tool_use, [{
    event: "PreToolUse", type: "command", command: "npm run lint",
    timeout: 30, matcher: "exec"
  }]);
});

test("removing a hook removes that entry by group and index", async () => {
  const root = fs.mkdtempSync(path.join(TMP, "ws-"));
  const panel = makePanel(root);
  const file = path.join(root, ".vtx", "hooks.yml");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, yaml.dump({
    pre_tool_use: [
      { event: "PreToolUse", type: "command", command: "lint" },
      { event: "PreToolUse", type: "command", command: "lint" },
      { event: "PreToolUse", type: "command", command: "test" }
    ]
  }));

  vscode.window.answer = "Remove";
  await panel.removeHook({ source: file, group: "pre_tool_use", index: 0, event: "PreToolUse", command: "lint" });
  vscode.window.answer = undefined;

  const kept = hooksOf(file).pre_tool_use.map((h) => h.command);
  assert.deepStrictEqual(kept, ["lint", "test"], "one removal removes one hook");
});

test("removing the only hook for a group takes the group with it", async () => {
  const root = fs.mkdtempSync(path.join(TMP, "ws-"));
  const panel = makePanel(root);
  const file = path.join(root, ".vtx", "hooks.yml");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, yaml.dump({
    pre_tool_use: [{ event: "PreToolUse", type: "command", command: "lint" }]
  }));

  vscode.window.answer = "Remove";
  await panel.removeHook({ source: file, group: "pre_tool_use", index: 0, event: "PreToolUse", command: "lint" });
  vscode.window.answer = undefined;

  assert.deepStrictEqual(hooksOf(file), {}, "no empty group left behind");
});

test("mcpServerVerb edits the project mcp.json file", () => {
  const root = fs.mkdtempSync(path.join(TMP, "ws-"));
  const panel = makePanel(root);
  const file = path.join(root, ".vtx", "mcp.json");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify({
    mcpServers: { github: { url: "https://example.com/mcp" } }
  }, null, 2));

  panel.mcpServerVerb("disable", "github", "project", root);
  assert.strictEqual(JSON.parse(fs.readFileSync(file, "utf8")).mcpServers.github.enabled, false);

  panel.mcpServerVerb("enable", "github", "project", root);
  assert.ok(!("enabled" in JSON.parse(fs.readFileSync(file, "utf8")).mcpServers.github));

  panel.mcpServerVerb("remove", "github", "project", root);
  assert.deepStrictEqual(JSON.parse(fs.readFileSync(file, "utf8")).mcpServers, {});
});

test("disposing the current settings panel clears its watcher state", () => {
  const root = fs.mkdtempSync(path.join(TMP, "ws-"));
  const panel = makePanel(root);
  let stopped = false;
  let panelDisposed = false;
  panel.stopWatching = () => { stopped = true; };
  panel.panel.dispose = () => { panelDisposed = true; };
  SettingsPanel.current = panel;

  SettingsPanel.disposeCurrent();

  assert.strictEqual(stopped, true);
  assert.strictEqual(panelDisposed, true);
  assert.strictEqual(panel.disposed, true);
  assert.strictEqual(SettingsPanel.current, undefined);
});

test.after(() => {
  fs.rmSync(TMP, { recursive: true, force: true });
});
