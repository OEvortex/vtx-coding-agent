// Mock host payload for the settings webview, shared by the browser preview
// (preview-settings.js) and the jsdom harness used in tests, so both exercise
// the same shape the extension host really sends (see sendData in
// src/settings/settingsPanel.ts).
//
// The data is deliberately generic (no real project or personal data) so
// screenshots taken from it are safe to publish.

const HOME = "/Users/dev";
const USER_DIR = HOME + "/.vtx";

// Must mirror ROW_DEFAULTS in src/settings/settingsPanel.ts, since the host
// always sends an effective value for every row.
const DEFAULTS = {
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
  "task.max_concurrent": 4,
  extensions: []
};

// What the Global config sets itself. Vtx has no project config.yml, so there
// is nothing for a folder to override in the value rows.
const GLOBAL_SET = {
  "llm.default_model": "claude-sonnet-4-5",
  "ui.model_provider_filter": "anthropic",
  "permissions.mode": "auto"
};
const FOLDER_SET = {};

function valueGroup(scope, root, title, file, exists, over, setKeys) {
  return {
    scope, root, title, path: file, exists,
    values: Object.assign({}, DEFAULTS, over),
    setKeys
  };
}

// `opts.multiRoot` adds a second workspace folder.
// `opts.empty` renders with nothing configured, for the empty states.
function buildData(opts) {
  const o = opts || {};
  const folders = o.multiRoot
    ? [{ name: "web-app", path: HOME + "/Projects/web-app" }, { name: "api-service", path: HOME + "/Projects/api-service" }]
    : [{ name: "web-app", path: HOME + "/Projects/web-app" }];

  const userGroup = valueGroup(
    "user", undefined, "Global", USER_DIR + "/config.yml", !o.empty,
    o.empty ? {} : GLOBAL_SET, o.empty ? [] : Object.keys(GLOBAL_SET)
  );

  // One entry per scope, the way every per-scope section is shaped.
  const byScope = (make) => [Object.assign({ scope: "user", title: "Global" }, make("user"))].concat(
    folders.map((f) => Object.assign(
      { scope: "project", root: f.path, title: folders.length === 1 ? "Workspace" : "Workspace · " + f.name },
      make("project", f)
    ))
  );

  return {
    folders,
    models: {
      families: [
        { id: "adaptive", name: "Adaptive", default: "adaptive", provider: "openai-codex" },
        { id: "claude", name: "Claude Sonnet 4.5", default: "claude-sonnet-4-5", provider: "anthropic" },
        { id: "gpt", name: "GPT-5", default: "gpt-5", provider: "openai-codex" }
      ],
      providers: [
        { slug: "openai-codex", name: "OpenAI Codex" },
        { slug: "anthropic", name: "Anthropic" }
      ]
    },
    valuesByScope: [userGroup],
    folderOverrides: {},
    extension: { defaultModel: o.empty ? "" : "claude-sonnet-4-5" },
    instructions: {
      byScope: byScope((scope, f) => ({
        file: {
          path: (scope === "user" ? USER_DIR : f.path) + "/AGENTS.md",
          exists: !o.empty,
          kind: "AGENTS.md"
        }
      })),
      loaded: o.empty ? [] : [
        { name: "AGENTS", path: USER_DIR + "/AGENTS.md", provider: "agents_standard", providerLabel: "AGENTS.md", trigger: "always_on", scope: "global" },
        { name: "AGENTS", path: folders[0].path + "/AGENTS.md", provider: "agents_standard", providerLabel: "AGENTS.md", trigger: "always_on", scope: "workspace" },
        { name: "global_rules", path: HOME + "/.agents/rules/global.md", provider: "agents_standard", providerLabel: ".agents", trigger: "always_on", scope: "global" },
        { name: "AGENTS", path: HOME + "/.vtx/skills/review/AGENTS.md", provider: "agents_standard", providerLabel: "AGENTS.md", trigger: "always_on", scope: "global" }
      ]
    },
    skills: {
      byScope: byScope((scope, f) => ({
        list: o.empty ? [] : (scope === "user"
          ? [
              { name: "git-workflow", description: "Git branching, commit, PR, and quality gate conventions.", dir: HOME + "/.agents/skills/git-workflow", path: HOME + "/.agents/skills/git-workflow/SKILL.md", scope: "user" },
              { name: "release-notes", description: "Draft release notes from the commits since the last tag.", dir: USER_DIR + "/skills/release-notes", path: USER_DIR + "/skills/release-notes/SKILL.md", scope: "user" }
            ]
          : [{ name: "wrap-up", description: "Write a handoff note before ending a session.", dir: f.path + "/.agents/skills/wrap-up", path: f.path + "/.agents/skills/wrap-up/SKILL.md", scope: "project" }])
      }))
    },
    mcp: {
      byScope: byScope((scope, f) => ({
        servers: o.empty ? [] : (scope === "user"
          ? [
              { name: "github", scope: "user", transport: "http", file: USER_DIR + "/mcp.json", detail: "https://api.example.com/mcp", disabled: false, loggedIn: true, oauthCapable: true, envKeys: [], headerKeys: [], source: "vtx" },
              { name: "time", scope: "user", transport: "stdio", file: USER_DIR + "/mcp.json", detail: "stdio · uvx", disabled: false, loggedIn: false, oauthCapable: false, envKeys: [], headerKeys: [], source: "vtx" },
              { name: "issue-tracker", scope: "user", transport: "http", file: USER_DIR + "/mcp.json", detail: "https://tracker.example.com/mcp", disabled: true, loggedIn: false, oauthCapable: true, envKeys: ["TRACKER_TOKEN"], headerKeys: [], source: "vtx" }
            ]
          : [{ name: "postgres", scope: "project", transport: "stdio", file: f.path + "/.vtx/mcp.json", detail: "stdio · npx", disabled: false, loggedIn: false, oauthCapable: false, envKeys: ["DATABASE_URL"], headerKeys: [], source: "vtx" }])
      }))
    },
    hooks: {
      byScope: byScope((scope, f) => ({
        entries: o.empty ? [] : (scope === "user"
          ? [{ event: "PreToolUse", group: "pre_tool_use", index: 0, matcher: "exec", type: "command", command: "~/bin/audit-exec.sh", timeout: 5, source: USER_DIR + "/hooks.yml" }]
          : [
              { event: "PostToolUse", group: "post_tool_use", index: 0, matcher: "edit", type: "command", command: "npm run lint -- --fix", source: f.path + "/.vtx/hooks.yml" },
              { event: "SessionStart", group: "session_start", index: 0, matcher: "", type: "prompt", prompt: "Check the open pull requests before starting.", source: f.path + "/.vtx/hooks.yml" }
            ])
      })),
      loaded: o.empty ? [] : [
        { id: "<hook 1>", name: "permission_request", events: ["permission_request"], sourcePath: USER_DIR + "/hooks.yml", provider: "vtx", providerLabel: "hooks.yml", scope: "global", format: "vtx" },
        { id: "<hook 2>", name: "post_tool", events: ["post_tool"], sourcePath: USER_DIR + "/hooks.yml", provider: "vtx", providerLabel: "hooks.yml", scope: "global", format: "vtx" }
      ]
    },
    plugins: { list: o.empty ? [] : [
      { name: "tools", description: "vtx.plugins.builtins.services.tools:PLUGIN", disabled: false },
      { name: "sandbox", description: "vtx.plugins.builtins.services.sandbox:PLUGIN", disabled: true }
    ] },
    permissions: {
      byScope: [{ scope: "user", title: "Global", mode: o.empty ? "prompt" : "auto" }]
    }
  };
}

module.exports = { DEFAULTS, GLOBAL_SET, FOLDER_SET, buildData };
