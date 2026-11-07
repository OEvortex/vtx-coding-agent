// Regression tests for the settings webview, run against the real bundle via the
// jsdom harness. Run with: npm test   (compiles the bundle first).
//
// These guard the two properties the surface is built on:
//   1. Scope is a tab, so a setting is rendered once, and the row still says
//      whether the active scope sets it or inherits it.
//   2. Simplifying the layout removed no control. Every action a section offered
//      is still reachable, and every config key still has a row.

const { after, test } = require("node:test");
const assert = require("node:assert");
const { createSettings: mountSettings } = require("./settings-harness");
const { DEFAULTS, GLOBAL_SET, FOLDER_SET } = require("./settings-fixture");

const settings = [];
const createSettings = (...args) => {
  const instance = mountSettings(...args);
  settings.push(instance);
  return instance;
};
after(() => settings.forEach(({ window }) => window.close()));

const SECTIONS = ["General", "Instructions", "Skills", "Plugins", "MCP Servers", "Hooks", "Permissions", "Advanced"];

// Every action that performs a distinct operation, per section. A missing entry
// here means a control was lost in a refactor.
const REQUIRED_ACTIONS = {
  General: ["Open it", "Clear it", "Open in VS Code settings"],
  Instructions: ["Edit instructions", "Remove instructions"],
  Skills: ["Add", "Edit SKILL.md", "Remove skill"],
  Plugins: ["Install", "Update plugin", "Remove plugin"],
  "MCP Servers": ["Add", "Log out", "Log in (OAuth)", "Enable", "Disable", "Edit config", "Remove"],
  Hooks: ["Add", "Edit source", "Remove hook"],
  Permissions: ["Add", "Remove Exec(git status)"],
  Advanced: ["Open this config file"]
};

test("a value of the wrong shape is shown, not left as a blank page", () => {
  // The panel renders what is in the file, and a hand edited config can hold a
  // string where a list belongs. Every other control coerces whatever it is given.
  const h = createSettings();
  const data = JSON.parse(JSON.stringify(h.data));
  for (const g of data.valuesByScope) {
    g.values["sandbox.allowed_domains"] = "github.com";
  }
  h.send({ type: "settings:data", data });
  h.openSection("Advanced");

  assert.ok(h.row("sandbox.allowed_domains"), "the row is still rendered");
  assert.strictEqual(h.control("sandbox.allowed_domains").value, "github.com", "with the value the file holds");
  assert.ok(h.rowKeys().length > 1, "and so is the rest of the section");

  // Search renders every section, so one bad value there took the results with it.
  h.search("domains");
  assert.ok(h.visibleRows().length > 0, "search still finds it");
  assert.deepStrictEqual(h.consoleErrors, [], "nothing threw");
});

test("the sidebar lists every section once", () => {
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
  assert.deepStrictEqual(
    seen.slice().sort(),
    Object.keys(DEFAULTS).sort(),
    "a config key lost its row, or is rendered in more than one section"
  );
  assert.deepStrictEqual(h.consoleErrors, []);
});

test("each section keeps every action it offered", () => {
  for (const s of SECTIONS) {
    const h = createSettings();
    h.openSection(s);
    const actions = h.actions();
    for (const want of REQUIRED_ACTIONS[s]) {
      assert.ok(actions.includes(want), `${s} lost the "${want}" action. Present: ${JSON.stringify(actions)}`);
    }
    assert.deepStrictEqual(h.consoleErrors, []);
  }
});

test("scope is chosen once, and each scope edits its own config file", () => {
  const h = createSettings();
  assert.deepStrictEqual(h.scopes(), ["Global", "Workspace"]);
  assert.strictEqual(h.activeScope(), "Global");
  assert.match(h.scopeFile(), /\.config\/devin\/config\.json$/);

  h.openScope("Workspace");
  assert.strictEqual(h.activeScope(), "Workspace");
  assert.match(h.scopeFile(), /web-app\/\.devin\/config\.json$/);
  // Switching scope tells the host, so project-scoped CLI verbs run in that folder.
  assert.strictEqual(h.last("settings:setRoot").path, h.data.folders[0].path);
});

