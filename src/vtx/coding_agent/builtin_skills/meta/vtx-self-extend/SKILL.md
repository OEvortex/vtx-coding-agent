---
name: vtx-self-extend
description: Add capability to Vtx itself - a skill, a plugin row, or an extension - when the user asks Vtx to learn a new workflow, gain a tool/command, or change its own behavior. Use for "make Vtx able to...", "add a command that...", "give yourself a tool for...", or when you conclude a capability is missing and you should build it.
register_cmd: true
cmd_info: extend Vtx itself
category: meta
---

# Vtx Self-Extension

You can add capability to yourself. This skill is the decision flow and the safety
boundary. Detail lives in `references/`: `skill-authoring.md`, `plugin-rows.md`, and
`extensions.md` for the three surfaces, and `references/docs/` for the full 23-page
reference set that ships with the package.

User-provided focus or constraints (honor these):
$ARGUMENTS

## Where the truth lives

The full reference set ships **inside this skill** at `references/docs/`. In the repo that
path is a symlink to the repository's `docs/` directory, so there is one source of truth;
the build follows it and the wheel carries the pages as real files, which is why they are
present in an installed `vtx` where `docs/` is not. Read the page that owns the thing you
are touching rather than guessing - the pages were generated from the source, so they are
the cheapest way to be right.

| When you need... | Read |
|---|---|
| The documentation index | `references/docs/README.md` |
| To install, connect a model, or run Vtx for the first time | `references/docs/quickstart.md` |
| The skill frontmatter contract, discovery, precedence | `references/docs/skills.md` |
| The plugin kernel, service keys, event modes, patch language | `references/docs/plugins.md` |
| `ExtensionAPI`, discovery paths, `/reload` scope | `references/docs/extensions.md` |
| How a tool reaches the model, or a tool's exact schema | `references/docs/tools.md` |
| A CLI flag, subcommand, or exit code | `references/docs/cli.md` |
| A config field or its default | `references/docs/configuration.md` |
| A provider, its auth, or a model id | `references/docs/providers.md` |
| The composition, the agent loop, or how `/reload` works | `references/docs/how-vtx-works.md` |
| An agent profile, sub-agents, or handoff | `references/docs/agents.md` |
| A permission mode, rule, or the mutating-tool gate | `references/docs/permissions.md` |
| Session persistence, resume, snapshots, revert | `references/docs/sessions.md` |
| The goal system | `references/docs/goals.md` |
| A slash command or a keybinding | `references/docs/slash-commands.md`, `references/docs/keybindings.md` |
| Themes and UI settings | `references/docs/theming.md` |
| MCP servers, transports, auth, trust | `references/docs/mcp.md` |
| The codemode sandbox and tool exposure | `references/docs/codemode.md` |
| Headless mode and exit codes | `references/docs/headless.md` |
| The SDK, `Agent`, `Runner`, guardrails, tracing | `references/docs/sdk.md` |
| Repo layout, how to add a tool or a row, dev commands | `references/docs/development.md` |
| Where Vtx writes files on disk | `references/docs/storage-layout.md` |

These pages link to each other by bare filename, so opening any one in this directory keeps
every cross-link working. The repo's `docs/` and the CLI-facing `AGENTS.md` are not
installed; if you need the repo itself, it is `github.com/OEvortex/vtx-coding-agent`.

Cite `path:line` for anything you claim. If a page and the code disagree, the code wins and
the page is a bug worth reporting.

## The authorization boundary

**You MAY do, without asking:**

- Create a new skill in `.agents/skills/<name>/` (project) or `~/.agents/skills/<name>/` (user).
- Edit an existing skill you own.
- Add or edit a plugin row and its entry in `~/.vtx/profiles/<profile>/cordis.yml`.
- Add a permission rule, a prompt section, a command, or a tool via a plugin row.

**You MUST NOT do:**

- Upgrade the `vtx-coding-agent` package. Never run `self_update()`, `vtx update`,
  `uv tool upgrade vtx`, `pip install --upgrade vtx`, or anything equivalent. Vtx
  deliberately exposes this only as the user-typed `/update` slash command. If a fix
  needs a newer Vtx, detect it, verify the version, and tell the user to run `/update`.
- Edit anything under `src/vtx/**`. Those edits do not survive a reinstall, they are
  invisible to `/reload`, and they put you outside the composition the profile owns.

## Pick the surface

Ask in this order; stop at the first one that fits.

