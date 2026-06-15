# Storage layout

Vtx stores everything under `~/.vtx/` (with `XDG_CONFIG_HOME` honored). This doc lists every file and directory the app touches, what it contains, and how to clean up state for testing or troubleshooting.

## Top-level layout

```text
~/.vtx/
├── config.yml             # user config (YAML)
├── config.yml.bak.<ts>    # timestamped backups created by migrations
├── bin/                   # optional, tool binaries dropped here are auto-detected
│   ├── fd
│   └── rg
├── openai_auth.json       # OpenAI Codex OAuth credentials (mode 0600)
├── copilot_auth.json      # GitHub Copilot OAuth credentials (mode 0600)
├── dynamic_auth.json      # API keys for airouter/opencode/kilo/tokenrouter (mode 0600)
├── models/                # dynamic provider model catalogs
│   ├── airouter.json
│   ├── opencode.json
│   ├── kilo.json
│   └── tokenrouter.json
└── sessions/              # append-only JSONL session files
    └── <safe-cwd>/
        ├── 3f2a8c1b.jsonl
        ├── 9a1b7e22.jsonl
        └── ...
```

`XDG_CONFIG_HOME` is honored: if it's set, the base is `~/.vtx/` instead of `~/.vtx/`.

## File reference

### `config.yml`

The user config. Created on first run from `src/vtx/defaults/config.yml`. Migrated forward automatically when the schema changes — the old version is backed up as `config.yml.bak.<timestamp>` and the new version is written atomically.

See [configuration.md](configuration.md) for the full schema.

### `config.yml.bak.<timestamp>`

A timestamped backup written before every migration. The format is `<config-name>.bak.YYYYMMDDHHMMSS`. Keep the most recent one; older backups can be deleted.

If a migration is interrupted (e.g. disk full), the in-memory migrated config is still used for the current run, but the next run will migrate again.

### `bin/`

Optional. If you drop `fd` or `rg` here, Vtx auto-discovers them without needing them on `PATH`. Useful in CI or sandboxes. Permissions match whatever you set; Vtx runs them as-is.

### `openai_auth.json`

```json
{
  "refresh": "<refresh-token>",
  "access": "<access-token>",
  "expires": 1718000000000,
  "account_id": "<chatgpt-account-id>"
}
```

Written by `/login` for the OpenAI Codex provider. The file is `chmod 0600` on POSIX. The `access` token is refreshed automatically when within 60 seconds of expiry; the `refresh` token is rotated by OpenAI on every refresh.

To force a re-login: `rm openai_auth.json` and run `/login`.

### `copilot_auth.json`

The GitHub Copilot OAuth credentials. Format and lifecycle are similar to `openai_auth.json`. If `gh` is logged in, Vtx reuses `gh`'s token and may not write this file at all.

### `dynamic_auth.json`

```json
{
  "airouter": "...",
  "kilo": "..."
}
```

API keys for the four dynamic catalog providers, written by `/login <provider>`. Mode `0600` on POSIX. YAML form is also supported (`dynamic_auth.yml`) and takes precedence when both exist.

Lookup priority: `<NAME>_API_KEY` env var → this file → placeholder (for free-tier providers).

### `models/`

Per-provider model catalog cache. Each file is a `CachedCatalog` JSON dump:

```json
{
  "provider": "kilo",
  "fetched_at": 1718000000.0,
  "models": [
    {
      "id": "...",
      "name": "...",
      "context_window": 200000,
      "max_tokens": 16384,
      "supports_images": true,
      "supports_thinking": true,
      "is_free": false,
      "pricing_known": true,
      "raw": { ... raw response from the gateway ... }
    },
    ...
  ]
}
```

6-hour TTL with stale-while-revalidate. Refresh on demand with `/model refresh [provider]`. You can delete individual files to force a re-fetch on next lookup.

The cache location can be overridden with `VTX_MODELS_CACHE_DIR`.

