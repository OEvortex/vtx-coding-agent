# Storage layout

Vtx keeps its state under a single config directory (`~/.vtx`) plus project-local `.vtx/` and `.agents/` folders.

## User config directory (`~/.vtx`)

| Path | Purpose |
|------|---------|
| `~/.vtx/config.yml` | Main configuration (see [configuration.md](configuration.md)) |
| `~/.vtx/sessions/<safe_cwd>/*.jsonl` | Per-project conversation history (see [sessions.md](sessions.md)) |
| `~/.vtx/copilot_auth.json` | GitHub Copilot OAuth token cache; other per-provider credential files sit alongside |
| `~/.vtx/providers/*.yaml` | User-wide custom provider definitions (see [providers.md](providers.md)) |
| `~/.vtx/models/` | Cached dynamic model catalogs (~6 h TTL) |
| `~/.vtx/scratchpads/vtx-scratchpad-*` | Per-session scratch space |
| `~/.vtx/agent/*.py` | Global handoff agent profiles (see [agents.md](agents.md)) |
| `~/.vtx/agent/extensions/*.py` | Global extensions (see [extensions.md](extensions.md)) |
| `~/.vtx/skills/<name>/` | Skills in the legacy vtx location |
| `~/.vtx/bin/` | Auto-downloaded binaries (`fd`, `rg`) |
| `~/.vtx/hooks.yml` | Global hook config (project `.vtx/hooks.yml` takes precedence) |
| `~/.vtx/installed_extensions.yml` | `vtx install` ledger |

## Project-local

| Path | Purpose |
|------|---------|
| `.vtx/providers/*.yaml` | Project custom providers (highest precedence) |
| `.vtx/agent/*.py` | Project handoff agents, walked up to the git root |
| `.vtx/extensions/*.py` | Project extensions |
| `.vtx/hooks.yml` | Project hooks |
| `AGENTS.md` / `CLAUDE.md` | Project guidelines, discovered from git root down to cwd |
| `.agents/skills/<name>/` | Project skills (see [skills.md](skills.md)) |

## Global skills

`~/.agents/skills/<name>/` — user-wide skills available in every project.

## Session files

One JSONL event per line; header first, then a tree of entries. Directories are created with `0o700`. See [sessions.md](sessions.md) for the entry types.

## Notes

- The config dir is `Path.home() / ".vtx"` (`core.paths.get_config_dir`).
- Nearer files win: project beats global, cwd beats git-root when walking up.