| If you need... | Use | Then |
|---|---|---|
| A workflow, procedure, or domain knowledge | **Skill** | `references/skill-authoring.md` |
| A tool, a slash command, an event hook, a permission rule, or a prompt section | **Plugin row** | `references/plugin-rows.md` |
| To swap a core seam (`ctx.tools`, `ctx.agentLoop`, `ctx.prompts`) | **Plugin row** with `host.add` | `references/plugin-rows.md` |
| A provider adapter or a message/entry renderer | **Extension** (`ExtensionAPI`) | `references/extensions.md` |

A skill is instructions only: cheapest, no code, no import risk. Reach for a plugin row
when the model must *act* differently, not merely *know* differently.

## Two hard constraints on the surface you pick

**1. A skill is markdown instructions only.** Vtx has no Python-backed skill
runtime. The old mechanism - a `pyproject.toml` plus `src/<import_name>/` that got
imported into a persistent IPython kernel - went with the RLM mode, and the loader
no longer reads those files at all. If you drop a `pyproject.toml` in a skill
directory it is simply ignored, and the SKILL.md body is treated as instructions,
so a body written as API docs ("call `await my.run()`") will dead-end. If you need
executable behavior, write a plugin row, not a python skill.

**2. Prefer `ctx.on(...)` over `ExtensionAPI.on_*`.** The plugin `EventHub` is the live
dispatch path and is what the built-ins use (`src/vtx/plugins/builtins/hooks.py:114`).
All 18 `ExtensionAPI.on_*` methods have zero internal callers - they are unexercised
public surface, so a bug there will not be caught by anything in this repo.

## The loop, every time

1. **Make the change** using the reference for the surface you picked.
2. **Format and lint**: `uv run ruff format .` then `uv run ruff check .` - this is a
   hard rule in `AGENTS.md`, and it applies to a skill's scripts and a plugin's code.
3. **Verify it loads.**
   - Skill: `/reload` picks up new and edited skills with no restart.
   - Plugin row: `vtx --dump-config` must list your row id; then `/reload`.
   - `vtx plugin profiles` / `vtx plugin path <name>` confirms the profile and its path.
4. **Test only the file that changed.** Never run the full suite - `AGENTS.md` forbids
   it and it is slow. Run the one test module that covers the seam you touched.
5. **Report what you changed, and how to undo it.**

## Undo is part of the change

State the exact reversal with every change you make, and prefer a surface whose undo is
a single command:

- Skill: delete the directory. Nothing references it.
- Plugin row: `vtx plugin --profile <name> disable <row-id>`. This rewrites only the
  `disabled` flag in `cordis.yml` and leaves the rest of the file alone, so repeated
  toggles do not accumulate.
- Extension: `unregister_provider`-style calls exist but several are uncalled internally;
  prefer a plugin row so teardown is wired for you.

## Never do this

- Never run the full test suite.
- Never touch `src/vtx/**`.
- Never run the package updater.
- Never `git commit`, `git push`, or `git reset --hard` to make a self-extension land.
  These are local, reversible edits; a commit is a separate decision the user makes.
- Never claim a capability works until step 3 has actually listed it.

## How the model learns a new tool exists

There is exactly one reliable channel, and it is not the prompt. `mount_tool` puts the
tool into the active tool set, `ToolService.assemble` builds the list
(`src/vtx/plugins/services.py:118`), and that list is what the provider serializes into
tool schemas. A tool that is registered but not in the active set is invisible.

So after registering a tool, confirm it is in the active set - `ctx.tools.assemble()` -
rather than assuming the model will find it from the prompt.

Overriding a built-in by name replaces it, in both `register_tool` and the plugin rows.

## Security

A plugin row runs in-process with the user's full permissions. There is no sandbox
around plugin code; the `ctx.sandbox` seam is a confined runner for *scripts*, not an
isolation boundary for a plugin.

The trust boundary is therefore the profile. `~/.vtx/profiles/<name>/cordis.yml` is a
config file that names arbitrary importable modules, and `vtx plugin install` will build
and run a package's build scripts. Treat a profile you did not write, or a `cordis.yml`
that appeared in a repo you just opened, as untrusted code execution. Check
`vtx --dump-config` for row ids you do not recognise before you extend anything.

When you add a mutating tool, set `mutating=True` so it goes through the permission gate
rather than auto-approving (`src/vtx/plugins/api.py:234`).