test("a multi-root workspace gets one scope per folder, not a copy of every setting", () => {
  const h = createSettings({ multiRoot: true });
  assert.deepStrictEqual(h.scopes(), ["Global", "web-app", "api-service"]);
  // One row per key regardless of how many folders are open.
  assert.strictEqual(h.rowKeys().length, new Set(h.rowKeys()).size);

  h.openScope("api-service");
  assert.match(h.scopeFile(), /api-service\/\.devin\/config\.json$/);
  // That folder sets nothing, so nothing is marked as set there.
  assert.deepStrictEqual(h.rowKeys().filter(h.isSetHere), []);
});

test("a row says whether the active scope sets it or inherits it", () => {
  const h = createSettings();
  // Global scope: the keys the Global config sets are marked.
  assert.strictEqual(h.isSetHere("notify"), true);
  assert.strictEqual(h.isSetHere("attribution"), false);

  h.openScope("Workspace");
  // Workspace scope: only its own overrides are marked, the rest are inherited.
  assert.deepStrictEqual(h.rowKeys().filter(h.isSetHere).sort(), Object.keys(FOLDER_SET).sort());
  assert.ok(h.document.querySelector(".settings-inherited"), "the inherited legend should explain unmarked rows");
  // An inherited row shows the value that actually applies, not the bare default.
  assert.strictEqual(h.control("notify").value, GLOBAL_SET.notify);
});

test("a value the dropdown does not offer is still the value shown", () => {
  // A <select> with nothing matching shows its first option, so a `notify` written
  // by a newer CLI (or by hand) read as "Never", the most damaging thing that row
  // can say, while the dot beside it claimed the scope had set it deliberately.
  const h = createSettings();
  const data = JSON.parse(JSON.stringify(h.data));
  for (const g of data.valuesByScope) {
    g.values.notify = "on-completion";
  }
  h.send({ type: "settings:data", data });

  const control = h.control("notify");
  assert.strictEqual(control.value, "on-completion", "the row shows what the file holds");
  assert.ok(
    [...control.options].some((o) => o.value === "on-completion"),
    "carried as an option of its own, so choosing another is a real change"
  );
  assert.deepStrictEqual(h.consoleErrors, []);
});

test("the inherited legend is absent where nothing is inherited", () => {
  const h = createSettings();
  h.openScope("Workspace").openSection("Hooks");
  assert.strictEqual(h.document.querySelector(".settings-inherited"), null,
    "hooks are per scope, so the value-inheritance legend must not appear");
});

test("editing a value targets the active scope", () => {
  const h = createSettings();
  const toggle = h.control("attribution");
  toggle.checked = false;
  toggle.dispatchEvent(new h.window.Event("change"));
  assert.deepStrictEqual(h.last("settings:setPath"), {
    type: "settings:setPath", scope: "user", path: "attribution", value: false
  });

  h.openScope("Workspace");
  const t2 = h.control("attribution");
  t2.checked = false;
  t2.dispatchEvent(new h.window.Event("change"));
  const msg = h.last("settings:setPath");
  assert.strictEqual(msg.scope, "project");
  assert.strictEqual(msg.root, h.data.folders[0].path);
});

test("clearing an override deletes the key rather than writing a value", () => {
  const h = createSettings();
  h.openScope("Workspace").click("Clear override, use the Global value");
  const msg = h.last("settings:setPath");
  assert.strictEqual(msg.scope, "project");
  assert.strictEqual(msg.root, h.data.folders[0].path);
  assert.ok(Object.keys(FOLDER_SET).includes(msg.path));
  // No value at all means "remove this key", which is what falls back to Global.
  assert.ok(!("value" in msg), "clearing must not write a value: " + JSON.stringify(msg));
});

test("a group offers a reset only when the active scope sets something in it", () => {
  const h = createSettings();
  // The Global config sets notify and respect_gitignore, both in Behaviour.
  assert.ok(h.actions().includes("Reset Behaviour to defaults in Global"));
  // It sets nothing in Session, so there is nothing to reset.
  assert.ok(!h.actions().some((a) => a.startsWith("Reset Session")));

  h.click("Reset Behaviour to defaults in Global");
  const msg = h.last("settings:resetSection");
  assert.strictEqual(msg.scope, "user");
  assert.deepStrictEqual(msg.keys, [
    "attribution", "auto_update", "notify", "respect_gitignore", "include_gitignored_files", "show_hints"
  ]);
});

