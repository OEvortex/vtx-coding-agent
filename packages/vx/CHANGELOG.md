# Changelog

All notable changes to vx are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-24

### Added

- **Initial release** — vx is a minimal branded coding agent built on the [ctx-sdk](https://pypi.org/project/ctx-sdk/) harness and [ctx-agent](https://pypi.org/project/ctx-agent/) product. It provides the `vx` console script with the same flags and behavior as `ctx`, but with its own branding and terminal UI.
- **`vx` CLI** — drop-in replacement for `ctx` with identical flags: `--model`, `--provider`, `--prompt`, `--api-key`, `--base-url`, `--continue`, `--resume`, `--extension`, `--agent`, `--profile`, etc.
- **Branding** — italic wordmark `𝒗x` and standalone program name, version, and welcome screen.
- **Dependencies** — depends on `ctx-sdk>=0.1.0` and `ctx-agent>=0.1.0` for the full agent harness and coding agent functionality.

### Changed

- **Monorepo split** — vx was extracted from the monolithic `vtx-coding-agent` package as a lightweight branded entry point. The heavy lifting is delegated to ctx-sdk and ctx-agent.
