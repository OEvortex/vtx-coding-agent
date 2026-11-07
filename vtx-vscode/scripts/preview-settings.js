// Generates a self-contained HTML page that renders the real settings webview
// (media/main.css + dist/settings.js + media/settings-body.html) outside VS Code,
// driven by a mock host payload. Open it in a browser (or Playwright) to see and
// iterate on the settings surface without a build-install-reload loop.
//
//   npm run compile && node scripts/preview-settings.js
//   # then: playwright-cli open "file://.../scripts/.preview/settings.html"
//
// Flags:
//   --section <id>   which section to open (default: general)
//   --multi-root     render two workspace folders, so the scope tabs show both
//   --empty          render with nothing configured, to check the empty states
//
// The mock payload comes from settings-fixture.js, which the tests use too.

const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");
const { buildData } = require("./settings-fixture");

const ROOT = path.resolve(__dirname, "..");
const OUT_DIR = path.join(ROOT, "scripts", ".preview");
const MEDIA = path.join(ROOT, "media");

// VS Code "Dark Modern"-ish tokens, enough for the settings panel to look right.
const VARS = {
  "font-family": "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
  "font-size": "13px",
  "editor-font-family": "'SF Mono', Menlo, Monaco, 'Courier New', monospace",
  foreground: "#cccccc",
  "editor-background": "#1f1f1f",
  "sideBar-background": "#181818",
  "panel-border": "#2b2b2b",
  "editorWidget-background": "#202020",
  "widget-border": "#2b2b2b",
  "list-hoverBackground": "#2a2d2e",
  "toolbar-hoverBackground": "#2a2d2e",
  "list-activeSelectionBackground": "#04395e",
  "list-activeSelectionForeground": "#ffffff",
  "input-foreground": "#cccccc",
  "input-background": "#313131",
  "input-border": "#3c3c3c",
  "input-placeholderForeground": "#8b8b8b",
  // Dark Modern's dropdown tokens, so the pickers preview in their real colours.
  "dropdown-background": "#313131",
  "dropdown-foreground": "#cccccc",
  "dropdown-border": "#3c3c3c",
  "dropdown-listBackground": "#1f1f1f",
  "settings-dropdownBackground": "#313131",
  "settings-dropdownForeground": "#cccccc",
  "settings-dropdownBorder": "#3c3c3c",
  "editorHoverWidget-background": "#202020",
  "editorHoverWidget-border": "#454545",
  "editorHoverWidget-foreground": "#cccccc",
  "button-background": "#0078d4",
  "button-foreground": "#ffffff",
  "button-secondaryBackground": "#313131",
  "button-secondaryForeground": "#cccccc",
  "badge-background": "rgba(127,127,127,0.22)",
  "badge-foreground": "#cccccc",
  "focusBorder": "#0078d4",
  "descriptionForeground": "#9d9d9d",
  "charts-blue": "#4daafc",
  "editorWarning-foreground": "#cca700",
  "inputValidation-errorBackground": "#5a1d1d",
  "inputValidation-errorBorder": "#be1100",
  "icon-foreground": "#c5c5c5"
};
const rootVars = Object.entries(VARS).map(([k, v]) => `  --vscode-${k}: ${v};`).join("\n");

const args = process.argv.slice(2);
const flag = (name) => args.indexOf(name) >= 0;
const opt = (name, fallback) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback;
};
const section = opt("--section", "general");
const multiRoot = flag("--multi-root");
const bare = flag("--empty");

const DATA = buildData({ multiRoot, empty: bare });

// Reference each asset with its modification time, so a browser reload always
// picks up the stylesheet and bundle just built rather than a cached copy.
function asset(file) {
  // An href is a URL, not a path, so it keeps forward slashes on Windows too.
  const rel = path.relative(OUT_DIR, file).split(path.sep).join("/");
  const stamp = fs.existsSync(file) ? fs.statSync(file).mtimeMs : 0;
  return rel + "?v=" + Math.round(stamp);
}
const cssMain = asset(path.join(MEDIA, "main.css"));
const cssCodicon = asset(path.join(MEDIA, "codicon", "codicon.css"));
const bundle = asset(path.join(ROOT, "dist", "settings.js"));
const body = fs.readFileSync(path.join(MEDIA, "settings-body.html"), "utf8");

const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<style>
:root {
${rootVars}
}
html, body { height: 100%; margin: 0; font-family: var(--vscode-font-family); font-size: var(--vscode-font-size); }
</style>
<link rel="stylesheet" href="${cssCodicon}" />
<link rel="stylesheet" href="${cssMain}" />
</head>
<body class="settings-body vscode-dark" data-nonce="preview">
${body}
<script>
  // Mock the VS Code webview API before the bundle loads.
  window.__posted = [];
  window.acquireVsCodeApi = () => ({ postMessage: (m) => window.__posted.push(m), getState: () => undefined, setState: () => {} });
</script>
<script src="${bundle}"></script>
<script>
  window.dispatchEvent(new MessageEvent("message", { data: { type: "settings:data", data: ${JSON.stringify(DATA)} } }));
  // Open the requested section by clicking its nav item, the way a user would.
  const want = ${JSON.stringify(section.toLowerCase())};
  for (const label of document.querySelectorAll("#settings-nav .settings-nav-item span")) {
    if (label.textContent.toLowerCase().indexOf(want) === 0) { label.parentElement.click(); break; }
  }
</script>
</body>
</html>`;

fs.mkdirSync(OUT_DIR, { recursive: true });
const outFile = path.join(OUT_DIR, "settings.html");
fs.writeFileSync(outFile, html, "utf8");
console.log("Preview written to:", outFile);
console.log("Open with: playwright-cli open \"" + pathToFileURL(outFile).href + "\"");
