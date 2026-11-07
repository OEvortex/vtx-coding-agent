// Generates a self-contained HTML page that renders the real chat webview
// (media/main.css + dist/webview.js + media/webview-body.html) outside VS Code,
// driven by a mock host scenario. Open it in a browser (or Playwright) to see
// and iterate on the UI without a build-install-reload loop.
//
//   npm run compile && node scripts/preview.js
//   # then: playwright-cli open "file://.../scripts/.preview/index.html"
//
// A --scenario flag selects which mock conversation to render (default: full),
// and --width sets the panel width in pixels (default 460, the narrow sidebar).
// The README screenshots are captured at a wider one, see docs/screenshots.md.
// --surface editor renders the page as a chat tab (one chat, no session list),
// which is what a detached chat looks like.

const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");

// An href is a URL, not a path, so it keeps forward slashes on Windows too, and
// it carries the file's modification time so a reload picks up the stylesheet or
// bundle just built rather than a cached copy.
const href = (from, to) => {
  const rel = path.relative(from, to).split(path.sep).join("/");
  const stamp = fs.existsSync(to) ? fs.statSync(to).mtimeMs : 0;
  return rel + "?v=" + Math.round(stamp);
};

const ROOT = path.resolve(__dirname, "..");
const OUT_DIR = path.join(ROOT, "scripts", ".preview");
const MEDIA = path.join(ROOT, "media");

const widthIdx = process.argv.indexOf("--width");
const panelWidth = widthIdx >= 0 ? Number(process.argv[widthIdx + 1]) || 460 : 460;
const surfaceIdx = process.argv.indexOf("--surface");
const surface = surfaceIdx >= 0 && process.argv[surfaceIdx + 1] === "editor" ? "editor" : "view";

// VS Code "Dark Modern"-ish tokens, enough for the chat panel to look right.
const VARS = {
  "font-family": "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
  "font-size": "13px",
  "editor-font-family": "'SF Mono', Menlo, Monaco, 'Courier New', monospace",
  foreground: "#cccccc",
  "editor-background": "#1f1f1f",
  "sideBar-background": "#181818",
  "panel-border": "#2b2b2b",
  "editorWidget-background": "#202020",
  "list-hoverBackground": "#2a2d2e",
  "toolbar-hoverBackground": "#2a2d2e",
  "input-foreground": "#cccccc",
  "input-background": "#313131",
  "input-border": "#3c3c3c",
  "dropdown-background": "#313131",
  "textLink-foreground": "#4daafc",
  "textCodeBlock-background": "#2b2b2b",
  "progressBar-background": "#0078d4",
  "testing-iconPassed": "#3fb950",
  "testing-iconFailed": "#f85149",
  "errorForeground": "#f85149",
  "chat-requestBubbleBackground": "#2b2b2b",
  "focusBorder": "#0078d4",
  "descriptionForeground": "#9d9d9d",
  "button-background": "#0078d4",
  "button-foreground": "#ffffff",
  "button-hoverBackground": "#026ec1",
  "button-secondaryBackground": "#313131",
  "button-secondaryForeground": "#cccccc",
  "button-secondaryHoverBackground": "#3c3c3c",
  "chat-requestBorder": "#3c3c3c",
  "chat-requestBackground": "#1a1a1a",
  "chat-thinkingShimmer": "#e6e6e6",
  "textPreformat-foreground": "#d4d4d4",
  "textPreformat-background": "rgba(127,127,127,0.18)",
  "textPreformat-border": "rgba(127,127,127,0.22)",
  "textBlockQuote-background": "rgba(127,127,127,0.08)",
  "textBlockQuote-border": "#3c3c3c",
  "badge-background": "rgba(127,127,127,0.22)",
  "list-hoverBackground": "#2a2d2e",
  "list-inactiveSelectionBackground": "#37373d",
  "list-inactiveSelectionForeground": "#cccccc",
  "list-activeSelectionBackground": "#04395e",
  "list-activeSelectionForeground": "#ffffff",
  "input-placeholderForeground": "#8b8b8b",
  "panel-background": "#181818",
  "editorWarning-foreground": "#cca700",
  "icon-foreground": "#c5c5c5"
};

const rootVars = Object.entries(VARS)
  .map(([k, v]) => `  --vscode-${k}: ${v};`)
  .join("\n");

