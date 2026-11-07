// Mock host payload for the settings webview, shared by the browser preview
// (preview-settings.js) and the jsdom harness used in tests, so both exercise
// the same shape the extension host really sends (see sendData in
// src/settings/settingsPanel.ts).
//
// The data is deliberately generic (no real project or personal data) so
// screenshots taken from it are safe to publish.

const HOME = "/Users/dev";
const USER_DIR = HOME + "/.config/devin";

// Must mirror ROW_DEFAULTS in src/settings/settingsPanel.ts, since the host
// always sends an effective value for every row.
const DEFAULTS = {
  "agent.model": "",
  "agent.show_history_on_continue": true,
  attribution: true,
  auto_update: true,
  notify: "smart",
  respect_gitignore: false,
  include_gitignored_files: false,
  show_hints: true,
  "proxy.mode": "system",
  "proxy.url": "",
  "proxy.no_proxy": "",
  "sandbox.network_mode": "full",
  "sandbox.allowed_domains": [],
  "sandbox.denied_domains": [],
  theme_mode: "",
  unicode_mode: "auto",
  pty_for_noninteractive_exec: false,
  disable_osc: false,
  "read_config_from.agents_standard": true,
  "read_config_from.cursor": true,
  "read_config_from.windsurf": true,
  "read_config_from.claude": true
};

// What the Global scope sets itself, and what the first workspace folder adds on
// top, so the fixture covers set-here, inherited, and overridden rows at once.
const GLOBAL_SET = { notify: "always", respect_gitignore: true, "proxy.mode": "manual", "proxy.url": "http://proxy.example.com:8080" };
const FOLDER_SET = { "agent.model": "claude-sonnet-4-5", show_hints: false };

function valueGroup(scope, root, title, file, exists, over, setKeys) {
  return {
    scope, root, title, path: file, exists,
    values: Object.assign({}, DEFAULTS, over),
    setKeys
  };
}