test("search matches rows across every section", () => {
  const h = createSettings();
  h.search("gitignore");
  const labels = h.visibleRows().map((r) => h.text(r.querySelector(".settings-field-label")));
  assert.deepStrictEqual(labels, ["Respect .gitignore for tool access", "Include gitignored files in @ completions"]);

  // A term from another section still reaches it, without changing section first.
  h.search("proxy");
  assert.ok(h.visibleRows().length >= 3, "expected the Advanced proxy rows");
  assert.ok(h.all(".settings-result-head").some((e) => h.text(e) === "Advanced"));

  h.search("zzzz-no-such-setting");
  assert.strictEqual(h.visibleRows().length, 0);
  assert.match(h.text(h.document.querySelector(".settings-empty")), /No settings match/);
  assert.deepStrictEqual(h.consoleErrors, []);
});

test("only CLI settings get a row; an extension setting that overrides one is a notice", () => {
  const h = createSettings();
  // No row of its own: the extension's model setting belongs to VS Code.
  assert.ok(!h.rowKeys().includes("devin.defaultModel"));
  // It overrides the CLI model, so the CLI row carries a warning about it.
  const notice = h.document.querySelector(".settings-notice");
  assert.ok(notice, "expected a notice about the VS Code setting overriding the model");
  assert.match(h.text(notice), /devin\.defaultModel/);
  assert.ok(h.row("agent.model").contains(notice), "the notice belongs to the model row");

  h.click("Open it");
  assert.deepStrictEqual(h.last(), { type: "settings:openExtensionSettings", query: "defaultModel" });
  h.click("Clear it");
  assert.strictEqual(h.last().type, "settings:clearExtensionModel");
});

test("the override notice is absent when there is no conflict to report", () => {
  const h = createSettings({ empty: true });
  assert.strictEqual(h.document.querySelector(".settings-notice"), null);
});

test("plugins are their own section, and offer no scope to choose", () => {
  const h = createSettings();
  h.openSection("Plugins");
  // The CLI installs plugins once for the machine, so a scope picker would lie.
  assert.deepStrictEqual(h.scopes(), []);
  assert.strictEqual(h.document.querySelector(".settings-scope-select"), null);
  assert.strictEqual(h.scopeFile(), "");
  assert.ok(h.actions().includes("Install"));
  // An absent picker must leave nothing behind (append() stringifies a null).
  assert.ok(!h.text(h.document.getElementById("settings-toolbar")).includes("null"));

  // Skills keeps its own scope, and no longer carries the plugin list.
  h.openSection("Skills");
  assert.deepStrictEqual(h.scopes(), ["Global", "Workspace"]);
  assert.ok(!h.actions().includes("Install"));
});

test("a workspace with several folders gets a picker, not a row of tabs", () => {
  const h = createSettings({ multiRoot: true });
  // Two scopes stay as tabs (one click); more would wrap into a wall of buttons.
  assert.ok(h.document.querySelector(".settings-scope-select"), "expected a scope picker");
  assert.strictEqual(h.document.querySelector(".settings-scope-btn"), null);

  // Global sits on its own and the folders are grouped, which is what gives the
  // list its heading and divider.
  const groups = h.all(".settings-scope-select optgroup");
  assert.deepStrictEqual(groups.map((g) => g.label), ["Workspace folder"]);
  assert.deepStrictEqual([...groups[0].children].map(h.text), ["web-app", "api-service"]);
  const sel = h.document.querySelector(".settings-scope-select");
  assert.strictEqual(sel.children[0].tagName, "OPTION", "Global should not be inside the group");
  assert.strictEqual(h.text(sel.children[0]), "Global");

  const picker = h.document.querySelector(".settings-scope-select");
  picker.value = h.data.folders[1].path;
  picker.dispatchEvent(new h.window.Event("change"));
  assert.strictEqual(h.last("settings:setRoot").path, h.data.folders[1].path);
  assert.match(h.scopeFile(), /api-service/);

  const single = createSettings();
  assert.ok(single.document.querySelector(".settings-scope-btn"), "one folder should stay as tabs");
  assert.strictEqual(single.document.querySelector(".settings-scope-select"), null);
});

