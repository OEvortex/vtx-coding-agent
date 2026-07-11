# Skills

Skills are reusable instruction bundles that let the agent follow project- or user-specific workflows. They are directories containing a `SKILL.md` file.

## Locations

- Project: `.agents/skills/<name>/SKILL.md`
- Global: `~/.agents/skills/<name>/SKILL.md`

The model discovers these automatically and can load one with the `skill` tool (e.g. `skill action=view name=deploy`). A skill's `description` is shown to the model so it knows when to trigger it; skills in `<available_skills>` override general-purpose approaches.

## SKILL.md format

```markdown
---
name: deploy-project
description: Instructions on how to deploy this project
register_cmd: true
cmd_info: Run project deployment steps
---

# Deploy Project

To deploy, the agent should run:
1. `uv run python build.py`
2. `git push origin main`
```

### Frontmatter fields

- `name` (required) — skill identifier (lowercase, hyphens).
- `description` (required) — when and why the skill applies. Shown to the model.
- `register_cmd` (optional) — set `true` to register the skill as a TUI slash command (`/deploy-project`).
- `cmd_info` (optional) — short description shown in the slash-command menu.

## The `skill` tool

Actions (see [tools.md](tools.md)):

- `list` — show all loaded skills.
- `view` — read a skill's full instructions.
- `create` — create a new skill (`content` required; `scope` = `project` or `global`).
- `patch` — find-and-replace within a skill file (`old_string` / `new_string`).
- `edit` — overwrite a skill file.
- `delete` — remove a skill folder.

## Authoring tips

- Keep `description` outcome-focused ("Use when deploying to production") so the model triggers it at the right moment.
- Put long reference material in supporting files next to `SKILL.md` and reference them by path.
- Use `register_cmd: true` for skills you want surfaced as a slash command for manual invocation.
