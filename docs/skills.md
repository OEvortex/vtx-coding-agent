# Skills

Skills are reusable instruction packs that Vtx can discover, render into the system prompt, or expose as manual slash commands. This doc covers authoring, the full frontmatter schema, and how the loading pipeline works.

A skill is just a directory with a `SKILL.md` file. The file is plain Markdown with a small YAML frontmatter block. That's it.

## The minimum viable skill

Create a directory and a `SKILL.md` inside it:

```bash
mkdir -p ~/.agents/skills/my-skill
$EDITOR ~/.agents/skills/my-skill/SKILL.md
```

```markdown
---
name: my-skill
description: One-line summary of what this skill does.
---

# My skill

Detailed instructions for the agent. The system prompt will tell the model
to load this file with the read tool whenever the description matches the
current task.
```

The `name` in the frontmatter **must** match the directory name. If they don't match, Vtx emits a startup warning but the skill still loads.

## Discovery paths

Vtx looks for skills in two places:

| Path | Scope | Use it for |
| --- | --- | --- |
| `<cwd-or-ancestor>/.agents/skills/<name>/SKILL.md` | Project | Repo-specific workflows, conventions, and escape hatches. Committed to the repo. |
| `~/.agents/skills/<name>/SKILL.md` | User (global) | Your personal workflows, applied to every project you work on. |

Project skills take precedence over user skills with the same name (the project one wins, the user one is ignored with a startup warning).