test("there is no manual refresh, since the panel follows the files itself", () => {
  const h = createSettings();
  for (const s of SECTIONS) {
    h.openSection(s);
    assert.ok(!h.actions().includes("Refresh"), s + " should not offer a Refresh button");
  }
  assert.ok(!h.posted.some((m) => m.type === "settings:refresh"));
});

test("icon-only actions carry a tooltip, not just an accessible name", () => {
  const h = createSettings();
  h.openSection("MCP");
  const icons = h.all("#settings-content .settings-icon-btn");
  assert.ok(icons.length >= 4);
  for (const b of icons) {
    const tip = b.getAttribute("data-tip");
    assert.ok(tip, "an icon-only action needs a tooltip: " + b.outerHTML.slice(0, 80));
    // The tooltip and the accessible name must not drift apart.
    assert.strictEqual(tip, b.getAttribute("aria-label"));
    // The platform tooltip is replaced, not doubled up.
    assert.strictEqual(b.getAttribute("title"), null);
  }
});

test("moving between sections asks the host to re-read the files", () => {
  const h = createSettings();
  h.openSection("Skills");
  assert.ok(h.posted.some((m) => m.type === "settings:reload"), "expected a reload on section change");
  // Switching scope does the same, through the message that retargets the CLI.
  h.posted.length = 0;
  h.openScope("Workspace");
  assert.ok(h.posted.some((m) => m.type === "settings:setRoot"));
});

test("a typed value gets the full row width, a fixed-size control does not", () => {
  const h = createSettings();
  h.openSection("Advanced");
  // proxy.url is typed, so its row stacks; proxy.mode is a dropdown, so it does not.
  assert.ok(h.row("proxy.url").classList.contains("stacked"));
  assert.ok(!h.row("proxy.mode").classList.contains("stacked"));
  assert.ok(h.row("sandbox.allowed_domains").classList.contains("stacked"));
  h.openSection("General");
  assert.ok(!h.row("attribution").classList.contains("stacked"), "a toggle row should stay inline");
});

test("the hook form names its value field after the type, and fits the prompt", () => {
  const h = createSettings();
  h.openSection("Hooks").click("Add");
  const labels = () => h.all(".settings-modal .settings-field-label").map(h.text);
  const modal = () => h.document.querySelector(".settings-modal");

  assert.deepStrictEqual(labels(), ["Event", "Type", "Command", "Matcher", "Timeout"]);
  assert.ok(modal().querySelector("input[type=text]"));
  assert.strictEqual(modal().querySelector("textarea"), null);

  // Switching to a prompt renames the field and swaps in a multi-line control.
  const type = h.all(".settings-modal select")[1];
  type.value = "prompt";
  type.dispatchEvent(new h.window.Event("change"));
  assert.deepStrictEqual(labels(), ["Event", "Type", "Prompt", "Matcher", "Timeout"]);
  assert.ok(modal().querySelector("textarea"), "a prompt needs room for more than one line");

  // What was typed in each control survives switching between them.
  modal().querySelector("textarea").value = "Check the open pull requests.";
  type.value = "command";
  type.dispatchEvent(new h.window.Event("change"));
  type.value = "prompt";
  type.dispatchEvent(new h.window.Event("change"));
  assert.strictEqual(modal().querySelector("textarea").value, "Check the open pull requests.");

  h.click("Add hook");
  const msg = h.last("settings:addHook");
  assert.strictEqual(msg.hookType, "prompt");
  assert.strictEqual(msg.value, "Check the open pull requests.");
});

test("a disabled MCP server reads as switched off", () => {
  const h = createSettings();
  h.openSection("MCP");
  const rows = h.all(".settings-list-row");
  const off = rows.filter((r) => r.classList.contains("disabled"));
  assert.strictEqual(off.length, 1, "only the disabled server should be marked");
  assert.strictEqual(h.text(off[0].querySelector(".settings-list-name")), "issue-tracker");
  // The name is its own element so the strike-through misses the tags.
  assert.ok(off[0].querySelector(".settings-list-title .settings-tag"));
  // An enabled server carries no state class.
  const on = rows.find((r) => h.text(r.querySelector(".settings-list-name")) === "github");
  assert.ok(!on.classList.contains("disabled"));
});

