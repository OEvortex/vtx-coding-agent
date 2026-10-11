# Skills

A skill is a directory of markdown instructions that the model reads on demand, and this page covers how Vtx discovers them, what frontmatter it parses, and how a skill reaches the model. Use a skill when a workflow needs more context than a prompt template but no executable integration point.

Vtx skills are markdown only. The old python-skill mechanism (a `pyproject.toml` plus a `src/` package imported into a persistent IPython kernel) went with the RLM mode, and the loader no longer reads those files at all: a `pyproject.toml` dropped in a skill directory is ignored, and the `SKILL.md` body is treated as instructions (`src/vtx/coding_agent/builtin_skills/meta/vtx-self-extend/SKILL.md:51`). The `skill` tool's `run` action states the same thing when you call it (`src/vtx/coding_agent/tools/skill.py:226`). There is no `is_kernel_skill` and no `skills_for_mode` any more.

## Layout

A skill is a directory containing `SKILL.md`. Anything else in the directory is optional and is read only when the instructions reference it.

```text
release-notes/
├── SKILL.md            # required: frontmatter, then instructions
├── references/
│   └── changelog.md    # optional detail, loaded only when referenced
└── scripts/
    └── draft.py        # optional helper the instructions tell the model to run
```

The loader accepts only directories: `_load_skill_from_dir` returns `None` for any directory without a `SKILL.md` file, and entries whose name starts with `.` are skipped (`src/vtx/agent/context/skills.py:181`, `:249`). Paths inside the body resolve against the directory that holds `SKILL.md`, which the `skill` tool restates in its output (`src/vtx/coding_agent/tools/skill.py:195`).

## Frontmatter

`SKILL.md` opens with a `---` fenced block, parsed as YAML when PyYAML is importable and by a line scanner otherwise (`src/vtx/agent/context/skills.py:94`). The loader reads exactly these keys.

| Key | Required | Rules |
|---|---|---|
| `name` | yes | Lowercase `a-z`, digits, hyphens. Max 64 chars. No leading, trailing, or consecutive hyphens. Must equal the parent directory name. |
| `description` | yes | Max 1024 chars. An empty or missing description means the skill is not loaded at all. |
| `register_cmd` | no | `true` also exposes the skill as `/<name>`. `only` registers the command and keeps the skill out of the prompt catalog. Defaults to `false`. |
| `cmd_info` | no | Short label for the `/` menu. Max 32 chars. |
| `category` | no | Lowercased on read. Max 32 chars. Defaults to `general`. |
| `include_in_prompt` | no | Not read from frontmatter. See the note below. |

The limits are the module constants `MAX_NAME_LENGTH` (64), `MAX_DESCRIPTION_LENGTH` (1024), `MAX_CMD_INFO_LENGTH` (32), and `MAX_CATEGORY_LENGTH` (32), with `DEFAULT_SKILL_CATEGORY = "general"` (`src/vtx/agent/context/skills.py:25`).

Every rule above except the missing description is a **warning, not a rejection**. `_validate_skill` collects a `SkillWarning` for a name that does not match the directory, an over-long name, description, `cmd_info`, or category, and a name that breaks the character rules; the skill still loads (`src/vtx/agent/context/skills.py:132`). The only hard failure is an empty description, which returns `None` (`:203`).

`register_cmd` is opt-in, and the reason is recorded in the loader: defaulting it to true put every installed skill in the slash list and let a skill named `compact` shadow the real `/compact` (`src/vtx/agent/context/skills.py:206`). The `only` value is the escape hatch for a command that must not be routable by the model:

```yaml
register_cmd: only      # /my-thing works; the skill stays out of <available_skills>
```

`include_in_prompt` is a field on `Skill` (`src/vtx/agent/context/skills.py:56`) and both catalog renderers filter on it (`:511`, `:646`), but `_load_skill_from_dir` never reads it from frontmatter. The loader sets it to `not cmd_only` (`:226`), so `register_cmd: only` is currently the only way to produce `include_in_prompt: false`.

`license`, `allowed-tools`, and `disable-model-invocation` are Agent-Skills keys that Vtx ignores. Anything else you put in the block is parsed and discarded.

## Where skills come from

`load_skills` reads three places, in this order, and the first directory to supply a name wins (`src/vtx/agent/context/skills.py:375`):

