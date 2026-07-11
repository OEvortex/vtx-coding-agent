# Local models

Vtx works with any OpenAI-compatible local server. Common choices: Ollama, llama.cpp (`server`), LM Studio, and vLLM.

## Quick start (Ollama)

```bash
# Start Ollama and pull a model
ollama pull qwen2.5-coder:7b

# Point Vtx at it
export OPENAI_BASE_URL=http://localhost:11434/v1
vtx --provider openai -m qwen2.5-coder:7b
```

Or set it permanently in `config.yml`:

```yaml
llm:
  default_provider: "openai"
  default_model: "qwen2.5-coder:7b"
  default_base_url: "http://localhost:11434/v1"
```

## Registering a local provider

The cleanest path is a custom provider (see [providers.md](providers.md)):

```yaml
# .vtx/providers/local.yaml
slug: local
display_name: "Local LLM"
family: openai_compat
base_url: "http://localhost:11434/v1"
api_key_env: null          # local servers usually need no key
api_key_optional: true
is_local: true             # skip auto-env detection; mark as local
fetch_models: true         # auto-discover models from /models
```

Then: `vtx --provider local -m <model>`.

## Tips

- Set `default_thinking_level` to `none` or `minimal` for smaller local models that don't support reasoning tokens.
- `is_local: true` excludes the endpoint from API-key auto-detection and marks it in the `/model` picker.
- If the server uses a self-signed cert, pass `--insecure-skip-verify` or set `llm.tls.insecure_skip_verify: true`.
- Tool calling support depends on the model/server; set `supports_tools: false` in the provider entry if your local model can't call tools reliably and rely on the agent's text-based fallback.
