// Regression tests for the settings webview, run against the real bundle via the
// jsdom harness. Run with: npm test   (compiles the bundle first).
//
// These guard the Vtx surface: global-only value rows, per-folder file sections,
// mcp.json edits, hooks.yml edits, plugin rows, and permissions.mode.

const { after, test } = require("node:test");
const assert = require("node:assert");
const { createSettings: mountSettings } = require("./settings-harness");
const { DEFAULTS } = require("./settings-fixture");

const settings = [];
const createSettings = (...args) => {
  const instance = mountSettings(...args);
  settings.push(instance);
  return instance;
};
after(() => settings.forEach(({ window }) => window.close()));

const SECTIONS = ["General", "Instructions", "Skills", "Plugins", "MCP Servers", "Hooks", "Permissions", "Advanced"];

test("the sidebar lists every Vtx section once", () => {
  const h = createSettings();
  assert.deepStrictEqual(h.sections(), SECTIONS);
  assert.strictEqual(h.activeSection(), "General");
  assert.deepStrictEqual(h.consoleErrors, []);
});

test("every config key still has exactly one row, somewhere", () => {
  const h = createSettings();
  const seen = [];
  for (const s of SECTIONS) {
    h.openSection(s);
    seen.push(...h.rowKeys());
  }
  assert.deepStrictEqual(seen.slice().sort(), Object.keys(DEFAULTS).sort());
  assert.deepStrictEqual(h.consoleErrors, []);
});

test("global-only sections hide the scope picker", () => {
  const h = createSettings();
  for (const s of ["General", "Plugins", "Permissions", "Advanced"]) {
    h.openSection(s);
    assert.deepStrictEqual(h.scopes(), [], s + " should not offer a scope");
  }
  h.openSection("Instructions");
  assert.deepStrictEqual(h.scopes(), ["Global", "Workspace"]);
});

test("value rows are global only and write through settings:setPath", () => {
  const h = createSettings();
  h.openSection("General");
  const toggle = h.control("ui.show_welcome_shortcuts");
  toggle.checked = false;
  toggle.dispatchEvent(new h.window.Event("change"));
  assert.deepStrictEqual(h.last("settings:setPath"), {
    type: "settings:setPath", scope: "user", path: "ui.show_welcome_shortcuts", value: false
  });
});

test("a value the dropdown does not offer is still the value shown", () => {
  const h = createSettings();
  const data = JSON.parse(JSON.stringify(h.data));
  for (const g of data.valuesByScope) {
    g.values["compaction.on_overflow"] = "pause-later";
  }
  h.send({ type: "settings:data", data });
  h.openSection("Advanced");
  const control = h.control("compaction.on_overflow");
  assert.strictEqual(control.value, "pause-later");
  assert.ok([...control.options].some((o) => o.value === "pause-later"));
  assert.deepStrictEqual(h.consoleErrors, []);
});

test("the extension model override is reported against vtx.defaultModel", () => {
  const h = createSettings();
  assert.ok(!h.rowKeys().includes("vtx.defaultModel"));
  const notice = h.document.querySelector(".settings-notice");
  assert.ok(notice, "expected a notice about the VS Code setting overriding the model");
  assert.match(h.text(notice), /vtx\.defaultModel/);
  assert.ok(h.row("llm.default_model").contains(notice));
  h.click("Clear it");
  assert.strictEqual(h.last().type, "settings:clearExtensionModel");
});

test("plugin rows enable and disable through vtx plugin", () => {
  const h = createSettings();
  h.openSection("Plugins");
  assert.deepStrictEqual(h.scopes(), []);
  h.click("Disable row");
  assert.deepStrictEqual(h.last("settings:pluginVerb"), {
    type: "settings:pluginVerb", verb: "disable", arg: "tools"
  });
  h.click("Enable row");
  assert.deepStrictEqual(h.last("settings:pluginVerb"), {
    type: "settings:pluginVerb", verb: "enable", arg: "sandbox"
  });
});

