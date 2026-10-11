# Providers

Vtx ships a fixed catalog of LLM providers, each with an API type, a base URL, and one of three authentication methods (API key, browser OAuth, or none at all). This page lists every provider in the catalog, explains how to pick one, and covers auth modes, OAuth logins, local servers, custom providers, and how the model list is discovered.

## The catalog

The catalog is loaded from `src/vtx/ai/provider.yaml` and merged with three OAuth providers registered in Python, so the shipped count is 59 + 3 = **62 providers** (`src/vtx/ai/provider_catalog.py:222`, `src/vtx/ai/provider_catalog.py:146`). `list_providers()` returns them in `provider.yaml` order with the OAuth entries appended (`src/vtx/ai/provider_catalog.py:256`), and anything you add as a custom provider joins the same list (see [Custom providers](#custom-providers)).

Fifty-six of the 59 `provider.yaml` entries declare `family: openai_compat` and three declare `family: anthropic`; the three OAuth entries all declare `openai_compat`, which makes 59 `openai_compat` and 3 `anthropic` in total.

Do not confuse that count with the CLI's `--provider` argument, which takes one of only **16** values. Those come from `PROVIDER_API_BY_NAME`, a hardcoded slug-to-API-type map (`src/vtx/ai/providers/__init__.py:4`) passed straight to `argparse` as `choices=sorted(PROVIDER_API_BY_NAME)` (`src/vtx/coding_agent/cli.py:53`). They are: `aerolink`, `airouter`, `anthropic`, `codex`, `deepseek`, `kilo`, `ollama`, `openai`, `openai-codex`, `openai-responses`, `opencode`, `opengateway`, `openrouter`, `tokenrouter`, `zhipu`, `zyloo`. Two of them, `openai-codex` and `openai-responses`, are not catalog slugs.

A provider's API type comes from its `family` field: `openai_compat` maps to `openai-sdk`, `anthropic` maps to `anthropic`, and the slugs `codex` and `openai-responses` are special-cased to `openai-responses` even though they declare `openai_compat` (`src/vtx/ai/provider_catalog.py:349`). Exactly three providers speak the Anthropic API: `anthropic`, `aerolink`, and `fastrouter`. One, `codex`, speaks the Responses API. One, `ollama`, is local (`is_local`) and needs no key.

| Slug | Name | API type | Auth | Env var |
|---|---|---|---|---|
| `aerolink` | Aerolink | `anthropic` | API key | `ANTHROPIC_API_KEY` |
| `aihubmix` | AIHubMix | `openai-sdk` | API key | `AIHUBMIX_API_KEY` |
| `airouter` | Airouter | `openai-sdk` | API key | `AIROUTER_API_KEY` |
| `anthropic` | Anthropic | `anthropic` | API key | `ANTHROPIC_API_KEY` |
| `apertis` | Apertis AI | `openai-sdk` | API key | `APERTIS_API_KEY` |
| `badtheorylabs` | BadTheoryLabs | `openai-sdk` | API key | `BADTHEORYLABS_API_KEY` |
| `bai` | B.ai | `openai-sdk` | API key | `BAI_API_KEY` |
| `baseten` | Baseten | `openai-sdk` | API key | `BASETEN_API_KEY` |
| `berget` | Berget AI | `openai-sdk` | API key | `BERGET_API_KEY` |
| `blackbox` | Blackbox AI | `openai-sdk` | API key | `BLACKBOX_API_KEY` |
| `chutes` | Chutes AI | `openai-sdk` | API key | `CHUTES_API_KEY` |
| `clarifai` | Clarifai | `openai-sdk` | API key | `CLARIFAI_API_KEY` |
| `cline` | Cline | `openai-sdk` | OAuth | `CLINE_API_KEY` |
| `codex` | OpenAI Codex | `openai-responses` | OAuth | `OPENAI_API_KEY` |
| `conduit` | Conduit | `openai-sdk` | API key | `CONDUIT_API_KEY` |
| `cortecs` | Cortecs | `openai-sdk` | API key | `CORTECS_API_KEY` |
| `crof` | Crof AI | `openai-sdk` | API key | `CROF_API_KEY` |
| `deepinfra` | DeepInfra | `openai-sdk` | API key | `DEEPINFRA_API_KEY` |
| `deepseek` | DeepSeek | `openai-sdk` | API key | `DEEPSEEK_API_KEY` |
| `dialagram` | Dialagram | `openai-sdk` | API key | `DIALAGRAM_API_KEY` |
| `dinference` | Dinference | `openai-sdk` | API key | `DINFERENCE_API_KEY` |
| `experientiallabs` | Experiential Labs | `openai-sdk` | API key | `EXPERIENTIALLABS_API_KEY` |
| `fastrouter` | FastRouter | `anthropic` | API key | `FASTROUTER_API_KEY` |
| `fireworks` | Fireworks AI | `openai-sdk` | API key | `FIREWORKS_API_KEY` |
| `freemodel` | FreeModel | `openai-sdk` | API key | `FREEMODEL_API_KEY` |
| `friendli` | Friendli | `openai-sdk` | API key | `FRIENDLI_API_KEY` |
| `github-copilot` | GitHub Copilot | `openai-sdk` | OAuth | `GITHUB_TOKEN` |
| `groq` | Groq | `openai-sdk` | API key | `GROQ_API_KEY` |
| `hicapai` | HicapAI | `openai-sdk` | API key | `HICAPAI_API_KEY` |
| `huggingface` | Hugging Face | `openai-sdk` | API key | `HF_TOKEN` |
| `jiekou` | Jiekou AI | `openai-sdk` | API key | `JIEKOU_API_KEY` |
| `kilo` | Kilo Gateway | `openai-sdk` | API key | `KILO_API_KEY` |
| `kimchi` | Kimchi | `openai-sdk` | API key | `KIMCHI_API_KEY` |
| `knox` | Knox | `openai-sdk` | API key | `KNOX_API_KEY` |
| `lightningai` | LightningAI | `openai-sdk` | API key | `LIGHTNINGAI_API_KEY` |
| `llmgateway` | LLMGateway | `openai-sdk` | API key | `LLMGATEWAY_API_KEY` |
| `meganova` | MegaNova | `openai-sdk` | API key | `MEGANOVA_API_KEY` |
| `meta` | Meta AI | `openai-sdk` | API key | `META_API_KEY` |
| `minimax` | MiniMax | `openai-sdk` | API key | `MINIMAX_API_KEY` |
| `mistral` | Mistral AI | `openai-sdk` | API key | `MISTRAL_API_KEY` |
| `moark` | Moark | `openai-sdk` | API key | `MOARK_API_KEY` |
| `modelscope` | ModelScope | `openai-sdk` | API key | `MODELSCOPE_API_KEY` |
| `moonshot` | MoonshotAI | `openai-sdk` | API key | `MOONSHOT_API_KEY` |
| `nanogpt` | NanoGPT | `openai-sdk` | API key | `NANOGPT_API_KEY` |
| `nvidia` | NVIDIA NIM | `openai-sdk` | API key | `NVIDIA_API_KEY` |
| `ollama` | Ollama (local) | `openai-sdk` | No key | none |
| `openai` | OpenAI | `openai-sdk` | API key | `OPENAI_API_KEY` |
| `opencode` | OpenCode Zen | `openai-sdk` | API key | `OPENCODE_API_KEY` |
| `opengateway` | Gitlawb Opengateway | `openai-sdk` | API key | `OPENGATEWAY_API_KEY` |
| `openrouter` | OpenRouter | `openai-sdk` | API key | `OPENROUTER_API_KEY` |
| `pollinations` | Pollinations AI | `openai-sdk` | API key | `POLLINATIONS_API_KEY` |
| `routingrun` | Routing.run | `openai-sdk` | API key | `ROUTINGRUN_API_KEY` |
| `sarvam` | Sarvam AI | `openai-sdk` | API key | `SARVAM_API_KEY` |
| `seraphyn` | Seraphyn | `openai-sdk` | API key | `SERAPHYN_API_KEY` |
| `sherlock` | Sherlock (CloudFerro) | `openai-sdk` | API key | `SHERLOCK_API_KEY` |
| `together` | Together AI | `openai-sdk` | API key | `TOGETHER_API_KEY` |
| `tokenharbor` | TokenHarbor | `openai-sdk` | API key | `TOKENHARBOR_API_KEY` |
| `tokenrouter` | TokenRouter | `openai-sdk` | API key | `TOKENROUTER_API_KEY` |
| `vercelai` | Vercel AI | `openai-sdk` | API key | `VERCELAI_API_KEY` |
| `zenmux` | Zenmux | `openai-sdk` | API key | `ZENMUX_API_KEY` |
| `zhipu` | ZhiPu | `openai-sdk` | API key | `ZAI_API_KEY` |
| `zyloo` | Zyloo | `openai-sdk` | API key | `ZYLOO_API_KEY` |

### `--provider` accepts fewer slugs than the catalog contains

`--provider` is not validated against the catalog. It is validated against `PROVIDER_API_BY_NAME`, so it rejects 48 of the 62 catalog providers with an argparse error before the runtime is ever reached (`src/vtx/ai/providers/__init__.py:4`, `src/vtx/coding_agent/cli.py:53`):

```bash
vtx --provider groq --model llama-3.3-70b
```

```text
usage: cli.py [-h] [--model MODEL]
              [--provider {aerolink,airouter,anthropic,codex,deepseek,kilo,ollama,openai,
                           openai-codex,openai-responses,opencode,opengateway,openrouter,
                           tokenrouter,zhipu,zyloo}]
              [--prompt [PROMPT]] [--api-key API_KEY] [--base-url BASE_URL]
              [--openai-compat-auth {auto,required,none}]
              [--anthropic-compat-auth {auto,required,none}]
              [--insecure-skip-verify] [--continue] [--resume RESUME_SESSION]
              [--extension PATH] [--no-extensions] [--agent NAME]
              [--agent-file PATH] [--no-agents] [--list-agents]
              [--list-extensions] [--profile NAME] [--patch PATH]
              [--dump-config] [--version]
              {update,plugin} ...
cli.py: error: argument --provider: invalid choice: 'groq' (choose from 'aerolink',
              'airouter', 'anthropic', 'codex', 'deepseek', 'kilo', 'ollama', 'openai',
              'openai-codex', 'openai-responses', 'opencode', 'opengateway', 'openrouter',
              'tokenrouter', 'zhipu', 'zyloo')
```

Two accepted slugs are themselves not catalog entries: `openai-codex` and `openai-responses` appear in `PROVIDER_API_BY_NAME` but have no entry in `provider.yaml` or the OAuth block, so passing either one is accepted and then falls through to model discovery rather than to a provider entry.

Use `llm.default_provider` for the other 48, or pick a model that belongs to the provider you want, because the config value and the model id are what actually select the provider (`src/vtx/tui/app.py:146`). `/model` lists models across every provider regardless of `--provider`'s allowlist.

## Picking a provider

### On the command line

```bash
vtx --provider anthropic --model claude-sonnet-4-20250514
vtx --provider ollama --model qwen3-coder --base-url http://localhost:11434/v1
vtx --model gpt-5.5 --provider codex
```

| Flag | Effect |
|---|---|
| `--provider` | Sets the initial provider, restricted to the 16 slugs above (`src/vtx/coding_agent/cli.py:53`) |
| `--model` | Sets the initial model (`src/vtx/coding_agent/cli.py:52`) |
| `--api-key`, `-k` | Explicit key, wins over every other source (`src/vtx/tui/app.py:155`) |
| `--base-url`, `-u` | Overrides the provider's base URL (`src/vtx/tui/app.py:156`) |
| `--openai-compat-auth` | `auto`, `required`, or `none` (`src/vtx/coding_agent/cli.py:66`) |
| `--anthropic-compat-auth` | `auto`, `required`, or `none` (`src/vtx/coding_agent/cli.py:71`) |
| `--insecure-skip-verify` | Turns off TLS certificate verification (`src/vtx/coding_agent/cli.py:76`) |

### In the config file

```yaml
llm:
  default_provider: "anthropic"
  default_model: "claude-sonnet-4-20250514"
  default_base_url: ""
```

`llm.default_provider` and `llm.default_model` are free-form strings, so they are not limited to the `--provider` allowlist (`src/vtx/core/config.py:131`, `src/vtx/core/config.py:132`).

### In the TUI

`/provider` opens a picker over the configured providers and writes `ui.model_provider_filter` (`src/vtx/tui/commands/providers.py:18`), which narrows the `/model` list to one provider. `/model` picks a model from the catalog and switches providers as a side effect (`src/vtx/tui/commands/models.py:47`). When you change either one, the choice is recorded in `last_selected` so the next session starts on it (`src/vtx/core/config.py:1128`).

### Automatically from the environment

With no flag and no config, `detect_provider_from_env()` picks the provider (`src/vtx/ai/provider_catalog.py:262`):

1. `VTX_PROVIDER`, if it names a provider in the catalog.
2. The first non-local provider, in catalog order, whose `api_key_env` is set.
3. The first provider whose OAuth credential file is present (`cline`, then `github-copilot`, then `codex`).
4. The first local, key-optional provider (`ollama`).
5. `openai` as the fallback (`DEFAULT_PROVIDER_SLUG`, `src/vtx/ai/provider_catalog.py:248`).

That last step means a machine with no keys configured resolves to `openai`, regardless of the `openai-codex` default in the shipped config file.

## How the flags resolve

The runtime resolves the model and provider independently, then reconciles them:

```python
initial_model = model or last_selected.model_id or config.llm.default_model
initial_model_provider = (
    provider if provider is not None
    else (last_selected.provider if last_selected.provider
          else (config.llm.default_provider if model is None else None))
)
```

(`src/vtx/tui/app.py:145`, `src/vtx/tui/app.py:146`)

Two consequences follow from that code:

- Passing `--model` alone leaves `model_provider` as `None`, so the provider is inferred from the model when the model is looked up.
- Passing `--provider` alone keeps the configured default model, which may not belong to that provider.

The model lookup is the last word. `_model_api_and_base_url()` checks the catalog first, then the dynamic (cache-only) catalog, and only then falls back to `resolve_provider_api_type(provider)` plus the provider's default base URL (`src/vtx/agent/runtime.py:926`). When the provider filter does not match, `find_model()` retries without it and scans every provider for the model id (`src/vtx/ai/provider_catalog.py:637`). That scan is why `--model gpt-5.5 --provider aerolink` still resolves to the Codex endpoint for `gpt-5.5`, and the runtime logs a warning when it happens (`src/vtx/agent/runtime.py:986`).

Base URL resolution runs in the same order, with `--base-url` sitting above all of it (`src/vtx/tui/app.py:156`, `src/vtx/agent/runtime.py:926`):

```text
--base-url  >  the model's own base_url  >  default_base_url_for_provider(slug)  >  default_base_url_for_api(api_type)
```

The last two default to `https://api.openai.com/v1` when nothing matches (`src/vtx/agent/runtime.py:59`, `src/vtx/agent/runtime.py:61`).

A resumed session records its own provider, model, and base URL, and those beat the flags unless you override them explicitly (`src/vtx/agent/runtime.py:1024`).

## Auth modes for compatible endpoints

The OpenAI and Anthropic SDKs do not agree on when a key is optional. Vtx normalizes this with one of three modes, applied separately to the two transports (`src/vtx/ai/base.py:99`).

| Mode | Behavior |
|---|---|
| `auto` | Use the key if one is present. If the base URL is local, send the placeholder key instead of failing (`src/vtx/ai/base.py:151`) |
| `required` | Demand a key. No env var, no placeholder, and the provider constructor raises (`src/vtx/ai/base.py:136`) |
| `none` | Never look for a key. Send `vtx-local` as the `Authorization` header (`src/vtx/ai/base.py:98`, `src/vtx/ai/base.py:151`) |

The resolution order inside `resolve_api_key()` is:

```python
if explicit_api_key:
    return explicit_api_key
for env_var in env_vars:
    value = os.environ.get(env_var)
    if value:
        return value
if auth_mode == "none":
    return LOCAL_API_KEY_PLACEHOLDER
if auth_mode == "auto" and is_local_base_url(base_url):
    return LOCAL_API_KEY_PLACEHOLDER
return None
```

(`src/vtx/ai/base.py:136`)

`is_local_base_url()` treats `localhost`, `127.0.0.1`, `0.0.0.0`, `::1`, any `.local` hostname, and any loopback, private, or link-local IP address as local (`src/vtx/ai/base.py:102`).

Set the mode per endpoint:

```bash
vtx --openai-compat-auth none --provider ollama
vtx --anthropic-compat-auth required --provider anthropic
```

```yaml
llm:
  auth:
    openai_compat: "auto"
    anthropic_compat: "auto"
```

The defaults come from `src/vtx/core/config.py:122` and `src/vtx/core/config.py:123`. Precedence is `--openai-compat-auth` over `llm.auth.openai_compat` (`src/vtx/tui/app.py:162`), and `--anthropic-compat-auth` over `llm.auth.anthropic_compat` (`src/vtx/tui/app.py:165`).

The two constructors apply these differently. `AnthropicSDKProvider` only ever looks at `ANTHROPIC_API_KEY`, ignoring the provider's own env var (`src/vtx/ai/providers/anthropic_sdk.py:41`). `OpenAISDKProvider` uses the provider's `api_key_env`, falling back to `OPENAI_API_KEY`, then to a key stored by `/login`, and only then refuses to start (`src/vtx/ai/providers/openai_sdk.py:49`).

## `--insecure-skip-verify`

`--insecure-skip-verify` sets `llm.tls.insecure_skip_verify` on the live config object before the provider is built (`src/vtx/coding_agent/cli.py:203`). With it on, `make_http_client()` returns a real `httpx.AsyncClient` with `verify=False`; with it off, the function returns `None` so each SDK builds its own verifying client (`src/vtx/ai/base.py:125`). The timeout on that client comes from `llm.request_timeout_seconds`, so an insecure run also uses that value rather than the SDK default (`src/vtx/ai/base.py:131`).

```bash
# self-signed certificate on a LAN-hosted gateway
vtx --provider zyloo --base-url https://10.0.0.5:8443/v1 --insecure-skip-verify
```

```yaml
llm:
  tls:
    insecure_skip_verify: true
```

The config key is only consulted when the flag is absent. The flag is a field assignment, not a config write, so it does not persist and a later `/reload` reverts to the file value (`src/vtx/coding_agent/cli.py:203`).

## OAuth login

Three providers authenticate through a browser flow: `github-copilot`, `cline`, and `codex`. Run `/login`, then pick the `oauth` row, then the provider (`src/vtx/tui/commands/auth.py:74`). The OAuth row is only offered when `src/vtx/ai/oauth` registers a flow for the provider (`src/vtx/tui/commands/auth.py:64`).

| Provider | Flow | Storage |
|---|---|---|
| `github-copilot` | GitHub device code | `~/.vtx/copilot_auth.json` (`src/vtx/ai/oauth/copilot.py:48`) |
| `codex` | OAuth with a local callback server | `~/.vtx/codex_auth.json`, falling back to the legacy `~/.vtx/openai_auth.json` (`src/vtx/ai/oauth/codex.py:67`) |
| `cline` | WorkOS device code, then a Cline token exchange | `~/.vtx/cline_auth.json` (`src/vtx/ai/oauth/cline.py:68`) |

The Codex flow opens a callback server on `http://localhost:1455/auth/callback` (`src/vtx/ai/oauth/codex.py:35`) and fails with a specific message if that port is taken or the callback never arrives (`src/vtx/ai/oauth/codex.py:361`, `src/vtx/ai/oauth/codex.py:393`). It also reads the native Codex CLI credentials from `~/.codex/auth.json` before starting its own flow (`src/vtx/ai/oauth/codex.py:63`). Cline does the same from `~/.cline/data/settings/providers.json` (`src/vtx/ai/oauth/cline.py:72`).

`/logout` lists only the providers that have something saved and deletes it (`src/vtx/tui/commands/auth.py:426`). OAuth credentials are what `detect_provider_from_env()` probes in step 3, so logging in is enough to change the auto-detected provider on the next run (`src/vtx/ai/provider_catalog.py:277`).

## API keys pasted through `/login`

Pick the `API key` row and paste the key. Vtx stores it in the dynamic auth file and looks it up per provider slug, so it works for any provider including custom ones (`src/vtx/ai/oauth/dynamic.py:169`). The file is written with mode 0600 and lives at the first of these that resolves (`src/vtx/ai/oauth/dynamic.py:52`):

```text
~/vtx/dynamic_auth.yml
$XDG_CONFIG_HOME/vtx/dynamic_auth.json
~/.vtx/dynamic_auth.json
```

A key from `/login` sits below the provider's env var in the resolution order and above "no key" (`src/vtx/ai/providers/openai_sdk.py:49`). `is_provider_configured()` consults this file, which is how `/model` and `/login` tell a keyed provider apart from an unconfigured one (`src/vtx/ai/provider_catalog.py:343`).

## Local models

`ollama` is the only provider in the catalog flagged both `api_key_optional: true` and `is_local: true` (`src/vtx/ai/provider_catalog.py:315`, `src/vtx/ai/provider_catalog.py:307`). That has two visible effects: `is_provider_configured()` returns `True` for it with no key at all, and it is the target of step 4 in `detect_provider_from_env()`.

```bash
vtx --provider ollama --model qwen3-coder
```

```yaml
llm:
  default_provider: "ollama"
  default_model: "qwen3-coder"
  default_base_url: "http://localhost:11434/v1"
  auth:
    openai_compat: "none"
```

Any other local server works. Point `--base-url` at it and set `--openai-compat-auth none` (or leave it `auto` and let `is_local_base_url()` recognize the address), which keeps the request from failing on a missing key (`src/vtx/ai/base.py:151`, `src/vtx/ai/base.py:102`).

The `/login` picker labels these providers "local / no key needed" and offers no key prompt for them (`src/vtx/tui/commands/auth.py:124`).

## Custom providers

You can add a provider without editing `provider.yaml`. Three routes, all funnelling through the same validation (`src/vtx/ai/provider_catalog.py:431`):

1. Drop a YAML file in `~/.vtx/providers/*.yaml`.
2. Drop a YAML file in `<cwd>/.vtx/providers/*.yaml`.
3. Call `register_custom_provider()` from Python before the first catalog lookup.

Files load in sorted filename order, global first then project-local, so project-local wins and an earlier file's slug can be overwritten (`src/vtx/ai/provider_catalog.py:448`). `family` must be `openai_compat` or `anthropic`; anything else raises `ValueError` (`src/vtx/ai/provider_catalog.py:505`).

```yaml
slug: my-gateway
display_name: My Gateway
description: Internal OpenAI-compatible gateway
family: openai_compat
base_url: https://gateway.internal.example.com/v1
api_key_env: MY_GATEWAY_API_KEY
known_models:
  - llama-3.3-70b
  - qwen3-coder
supports_tools: true
supports_vision: true
supports_thinking: true
fetch_models: true
models_endpoint: /models
```

Optionally nest a `model_parser` block to tell the fetcher how to read a non-standard `/models` response; the defaults are `data` for the array, `id` for the id, `context_length` for the context window, and `max_completion_tokens` for the output cap (`src/vtx/ai/provider_catalog.py:72`).

Every custom provider with a `base_url` is mirrored into the dynamic fetcher automatically, so it shows up in `/model` and responds to `/model refresh` (`src/vtx/ai/provider_catalog.py:546`, `src/vtx/ai/provider_catalog.py:583`).

## Dynamic model discovery

The static catalog knows `known_models` for each provider. To see what a provider actually serves today, run a refresh:

```bash
# inside the TUI
/model
/model refresh
/model refresh anthropic
```

`/model refresh` fetches every provider's `/models` endpoint; `refresh <slug>` fetches one (`src/vtx/tui/commands/models.py:47`). 58 of the 59 `provider.yaml` entries have `fetch_models: true`, and all 59 have a `base_url`, so every one of them is a dynamic provider (`src/vtx/ai/provider_catalog.py:110`).

Fetched results are cached per provider under `~/.vtx/models/<slug>.json`, or under `$VTX_MODELS_CACHE_DIR` if set (`src/vtx/ai/dynamic_models.py:302`, `src/vtx/ai/model_fetcher.py:54`). The cache is valid for six hours and each fetch has a seven-second timeout, so a slow provider cannot stall startup (`src/vtx/ai/dynamic_models.py:54`, `src/vtx/ai/dynamic_models.py:55`). Refreshes are de-duplicated per provider with a 60-minute cooldown to avoid hammering an endpoint (`src/vtx/ai/model_fetcher.py:39`).

Context windows and output caps do not come from the provider. They come from models.dev, cached for 24 hours in `~/.vtx/models_dev_limits.json` (`src/vtx/ai/context_length.py:21`, `src/vtx/ai/context_length.py:22`). When a model is not in that table, `safe_max_output_tokens()` falls back to 8192 tokens under 128k context, 16384 under 256k, and 32768 above that (`src/vtx/ai/context_length.py:25`). The full harness default for an unknown model is `agent.default_context_window`, 200000 by default (`src/vtx/core/config.py:149`).

An empty cache is not an error: `/model` says "No models configured" and tells you to run a refresh (`src/vtx/tui/commands/models.py:93`).

## Known gaps

- `--provider` and the catalog disagree. The flag accepts 16 slugs (`src/vtx/ai/providers/__init__.py:4`), the catalog has 62, and two accepted slugs (`openai-codex`, `openai-responses`) match nothing. Use `llm.default_provider` for the other 48.
- The shipped `llm.default_provider` is `openai-codex` (`src/vtx/core/defaults/config.yml:5`), which is not a catalog slug. It resolves anyway, but only because `find_model()` falls back to a catalog-wide scan for the model id (`src/vtx/ai/provider_catalog.py:637`). Pick `codex` or `openai` instead.
- `AnthropicSDKProvider` ignores the provider's `api_key_env` and only reads `ANTHROPIC_API_KEY` (`src/vtx/ai/providers/anthropic_sdk.py:41`), so `aerolink` and `fastrouter` share that one variable with `anthropic`.
- The three OAuth providers declare an env var (`GITHUB_TOKEN`, `CLINE_API_KEY`, `OPENAI_API_KEY`) but none of them reads it as a credential. They read the OAuth files only, and each has its own non-standard grant flow (`src/vtx/tui/commands/auth.py:5`).
- There is no CLI flag to list providers or to refresh models. `/model refresh` is the only entry point, and it requires the TUI.
- No custom provider can declare a new API type. `family` is restricted to `openai_compat` and `anthropic`, so a Responses-only or a genuinely custom transport is not expressible (`src/vtx/ai/provider_catalog.py:505`).
- `detect_provider_from_env()` scans in `provider.yaml` order, so with several keys exported the winner depends on file ordering rather than on anything you chose (`src/vtx/ai/provider_catalog.py:270`).
- `llm.default_thinking_level` is not validated, and the model's `thinking_level_map` can null out a level the catalog advertises, so a typo surfaces only when the provider clamps it (`src/vtx/core/config.py:134`, `src/vtx/ai/base.py:272`).

Related pages: [configuration.md](configuration.md), [theming.md](theming.md), [cli.md](cli.md), [permissions.md](permissions.md), [skills.md](skills.md), [plugins.md](plugins.md).
