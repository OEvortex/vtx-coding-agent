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

## Python skills

A skill that also ships a `pyproject.toml` and `src/<import_name>/__init__.py` is
loaded as a **python skill** rather than as instructions.

```
.agents/skills/word-count/
├── SKILL.md          # API reference for the module
├── pyproject.toml
└── src/word_count/__init__.py
```

Python skills are discovered and importable, but nothing executes them. They were
imported into the persistent Python kernel that the RLM mode provided; that
kernel is gone, replaced by the codemode sandbox, which runs a confined script
rather than exposing a long-lived interpreter.

So python skills are **hidden from every discovery surface** — the system-prompt
catalog, the `skill` tool's `list`, and the `/` command menu — and
`register_cmd: true` does not force one back in. Advertising one would spend
context on a dead end: the description reads as generally applicable, the agent
loads it, and then has nothing to call it with.

`skill(action="run")` returns the file instead of running it, so a python skill
is still readable. A `SKILL.md` body is instructions for a model, and the model
can follow those directly.

Vtx ships no python skills. All five that were bundled (`agent-message`,
`agent-observe`, `compact`, `refine`, `edit`) were removed: the first four with
the kernel that imported them, and `edit` after that, because a python skill with
no interpreter is documentation for a module nothing can call.

## Discovery paths

Loaded in priority order:

1. `<cwd>/.agents/skills/<name>/` — walked up to the git root; nearer dirs win on name collision.
2. `~/.agents/skills/<name>/` — user-wide skills.
3. `~/.vtx/skills/<name>/` — legacy/global vtx dir.
4. Built-in skills bundled in the package (`coding_agent/builtin_skills/`), synced on startup.

## How they trigger

- **Model-invoked**: the skills catalog (name + description) rides along in the system prompt; the model calls `skill(action="load", name=...)`. The SKILL.md body (frontmatter stripped) plus the skill's directory becomes the working instructions, so `scripts/...` and `reference/...` inside a skill resolve against the skill directory.

  The catalog is a snapshot in the system prompt. A skill installed or deleted mid-session is announced at the next cold boundary as a `<vtx:skills-refresh>` context message that supersedes the earlier list, naming what was added, changed, and removed — the prompt itself is not rebuilt, so the cached prefix behind it stays valid.
- **User-invoked**: type `/my-skill do the thing`. With `register_cmd: true` the skill appears in slash-command autocomplete; `$ARGUMENTS` receives `do the thing`.

## Managing skills

The agent loads a skill with `skill(action="load", name=...)` and can manage skills itself via the same tool (`create`, `patch`, `edit`, `delete`, scope `project` or `global`) — see [tools.md](tools.md#skill). Users just edit markdown.

## SDK

SDK agents can load the same skills — see [sdk/skills.md](sdk/skills.md).