### `sessions/`

Append-only JSONL files. See [sessions.md](sessions.md) for the format. The directory layout is one folder per `cwd`:

```text
sessions/
├── home-me-projects-foo/
│   ├── 3f2a8c1b.jsonl
│   └── 9a1b7e22.jsonl
└── home-me-projects-bar/
    └── 7c41b637.jsonl
```

`<safe-cwd>` is the absolute path with `/` and `\` replaced by `-` and leading/trailing `-` stripped. Created with mode `0700` because sessions can contain sensitive content (your prompts, the model's responses, file paths, sometimes credentials you pasted).

Inside a `<safe-cwd>/` directory, each session is a single JSONL file named after its 8-character id. There is no index — listing is a directory scan.

## Global state (outside `~/.vtx`)

### `~/.agents/skills/`

User-global skills. Each subdirectory is a skill with a `SKILL.md` file inside. See [skills.md](skills.md).

### Project-local state (`.agents/skills/`, `AGENTS.md`, `CLAUDE.md`)

In the cwd or any ancestor up to the git root:

- `.agents/skills/<name>/SKILL.md` — project skills
- `AGENTS.md` — project instructions
- `CLAUDE.md` — alternative name for `AGENTS.md` (same content, used interchangeably)

These are read but never written. To clear, just delete the files.

## Cleaning up state

| Goal | What to delete |
| --- | --- |
| Reset to defaults | `rm -rf ~/.vtx/` (loses sessions and OAuth) |
| Re-login to OpenAI Codex | `rm ~/.vtx/openai_auth.json` |
| Re-login to GitHub Copilot | `rm ~/.vtx/copilot_auth.json` |
| Force a model catalog refresh | `rm ~/.vtx/models/<provider>.json` |
| Log out of one dynamic provider | `/logout <provider>` (clears the key) or edit `dynamic_auth.json` |
| Wipe all sessions for a cwd | `rm -rf ~/.vtx/sessions/<safe-cwd>/` |
| Wipe all sessions | `rm -rf ~/.vtx/sessions/` |
| Wipe all config | `rm ~/.vtx/config.yml` (next run recreates from defaults) |

## Path resolution order

For most lookups, Vtx checks in this order:

1. **CLI flag** — overrides everything for the current run.
2. **Config** — `~/.vtx/config.yml`.
3. **Built-in default** — `src/vtx/defaults/config.yml` (shipped in the wheel).

For credentials, the order is:

1. **Env var** (`OPENAI_API_KEY`, `KILO_API_KEY`, etc.).
2. **OAuth / stored file** in `~/.vtx/`.
3. **Placeholder** for providers that support unauthenticated access (Airouter, Kilo).

For sessions, the cwd at session creation is what's stored. Resuming later from a different cwd is allowed as long as you reference the session by id or use `--continue`.

## Permissions on disk

| Path | Mode | Why |
| --- | --- | --- |
| `~/.vtx/` | `0755` | Standard config dir permissions. |
| `~/.vtx/config.yml` | `0644` | Default; may contain your custom prompt — lock down if you care. |
| `~/.vtx/sessions/` | `0755` | Standard. |
| `~/.vtx/sessions/<safe-cwd>/` | `0700` | Sessions can contain sensitive content. |
| `~/.vtx/sessions/<safe-cwd>/<id>.jsonl` | `0600` | Same. |
| `~/.vtx/openai_auth.json` | `0600` | Contains a refresh token. |
| `~/.vtx/copilot_auth.json` | `0600` | Contains a Copilot token. |
| `~/.vtx/dynamic_auth.json` | `0600` | Contains API keys. |
| `~/.vtx/models/*.json` | `0644` | Public model metadata, no secrets. |

On Windows, `chmod` is a no-op for some of these — the file ACLs are inherited from the user's profile, which is typically already locked down. Don't share the config dir on a multi-user Windows host.
