# Vtx documentation

This folder is the reference companion to the top-level [README](../README.md). The README is the tour; the docs here are the deep dive.

## User docs

| Doc | What it covers |
| --- | --- |
| [configuration.md](configuration.md) | Every YAML config field with its default, validation rule, and CLI override |
| [providers.md](providers.md) | Built-in LLM providers, OAuth and API-key auth, dynamic catalog providers, env vars |
| [tools.md](tools.md) | The 6 core tools + 2 web tools — parameters, mutating flag, and worked examples |
| [permissions.md](permissions.md) | The `prompt`/`auto` modes, the safe-command allowlist, and the decision algorithm |
| [sessions.md](sessions.md) | JSONL session format, resume, handoff, `/export`, compaction, and tree navigation |
| [skills.md](skills.md) | Authoring skills — frontmatter, `$ARGUMENTS`, `register_cmd`, discovery paths |
| [theming.md](theming.md) | The full theme catalog and palette tokens |
| [headless.md](headless.md) | The `-p`/`--prompt` non-interactive flow, exit codes, stdin handling |
| [local-models.md](local-models.md) | Tested local models, llama-server setup, model-specific config tuning |
| [storage-layout.md](storage-layout.md) | Every file Vtx touches on disk — config, sessions, models, auth |

## Contributor docs

| Doc | What it covers |
| --- | --- |
| [architecture.md](architecture.md) | Internal module map, message types, request flow, design decisions |
| [development.md](development.md) | Build, test, lint, typecheck, and release Vtx itself |
| [e2e-test-coverage-review.md](e2e-test-coverage-review.md) | State of the tmux e2e harness and recommended additions |

## How to read these

- **New to Vtx:** start with the [README](../README.md), then read [configuration.md](configuration.md) and [tools.md](tools.md).
- **Adding a provider:** [providers.md](providers.md) and [configuration.md](configuration.md) (the `llm` section).
- **Adding a tool:** [tools.md](tools.md) and [architecture.md](architecture.md) (the `tools/` layer).
- **Adding a skill:** [skills.md](skills.md).
- **Debugging sessions or compaction:** [sessions.md](sessions.md).
- **Tuning for a local model:** [local-models.md](local-models.md) and [configuration.md](configuration.md) (the `compaction` section).
- **Working on Vtx itself:** [development.md](development.md), then [architecture.md](architecture.md).