test("a server Windsurf owns is listed apart, and managed in place", () => {
  const h = createSettings();
  h.openSection("MCP");
  const titles = h.all(".settings-group-title").map((t) => h.text(t));
  assert.ok(titles.includes("MCP servers from Windsurf"), "expected a group of its own: " + titles.join(", "));

  // Vtx's own servers stay in the first group, Windsurf's in the second.
  const groups = h.all("#settings-content .settings-group");
  const wind = groups.find((g) => h.text(g.querySelector(".settings-group-title")) === "MCP servers from Windsurf");
  const names = [...wind.querySelectorAll(".settings-list-name")].map((n) => h.text(n));
  assert.deepStrictEqual(names, ["godot-ai"]);
  const own = groups.find((g) => h.text(g.querySelector(".settings-group-title")) === "MCP servers");
  assert.ok(![...own.querySelectorAll(".settings-list-name")].some((n) => h.text(n) === "godot-ai"));

  // The CLI cannot write another tool's config, so the row says where it belongs
  // and the host writes the file itself.
  const disable = [...wind.querySelectorAll(".settings-icon-btn")].find((b) => b.getAttribute("aria-label") === "Disable");
  disable.click();
  const msg = h.last("settings:mcpVerb");
  assert.strictEqual(msg.source, "windsurf", "without this the CLI would be asked, and would fail");
  assert.strictEqual(msg.name, "godot-ai");
  assert.strictEqual(msg.verb, "disable");
});

test("an action that hands work to the host shows itself running", () => {
  const h = createSettings();
  h.openSection("MCP");
  const disable = h.all("#settings-content .settings-icon-btn").find((b) => b.getAttribute("aria-label") === "Disable");
  disable.click();
  assert.ok(h.last("settings:mcpVerb"), "the verb should have been sent");
  assert.ok(disable.classList.contains("busy"), "the button should show the work running");
  assert.strictEqual(disable.disabled, true, "and take no further clicks, so it cannot fire twice");
  assert.ok(disable.querySelector(".codicon-loading"), "expected a spinner in place of the icon");

  // Answering with nothing changed (a declined confirmation) still releases it.
  h.send({ type: "settings:idle" });
  assert.ok(!disable.classList.contains("busy"));
  assert.strictEqual(disable.disabled, false);
  assert.ok(!disable.querySelector(".codicon-loading"));
});

test("every action the panel counts as work is answered by the host", () => {
  // The panel and the host each keep a list of what counts as work, and they
  // disagreed: the panel treated an OAuth login as work and the host answered only
  // for writes, so the key button span for fifteen seconds until a timer gave up.
  // The host now answers every message, so the two lists cannot drift apart again.
  const h = createSettings();
  h.openSection("MCP");
  const login = h.all("#settings-content .settings-icon-btn").find((b) => b.getAttribute("aria-label") === "Log in (OAuth)");
  assert.ok(login, "the OAuth login action is there");
  login.click();
  assert.ok(h.last("settings:mcpLogin"), "it hands the work to the host");
  assert.ok(login.classList.contains("busy"), "and shows itself running");

  // What the host now sends for every message, whether or not it wrote anything.
  h.send({ type: "settings:idle" });
  assert.ok(!login.classList.contains("busy"), "so the button is released");
  assert.strictEqual(login.disabled, false);
});

test("every kind of control shows it, and only when work was really started", () => {
  const h = createSettings();
  // A toggle writes, so it shows the write running.
  const box = h.control("attribution");
  box.checked = false;
  box.dispatchEvent(new h.window.Event("change"));
  assert.ok(h.last("settings:setPath"));
  assert.ok(box.closest(".settings-toggle").classList.contains("busy"));

  // Removing a permission chip is a write too.
  const h2 = createSettings();
  h2.openSection("Permissions");
  const x = h2.all(".settings-perm-x")[0];
  x.click();
  assert.ok(h2.last("settings:permission"));
  assert.ok(x.classList.contains("busy"));

  // Opening a form starts nothing, so its button must not sit there spinning.
  const h3 = createSettings();
  h3.openSection("Skills");
  const add = h3.all("#settings-content .settings-btn").find((b) => h3.text(b) === "Add");
  add.click();
  assert.ok(!add.classList.contains("busy"));
  assert.strictEqual(h3.document.querySelector(".settings-modal .codicon-loading"), null);
});