For project skills, the ancestor walk goes from the cwd up to the git root (or the cwd if there's no git root), inclusive. So a skill in `<git-root>/.agents/skills/` is visible to every subdirectory of the repo.

## Frontmatter

```yaml
---
name: my-skill                  # required, matches directory name
description: ...                # required, 1-1024 chars
register_cmd: false             # optional, see below
cmd_info: ""                    # optional, max 32 chars
category: general               # optional, max 32 chars, default "general"
---
```

### `name` (required)

- Lowercase letters, digits, and hyphens only. Regex: `^[a-z0-9-]+$`.
- No leading or trailing hyphen.
- No consecutive hyphens (`--`).
- Max 64 characters.
- Must match the parent directory name.

### `description` (required)

- 1–1024 characters.
- Used for two things: discovery (the model reads this to decide whether to load the skill) and prompt context (it's listed in the system prompt under `<available_skills>`).
- Write it as you'd describe the skill to a colleague who has never seen it. The model decides whether to load your file based on this string, so be specific.

### `register_cmd` (optional, default `false`)

Controls whether the skill is also exposed as a manual slash command.

| Value | Behavior |
| --- | --- |
| `false` (or omitted) | Skill is described to the model in the prompt. The model can choose to load it on its own when relevant. Not exposed as a slash command. |
| `true` | Skill is described to the model **and** exposed as `/<name>`. The user can invoke it manually with the slash command. |
| `only` | Skill is **not** described to the model. It's only available as a manual `/<name>` slash command. Use this for "do this exact thing" workflows that the model shouldn't improvise around. |

Boolean parsing is forgiving: `true`, `True`, `TRUE`, `yes`, `on`, `1` all work. The string `"only"` is the special value that hides the skill from the prompt.

### `cmd_info` (optional, max 32 chars)

Short label shown in the `/` slash-command popup. Example: `cmd_info: "review code changes"`. Without this, the popup falls back to a truncated `description`.

### `category` (optional, max 32 chars, default `"general"`)

Used to group skills in the system prompt's `<available_skills>` block. Skills in the same category are listed together. Built-in categories shipped with Vtx: `general`, `setup`, `review`. Use anything you like for your own skills.

## `$ARGUMENTS`

If your skill's body contains the literal string `$ARGUMENTS`, Vtx substitutes the rest of the slash command line into that spot. If the body doesn't contain `$ARGUMENTS`, Vtx appends the args on a new line at the end (or the start, if the body is empty — it just becomes the prompt).

Examples:

```markdown
---
name: review
register_cmd: true
cmd_info: review code changes
description: Review code changes and return prioritized, actionable findings
---

Review the requested code changes as if you are reviewing another engineer's PR.

User-provided target or constraints (honor these):
$ARGUMENTS
```

Run with `/review` (no args) → the model gets the skill body with no extra text.

Run with `/review PR#68 feat/headless-mode "feat: add non-interactive prompt mode"` → `$ARGUMENTS` is replaced with the entire argument string.

If you run `/review` and your skill body doesn't have `$ARGUMENTS`, Vtx appends the args on a new line at the end of the body. So `/review please look at the auth flow` adds a final line "please look at the auth flow" to the skill body.

## Path resolution

When your skill references relative paths, they resolve against the skill's directory, not the current working directory. The system prompt includes a "References are relative to `<skill-dir>`" hint so the model uses the right base. The header looks like:

```xml
<skill name="my-skill" location="/home/me/.agents/skills/my-skill/SKILL.md">
References are relative to /home/me/.agents/skills/my-skill.

# My skill
...
</skill>
```

If you want the model to be able to read companion files (e.g. a checklist, a sample output), put them in the skill's directory and reference them with paths like `./checklist.md`.

## Built-in skills

Vtx ships three built-in skills under `src/vtx/builtin_skills/`:

- **`init`** — `/init`: Create or update `AGENTS.md` for the current repository. Reads the repo's top-level files, configuration, and existing instruction files; produces a focused `AGENTS.md` that captures the highest-signal conventions. Category: `setup`.
- **`review`** — `/review`: Review code changes (working tree, base branch, commit SHA, or PR) and return prioritized, actionable findings. The skill ships with its own review rubric and the format for `[P0]`–`[P3]` findings. Category: `review`.
- **`skill-builder`** — `/skill-builder`: Scaffold a new skill (the directory + `SKILL.md` with valid frontmatter) at the right location, for either a project or a user-global skill. Category: `meta`.

All three have `register_cmd: true` and show up in the `/` popup. They're loaded from the wheel, so you can't edit them in place — fork the project or write your own.

## The skill loading pipeline

For reference (and for debugging the load order), the pipeline is in [`src/vtx/context/skills.py`](../src/vtx/context/skills.py):

1. **Project walk** — starting at the cwd, walk up to the git root. For each directory, load `<dir>/.agents/skills/<name>/SKILL.md`. Project skills are added to the result map first.
2. **User dir** — load `~/.agents/skills/<name>/SKILL.md`. If a name is already in the map (from a project skill), the user skill is skipped and a warning is emitted.
3. **Built-in** — load `src/vtx/builtin_skills/<name>/SKILL.md`. Same name-collision rule.
4. **Validate** — for each skill, check the frontmatter against the rules above. Warnings (not errors) are emitted; the skill still loads unless the failure is "no description".
5. **Render** — for the system prompt, only skills with `include_in_prompt: true` are rendered. That flag is `False` only when `register_cmd: "only"`.
6. **Sort and group** — skills in the prompt are grouped by `category`, sorted alphabetically within each group.

The full result (skills + warnings) is returned to the agent runner. Warnings are surfaced as startup messages in the TUI.

## Authoring tips

- **Description is the most important field.** The model uses it to decide whether to load your skill. Write it as if you're tagging the skill for a search. "Review code changes" is bad. "Review the current diff and return prioritized, actionable findings with line ranges" is good.
- **Keep skills focused.** One workflow per skill. If you find yourself writing "if the user wants X, do A; if they want Y, do B", split into two skills.
- **Make `cmd_info` a verb phrase.** It shows up in the slash popup as a one-line label: `/init — guided AGENTS.md setup`, `/review — review code changes`.
- **Use `$ARGUMENTS` for argument-passing workflows.** Don't make the model parse the args from the appended line — substitute them where they make sense in the body.
- **Test by listing.** Run `/` in the TUI, see what shows up. If your skill isn't listed, check the launch warnings for validation messages.
- **Local skills override global ones.** If you have a personal `/init` in `~/.agents/skills/` and a project one in `.agents/skills/`, the project one wins. Use namespaces (`init-frontend`, `init-backend`) if you need both.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Skill doesn't appear in `/` | Missing `register_cmd: true`, or the file isn't being found (wrong path, wrong name). Run with `--verbose` or check the launch warnings. |
| Skill appears in `/` but doesn't fire | The skill body might be empty or have a syntax error. Check the validation warnings. |
| Skill loads but the model ignores it | Description is too vague. The model isn't matching it to the current task. Be more specific. |
| `name "X" does not match directory "Y"` warning | Frontmatter `name` differs from the directory name. Fix one or the other. |
| `name collision: "X" already loaded from Y` | Two skills with the same name at different scopes. The project one wins; rename or delete the loser. |
| Skill file referenced by the body isn't found | The model used a path relative to the cwd instead of the skill directory. Re-phrase your reference (e.g. `./checklist.md` works because the model was told the base path in the skill header). |
