# Vtx documentation

This folder is the reference companion to the top-level [README](../README.md). The README is the tour; the docs here are the deep dive.

## Getting started

| Doc | What it covers |
| --- | --- |
| [configuration.md](configuration.md) | Every YAML config field with its default, validation rule, and CLI override |
| [providers.md](providers.md) | Built-in LLM providers, OAuth and API-key auth, custom providers, env vars |

## Features

| Doc | What it covers |
| --- | --- |
| [tools.md](tools.md) | The built-in tools — parameters, mutating flag, and worked examples |
| [goals.md](goals.md) | Persistent goals — guided drafts, task trees, auto-continue, completion audit, dashboard |
| [permissions.md](permissions.md) | The `prompt`/`auto` modes, safe-command allowlist, and the approval decision flow |
| [sessions.md](sessions.md) | JSONL session format, session tree, resume, handoff, `/export`, compaction, idle recap |
| [skills.md](skills.md) | Authoring skills — frontmatter, `$ARGUMENTS`, `register_cmd`, discovery paths |
| [agents.md](agents.md) | Switchable handoff agents — `.vtx/agent/<name>.py`, `Shift+Tab` cycling |
| [extensions.md](extensions.md) | Python extensions, the `ExtensionAPI`, lifecycle events, YAML hooks |
| [theming.md](theming.md) | The full theme catalog and palette tokens |
| [headless.md](headless.md) | The `-p`/`--prompt` non-interactive flow, exit codes, stdin handling |
| [local-models.md](local-models.md) | Ollama, llama.cpp and vLLM setup for local models |

## Reference

| Doc | What it covers |
| --- | --- |
| [architecture.md](architecture.md) | Package map, message types, turn lifecycle, event stream |
| [storage-layout.md](storage-layout.md) | Every file Vtx touches on disk — config, sessions, models, auth |
| [development.md](development.md) | Build, test, lint, typecheck, and release Vtx itself |
| [e2e-test-coverage-review.md](e2e-test-coverage-review.md) | State of the tmux e2e harness and recommended additions |

## SDK

The programmatic multi-agent SDK lives under [`sdk/`](sdk/README.md).

| Doc | What it covers |
| --- | --- |
| [sdk/README.md](sdk/README.md) | Overview and quick start |
| [sdk/runner.md](sdk/runner.md) | `Runner.run` / `run_sync` / `run_streamed` |
| [sdk/agents.md](sdk/agents.md) | The SDK `Agent` object and configuration |
| [sdk/tools.md](sdk/tools.md) | Custom tools via the `@tool` decorator |
| [sdk/multi_agent.md](sdk/multi_agent.md) | Handoffs and agents-as-tools |
| [sdk/approvals.md](sdk/approvals.md) | Human-in-the-loop tool approvals |
| [sdk/permissions.md](sdk/permissions.md) | Pluggable permission policies |
| [sdk/guardrails.md](sdk/guardrails.md) | Input/output/tool guardrails and tripwires |
| [sdk/sessions.md](sdk/sessions.md) | Pluggable session memory backends |
| [sdk/skills.md](sdk/skills.md) | Loading skills into SDK agents |
| [sdk/tracing.md](sdk/tracing.md) | Spans, traces, and trace processors |
