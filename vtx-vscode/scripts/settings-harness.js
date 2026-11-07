// Headless harness for the settings webview (webview/settings.js, bundled to
// dist/settings.js). It mounts the real panel DOM (media/settings-body.html) in
// jsdom, loads the bundle, feeds it the mock host payload from
// settings-fixture.js, and lets you drive the UI and inspect what it posts back.
//
// Use it two ways:
//   - as a module in tests: const { createSettings } = require("./settings-harness")
//   - directly for a quick look:  node scripts/settings-harness.js
//
// Requires a current bundle, so run `npm run compile` first (the test script
// does this automatically).

const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");
const { buildData } = require("./settings-fixture");

const ROOT = path.resolve(__dirname, "..");

function createSettings(opts) {
  const bundlePath = path.join(ROOT, "dist", "settings.js");
  if (!fs.existsSync(bundlePath)) {
    throw new Error("dist/settings.js not found. Run `npm run compile` first.");
  }
  const body = fs.readFileSync(path.join(ROOT, "media", "settings-body.html"), "utf8");
  const bundle = fs.readFileSync(bundlePath, "utf8");

  const consoleErrors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("error", (...args) => consoleErrors.push(args.join(" ")));
  virtualConsole.on("jsdomError", (err) => consoleErrors.push(String(err)));

  const dom = new JSDOM(
    `<!DOCTYPE html><html><head></head><body class="settings-body">${body}</body></html>`,
    { runScripts: "outside-only", pretendToBeVisual: true, virtualConsole }
  );
  const { window } = dom;
  const document = window.document;

  const posted = [];
  window.acquireVsCodeApi = () => ({
    // The real postMessage structured-clones to the host, which drops undefined
    // values. JSON round-trip mirrors that, so a "delete this key" message looks
    // the same here as it does in the extension.
    postMessage: (m) => posted.push(JSON.parse(JSON.stringify(m))),
    getState: () => undefined,
    setState: () => {}
  });

  // The bundle is an IIFE; running it via the window's own Function constructor
  // gives it the jsdom realm as its global.
  new window.Function(bundle).call(window);

  const data = buildData(opts);
  window.dispatchEvent(new window.MessageEvent("message", { data: { type: "settings:data", data } }));

  const all = (sel) => [...document.querySelectorAll(sel)];
  const text = (e) => (e ? e.textContent.trim() : "");
  const visibleRows = () => all(".settings-field:not(.settings-hidden), .settings-list-row:not(.settings-hidden)");

  const api = {
    window,
    document,
    data,
    posted,
    consoleErrors,
    all,
    text,
    visibleRows,
    // Send a message to the webview, the way the extension host does.
    send(msg) {
      window.dispatchEvent(new window.MessageEvent("message", { data: msg }));
      return api;
    },
    // Section names in the sidebar, in order.
    sections: () => all("#settings-nav .settings-nav-item").map(text),
    activeSection: () => text(document.querySelector(".settings-nav-item.active")),
    // Scope labels in order, from whichever control the toolbar chose (tabs for
    // two scopes, a picker for more). Empty on a section with no scope to choose.
    scopes: () => {
      const sel = document.querySelector(".settings-scope-select");
      if (sel) return [...sel.options].map((o) => o.textContent.trim());
      return all(".settings-scope-btn").map(text);
    },
    activeScope: () => {
      const sel = document.querySelector(".settings-scope-select");
      if (sel) return text([...sel.options].find((o) => o.selected));
      return text(document.querySelector(".settings-scope-btn.active"));
    },
    // The config file line under the toolbar.
    scopeFile: () => text(document.querySelector(".settings-scope-file-path")),
    openSection(label) {
      const item = all("#settings-nav .settings-nav-item").find((b) => text(b).toLowerCase().startsWith(label.toLowerCase()));
      if (!item) throw new Error("no such section: " + label);
      item.click();
      return api;
    },
    openScope(label) {
      const sel = document.querySelector(".settings-scope-select");
      if (sel) {
        const opt = [...sel.options].find((o) => o.textContent.trim() === label);
        if (!opt) throw new Error("no such scope: " + label);
        sel.value = opt.value;
        sel.dispatchEvent(new window.Event("change"));
        return api;
      }
      const tab = all(".settings-scope-btn").find((b) => text(b) === label);
      if (!tab) throw new Error("no such scope: " + label);
      tab.click();
      return api;
    },
    search(q) {
      const input = document.querySelector(".settings-search");
      input.value = q;
      input.dispatchEvent(new window.Event("input"));
      return api;
    },
    // The row for one config key, and how it is marked.
    row: (key) => document.querySelector('[data-key="' + key + '"]'),
    rowKeys: () => all("[data-key]").map((e) => e.getAttribute("data-key")),
    isSetHere: (key) => !!(api.row(key) && api.row(key).querySelector(".settings-dot")),
    // Every action a section offers, by accessible name, for a no-control-lost guard.
    actions: () => all("#settings-content button").map((b) => b.getAttribute("aria-label") || text(b)).filter(Boolean),
    // Click an action by its accessible name (tooltip or label).
    click(name) {
      const b = all("#settings-content button, .settings-modal button").find(
        (x) => (x.getAttribute("aria-label") || text(x)) === name
      );
      if (!b) throw new Error("no such action: " + name);
      b.click();
      return api;
    },
    // The last message posted to the host, or the last of a given type.
    last: (type) => [...posted].reverse().find((m) => !type || m.type === type),
    control: (key) => api.row(key) && api.row(key).querySelector("input, select")
  };
  return api;
}

module.exports = { createSettings };

// Quick manual look when run directly.
if (require.main === module) {
  const h = createSettings();
  console.log("sections :", h.sections().join(", "));
  console.log("scopes   :", h.scopes().join(", "));
  console.log("file     :", h.scopeFile());
  console.log("keys     :", h.rowKeys().length, "value rows on", h.activeSection());
  h.openScope("Workspace");
  console.log("workspace:", h.scopeFile());
  console.log("set here :", h.rowKeys().filter(h.isSetHere).join(", "));
}
