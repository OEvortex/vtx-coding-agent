# Changelog

All notable changes to vtx-coding-agent are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-11-24

### Added

- **Deprecation bridge** — this is the final release of `vtx-coding-agent`. It installs `ctx-agent`, `ctx-sdk`, and `ctx-term` as dependencies and provides the `vtx` command as a compatibility wrapper that forwards all arguments to `ctx`.

### Changed

- **Renamed to ctx** — the project has been split into three PyPI packages:
  - [**ctx-agent**](https://pypi.org/project/ctx-agent/) — the coding agent: TUI, headless CLI, built-in tools, and the `ctx` console script
  - [**ctx-term**](https://pypi.org/project/ctx-term/) — terminal UI building blocks: Textual widgets, blocks, formatting
  - [**ctx-sdk**](https://pypi.org/project/ctx-sdk/) — core agent harness: agent loop, sessions, tools, 65 LLM providers, Python SDK

### Removed

- **All functionality** — the `vtx` command still works but emits a `DeprecationWarning` on each run. Migrate to `ctx` by installing `ctx-agent`.