1. `<cwd>/.agents/skills/`, then the same path in each ancestor directory, stopping at the git root when there is one and at the cwd otherwise (`_project_skill_dirs`, `:309`; `_find_git_root`, `:294`). A skill in the repo root is therefore visible from any subdirectory of that repo.
2. `~/.agents/skills/` (`get_agents_dir`, `src/vtx/core/paths.py:47`).
3. `<vtx config dir>/skills/`, normally `~/.vtx/skills/`. Skills loaded from here are marked `bundled = True` (`:414`). This is where `sync_builtin_skills` copies the skills that ship inside installed packages (`:342`).

A fourth source is merged in after the fact: `load_builtin_cmd_skills` walks each registered package's `builtin_skills/` directory, two levels deep, so `builtin_skills/<category>/<skill>/SKILL.md` is found (`:424`). `Context.load` merges that list behind the on-disk list, so a project or user skill with the same name as a bundled one wins (`src/vtx/agent/context/loader.py:36`; `merge_registered_skills`, `:499`).

Precedence is therefore: the deepest project directory, then the shallowest project ancestor, then `~/.agents/skills`, then the synced and bundled copies.

A collision is first-write-wins and produces a warning rather than an error (`src/vtx/agent/context/skills.py:392`). If you edit a skill and nothing changes, you are probably editing the losing copy.

## How a skill reaches the model

The system prompt carries one flat list, not the bodies. `formatted_skills` renders a `<available_skills>` block with one `<skill>` entry per skill holding only `<name>` and `<description>`, sorted by name (`src/vtx/agent/context/skills.py:510`). It deliberately omits the path: naming the file made the model read it directly and skip the `skill` tool.

```xml
## Skills (mandatory)

<available_skills>
  <skill>
    <name>review</name>
    <description>Review code changes and return prioritized, actionable findings</description>
  </skill>
</available_skills>
```

The prompt builder appends that block as the fourth section, after the base identity, tooling rules, and discovered `AGENTS.md` files, and before the git snapshot (`src/vtx/agent/prompts/builder.py:102`). The body is read only when the model calls `skill(action="load")`.

When the catalog is too large, `formatted_skills_index` renders one line per skill under a budget of 1200 estimated tokens, four characters to a token, picking lines round-robin across categories so a single large category cannot crowd the rest out; skills that do not fit are counted in a trailing note (`:555`, `:563`, `:627`). Omitted skills stay reachable through `skill(action="list")`.

## The `skill` tool

The `skill` tool is registered by the `tool-skill` plugin row (`src/vtx/plugins/builtins/__init__.py:49`), so `vtx plugin disable tool-skill` removes it from the composition. That row does take effect, unlike four others - see [plugins.md](plugins.md).

| Action | Parameters | What it does |
|---|---|---|
| `load` | `name`, `file_path?` | Returns the stripped body, the base directory, and up to 10 files beside `SKILL.md`. The model's normal way to use a skill. |
| `list` | | Every skill from project, user, and bundled scopes, each tagged `project`, `global`, or `bundled`. |
| `view` | `name`, `file_path?` | Raw file read, frontmatter included. |
| `create` | `name`, `content`, `scope?` | Writes `SKILL.md` into `.agents/skills/<name>` or `~/.agents/skills/<name>`. |
| `edit` | `name`, `content`, `file_path?` | Overwrites the file. |
| `patch` | `name`, `old_string`, `new_string`, `file_path?` | Find-replace. Fails when `old_string` is absent or not unique. |
| `delete` | `name` | `shutil.rmtree` on the skill directory. |
| `run` | `name`, `file_path?` | Returns the file and says that the persistent kernel is gone. Use `view` for the path alone. |

The literals are the `action` field of `SkillParams` (`src/vtx/coding_agent/tools/skill.py:28`). The mutating actions refuse to touch a bundled skill (`:260`). `name` is required for every action except `list` (`:129`).

The tool resolves a name through the same three on-disk scopes and then the packaged `builtin_skills` tree (`find_skill_dir`, `:134`). `create` validates the name with `islower()` and `replace("-", "").isalnum()`, which is looser than the loader's rules: it accepts a leading, trailing, or doubled hyphen that the loader will then warn about.

## Registered slash commands

A skill with `register_cmd: true` becomes `/<name>`. `_sync_slash_commands` appends it to the command list with `cmd_info` as its description, falling back to the first 32 characters of the description (`src/vtx/tui/app.py:486`). A skill whose name collides with a built-in command is not listed twice, and the built-in wins at submit time (`:1076`), which is why a skill cannot shadow `/compact`.

