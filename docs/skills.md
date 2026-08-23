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