test("MCP rows edit the Vtx mcp.json scope", () => {
  const h = createSettings();
  h.openSection("MCP Servers");
  h.click("Disable");
  const msg = h.last("settings:mcpVerb");
  assert.strictEqual(msg.verb, "disable");
  assert.strictEqual(msg.name, "github");
  assert.strictEqual(msg.scope, "user");
  h.openScope("Workspace");
  h.click("Remove");
  const projectMsg = h.last("settings:mcpVerb");
  assert.strictEqual(projectMsg.verb, "remove");
  assert.strictEqual(projectMsg.name, "postgres");
  assert.strictEqual(projectMsg.scope, "project");
  assert.strictEqual(projectMsg.root, h.data.folders[0].path);
});

test("MCP OAuth opens the interactive Vtx signin flow", () => {
  const h = createSettings();
  h.openSection("MCP Servers");
  h.click("Sign in (OAuth)");
  assert.deepStrictEqual(h.last("settings:mcpLogin"), {
    type: "settings:mcpLogin", name: "issue-tracker"
  });
  h.click("Sign out");
  assert.deepStrictEqual(h.last("settings:mcpLogin"), {
    type: "settings:mcpLogin", name: "github", signout: true
  });
});

test("hooks remove by hooks.yml group and index", () => {
  const h = createSettings();
  h.openSection("Hooks");
  h.click("Remove hook");
  assert.deepStrictEqual(h.last("settings:removeHook"), {
    type: "settings:removeHook",
    source: "/Users/dev/.vtx/hooks.yml",
    group: "pre_tool_use",
    index: 0,
    event: "PreToolUse",
    command: "~/bin/audit-exec.sh"
  });
});

test("the hook form offers the Vtx events and hook types", () => {
  const h = createSettings();
  h.openSection("Hooks").click("Add");
  const selects = h.all(".settings-modal select");
  const events = [...selects[0].options].map((o) => o.value);
  assert.deepStrictEqual(events, [
    "SessionStart", "SessionEnd", "TurnStart", "TurnEnd", "PreToolUse",
    "PostToolUse", "PostToolUseFailure", "PermissionRequest", "PermissionDenied",
    "PreCompact", "PostCompact"
  ]);
  const types = [...selects[1].options].map((o) => o.value);
  assert.deepStrictEqual(types, ["command", "prompt", "http", "agent"]);
});

test("permissions expose only the Vtx mode", () => {
  const h = createSettings();
  h.openSection("Permissions");
  const control = h.control("permissions.mode");
  assert.strictEqual(control.value, "auto");
  control.value = "prompt";
  control.dispatchEvent(new h.window.Event("change"));
  assert.deepStrictEqual(h.last("settings:setPath"), {
    type: "settings:setPath", scope: "user", path: "permissions.mode", value: "prompt"
  });
});

test("a list value of the wrong shape is shown, not left as a blank page", () => {
  const h = createSettings();
  const data = JSON.parse(JSON.stringify(h.data));
  for (const g of data.valuesByScope) {
    g.values["ui.hidden_models"] = "provider:model";
  }
  h.send({ type: "settings:data", data });
  h.openSection("Advanced");
  assert.strictEqual(h.control("ui.hidden_models").value, "provider:model");
  assert.ok(h.rowKeys().length > 1);
  assert.deepStrictEqual(h.consoleErrors, []);
});

test("the instructions section reports every rule the agent has loaded", () => {
  const h = createSettings();
  h.openSection("Instructions");
  const rows = h.document.querySelectorAll(".settings-list-row");
  const text = [...rows].map((r) => h.text(r.querySelector(".settings-list-title"))).join(" | ");
  assert.match(text, /global_rules/);
  assert.match(text, /\.agents/);
  assert.ok(h.actions().includes("Open this file"));
  assert.deepStrictEqual(h.consoleErrors, []);
});

test("the hooks section reports the hooks the agent has loaded", () => {
  const h = createSettings();
  h.openSection("Hooks");
  const titles = [...h.document.querySelectorAll(".settings-list-row")].map((r) => h.text(r));
  assert.ok(titles.some((t) => /permission_request/.test(t)));
  assert.ok(h.actions().includes("Open the file it comes from"));
  assert.ok(h.actions().includes("Add"));
  assert.ok(h.actions().includes("Remove hook"));
  assert.deepStrictEqual(h.consoleErrors, []);
});
