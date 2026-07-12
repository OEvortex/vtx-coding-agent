# Skills

Skills are markdown files that teach the agent domain-specific behaviors. They live in `skills/<name>/SKILL.md` and are loaded on demand.

## How Skills Work

1. **Discovery**: Skills are scanned from `skills/` directories at startup.
2. **Loading**: The skill's frontmatter (`name` + `description`) is always in context (~100 tokens). The body loads only when the skill triggers.
3. **Triggering**: Skills activate when the agent recognizes a relevant task or when the user invokes them via `/skill`.

## Built-in Skills

### Always-Loaded Skills

These skills are loaded at every session start:

#### `memory`
Documents the memory system: `SOUL.md` (agent identity), `USER.md` (user preferences), `MEMORY.md` (Dream-managed long-term memory), and `history.jsonl` search patterns.

#### `my`
Self-awareness guide for the `my` tool — diagnosing state, checking budgets, adapting configuration.

### On-Demand Skills

These skills load when triggered:

| Skill | Description | Requirements |
|-------|-------------|--------------|
| `weather` | Weather queries via wttr.in and Open-Meteo | `curl` |
| `cron` | Schedule reminders and recurring tasks | Uses `cron` tool |
| `summarize` | Summarize URLs, podcasts, files | `summarize` CLI (brew install) |
| `github` | GitHub operations (issues, PRs, CI) | `gh` CLI |
| `tmux` | Remote-control tmux sessions | `tmux` (macOS/Linux) |
| `long-goal` | Sustained objectives via long_task/complete_goal | Uses `long_task`/`complete_goal` tools |
| `skill-creator` | Guide for creating new AgentSkills | — |
| `clawhub` | Search/install skills from ClawHub registry | `npx` |
| `image-generation` | Guide for `generate_image` tool usage | — |
| `update-setup` | One-time wizard for upgrade skill | — |

## Custom Skills

### SKILL.md Format

```markdown
---
name: my-skill
description: What this skill does
---

# My Skill

Instructions for the agent when this skill is active.

## Usage

Step-by-step guide for using this skill.
```

### Frontmatter Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Unique skill identifier |
| `description` | yes | Brief description (loaded in context) |
| `register_cmd` | no | If `true`, registers as a `/skill-name` slash command |

### Discovery Paths

Skills are discovered from:
- `skills/` in the workspace
- `~/.vtx/claw/skills/`
- Built-in skills in the agenite-claw package

### Creating a Skill

1. Create a directory: `skills/my-skill/`
2. Create `skills/my-skill/SKILL.md` with frontmatter and instructions
3. Restart the gateway or use `/skill` to verify it appears

## ClawHub

Skills can be installed from the [ClawHub](https://clawhub.com) public registry:

```bash
npx --yes clawhub@latest install <slug> --workdir ~/.vtx/claw/workspace
```

The `--workdir` flag ensures skills install to the agenite-claw workspace.
