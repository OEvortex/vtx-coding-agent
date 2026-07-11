# Providers

Vtx talks to any OpenAI-compatible or Anthropic-compatible endpoint. Built-in providers ship in `src/vtx/llm/providers/provider.yaml`; you can add your own **without editing source code** in three ways. Custom providers are first-class: they appear in the `/model` picker, are auto-detected from environment variables, and have their model catalogs fetched automatically.

## Built-in providers

The catalog includes OpenAI, Anthropic, Azure AI Foundry, DeepSeek, GitHub Copilot, OpenAI Codex, Ollama-compatible local servers, and more. Pick one with `--provider <slug>` or interactively via `/model` and the `/provider` dropdown.

## Custom providers

### Option 1 — Project-local (recommended for teams)

Drop a YAML file into `.vtx/providers/` at your repo root (gitignored by default; commit it to share):

```bash
mkdir -p .vtx/providers
cat > .vtx/providers/acme.yaml <<'EOF'
slug: acme
display_name: "Acme AI Gateway"
description: "Internal OpenAI-compatible gateway."
family: openai_compat
base_url: https://ai.acme.internal/v1
api_key_env: ACME_API_KEY
fetch_models: true
EOF
```

### Option 2 — User-wide

Same shape in `~/.vtx/providers/<name>.yaml` (applies to every project).

> **Precedence:** built-in providers → `~/.vtx/providers` → `.vtx/providers` (project-local wins on slug collision).

### Option 3 — Programmatic (Python)

```python
from vtx.llm.provider_catalog import register_custom_provider

register_custom_provider(
    "acme",
    display_name="Acme AI Gateway",
    family="openai_compat",        # openai_compat | anthropic | supercode
    base_url="https://ai.acme.internal/v1",
    api_key_env="ACME_API_KEY",
    fetch_models=True,             # pull model list from <base_url>/models
)
```

### Using a custom provider

```bash
export ACME_API_KEY=sk-...
vtx --provider acme -m <model-id>
# or just: vtx  (auto-detected when the provider's API key is set in the env)
```

## `provider.yaml` schema

Every field from the built-in catalog is supported:

```yaml
- slug: acme                      # REQUIRED. Unique id; used by --provider and configs.
  display_name: "Acme AI Gateway" # Shown in UIs (defaults to slug).
  description: "Internal gateway" # One-line description (optional).
  family: openai_compat          # REQUIRED. "openai_compat" | "anthropic" | "supercode".
  base_url: "https://ai.acme.internal/v1"  # API root. Omit for key-only gateways.
  api_key_env: ACME_API_KEY      # Env var holding the key. null = no key needed.
  known_models:                  # Fallback model list (used when fetch fails).
    - acme-large
    - acme-fast
  supports_tools: true            # Tool/function calling support (default true).
  supports_vision: false          # Image input support (default false).
  supports_thinking: false        # Reasoning/thinking token support (default false).
  api_key_optional: false         # Works without an API key (default false).
  is_local: false                 # Marks a local endpoint (skipped in auto-env detection).
  max_tokens: null                # Optional hard cap on max output tokens.
  fetch_models: true              # Auto-fetch catalog from <base_url>/models (default false).
  models_endpoint: "/models"      # Path appended to base_url (default "/models").
  openmodelendpoint: false        # Catalog is public (no key needed for discovery).
  headers:                        # Extra static request headers.
    X-Acme-Project: "vtx"
  model_parser:                   # How to parse the /models JSON response.
    array_path: "data"            # JSON key holding the model array (default "data").
    id_field: "id"                # Field for the model id (default "id").
    name_field: "name"            # Field for the display name (default "name").
    context_field: "context_length"
    output_field: "max_completion_tokens"
    cooldown_minutes: 60          # Cache TTL for the fetched catalog.
```

## Notes

- For **Anthropic-compatible** gateways set `family: anthropic` and point `base_url` at the gateway root.
- For **local servers** (LM Studio, vLLM, llama.cpp, Ollama) use `is_local: true`, `api_key_env: null`, `api_key_optional: true`, and `fetch_models: true` so models are discovered automatically. See [local-models.md](local-models.md).