// Shared composer options so the model / mode pickers and the context ring
// look populated, the way they do against a real CLI.
const MODES = [
  { value: "accept-edits", name: "Code" },
  { value: "ask", name: "Ask" },
  { value: "plan", name: "Plan" },
  { value: "bypass", name: "Bypass" }
];
const MODELS = [
  { id: "adaptive", name: "Adaptive", default: "adaptive", variants: [{ value: "adaptive", name: "Adaptive" }] },
  { id: "claude", name: "Claude Sonnet 4.5", default: "claude-sonnet-4-5", variants: [{ value: "claude-sonnet-4-5", name: "Sonnet" }, { value: "claude-opus-4-5", name: "Opus" }] },
  { id: "gpt", name: "GPT-5", default: "gpt-5", variants: [{ value: "gpt-5", name: "GPT-5" }] }
];
const options = (currentModel, currentMode) => ({ type: "options", modes: MODES, currentMode: currentMode || "accept-edits", models: MODELS, currentModel: currentModel || "adaptive" });

// A rich conversation exercising text, thinking, plan, tool cards, file rows,
// a follow-up message, plus a permission prompt and an interactive question.
const SCENARIOS = {
  full: [
    { type: "ready" },
    { type: "body", body: "thread" },
    { type: "capabilities", root: "/Users/dev/Projects/web-app", revert: true, editRequests: "inline", checkpoints: true, showFileChanges: true, contextUsage: true, sessionShare: true },
    options("adaptive"),
    { type: "userMessage", text: "Refactor the auth module to centralise token handling, then run the tests.",
      attachments: [{ label: "token-service.ts", type: "file" },
        { label: "Screenshot 2026-02-14.png", type: "image", thumb: "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0MCIgaGVpZ2h0PSI0MCI+PHJlY3Qgd2lkdGg9IjQwIiBoZWlnaHQ9IjQwIiBmaWxsPSIjMzc3ZGZmIi8+PC9zdmc+" }] },
    { type: "busy", value: true },
    { type: "assistantStart" },
    { type: "thoughtChunk", text: "They want a refactor plus a test run, so I should start by understanding the current shape of the auth module.\n\nToken creation and refresh look tangled together in `token.ts`, so pulling them into a dedicated `TokenService` would isolate the logic and make it testable.\n\nOnce the callers are updated I can run the suite to confirm nothing regressed." },
    { type: "assistantChunk", text: "Sure. Here's the plan, then I'll get started." },
    { type: "plan", entries: [
      { content: "Read the current auth module", status: "completed" },
      { content: "Extract token handling into TokenService", status: "in_progress" },
      { content: "Run the test suite", status: "pending" }
    ] },
    { type: "toolCall", id: "t1", title: "Read src/auth/token.ts", kind: "read", status: "completed", locations: [{ path: "src/auth/token.ts", line: 42 }] },
    { type: "fileChange", path: "src/auth/token-service.ts", added: 34, removed: 6, created: true },
    { type: "thoughtChunk", text: "The callers still import the old helpers, so those need updating before the tests will pass.", messageId: "t-mid" },
    { type: "fileChange", path: "src/auth/session.ts", added: 4, removed: 2 },
    { type: "toolCall", id: "t2", title: "Edit src/auth/token-service.ts", kind: "edit", status: "completed", rawInput: { path: "src/auth/token-service.ts" }, content: [{ type: "diff", path: "src/auth/token-service.ts", added: 34, removed: 6 }] },
    { type: "toolCall", id: "t3g", title: "Find files", kind: "search", status: "completed",
      rawInput: { query: "src/**/*.ts" }, content: [{ type: "link", path: "/Users/dev/Projects/web-app/src/acp/client.ts" }, { type: "link", path: "/Users/dev/Projects/web-app/src/acp/connection.ts" }, { type: "link", path: "/Users/dev/Projects/web-app/src/acp/terminal.ts" }, { type: "link", path: "/Users/dev/Projects/web-app/src/acp/types.ts" }, { type: "link", path: "/Users/dev/Projects/web-app/src/chat/chatManager.ts" }, { type: "link", path: "/Users/dev/Projects/web-app/src/chat/chatViewProvider.ts" }, { type: "link", path: "/Users/dev/Projects/web-app/src/chat/transcriptLog.ts" }, { type: "link", path: "/Users/dev/Projects/web-app/src/cli/locate.ts" }, { type: "link", path: "/Users/dev/Projects/web-app/src/cli/models.ts" }, { type: "link", path: "/Users/dev/Projects/web-app/src/cli/reaper.ts" }, { type: "link", path: "/Users/dev/Projects/web-app/src/cli/sessionLocks.ts" }, { type: "link", path: "/Users/dev/Projects/web-app/src/diff/changeTracker.ts" }, { type: "link", path: "/Users/dev/Projects/web-app/src/diff/diffStat.ts" }, { type: "link", path: "/Users/dev/Projects/web-app/src/extension.ts" }, { type: "link", path: "/Users/dev/Projects/web-app/src/session/sessionList.ts" }, { type: "link", path: "/Users/dev/Projects/web-app/src/session/sessionStore.ts" }, { type: "link", path: "/Users/dev/Projects/web-app/src/ui/statusBar.ts" }] },
    { type: "toolCall", id: "t3h", title: "Run a script", kind: "execute", status: "completed",
      rawInput: { command: "cd /Users/dev/Projects/web-app && python3 - <<'PY'\nimport re\np='src/auth/token.ts'\ns=open(p).read()\nopen(p,'w').write(s)\nPY" },
      content: [{ type: "text", text: "Exited with code 0" }] },
    { type: "toolCall", id: "t3d", title: "Run npm run dev", kind: "execute", status: "in_progress",
      rawInput: { command: "npm run dev" }, terminalId: "term-dev" },
    { type: "terminalOutput", terminalId: "term-dev", output: "ready on http://localhost:3000\n", integrated: true },
    { type: "toolCall", id: "t3", title: "Run npm test", kind: "execute", status: "completed",
      rawInput: { command: "npm test --workspace=packages/auth -- --coverage --runInBand --reporters=default --reporters=jest-junit" },
      content: [{ type: "text", text: "PASS  auth.test.ts\n  \u2713 issues a token (12 ms)\n  \u2713 rejects an expired token (4 ms)\n\nTests: 2 passed, 2 total" }] },
    { type: "toolCall", id: "t3b", title: "Capture browser screenshot", kind: "other", status: "completed",
      content: [{ type: "image", mime: "image/png", data: "iVBORw0KGgoAAAANSUhEUgAAAEAAAAAgCAYAAACinX6EAAAAT0lEQVR4nO3OsQ2AMAwF0P9DKGgo2H9DKGgoWCkFDYUUKQUFhZ+ku5NlyRIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB8dwFmFwGVAAAAAElFTkSuQmCC" }] },
    { type: "toolCall", id: "t4", title: "Checked on subagent", kind: "other", status: "completed",
      meta: { inferenceToolName: "read_subagent" }, rawInput: { agent_id: "78cc5558", block: true, timeout: 600 },
      content: [{ type: "text", text: "The explore agent mapped every session write path." }] },
    { type: "fileChange", path: "src/auth/token-service.ts", added: 34, removed: 6 },
    { type: "assistantChunk", text: "All tests pass. Token handling now lives in [src/auth/token-service.ts](src/auth/token-service.ts), and the callers were updated to use it. Anything else you'd like adjusted?" },
    { type: "assistantEnd" },
    // The revert step the agent pushes after a turn, which is what pins a
    // checkpoint (and a fork) to the turn that follows it.
    { type: "turnHead", head: 31, reliable: true },
    { type: "userMessage", text: "Yes, push it." },
    { type: "assistantStart" },
    { type: "assistantChunk", text: "I'll push the branch. It needs your approval to run `git`." },
    { type: "assistantEnd" },
    { type: "busy", value: false },
    { type: "usage", used: 41200, size: 200000, cost: 0.09 },
    // The six options a real agent sends for one shell command: allow once, three
    // widening grants, a mode switch, and reject. Only the first and the last are
    // buttons; the rest sit behind the chevron.
    { type: "permission", requestId: "p1", title: "Devin wants to run a command", command: "git push origin main", options: [
      { optionId: "allow_once", name: "Allow", kind: "allow_once" },
      { optionId: "allow_session", name: "Yes, allow `git` commands (this session)", kind: "allow_always" },
      { optionId: "allow_always", name: "Yes, always allow `git` commands in `web-app`", kind: "allow_always" },
      { optionId: "allow_always_global", name: "Yes, always allow `git` commands in all projects", kind: "allow_always" },
      { optionId: "switch_bypass", name: "Yes, switch to bypass mode", kind: "allow_always" },
      { optionId: "reject_once", name: "Reject", kind: "reject_once" }
    ] },
    { type: "elicitation", requestId: "e1", mode: "form", message: "A couple of quick questions", allowOther: true, schema: {
      type: "object", required: ["q0", "q1"], properties: {
        q0: { type: "string", title: "Next step", description: "What should I do next?", oneOf: [
          { const: "docs", title: "Update the docs" },
          { const: "pr", title: "Open a pull request" },
          { const: "nothing", title: "Nothing for now" }
        ] },
        q1: { type: "string", title: "Target branch", description: "Where should the change land?", oneOf: [
          { const: "main", title: "main" },
          { const: "develop", title: "develop" }
        ] }
      }
    } }
  ],
  // Pending state: after send, before the first token arrives.
  working: [
    { type: "ready" },
    { type: "body", body: "thread" },
    { type: "capabilities", revert: true, editRequests: "inline", checkpoints: true },
    { type: "sessionReady", sessionId: "s1" },
    { type: "userMessage", text: "Refactor the auth module and run the tests." },
    { type: "assistantStart" },
    { type: "busy", value: true }
  ],
  // Exercises the newer rendering: web search / fetch / MCP tools, a grouped
  // tool run, and a mermaid diagram.
  tools: [
    { type: "ready" },
    { type: "body", body: "thread" },
    { type: "capabilities", revert: true, contextUsage: true },
    options("gpt-5"),
    { type: "userMessage", text: "Research the release process and draw the flow." },
    { type: "busy", value: true },
    { type: "assistantStart" },
    { type: "toolCall", id: "s1", title: "Searched web for release checklist", kind: "fetch", meta: { inferenceToolName: "web_search" }, status: "completed", rawInput: { query: "software release checklist best practices" }, content: [{ type: "text", text: 'Found 5 result(s) for "software release checklist best practices"' }] },
    { type: "toolCall", id: "f1", title: "Fetched https://semver.org", kind: "fetch", meta: { inferenceToolName: "webfetch" }, status: "completed", rawInput: { url: "https://semver.org" }, content: [{ type: "text", text: "Fetched 4210 characters from https://semver.org" }] },
    { type: "toolCall", id: "m1", title: "Calling get_current_time from time", meta: { eventType: "mcp_tool_call", toolName: "mcp__time__get_current_time", inferenceToolName: "mcp__time__get_current_time" }, status: "completed", rawInput: { timezone: "UTC" }, content: [{ type: "text", text: '{\n  "timezone": "UTC",\n  "datetime": "2026-07-30T13:35:18+00:00",\n  "is_dst": false\n}' }] },
    { type: "assistantChunk", text: "Now let me look at the code." },
    { type: "toolCall", id: "r1", title: "Read src/release/plan.ts", kind: "read", status: "completed" },
    { type: "toolCall", id: "r2", title: "Read src/release/tag.ts", kind: "read", status: "completed" },
    { type: "toolCall", id: "r3", title: "Grep for version bump", kind: "search", status: "completed", rawInput: { query: "bumpVersion" } },
    { type: "assistantChunk", text: "Here is the release flow:\n\n```mermaid\nflowchart TD\n  A[Merge to main] --> B{Tests pass?}\n  B -- yes --> C[Bump version]\n  B -- no --> D[Fix and retry]\n  C --> E[Tag release]\n  E --> F[Publish]\n```\n\nThat covers the full path from merge to publish." },
    { type: "assistantEnd" },
    { type: "busy", value: false },
    { type: "usage", used: 88600, size: 200000, cost: 0.21 }
  ],
  // A focused code-editing turn: reasoning, an edit rendered as a diff card, a
  // benchmark run, the end-of-turn file-changes summary and the context ring.
  diff: [
    { type: "ready" },
    { type: "body", body: "thread" },
    { type: "capabilities", revert: true, editRequests: "inline", checkpoints: true, showFileChanges: true, contextUsage: true },
    options("claude-sonnet-4-5"),
    { type: "userMessage", text: "Orders load with an N+1 query when I include their line items. Fix it and prove the win with the benchmark." },
    { type: "busy", value: true },
    { type: "assistantStart" },
    { type: "thoughtChunk", text: "The serializer touches `order.line_items` inside a loop, so each order triggers its own query.\n\nA single `prefetch_related(\"line_items\")` on the queryset collapses that into two queries total. Then I can run the benchmark task to confirm the drop." },
    { type: "assistantChunk", text: "Found it. `OrderSerializer` walks `line_items` per order, so a page of 50 orders fires 51 queries. Switching the viewset queryset to prefetch the relation fixes it." },
    { type: "toolCall", id: "d0", title: "Read orders/api/views.py", kind: "read", status: "completed", locations: [{ path: "orders/api/views.py", line: 61 }] },
    { type: "toolCall", id: "d1", title: "Edit orders/api/views.py", kind: "edit", status: "completed", rawInput: { path: "orders/api/views.py" }, content: [{ type: "diff", path: "orders/api/views.py", added: 5, removed: 3 }] },
    { type: "toolCall", id: "d2", title: "Run python manage.py benchmark_orders", kind: "execute", status: "completed", rawInput: { command: "python manage.py benchmark_orders --page-size 50" }, content: [{ type: "text", text: "before:  51 queries   382 ms\nafter:    2 queries    24 ms\n\nrows returned: 50 (unchanged)\nOK" }] },
    { type: "fileChange", path: "orders/api/views.py", added: 5, removed: 3 },
    { type: "assistantChunk", text: "Done. The page now runs **2 queries instead of 51** and drops from 382 ms to 24 ms, with the same rows returned. Change is in [orders/api/views.py](orders/api/views.py)." },
    { type: "assistantEnd" },
    { type: "busy", value: false },
    { type: "usage", used: 33750, size: 200000, cost: 0.07 }
  ],
  // Two subagents running in parallel: one still working (shimmering title, its
  // prompt, streamed output and nested tool calls on the timeline) and one that
  // has finished and folded back down to its report.
  subagent: [
    { type: "ready" },
    { type: "body", body: "thread" },
    { type: "capabilities", revert: true, subagentControl: true, contextUsage: true },
    options("claude-sonnet-4-5"),
    { type: "userMessage", text: "Work out how sessions are persisted and how the status bar is wired up, in parallel." },
    { type: "busy", value: true },
    { type: "assistantStart" },
    { type: "assistantChunk", text: "Two independent questions, so I'll send an explore subagent after each." },
    { type: "subagentStart", id: "sa1", profile: "Explore", background: true, title: "Map session persistence",
      task: "Work out how a session is persisted between reloads.\n\nStart at `src/session/sessionStore.ts` and `src/session/sessionList.ts`, then trace who calls them from `src/chat/`. Report the storage key, what is written, and when it is read back. Do not change anything." },
    { type: "subagentChunk", parentId: "sa1", stream: "thought", text: "The store is the obvious entry point, so I will read it first and follow the callers from there." },
    { type: "toolCall", id: "sa1-t1", parentId: "sa1", title: "Read src/session/sessionStore.ts", kind: "read", status: "completed", locations: [{ path: "src/session/sessionStore.ts" }] },
    { type: "toolCall", id: "sa1-t2", parentId: "sa1", title: "Grep for sessionStore", kind: "search", status: "completed", rawInput: { query: "sessionStore" } },
    { type: "subagentChunk", parentId: "sa1", stream: "message", text: "Sessions are kept in workspace state under a single key, written on every turn and read back when the panel boots." },
    { type: "subagentEnd", id: "sa1", success: true, summary: "Sessions persist in VS Code workspace state, not on disk.\n\n`SessionStore` (`src/session/sessionStore.ts`) owns one `devin.sessions` key holding an id to metadata map. `ChatManager` writes through it whenever a turn completes, and reads the whole map back on activation to rebuild the session list." },
    { type: "subagentStart", id: "sa2", profile: "Explore", background: false, title: "Trace the status bar wiring",
      task: "Explain how the status bar item is created, updated, and disposed.\n\nRead `src/ui/statusBar.ts` and find every caller. Cover the click command, the hover tooltip, and which state changes trigger a re-render. Read only." },
    { type: "toolCall", id: "sa2-t1", parentId: "sa2", title: "Read src/ui/statusBar.ts", kind: "read", status: "completed", locations: [{ path: "src/ui/statusBar.ts", line: 33 }] },
    { type: "subagentChunk", parentId: "sa2", stream: "message", text: "One `StatusBarItem` is created on activation and re-rendered from a small state object." },
    { type: "toolCall", id: "sa2-t2", parentId: "sa2", title: "Grep for showInfo", kind: "search", status: "in_progress", rawInput: { query: "devin.showInfo" } },
    { type: "usage", used: 52400, size: 200000, cost: 0.12 }
  ],
  // The session browser: two workspace folders, grouped, with every liveness
  // state (running / waiting / waking / not running) and varied ages.
  sessions: [
    { type: "ready" },
    options("adaptive"),
    { type: "sessions", activeId: "s1",
      folders: [
        { path: "/Users/dev/Projects/web-app", name: "web-app" },
        { path: "/Users/dev/Projects/api-service", name: "api-service" }
      ],
      statuses: { s1: "running", s2: "idle", s4: "idle", s6: "starting" },
      sessions: [
        { id: "s1", short_id: "devin-8f2a", title: "Centralise token refresh in the auth module", working_directory: "/Users/dev/Projects/web-app", last_activity_ago: "2m ago" },
        { id: "s2", short_id: "devin-3c7d", title: "Fix a flaky integration test", working_directory: "/Users/dev/Projects/web-app", last_activity_ago: "1h ago" },
        { id: "s3", short_id: "devin-a10b", title: "Add pagination to the users endpoint", working_directory: "/Users/dev/Projects/web-app", last_activity_ago: "yesterday" },
        { id: "s4", short_id: "devin-6e4f", title: "Button component theme tokens", working_directory: "/Users/dev/Projects/api-service", last_activity_ago: "3d ago" },
        { id: "s5", short_id: "devin-b22c", title: "Move settings into env config", working_directory: "/Users/dev/Projects/api-service", last_activity_ago: "5d ago" },
        { id: "s6", short_id: "devin-9d81", title: "Investigate 502s on the API gateway", working_directory: "/Users/dev/Projects/api-service", last_activity_ago: "1w ago" }
      ]
    }
  ],
  // A chat opened on the surface that is not holding it: it says where it is, and
  // offers to bring it over rather than showing a copy of it.
  elsewhere: [
    { type: "ready" },
    options("adaptive"),
    { type: "elsewhere", id: "s1", title: "Centralise token refresh in the auth module",
      where: "an editor tab", here: "the side panel" }
  ]
};

