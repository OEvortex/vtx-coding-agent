# vtx-coding-agent

> **⚠️ DEPRECATED** — This package is no longer maintained. Please migrate to **[ctx-agent](https://pypi.org/project/ctx-agent/)**.

`vtx-coding-agent` version 2.0.0 is the final release. It installs `ctx-agent` (and its dependencies `ctx-sdk` and `ctx-term`) and provides the `vtx` command as a compatibility wrapper that forwards all arguments to `ctx`.

## Migration

```bash
# Old
pip install vtx-coding-agent
vtx -p "add tests"

# New
pip install ctx-agent
ctx -p "add tests"
```

The `vtx` command still works but will show a deprecation warning. All flags, subcommands, and behavior are identical to `ctx`.

## Packages

The project is now split into three PyPI packages:

| Package | Description |
|---------|-------------|
| **[ctx-agent](https://pypi.org/project/ctx-agent/)** | The coding agent: TUI, headless CLI, built-in tools, and the `ctx` console script |
| **[ctx-term](https://pypi.org/project/ctx-term/)** | Terminal UI building blocks: Textual widgets, blocks, formatting |
| **[ctx-sdk](https://pypi.org/project/ctx-sdk/)** | Core agent harness: agent loop, sessions, tools, 65 LLM providers, Python SDK |

## Documentation

Full reference at [github.com/OEvortex/ctx-coding-agent](https://github.com/OEvortex/ctx-coding-agent).

## License

[Apache License 2.0](LICENSE)
