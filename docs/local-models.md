# Local models

Vtx works with any local OpenAI-compatible server. No API key needed — loopback endpoints are detected automatically.

## Ollama

The catalog ships an `ollama` provider pointing at `http://localhost:11434/v1`:

```bash
ollama serve
ollama pull qwen3
vtx            # /provider → ollama, then /model picks up `ollama list` live
```

## llama.cpp / vLLM / any OpenAI-compatible server

```bash
# llama-server example
llama-server -m model.gguf --port 8080

vtx --provider openai --base-url http://localhost:8080/v1 \
    --openai-compat-auth none -m my-model
```

Anthropic-compatible local servers work the same way with `--anthropic-compat-auth none`.

## Self-signed TLS

For HTTPS endpoints with private certs:

```bash
vtx --insecure-skip-verify ...
```

or set `llm.tls.insecure_skip_verify: true` in config.

## Notes

- Model lists are fetched from the server's `/models` endpoint and cached ~6 h; use `/model` to refresh.
- Unknown context windows fall back to `agent.default_context_window` (200k) — lower it for small models so compaction triggers correctly.
- Tool calling requires a server that supports function/tool calling; text-only models degrade gracefully (tool calls are parsed from plain-text XML when possible).