// `opts.multiRoot` adds a second workspace folder that sets nothing.
// `opts.empty` renders with nothing configured, for the empty states.
function buildData(opts) {
  const o = opts || {};
  const folders = o.multiRoot
    ? [{ name: "web-app", path: HOME + "/Projects/web-app" }, { name: "api-service", path: HOME + "/Projects/api-service" }]
    : [{ name: "web-app", path: HOME + "/Projects/web-app" }];

  const userGroup = o.empty
    ? valueGroup("user", undefined, "Global", USER_DIR + "/config.json", false, {}, [])
    : valueGroup("user", undefined, "Global", USER_DIR + "/config.json", true, GLOBAL_SET, Object.keys(GLOBAL_SET));

  const folderGroups = folders.map((f, i) => {
    const own = i === 0 && !o.empty ? FOLDER_SET : {};
    return valueGroup(
      "project", f.path, folders.length === 1 ? "Workspace" : "Workspace · " + f.name,
      f.path + "/.devin/config.json", i === 0 && !o.empty,
      // A folder shows the Global values it inherits, plus whatever it overrides.
      Object.assign({}, o.empty ? {} : GLOBAL_SET, own),
      Object.keys(own)
    );
  });

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
        { id: "adaptive", name: "Adaptive", default: "adaptive" },
        { id: "claude", name: "Claude Sonnet 4.5", default: "claude-sonnet-4-5" },
        { id: "gpt", name: "GPT-5", default: "gpt-5" }
      ]
    },
    valuesByScope: [userGroup].concat(folderGroups),
    folderOverrides: Object.fromEntries(folders.map((f, i) => [f.path, i === 0 && !o.empty ? Object.keys(FOLDER_SET) : []])),
    extension: { defaultModel: o.empty ? "" : "claude-sonnet-4-5" },
    instructions: {
      byScope: byScope((scope, f) => ({
        file: {
          path: (scope === "user" ? USER_DIR : f.path) + "/AGENTS.md",
          exists: !o.empty,
          kind: "AGENTS.md"
        }
      })),
      // What the CLI reports as actually loaded, which is more than the one file
      // per scope the panel can edit: a Windsurf file and a plugin's own AGENTS.md
      // are in force here too, and no amount of scanning for known filenames in
      // known directories would find the second one.
      loaded: o.empty ? [] : [
        { name: "AGENTS", path: USER_DIR + "/AGENTS.md", provider: "agents_standard", providerLabel: "AGENTS.md", trigger: "always_on", scope: "global" },
        { name: "AGENTS", path: folders[0].path + "/AGENTS.md", provider: "agents_standard", providerLabel: "AGENTS.md", trigger: "always_on", scope: "workspace" },
        { name: "global_rules", path: HOME + "/.codeium/windsurf/memories/global_rules.md", provider: "windsurf", providerLabel: ".windsurf", trigger: "always_on", scope: "global" },
        { name: "AGENTS", path: HOME + "/.local/share/devin/cli/plugins/cache/superpowers/6.3.0/AGENTS.md", provider: "agents_standard", providerLabel: "AGENTS.md", trigger: "always_on", scope: "global" }
      ]
    },
    skills: {
      byScope: byScope((scope, f) => ({
        list: o.empty ? [] : (scope === "user"
          ? [
              { name: "git-workflow", description: "Git branching, commit, PR, and quality gate conventions.", dir: USER_DIR + "/skills/git-workflow", path: USER_DIR + "/skills/git-workflow/SKILL.md" },
              { name: "release-notes", description: "Draft release notes from the commits since the last tag.", dir: USER_DIR + "/skills/release-notes", path: USER_DIR + "/skills/release-notes/SKILL.md" }
            ]
          : [{ name: "wrap-up", description: "Write a handoff note before ending a session.", dir: f.path + "/.devin/skills/wrap-up", path: f.path + "/.devin/skills/wrap-up/SKILL.md" }])
      }))
    },
    mcp: {
      byScope: byScope((scope, f) => ({
        servers: o.empty ? [] : (scope === "user"
          ? [
              { name: "github", scope: "user", transport: "http", file: USER_DIR + "/mcp_config.json", detail: "https://api.example.com/mcp", disabled: false, loggedIn: true, oauthCapable: true, envKeys: [], headerKeys: [] },
              { name: "time", scope: "user", transport: "stdio", file: USER_DIR + "/mcp_config.json", detail: "stdio · uvx", disabled: false, loggedIn: false, oauthCapable: false, envKeys: [], headerKeys: [] },
              { name: "issue-tracker", scope: "user", transport: "http", file: USER_DIR + "/mcp_config.json", detail: "https://tracker.example.com/mcp", disabled: true, loggedIn: false, oauthCapable: true, envKeys: ["TRACKER_TOKEN"], headerKeys: [] },
              { name: "godot-ai", scope: "user", source: "windsurf", transport: "http", file: HOME + "/.codeium/windsurf/mcp_config.json", detail: "http://127.0.0.1:8000/mcp", disabled: false, loggedIn: false, oauthCapable: true, envKeys: [], headerKeys: [] }
            ]
          : [{ name: "postgres", scope: "project", transport: "stdio", file: f.path + "/.devin/mcp_config.json", detail: "stdio · npx", disabled: false, loggedIn: false, oauthCapable: false, envKeys: ["DATABASE_URL"], headerKeys: [] }])
      }))
    },
    hooks: {
      byScope: byScope((scope, f) => ({
        entries: o.empty ? [] : (scope === "user"
          ? [{ event: "PreToolUse", matcher: "exec", type: "command", command: "~/bin/audit-exec.sh", timeout: 5, source: USER_DIR + "/config.json" }]
          : [
              { event: "PostToolUse", matcher: "edit", type: "command", command: "npm run lint -- --fix", source: f.path + "/.devin/config.json" },
              { event: "SessionStart", matcher: "", type: "prompt", prompt: "Check the open pull requests before starting.", source: f.path + "/.devin/config.json" }
            ])
      })),
      // The CLI's own view, including a hook written in the Claude format, which
      // the editable list above reads from a file it does not own.
      loaded: o.empty ? [] : [
        { id: "<hook 1>", name: "permission_request", events: ["permission_request"], sourcePath: USER_DIR + "/config.json", provider: "devin", providerLabel: ".devin", scope: "global", format: "claude" },
        { id: "<hook 2>", name: "post_tool", events: ["post_tool"], sourcePath: USER_DIR + "/config.json", provider: "devin", providerLabel: ".devin", scope: "global", format: "claude" }
      ]
    },
    plugins: { list: o.empty ? [] : [{ name: "team-conventions", description: "Shared skills, hooks, and rules for the team." }] },
    permissions: {
      byScope: byScope((scope) => (o.empty
        ? { allow: [], deny: [], ask: [] }
        : scope === "user"
          ? { allow: ["Exec(git status)", "Exec(npm test)", "Read(**)"], deny: ["Exec(rm -rf *)"], ask: ["Exec(git push)"] }
          : { allow: ["Exec(npm run build)"], deny: [], ask: [] }))
    }
  };
}

module.exports = { buildData, DEFAULTS, GLOBAL_SET, FOLDER_SET };
