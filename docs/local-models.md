# Local Models

This document provides detailed information about running and configuring local models with Vtx.

## Choosing a model

Vtx leans heavily on tool calling: every file read, edit, search, and skill invocation is a structured tool call. Very small models (roughly 8B and below, or aggressive quantizations) often produce malformed tool calls, ignore tools entirely, or fail to follow skill instructions — which looks like Vtx "not working" when it's the model that can't drive the harness. If a tiny model keeps narrating instead of acting, or tool calls fail to parse, try a larger model before filing a bug.

The combinations in the table below are known to work and are a good baseline; among them, prefer the largest one your hardware can serve at a usable speed.

## Tested Models

> Tested on llama server build b8740

| Model | Quantization | Context Length | TPS | System Specs |
| ----- | -------------- | -------------- | --- | ------------ |
| `zai-org/glm-4.7-flash` | Q4_K_M | 65,536 | N/A | i7-14700F × 28, 64GB RAM, 24GB VRAM (RTX 3090) |
| `unsloth/Qwen3.5-27B-GGUF` | Q4_K_M | 32,768 | ~30 | i7-14700F × 28, 64GB RAM, 24GB VRAM (RTX 3090) |
| `unsloth/gemma-4-26B-A4B-it-GGUF` | UD-Q4_K_M | 32,768 | ~100 | i7-14700F × 28, 64GB RAM, 24GB VRAM (RTX 3090) |

Run Qwen3.5 27B on an RTX 3090 with a 32k context window using llama-server:

```bash
/path-to-llama-server/llama-server \
  --model /path-to-model/Qwen3.5-27B-Q4_K_M.gguf \
  --port 5000 \
  --ctx-size 32768 \
  --gpu-layers all \
  --threads 8 \
  --threads-batch 8 \
  --batch-size 1024 \
  --ubatch-size 512 \
  --flash-attn on
```

On this machine, that setup generates at roughly 30 tokens per second.

Then start Vtx for a one-off local session:

```bash
vtx --model unsloth/Qwen3.5-27B-GGUF --provider openai \
  --base-url http://localhost:5000/v1 \
  --openai-compat-auth none
```

Run Gemma 4 26B A4B on the same machine using llama-server:

```bash
/path-to-llama-server/llama-server \
  --model /path-to-model/gemma-4-26B-A4B-it-UD-Q4_K_M.gguf \
  --port 5000 \
  --ctx-size 32768 \
  --gpu-layers all \
  --threads 8 \
  --threads-batch 8 \
  --batch-size 1024 \
  --ubatch-size 512 \
  --flash-attn on \
  --temperature 1.5
```

Then start Vtx against that local server:

```bash
vtx --model unsloth/gemma-4-26B-A4B-it-GGUF --provider openai \
  --base-url http://localhost:5000/v1 \
  --openai-compat-auth none
```

To avoid passing provider, model, and auth flags every time you start Vtx, you can define your local setup in `~/.vtx/config.yml`. This also allows you to tune compaction to trigger at a specific point relative to your model's context window.

If this is your default setup, put it in `~/.vtx/config.yml` instead:

```yaml
llm:
  default_provider: "openai"
  default_model: "unsloth/gemma-4-26B-A4B-it-GGUF"
  default_base_url: "http://localhost:5000/v1"

  auth:
    openai_compat: "none"  # or "auto"

compaction:
  # Lower the threshold for small context windows so compaction fires earlier
  threshold_percent: 70
```

> **Note:** earlier Vtx versions stored config at `~/.vtx/config.yml`. If you have legacy files there, the v0.4.x release migrates them automatically into `~/.vtx/` on first run; the old path is no longer read.
