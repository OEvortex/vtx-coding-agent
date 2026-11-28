# Skills

How vx discovers and registers skills: what ships inside the package, how ctx's skill loader picks them up, and how the model loads one. The frontmatter schema and on-disk discovery rules are ctx's; this page covers only what vx adds on top.

## What vx ships

`packages/vx/src/vx/__init__.py` runs one line at import:

```python
register_skills_package("vx")
```

That tells ctx's skill system that the `vx` package owns a `builtin_skills/` directory. The call is eager on purpose: the loader builds the catalog during startup, before anything else in vx is imported, and a lazy registration would arrive after the catalog was built.

vx's bundled skills live at `packages/vx/src/vx/builtin_skills/<category>/<skill>/SKILL.md`. There is exactly one today:

| Skill | Category | `register_cmd` | Purpose |
|---|---|---|---|
| `swarm` | `meta` | `true` (`cmd_info: run several agents at once`) | The coordinator playbook for the swarm feature: how to split work, brief workers, keep the board moving, use channels, workspaces, forks, and models. See [swarm.md](swarm.md) |

Because `register_cmd: true`, typing `/swarm <task>` in vx renders the skill body through `render_skill_prompt` — `$ARGUMENTS` is replaced by the typed text — and runs it as a normal turn. The skill is also listed in `<available_skills>` so the model can load it with the `skill` tool unprompted.

## How discovery works

vx does not implement its own discovery; it rides ctx's. `register_skills_package("vx")` registers the package so ctx's `load_builtin_cmd_skills` walks `builtin_skills/` two levels deep (`<category>/<skill>/SKILL.md`) and merges the result into the prompt catalog behind the on-disk skills — a project or user skill with the same name wins.

The on-disk sources are ctx's, unchanged:

1. `<cwd>/.agents/skills/`, then each ancestor up to the git root
2. `~/.agents/skills/`
3. `<ctx config dir>/skills/` (normally `~/.ctx/skills/`), marked `bundled`

`sync_builtin_skills` copies registered package skills into `~/.ctx/skills/` on context load, so vx's `swarm` skill also appears there as a synced copy.

Frontmatter is parsed by ctx's loader: `name`, `description` (required), `register_cmd` (`true`/`only`), `cmd_info`, `category`. See the repo-level `docs/skills.md` for the full schema; vx changes none of it.

`/skills` in the TUI lists whatever `runtime.context.skills` holds, grouped by category with `local ·` prefixed to non-bundled project skills, and prints any `skill_warnings` first (`commands/system.py:140`).

## The `skill` tool

vx ships its own `SkillTool` (`tools/skill.py`) rather than reusing ctx's managing version. It is deliberately smaller: **load only**.

| Parameter | Meaning |
|---|---|
| `name` | Skill name as listed in `<available_skills>` |
| `file` | Optional path inside the skill directory, instead of `SKILL.md` |

`find_skill` resolves a name through `load_skills(cwd)` (project + global on-disk) merged with `load_builtin_cmd_skills()` (packaged `builtin_skills/` trees, including vx's). First match wins.

The result is a `<skill_content name="...">` block containing the frontmatter-stripped body, the skill's base directory ("relative paths in this skill are relative to that directory"), and up to 10 sibling files so scripts and references are discoverable. There are no `create`/`edit`/`delete` actions — authoring a skill is a file edit, which `apply_patch` covers.

## Slash-command skills

A skill with `register_cmd: true` doubles as `/<name>`: `_skill_command` in `commands/__init__.py` matches the typed command against `context.skills`, renders the body with `render_skill_prompt`, adds a `UserTurn` row for `/<name> <args>`, and enqueues it as a prompt. This is the only skill path that costs a model turn without the model choosing it.

Skill commands are tried **after** built-ins and extension commands, so a skill can never shadow `/model`, `/board`, or an extension's command — those names are claimed first.

## Reloading

`/reload` rebuilds the context (`rt.reload_context()`), which re-runs skill discovery and re-syncs bundled skills, so adding or editing a `SKILL.md` needs no restart.

## See also

- [commands.md](commands.md) for the slash-command router
- [swarm.md](swarm.md) for the bundled `swarm` skill's feature
- `docs/skills.md` (repo level) for ctx's full skill schema and discovery
