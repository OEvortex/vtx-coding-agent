// Tests for the host half of the settings panel (src/settings/settingsPanel.ts):
// the writes it makes to the user's own config files.
//
// The panel's own rendering is covered by settings.test.js through the jsdom
// harness. What is left here is what ends up on disk, where the risk is not that
// a write fails but that it leaves the file saying something the user did not
// ask for: a bucket that is empty rather than absent, or two hooks removed by a
// button that offered to remove one.
//
// The panel is driven directly rather than through its webview, and `sendData`
// is stubbed out on the instance: refreshing spawns the CLI and an agent, which
// says nothing about the write and would make this test depend on a machine
// having Devin installed.

const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const esbuild = require("esbuild");

const ROOT = path.resolve(__dirname, "..");
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "devin-settings-host-"));

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

// As much of a webview panel as the constructor touches.
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

const configOf = (root) => JSON.parse(fs.readFileSync(path.join(root, ".vtx", "config.json"), "utf8"));

test("removing the last permission leaves no empty bucket behind", () => {
  const root = fs.mkdtempSync(path.join(TMP, "ws-"));
  const panel = makePanel(root);

  panel.editPermission("project", "deny", "Exec(rm -rf /)", false, root);
  assert.deepStrictEqual(configOf(root).permissions, { deny: ["Exec(rm -rf /)"] });

  // Taking it away again has to leave the file as it was, not holding an empty
  // rule list for ever: nothing prunes one, and it reads as a deliberate setting.
  panel.editPermission("project", "deny", "Exec(rm -rf /)", true, root);
  assert.strictEqual("permissions" in configOf(root), false, JSON.stringify(configOf(root)));
});

test("removing a hook removes that hook, not every one like it", async () => {
  const root = fs.mkdtempSync(path.join(TMP, "ws-"));
  const panel = makePanel(root);
  const file = path.join(root, ".vtx", "hooks.v1.json");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  // Two hooks, same event, same matcher, same command. Nothing stops that, and
  // the Add form does not check, so a config can hold it by accident.
  fs.writeFileSync(file, JSON.stringify({
    PreToolUse: [{ matcher: "Bash", hooks: [{ command: "lint" }, { command: "lint" }, { command: "test" }] }]
  }, null, 2));

  vscode.window.answer = "Remove";
  await panel.removeHook({ source: file, event: "PreToolUse", matcher: "Bash", command: "lint" });
  vscode.window.answer = undefined;

  const kept = JSON.parse(fs.readFileSync(file, "utf8")).PreToolUse[0].hooks.map((h) => h.command);
  assert.deepStrictEqual(kept, ["lint", "test"], "one removal removes one hook");
});

test("removing the only hook for an event takes the event with it", async () => {
  const root = fs.mkdtempSync(path.join(TMP, "ws-"));
  const panel = makePanel(root);
  const file = path.join(root, ".vtx", "hooks.v1.json");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify({ PreToolUse: [{ matcher: "Bash", hooks: [{ command: "lint" }] }] }, null, 2));

  vscode.window.answer = "Remove";
  await panel.removeHook({ source: file, event: "PreToolUse", matcher: "Bash", command: "lint" });
  vscode.window.answer = undefined;

  assert.deepStrictEqual(JSON.parse(fs.readFileSync(file, "utf8")), {}, "no empty group, no empty event");
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