test("a submitted form waits for the host before closing", () => {
  const h = createSettings();
  h.openSection("Skills").click("Add");
  const name = h.document.querySelector(".settings-modal input[type=text]");
  name.value = "release-notes";
  h.click("Create skill");

  assert.strictEqual(h.last().type, "settings:createSkill");
  // Still open, with the submit button showing the work, so the result lands
  // somewhere the user is still looking.
  const modal = h.document.querySelector(".settings-modal");
  assert.ok(modal, "the form should stay open until the host answers");
  assert.ok(modal.querySelector(".codicon-loading"));

  h.send({ type: "settings:data", data: h.data });
  assert.strictEqual(h.document.querySelector(".settings-modal"), null, "and close once it has");
});

test("re-entering a text field without editing it writes nothing", () => {
  const h = createSettings();
  h.openSection("Advanced");
  const url = h.row("proxy.url").querySelector("input");
  url.dispatchEvent(new h.window.Event("blur"));
  assert.strictEqual(h.last("settings:setPath"), undefined, "an unchanged blur must not write");
  assert.ok(!url.classList.contains("busy"));

  url.value = "http://proxy.internal:3128";
  url.dispatchEvent(new h.window.Event("blur"));
  assert.strictEqual(h.last("settings:setPath").value, "http://proxy.internal:3128");
  assert.ok(url.classList.contains("busy"));
});

// --- What is actually in force ---------------------------------------------
// The panel can only edit one instructions file and one hooks file per scope, but
// the CLI loads rules and hooks from several other places, including plugins and
// other tools' config. Reporting only the files it can edit misrepresents what the
// agent is actually running under, so the CLI's own list is shown beside them.

test("the instructions section reports every rule the agent has loaded", () => {
  const h = createSettings();
  h.openSection("Instructions");
  const rows = h.document.querySelectorAll(".settings-list-row");
  const text = [...rows].map((r) => h.text(r.querySelector(".settings-list-title"))).join(" | ");
  // A Windsurf rule and a plugin's own AGENTS.md are in force, and neither is the
  // editable file above: scanning for known filenames would miss the plugin one.
  assert.match(text, /global_rules/, "a rule from another tool is still a rule");
  assert.match(text, /\.windsurf/, "and it says where it came from");
  assert.strictEqual([...rows].filter((r) => /AGENTS/.test(h.text(r))).length >= 3, true);
  // Each one opens the file it names, since that is where it is edited.
  assert.ok(h.actions().includes("Open this file"));
  assert.deepStrictEqual(h.consoleErrors, []);
});

test("the hooks section reports the hooks the agent has loaded", () => {
  const h = createSettings();
  h.openSection("Hooks");
  const titles = [...h.document.querySelectorAll(".settings-list-row")].map((r) => h.text(r));
  assert.ok(titles.some((t) => /permission_request/.test(t)), "a hook in force is listed even if this panel does not own its file");
  assert.ok(h.actions().includes("Open the file it comes from"));
  // The editable list is still there, with its own controls.
  assert.ok(h.actions().includes("Add"));
  assert.ok(h.actions().includes("Remove hook"));
  assert.deepStrictEqual(h.consoleErrors, []);
});

test("when the CLI cannot be asked, the panel claims nothing", () => {
  // `loaded` is undefined when the query agent could not be started. Saying "no
  // rules are loaded" would then be a lie, so the section is simply absent.
  const h = createSettings({ empty: true });
  h.openSection("Instructions");
  const groups = [...h.document.querySelectorAll(".settings-group-title")].map((e) => h.text(e));
  assert.ok(!groups.includes("In force now") || h.document.querySelector(".settings-empty"),
    "either it reports a real list, or it says nothing at all");
  assert.deepStrictEqual(h.consoleErrors, []);
});

test("with nothing configured, every section still offers a way in", () => {
  const h = createSettings({ empty: true });
  h.openSection("Instructions");
  assert.ok(h.actions().includes("Create AGENTS.md"));
  assert.match(h.scopeFile(), /^Not created yet: /);
  assert.ok(h.actions().includes("Create this config file"));

  for (const s of ["Skills", "MCP Servers", "Hooks"]) {
    h.openSection(s);
    assert.ok(h.actions().includes("Add"), s + " must still offer Add when empty");
    assert.ok(h.text(h.document.querySelector(".settings-empty")).length > 0, s + " needs an empty state");
  }
  assert.deepStrictEqual(h.consoleErrors, []);
});
