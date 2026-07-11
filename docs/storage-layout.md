# Storage layout

Vtx keeps its state under a single config directory (`~/.vtx`) plus project-local `.vtx/` and `.agents/` folders.

## User config directory (`~/.vtx`)

| Path | Purpose |
|------|---------|
| `~/.vtx/config.yml` | Main configuration (see [configuration.md](configuration.md)) |
| `~/.vtx/sessions/<safe_cwd>/*.jsonl` | Per-project conversation history (see [sessions.md](sessions.md)) |
| `~/.vtx/providers/*.yaml` | User-wide custom provider definitions (see [providers.md](providers.md)) |
| `~/.vtx/agent/*.py` | Global handoff agent profiles (see [agents.md](agents.md)) |
| `~/.vtx/agent/extensions/*.py` | Global extensions (see [extensions.md](extensions.md)) |
| `~/.vtx/AGENTS.md` | User-global project guidelines (see [skills.md](skills.md)) |
| `~/.vtx/bin/` | Downloaded bundled binaries (fd, ripgrep) |

## Project-local (`.vtx/` and `.agents/` at repo root)

| Path | Purpose |
|------|---------|
| `.vtx/providers/*.yaml` | Project-local custom providers (highest precedence) |
| `.vtx/agent/*.py` | Project-local handoff agent profiles |
| `.vtx/extensions/*.py` | Project-local extensions |
| `AGENTS.md` / `CLAUDE.md` | Project guidelines, discovered from git root down to cwd |
| `.agents/skills/<name>/` | Project-local skills (see [skills.md](skills.md)) |

## Global skills

`~/.agents/skills/<name>/` — user-wide skills available in every project.

## Session files

`~/.vtx/sessions/<safe_cwd>/<timestamp>_<session_id>.jsonl` — one JSONL event per line (see [sessions.md](sessions.md)).

## Notes

- The config dir name is `vtx` (`~/.vtx`); it is derived from `CONFIG_DIR_NAME` in `src/vtx/config.py`.
- Session directories are created with `0o700` permissions.
- Project-local providers/agents/extensions override global ones on name collision; nearer project files win over farther ones when walking up to the git root.
