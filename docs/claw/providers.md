# Providers

vtx-claw supports 50+ LLM providers through vtx's unified provider catalog. Providers are configured in `providers.<name>` in the JSON config.

## Provider Configuration

```json
{
  "providers": {
    "openai": {
      "api_key": "sk-..."
    },
    "anthropic": {
      "api_key": "sk-ant-..."
    },
    "custom": {
      "api_key": "my-key",
      "api_base": "http://localhost:11434/v1"
    }
  }
}
```

### ProviderConfig Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `api_key` | string \| null | `null` | API key (saved to vtx's dynamic_auth.json) |
| `api_base` | string \| null | `null` | Custom API base URL |
| `api_type` | `"auto"` \| `"chat_completions"` \| `"responses"` | `"auto"` | Request API surface (only for openai) |
| `extra_headers` | dict \| null | `null` | Custom HTTP headers |
| `extra_body` | dict \| null | `null` | Extra request body fields |
| `extra_query` | dict \| null | `null` | Extra query parameters |

## Auto-Detection

When `agents.defaults.provider` is `"auto"`, vtx-claw detects the provider from:
1. The `api_key` format (e.g., `sk-ant-` → Anthropic)
2. The `api_base` URL (e.g., `api.openai.com` → OpenAI)
3. Environment variables

## All Supported Providers

### Anthropic Family

| Provider | Description |
|----------|-------------|
| `anthropic` | Anthropic (Claude) |
| `aerolink` | Aerolink |
| `fastrouter` | FastRouter |
| `supercode` | Supercode |

### OpenAI-Compatible

| Provider | Description |
|----------|-------------|
| `openai` | OpenAI (GPT-4o, o1, etc.) |
| `openrouter` | OpenRouter (multi-provider gateway) |
| `deepseek` | DeepSeek |
| `groq` | Groq (fast inference) |
| `together` | Together AI |
| `mistral` | Mistral AI |
| `ollama` | Ollama (local) |
| `nvidia` | NVIDIA NIM |
| `huggingface` | Hugging Face |
| `fireworks` | Fireworks AI |
| `zhipu` | Zhipu AI (GLM) |
| `moonshot` | Moonshot AI |
| `minimax` | MiniMax |
| `modelscope` | ModelScope |
| `nanogpt` | NanoGPT |
| `kilo` | Kilo |
| `tokenrouter` | TokenRouter |
| `airouter` | AiRouter |
| `opencode` | OpenCode |
| `clarifai` | Clarifai |
| `baseten` | Baseten |
| `deepinfra` | DeepInfra |
| `blackbox` | Blackbox |
| `chutes` | Chutes |
| `freemodel` | FreeModel |
| `friendli` | Friendli |
| `pollinations` | Pollinations |
| `vercelai` | Vercel AI |
| `lightningai` | Lightning AI |
| `aihubmix` | AiHubMix |
| `berget` | Berget |
| `crof` | Crof |
| `dinference` | Dinference |
| `hicapai` | HiCapAI |
| `jiekou` | Jiekou |
| `kimchi` | Kimchi |
| `knox` | Knox |
| `llmgateway` | LLM Gateway |
| `meganova` | MegaNova |
| `moark` | Moark |
| `routingrun` | RoutingRun |
| `seraphyn` | Seraphyn |
| `sherlock` | Sherlock |
| `zenmux` | ZenMux |
| `zyloo` | Zyloo |
| `cline` | Cline |
| `conduit` | Conduit |
| `cortecs` | Cortecs |
| `dialagram` | Dialagram |
| `apertis` | Apertis |

### OAuth Providers

| Provider | Auth Method |
|----------|------------|
| `openai_codex` | Device flow OAuth |
| `github_copilot` | Device flow OAuth |

### Cloud Providers

| Provider | Description |
|----------|-------------|
| `azure_openai` | Azure OpenAI Service |
| `bedrock` | AWS Bedrock |

## Model Presets

Named model configurations for quick switching via `/model`:

```json
{
  "model_presets": {
    "fast": {
      "model": "anthropic/claude-sonnet-4-20250514",
      "provider": "anthropic",
      "max_tokens": 4096,
      "temperature": 0.1
    },
    "creative": {
      "label": "Creative Writing",
      "model": "openai/gpt-4o",
      "temperature": 0.8,
      "reasoning_effort": "high"
    }
  }
}
```

Switch presets:
- In chat: `/model fast`
- Config: set `agents.defaults.model_preset: "fast"`

## Fallback Models

Configure automatic fallback when the primary model fails:

```json
{
  "agents": {
    "defaults": {
      "model": "anthropic/claude-sonnet-4-20250514",
      "fallback_models": [
        "openai/gpt-4o",
        "deepseek/deepseek-chat"
      ]
    }
  }
}
```

## Custom OpenAI-Compatible Providers

For any OpenAI-compatible endpoint:

```json
{
  "providers": {
    "my-local": {
      "api_base": "http://localhost:11434/v1",
      "api_key": "not-needed"
    }
  }
}
```

Then set `agents.defaults.provider: "my-local"`.

## API Key Storage

API keys are managed via vtx's `dynamic_auth.json`:
- When you set `api_key` in config, it's saved to vtx's auth store
- The key is removed from `config.json` for security
- Use `vtx-claw provider login <name>` for OAuth providers