Typing `/<name> <query>` renders the body through `render_skill_prompt`, which substitutes `$ARGUMENTS` when the template contains it and otherwise appends the query after the body (`src/vtx/agent/context/skills.py:470`). `/skill:<name> <query>` is the explicit spelling for the same thing (`src/vtx/tui/app.py:1092`).

## Worked example

Create a project skill, then reload and run it.

`.agents/skills/release-notes/SKILL.md`:

```markdown
---
name: release-notes
description: Draft release notes from the CHANGELOG. Use when the user asks for release notes, a changelog summary, or "what changed since the last tag".
register_cmd: true
cmd_info: draft release notes
category: meta
---

# Release notes

Read `../changelog-format.md` for the house style, then summarise every
`feat:`, `fix:`, and `docs:` entry since the last tag.

User-provided focus or constraints (honour these):
$ARGUMENTS
```

```bash
mkdir -p .agents/skills/release-notes
# write the file above, then:
/reload                      # picks up new and edited skills with no restart
/release-notes 1.2.0..HEAD   # $ARGUMENTS becomes "1.2.0..HEAD"
```

`/reload` rebuilds the context, so `Context.reload` re-runs discovery and the slash list is re-synced (`src/vtx/agent/context/loader.py:42`; `src/vtx/tui/commands/reload.py:171`). Loading problems surface as launch warnings rather than a crash (`src/vtx/tui/app.py:649`).

You do not need `/reload` for a catalog change to reach a running turn. Each run boundary at depth 0 re-reads the catalog and, when the fingerprint changed, swaps one context message in place announcing what was added, removed, or reworded (`src/vtx/agent/loop.py:156`, `:206`). An unreadable catalog preserves the last known one rather than announcing that no skills exist.

## The bundled skills

`register_skills_package("vtx.coding_agent")` runs at package import (`src/vtx/coding_agent/__init__.py:36`), and `sync_builtin_skills` copies the result into `~/.vtx/skills/` on every context load (`src/vtx/agent/context/loader.py:31`).

| Skill | Category | Registered as a command |
|---|---|---|
| `google-colab` | `cloud` | yes |
| `modal` | `cloud` | yes |
| `review` | `review` | yes |
| `github` | `general` | yes |
| `goal` | `meta` | yes |
| `skill-builder` | `meta` | yes |
| `vtx-self-extend` | `meta` | yes |
| `init` | `setup` | yes |

`/goal` is one of these skills rather than a built-in command, which is why it is absent from the app's command router (`src/vtx/coding_agent/README.md:84`).

## Public API

`vtx.agent.skills` re-exports the loader, and `vtx.agent.context` re-exports the subset the prompt builder and the SDK use (`src/vtx/agent/skills.py:8`, `src/vtx/agent/context/__init__.py:4`). The SDK helper `format_skills_for_prompt` renders a plain `- name: description` list for an agent you build yourself (`src/vtx/agent/sdk/skills.py:40`).

```python
from vtx.agent.skills import load_skills

result = load_skills("/path/to/project")
for skill in result.skills:
    print(skill.name, skill.category, skill.register_cmd, skill.bundled)
for path, message in result.warnings:
    print("warning", path, message)
```

## Known gaps

- `include_in_prompt` is not parsed from frontmatter. Set `register_cmd: only` to get the effect.
- A name that does not match its directory, an over-long field, or a badly formed name is a warning only. The skill loads anyway.
- A name collision is silent beyond the warning: the losing copy is simply never reachable, and the warning is easy to miss in the startup list.
- The bundled skills are synced to `~/.vtx/skills/` on every context load, so editing a copy there changes what the model sees until the next sync overwrites it.
- `sync_builtin_skills` swallows every exception (`src/vtx/agent/context/skills.py:371`), so a copy failure leaves a stale or missing bundled tree with no diagnostic.
- The compact index drops skills once the budget is spent. A skill omitted from it is still installed and still loadable by name, but the model is never told it exists except through the omitted count.

See [plugins.md](plugins.md) for the row that registers the `skill` tool, [extensions.md](extensions.md) for the `ExtensionAPI` surface, [configuration.md](configuration.md) for config files, [cli.md](cli.md) for the command-line flags, and [tools.md](tools.md) for the tool contract.
