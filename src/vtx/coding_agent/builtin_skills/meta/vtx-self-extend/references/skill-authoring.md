# Skill authoring for Vtx

## Where a skill lives

Resolved in order, first match per name wins (`src/vtx/agent/context/skills.py:449`):

1. `.agents/skills/<name>/SKILL.md`, found by walking from the cwd up to the git root
   (`_project_skill_dirs`, `:383`). So a skill in the repo root is visible from any
   subdirectory of that repo, and nowhere else.
2. `~/.agents/skills/skills/<name>/SKILL.md` (`get_agents_dir()`, `src/vtx/core/paths.py:47`).
3. `<vtx config dir>/skills/<name>/SKILL.md` - typically `~/.vtx/skills/`.

A name collision is resolved first-write-wins, so if you edit a skill and nothing
changes, you are probably editing the losing copy. Confirm which one you hit before
debugging anything else.

## Layout

```
my-skill/
├── SKILL.md              # required: frontmatter + instructions
├── scripts/              # optional helper scripts the instructions reference
├── references/           # optional detail, loaded only when referenced
└── assets/               # optional templates and data
```

Paths inside the body resolve against the directory that contains `SKILL.md`.

## Frontmatter

Vtx parses exactly these keys (`src/vtx/agent/context/skills.py:62-67`):

| Key | Required | Rules |
|---|---|---|
| `name` | yes | lowercase, digits, hyphens. Max 64 chars. No leading/trailing/consecutive hyphens. **Must equal the parent directory name.** |
| `description` | yes | Max 1024 chars. **A missing or empty description means the skill is not loaded.** Write the trigger condition, not a feature list. |
| `register_cmd` | no | `true` also exposes it as `/<name>`. `only` registers the command but keeps it out of the system prompt. Opt-in: it defaults to `false` here, unlike the dataclass default. |
| `cmd_info` | no | Short label for the `/` menu. Max length enforced (`MAX_CMD_INFO_LENGTH`, `:209`). |
| `include_in_prompt` | no | Set `false` to keep the skill out of the prompt catalog. |
| `category` | no | Lowercase. Groups the skill in the index. Defaults to `DEFAULT_SKILL_CATEGORY`. |
| `kind` | no | Leave unset. `python` is parsed and then hidden - see the main SKILL. |

`license`, `allowed-tools`, and `disable-model-invocation` are **prime-agent keys, not
Vtx keys**. Vtx ignores them.

Write the description for routing. It is the only thing the model reads before deciding
to load the file, so name the concrete task and the phrases a request would contain.

## How the model finds it

There is no skill tool. Skills reach the model only as a flat list built by
`format_skills_for_prompt` (`src/vtx/agent/sdk/skills.py:40`):

```
# Available skills

- vtx-self-extend: Add capability to Vtx itself - ...
```

Only the name and description. The body loads when the model opens the file itself, so a
description that does not obviously match the request means the skill never fires.

## Verification

1. Re-read the frontmatter against the table above, especially `name` == directory name.
2. `/reload` picks up new and edited markdown skills with no restart. Loading problems
   surface as warnings - read them.
3. The skill is also callable as `/<name>` when `register_cmd: true`.