function build(scenarioName) {
  const scenario = SCENARIOS[scenarioName] || SCENARIOS.full;
  const body = fs.readFileSync(path.join(MEDIA, "webview-body.html"), "utf8");
  const cssMain = href(OUT_DIR, path.join(MEDIA, "main.css"));
  const cssCodicon = href(OUT_DIR, path.join(MEDIA, "codicon", "codicon.css"));
  const bundle = href(OUT_DIR, path.join(ROOT, "dist", "webview.js"));
  const mermaid = href(OUT_DIR, path.join(ROOT, "dist", "mermaid.js"));

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<style>
:root {
${rootVars}
}
html, body { height: 100%; margin: 0; }
body {
  font-family: var(--vscode-font-family);
  font-size: var(--vscode-font-size);
  color: var(--vscode-foreground);
  background: var(--vscode-sideBar-background);
}
/* Emulate the panel width (--width, default the narrow sidebar). */
#app { max-width: ${panelWidth}px; }
</style>
<link rel="stylesheet" href="${cssCodicon}" />
<link rel="stylesheet" href="${cssMain}" />
</head>
<body data-logo="" data-model-icons="{}" data-mermaid-src="${mermaid}" data-surface="${surface}" class="vscode-dark">
${body}
<script>
  // Mock the VS Code webview API before the bundle loads.
  window.__posted = [];
  window.acquireVsCodeApi = () => ({ postMessage: (m) => window.__posted.push(m), getState: () => undefined, setState: () => {} });
</script>
<script src="${bundle}"></script>
<script>
  const SCENARIO = ${JSON.stringify(scenario)};
  for (const msg of SCENARIO) {
    window.dispatchEvent(new MessageEvent("message", { data: msg }));
  }
  // Scenarios do not send a "sessions" message, so dismiss the boot overlay
  // directly rather than waiting out its 15s fallback timeout.
  const boot = document.getElementById("boot");
  if (boot) boot.classList.add("hidden");
</script>
</body>
</html>`;

  fs.mkdirSync(OUT_DIR, { recursive: true });
  const outFile = path.join(OUT_DIR, "index.html");
  fs.writeFileSync(outFile, html, "utf8");
  return outFile;
}

const flagIdx = process.argv.indexOf("--scenario");
const scenarioName = flagIdx >= 0 ? process.argv[flagIdx + 1] : "full";
const out = build(scenarioName);

console.log("Preview written to:", out);
console.log("Open with: playwright-cli open \"" + pathToFileURL(out).href + "\"");
