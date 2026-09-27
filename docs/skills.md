# Skills

Skills are markdown workflows the agent loads on demand, keeping the base prompt lean. Implemented in `src/ai/agent/context/skills.py`.

## Anatomy

```
.agents/skills/my-skill/
└── SKILL.md
```

```markdown
---
name: my-skill
description: One line shown to the model in the skills index.
category: general            # optional, default "general"
register_cmd: false          # optional: also expose as /my-skill
cmd_info: ""                 # optional: short hint for the slash command (max 32 chars)
---

Instructions for the agent. $ARGUMENTS is replaced by whatever the user
typed after the skill name (or the query passed to the `skill` tool).
```

Constraints enforced at load time: name ≤ 64 chars, description ≤ 1024 chars, category ≤ 32 chars. The directory name should match `name`; mismatches produce a warning.

## Python (kernel) skills

A skill that also ships a `pyproject.toml` and `src/<import_name>/__init__.py` is loaded as a **kernel skill** instead of as instructions. It is imported into the persistent Python kernel and called by its import name:

```
.agents/skills/refine/
├── SKILL.md          # API reference for the module
├── pyproject.toml
└── src/refine/__init__.py
```

```python
await refine.run("persist a memory about checking git status before committing")
```

The kernel only exists in RLM mode, so kernel skills are hidden in `tool_first` mode — they are left out of the system-prompt catalog, the `skill` tool's `list`, and the `/` command menu, and `register_cmd: true` does not force one back in. A tool-first agent has no `ipython` tool, so an advertised kernel skill is a dead end: the description reads as generally applicable, the agent loads it, and then has nothing to call it with. In RLM mode they are pre-imported in the kernel and listed with their import name.

`refine` is the one kernel skill with a tool-first counterpart: since the capability is a host-side pass, not a kernel call, `tool_first` sessions get the equivalent `refine` tool (see [tools.md](tools.md#refine)) rather than a dead-end skill.

## Discovery paths

Loaded in priority order:

1. `<cwd>/.agents/skills/<name>/` — walked up to the git root; nearer dirs win on name collision.
2. `~/.agents/skills/<name>/` — user-wide skills.
3. `~/.vtx/skills/<name>/` — legacy/global vtx dir.
4. Built-in skills bundled in the package (`coding_agent/builtin_skills/`), synced on startup.

## How they trigger

- **Model-invoked**: the skills index (name + one-line description) rides along in the system prompt; the model calls the `skill` tool with a name and query. The SKILL.md body (frontmatter stripped) becomes the working instructions.
- **User-invoked**: type `/my-skill do the thing`. With `register_cmd: true` the skill appears in slash-command autocomplete; `$ARGUMENTS` receives `do the thing`.

## Managing skills

The agent can manage skills itself via the `skill` tool (`list`, `view`, `create`, `patch`, `edit`, `delete`, scope `project` or `global`) — see [tools.md](tools.md#skill). Users just edit markdown.

## SDK

SDK agents can load the same skills — see [sdk/skills.md](sdk/skills.md).
