# Vtx

Vtx is a coding agent that runs in your terminal and works with the files on your
machine. Give it a goal and a working folder, and it reads files, runs commands, edits
code, and works through multi-step tasks without stopping to confirm each step.

Vtx is a harness: the tool registry, the provider seam, the permission gate, and the
agent loop are all composable rows, so the same core runs interactive in a TUI,
headless in CI, or embedded as an SDK.

## Start here

New to Vtx? Follow the [Quickstart](quickstart.md) to install, connect a model, and
finish a first task.

If Vtx is already installed, pick what you need:

- [Use Vtx interactively](cli.md) - the flags, prompts, and one-off runs.
- [Tools](tools.md) - every tool the model can call, with exact schemas and examples.
- [How Vtx works](how-vtx-works.md) - the plugin composition, the agent loop, and how
  a tool reaches the model.
- [Sessions](sessions.md) - resume work, branch a session, and revert a change.

## Configure it

- [Configuration](configuration.md) - the config file and every field.
- [Providers](providers.md) - the 62 providers in the shipped catalog, the 16 slugs
  `--provider` accepts, and how each authenticates.
- [Permissions](permissions.md) - modes, rules, and which tools are gated.
- [Theming](theming.md) - themes and terminal appearance.
- [Keybindings](keybindings.md) and [slash commands](slash-commands.md).

## Extend it

Vtx expects to be changed. Three surfaces, smallest first:

- [Skills](skills.md) - a directory of instructions the model loads on demand.
- [Plugins](plugins.md) - a row in the composition that can register a tool, a
  command, an event hook, or replace a core service.
- [Extensions](extensions.md) - the `ExtensionAPI` surface, for providers and
  renderers.

[Agents](agents.md) covers handoff agents and sub-agents. [Goals](goals.md) covers the
persistent objective system.

## Automate or embed

- [Headless](headless.md) - one prompt in, one exit code out, for CI.
- [SDK](sdk.md) - run Vtx inside your own application.
- [MCP](mcp.md) - connect external tool servers.
- [Codemode](codemode.md) - the confined sandbox the model runs Python in.

## Contributing

- [Development](development.md) - repo layout, the lint and test commands, and how to
  add a tool or a plugin row.
- [Storage layout](storage-layout.md) - every path Vtx writes to on disk.
