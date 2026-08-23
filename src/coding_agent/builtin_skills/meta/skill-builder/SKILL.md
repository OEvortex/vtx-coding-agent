---
name: skill-builder
description: Create a new Vtx skill (SKILL.md) with the correct frontmatter, body, and conventions
register_cmd: true
cmd_info: scaffold a new skill
category: meta
---

Create a new Vtx skill at the right location with the right shape.

A skill is a directory containing a `SKILL.md`. Vtx auto-discovers skills from `<cwd-or-ancestor>/.agents/skills/` (project) and `~/.agents/skills/` (user). Project skills shadow user skills with the same name. The skill name MUST match the directory name.

User-provided focus or constraints (honor these):
$ARGUMENTS

## Where to put it

- Project skill (preferred for repo-specific workflows): `<repo>/.agents/skills/<name>/SKILL.md`. Use this when the skill encodes conventions, commands, or escape hatches that only make sense in this repo.
- User skill (preferred for personal workflows): `~/.agents/skills/<name>/SKILL.md`. Use this for cross-repo utilities (e.g. "release-checklist", "draft-pr").
- Never put a skill at the repo root or in a non-`.agents/skills/` path — Vtx will not discover it.

## Frontmatter (YAML between `---` markers)

Required:
- `name` — lowercase letters, digits, hyphens. Must equal the directory name. Max 64 chars. No leading/trailing hyphen, no `--`.

Recommended:
- `description` — required (the loader rejects skills without one). One short sentence, max 1024 chars. Describe the trigger, not the implementation. The agent uses this to decide when to load the skill.
- `category` — one of `setup`, `review`, `workflows`, `meta`, `general`, etc. Categories group skills in the `<available_skills>` index. Default is `general` if omitted. Max 32 chars.
- `register_cmd: true` — expose the skill as a slash command (`/<name>`) and include it in the `/` menu. Add this when the skill is something the user would invoke explicitly.
- `register_cmd: only` — register the slash command but do NOT inject the skill into the system prompt. Use for heavy skills that should be loaded on demand only.
- `cmd_info` — short label shown in the slash menu (max 32 chars). Required when `register_cmd` is true.

Minimal valid frontmatter:

```yaml
---
name: my-skill
description: Do the X thing when the user asks for X
---
```

Full frontmatter:

```yaml
---
name: my-skill
description: Do the X thing when the user asks for X
register_cmd: true
cmd_info: run the X thing
category: workflows
---
```

## Body

The body is plain Markdown, shown verbatim when the skill loads. Treat it as instructions to a future agent.

Conventions:
- Open with a one-line summary of what the skill does.
- Include `User-provided focus or constraints (honor these):` followed by `$ARGUMENTS` so the slash-command arguments are passed through.
- Use `##` and `###` sections. Vtx renders them as headings; the agent uses them to skim.
- Prefer concrete commands, file paths, and decision rules over prose.
- Keep it focused. A skill that tries to cover everything is worse than several focused skills. Move long reference material to a separate file in the skill directory and link to it.

## Validating

After writing, sanity-check:
1. `name` matches the directory name.
2. Description is non-empty, one sentence, names the trigger condition.
3. If `register_cmd: true` is set, `cmd_info` is set and under 32 chars.
4. Category (if set) is short and reusable across skills.
5. No required frontmatter field is missing.

Vtx logs warnings to stderr on load for invalid skills. Restart Vtx or run `/new` to reload the index after creating a new skill.

## What to write vs. what to skip

Write:
- exact commands the agent would otherwise guess wrong
- repo or workflow-specific decision rules
- escape hatches for known gotchas
- the user-provided arguments passthrough line (`$ARGUMENTS`)

Skip:
- generic coding advice (the base system prompt already covers it)
- long tutorials that belong in README or docs
- speculative or unverifiable claims
- duplicated content that is already in `AGENTS.md` (reference it instead)

## Example

For a skill that drafts release notes from the recent git log:

Path: `~/.agents/skills/draft-release-notes/SKILL.md`

```markdown
---
name: draft-release-notes
description: Draft release notes from `git log` between the last tag and HEAD
register_cmd: true
cmd_info: draft release notes
category: workflows
---

Draft release notes for the next release.

User-provided focus or constraints (honor these):
$ARGUMENTS

## Steps

1. Find the last release tag: `git describe --tags --abbrev=0`.
2. Collect commits since that tag: `git log <tag>..HEAD --oneline --no-merges`.
3. Group by area (use the conventional-commit prefix or the directories touched).
4. Write a short bullet list under `## Highlights` and `## Fixes`, mirroring the user's requested tone.
5. Print the result; do not write to a file unless the user asked.

## Style

- One bullet per change. Lead with the user-facing effect, not the implementation.
- Group by area, not by commit order.
- Skip dependency bumps and pure refactors unless they affect behavior.
```

## Common mistakes to avoid

- Setting `description` to a list of features instead of a trigger condition. The agent reads the description to decide whether to load the skill — a vague description means the skill rarely fires.
- Putting long body content in `description`. Description is a summary, not the body.
- Forgetting `$ARGUMENTS`. Without it, slash-command arguments are dropped.
- Naming the skill `foo` but putting it in `.agents/skills/foo-bar/`. The loader warns and may not surface the skill.
- Setting `register_cmd: true` without `cmd_info`. The slash menu will show an empty entry.
- Writing a skill that duplicates an existing one. Run `/` and check the menu first; if a similar skill exists, extend it or add a category to disambiguate.
