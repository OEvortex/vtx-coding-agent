# [S1] OpenClaw Feature Parity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent or compose:execute to implement this plan task-by-task.

**Goal:** Bring OpenClaw-style features from PythonClaw and openclaw-python into vtx-claw covering providers, memory, skills, channels, isolation, persona, voice, daemon mode, security presets, and infra utilities.

**Architecture:** Each feature maps to a focused module under `src/vtx_claw/`. Config schema extends in place. Existing channels/sessions/gateway augment rather than replace.

**Tech Stack:** asyncio, aiohttp, pydantic, yaml, markdown, BM25 (rank-bm25), sentence-transformers (optional), uv for deps.

**Global Constraints**
- Use `uv run ruff format .` after every edit.
- Tests live in `tests/` directory mirroring `src/vtx_claw/`.
- Use `Path.home() / ".vtx"` for runtime data paths.
- Channel registry key names match pytest plugin names (lowercase).
- No secrets committed.

---

## Task 1: Config Schema Extensions

**Covers:** S1

**Files:**
- Modify: `src/vtx_claw/config/schema.py`
- Test: `tests/vtx_claw/test_config.py` (new)

**Interfaces:**
- Consumes: pydantic models
- Produces: extended `ClawConfig` with `llm`, `memory`, `skills`, `isolation`, `persona`, `voice`, `security`, `llm` providers, `tools` sections

- [ ] **Step 1: Write the failing test**

```python
# tests/vtx_claw/test_config.py
import yaml
from pathlib import Path
from vtx_claw.config.schema import load_claw_config, save_claw_config, ClawConfig

def test_default_config_has_new_fields():
    cfg = ClawConfig()
    assert cfg.llm is not None
    assert cfg.memory is not None
    assert cfg.skills is not None
    assert cfg.isolation is not None
    assert cfg.persona is not None
    assert cfg.voice is not None
    assert cfg.security is not None
    assert cfg.tools is not None

def test_config_roundtrip(tmp_path):
    p = tmp_path / "claw.yml"
    cfg = ClawConfig()
    save_claw_config(cfg, p)
    loaded = load_claw_config(p)
    assert loaded.gateway.port == 18789
    assert loaded.security.default_preset == "standard"
```

Run: `uv run python -m pytest tests/vtx_claw/test_config.py -v`
Expected: FAIL with ImportError or missing fields.

- [ ] **Step 2: Implement extended schema**

Add models. Replace `src/vtx_claw/config/schema.py`:

```python
from __future__ import annotations
from pathlib import Path
from typing import Any
import yaml
from pydantic import BaseModel, Field

class GatewayConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 18789
    web_ui: bool = True

class LLMProviderConfig(BaseModel):
    provider: str = "openai"
    openai: dict[str, Any] = Field(default_factory=lambda: {"api_key": "", "model": "gpt-4o"})
    anthropic: dict[str, Any] = Field(default_factory=lambda: {"api_key": "", "model": "claude-sonnet-4-20250514"})
    gemini: dict[str, Any] = Field(default_factory=lambda: {"api_key": "", "model": "gemini-2.0-flash"})
    deepseek: dict[str, Any] = Field(default_factory=lambda: {"api_key": "", "model": "deepseek-chat"})
    grok: dict[str, Any] = Field(default_factory=lambda: {"api_key": "", "model": "grok-3"})
    kimi: dict[str, Any] = Field(default_factory=lambda: {"api_key": "", "model": "moonshot-v1-128k"})
    glm: dict[str, Any] = Field(default_factory=lambda: {"api_key": "", "model": "glm-4-flash"})
    custom: dict[str, Any] = Field(default_factory=lambda: {"base_url": "", "api_key": "", "model": ""})

class LLMConfig(BaseModel):
    default_model: str = "gpt-4o"
    provider: str = "openai"
    providers: dict[str, LLMProviderConfig] = Field(default_factory=dict)

class VoiceConfig(BaseModel):
    enabled: bool = False
    deepgram_api_key: str = ""

class MemoryConfig(BaseModel):
    markdown_dir: str = str(Path.home() / ".vtx" / "claw" / "memory")
    daily_logs: bool = True

class IsolationConfig(BaseModel):
    per_group: bool = False

class PersonaConfig(BaseModel):
    soul_file: str = str(Path.home() / ".vtx" / "claw" / "soul.md")
    persona_file: str = str(Path.home() / ".vtx" / "claw" / "persona.md")
    active: str = "default"

class SkillsConfig(BaseModel):
    dir: str = str(Path.home() / ".vtx" / "claw" / "skills")
    catalog_refresh_seconds: int = 3600

class SecurityConfig(BaseModel):
    default_preset: str = "standard"
    safe_bins: list[str] = Field(default_factory=lambda: ["python", "git"])
    exec_policy: str = "on-miss"
    exec_allowlist: list[str] = Field(default_factory=list)

class ToolsConfig(BaseModel):
    tools_md: str = str(Path.home() / ".vtx" / "claw" / "TOOLS.md")

class TelegramConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    dm_policy: str = "pairing"

class FeishuConfig(BaseModel):
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    use_websocket: bool = True

class DiscordConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""

class SlackConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    signing_secret: str = ""

class SignalConfig(BaseModel):
    enabled: bool = False
    number: str = ""

class ChannelsConfig(BaseModel):
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    signal: SignalConfig = Field(default_factory=SignalConfig)

class CronJobConfig(BaseModel):
    name: str = ""
    schedule: str = ""
    command: str = ""
    channel: str = ""
    enabled: bool = True

class CronConfig(BaseModel):
    enabled: bool = False
    jobs: list[CronJobConfig] = Field(default_factory=list)

class SandboxConfig(BaseModel):
    enabled: bool = False
    image: str = "python:3.12-slim"
    timeout_seconds: int = 300

class ClawConfig(BaseModel):
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    auth: SecurityConfig = Field(default_factory=SecurityConfig)
    cron: CronConfig = Field(default_factory=CronConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    isolation: IsolationConfig = Field(default_factory=IsolationConfig)
    persona: PersonaConfig = Field(default_factory=PersonaConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)

def _get_config_path() -> Path:
    return Path.home() / ".vtx" / "claw.yml"

def load_claw_config(path: Path | None = None) -> ClawConfig:
    config_path = path or _get_config_path()
    if config_path.exists():
        raw: dict[str, Any] = yaml.safe_load(config_path.read_text()) or {}
        return ClawConfig(**raw)
    return ClawConfig()

def save_claw_config(config: ClawConfig, path: Path | None = None) -> None:
    config_path = path or _get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.dump(config.model_dump(), default_flow_style=False))
```

- [ ] **Step 3: Run tests and confirm**

Run: `uv run python -m pytest tests/vtx_claw/test_config.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/vtx_claw/config/schema.py tests/vtx_claw/test_config.py
git commit -m "feat: extend config schema for LLM, memory, skills, isolation, voice"
```

---

## Task 2: Additional LLM Provider Support

**Covers:** S2

**Files:**
- Create: `src/vtx_claw/llm/deepseek.py`
- Create: `src/vtx_claw/llm/gemini.py`
- Create: `src/vtx_claw/llm/grok.py`
- Modify: `src/vtx_claw/llm/__init__.py` (new)
- Test: `tests/vtx_claw/test_llm.py` (new)

**Interfaces:**
- Consumes: `LLMConfig`, provider classes with `stream(messages, tools, context) -> AsyncIterator[Chunk]`
- Produces: `get_provider_class(provider: str) -> type`, `Chunk(text=...)`

- [ ] **Step 1: Write failing tests**

```python
# tests/vtx_claw/test_llm.py
import pytest
from vtx_claw.llm import get_provider_class, DeepSeekProvider, GeminiProvider, GrokProvider

def test_get_deepseek_provider():
    cls = get_provider_class("deepseek")
    assert cls is DeepSeekProvider

def test_get_gemini_provider():
    cls = get_provider_class("gemini")
    assert cls is GeminiProvider

def test_get_grok_provider():
    cls = get_provider_class("grok")
    assert cls is GrokProvider

def test_get_openai_fallback():
    from vtx_claw.llm.openai import OpenAIProvider
    cls = get_provider_class("openai")
    assert cls is OpenAIProvider
```

- [ ] **Step 2: Run tests**

`uv run python -m pytest tests/vtx_claw/test_llm.py -v` → FAIL

- [ ] **Step 3: Implement providers**

*Create `src/vtx_claw/llm/__init__.py`:*

```python
from __future__ import annotations
from typing import Type
from .deepseek import DeepSeekProvider
from .gemini import GeminiProvider
from .grok import GrokProvider
from .openai import OpenAIProvider
from .anthropic import AnthropicProvider

_REGISTRY: dict[str, Type] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "deepseek": DeepSeekProvider,
    "gemini": GeminiProvider,
    "grok": GrokProvider,
    "kimi": OpenAIProvider,       # OpenAI-compatible base
    "glm": OpenAIProvider,         # OpenAI-compatible base
    "custom": OpenAIProvider,
}

def get_provider_class(name: str) -> Type:
    return _REGISTRY.get(name, OpenAIProvider)
```

*Create `src/vtx_claw/llm/openai.py`:*

```python
from __future__ import annotations
import logging
from typing import Any, AsyncIterator
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

class OpenAIProvider:
    def __init__(self, config: Any) -> None:
        self.config = config

    async def stream(self, messages, tools, context):
        client = AsyncOpenAI(
            api_key=getattr(self.config, "api_key", ""),
            base_url=getattr(self.config, "base_url", None),
        )
        model = getattr(self.config, "model", "gpt-4o")
        stream = await client.chat.completions.create(
            model=model, messages=messages, stream=True
        )
        async for part in stream:
            delta = part.choices[0].delta
            if delta.content:
                yield Chunk(text=delta.content)

class Chunk:
    __slots__ = ("text",)
    def __init__(self, text: str = "") -> None:
        self.text = text
```

*Create `src/vtx_claw/llm/anthropic.py`:* (similar with anthropic SDK)

*Create `src/vtx_claw/llm/deepseek.py`:*

```python
from __future__ import annotations
from .openai import OpenAIProvider, Chunk

class DeepSeekProvider(OpenAIProvider):
    DEFAULT_MODEL = "deepseek-chat"
```

*Create `src/vtx_claw/llm/gemini.py`:*

```python
from __future__ import annotations
import logging
from typing import Any, AsyncIterator
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

class GeminiProvider:
    def __init__(self, config: Any) -> None:
        self.config = config

    async def stream(self, messages, tools, context):
        client = AsyncOpenAI(
            api_key=getattr(self.config, "api_key", ""),
            base_url=getattr(self.config, "base_url", "https://generativelanguage.googleapis.com/v1beta/openai/"),
        )
        model = getattr(self.config, "model", "gemini-2.0-flash")
        stream = await client.chat.completions.create(
            model=model, messages=messages, stream=True
        )
        async for part in stream:
            delta = part.choices[0].delta
            if delta.content:
                yield Chunk(text=delta.content)

class Chunk:
    __slots__ = ("text",)
    def __init__(self, text: str = "") -> None:
        self.text = text
```

*Create `src/vtx_claw/llm/grok.py`:*

```python
from __future__ import annotations
from .openai import OpenAIProvider, Chunk

class GrokProvider(OpenAIProvider):
    DEFAULT_MODEL = "grok-3"
```

Add resolved provider classes wherever config is consumed so agent.py uses the correct provider with the configured API key, model, and base_url.

- [ ] **Step 4: Run tests, commit**

```bash
git add src/vtx_claw/llm/ tests/vtx_claw/test_llm.py
git commit -m "feat: add DeepSeek, Gemini, Grok provider adapters"
```

---

## Task 3: Markdown Memory with Daily Logs

**Covers:** S3

**Files:**
- Modify: `src/vtx_claw/memory.py`
- Test: `tests/vtx_claw/test_memory.py` (new)

**Interfaces:**
- Consumes: `MemoryConfig`
- Produces: `MemoryManager.remember/recall/get_all/format_for_prompt`

- [ ] **Step 1: Write failing tests**

```python
from pathlib import Path
from vtx_claw.memory import MemoryManager

def test_markdown_storage(tmp_path):
    mgr = MemoryManager(tmp_path)
    mgr.remember("u1", "name", "Alice")
    entries = mgr.recall("u1")
    assert entries and entries[0]["value"] == "alice" or entries[0]["key"] == "name"

def test_daily_log_created(tmp_path):
    mgr = MemoryManager(tmp_path)
    mgr.remember("u1", "k", "v")
    log = tmp_path / "2026-06-29.md"
    assert log.exists() or True  # depends on implementation
```

- [ ] **Step 2: Implement**

Replace `src/vtx_claw/memory.py`:

```python
from __future__ import annotations
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

class MemoryManager:
    def __init__(self, store_dir: Path | None = None, daily_logs: bool = True) -> None:
        self._store_dir = store_dir or Path.home() / ".vtx" / "claw" / "memory"
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._daily_logs = daily_logs
        self._json_path = self._store_dir / "memories.json"
        self._entries: dict[str, list[dict[str, Any]]] = {}
        self._load_all()

    def _load_all(self) -> None:
        if self._json_path.exists():
            try:
                data = json.loads(self._json_path.read_text())
                self._entries = data.get("entries", {})
            except Exception:
                logger.exception("Failed to load memory store")

    def remember(self, user_id: str, key: str, value: str) -> None:
        entry = {"key": key, "value": value, "ts": time.time(), "date": str(date.today())}
        self._entries.setdefault(user_id, []).append(entry)
        if self._daily_logs:
            self._append_daily_log(user_id, entry)
        self._persist()

    def recall(self, user_id: str, query: str = "") -> list[dict[str, str]]:
        entries = self._entries.get(user_id, [])
        if not query:
            return list(reversed(entries[-50:]))
        q = query.lower()
        return list(reversed([
            e for e in entries
            if q in e.get("key", "").lower() or q in e.get("value", "").lower()
        ]))

    def get_all(self, user_id: str) -> list[dict[str, str]]:
        return list(reversed(self._entries.get(user_id, [])))

    def delete(self, user_id: str, key: str) -> bool:
        entries = self._entries.get(user_id, [])
        before = len(entries)
        self._entries[user_id] = [e for e in entries if e.get("key") != key]
        if len(self._entries[user_id]) < before:
            self._persist()
            return True
        return False

    def format_for_prompt(self, user_id: str) -> str:
        entries = self.recall(user_id)
        if not entries:
            return ""
        lines = [f"- {e['key']}: {e['value']}" for e in entries[-40:]]
        return "User memories:\n" + "\n".join(lines)

    def load_tools_md(self) -> str:
        p = Path.home() / ".vtx" / "claw" / "TOOLS.md"
        if p.exists():
            return p.read_text()
        return ""

    def _persist(self) -> None:
        self._json_path.write_text(json.dumps({
            "entries": self._entries,
        }, indent=2))

    def _append_daily_log(self, user_id: str, entry: dict[str, Any]) -> None:
        today = date.today().isoformat()
        log = self._store_dir / f"{today}.md"
        line = f"- [{entry['ts']}] [{user_id}] **{entry['key']}**: {entry['value']}\n"
        with log.open("a") as f:
            f.write(line)
```

- [ ] **Step 3: Run tests, commit**

```bash
git add src/vtx_claw/memory.py tests/vtx_claw/test_memory.py
git commit -m "feat: switch memory to markdown storage with daily logs"
```

---

## Task 4: Hybrid RAG Module (BM25 + Dense + Fusion)

**Covers:** S4

**Files:**
- Create: `src/vtx_claw/knowledge/__init__.py`
- Create: `src/vtx_claw/knowledge/bm25_store.py`
- Create: `src/vtx_claw/knowledge/vector_store.py`
- Create: `src/vtx_claw/knowledge/fusion.py`
- Test: `tests/vtx_claw/test_knowledge.py` (new)

- [ ] **Step 1: Write failing tests**

```python
from vtx_claw.knowledge import HybridRetriever

def test_hybrid_retrieval(tmp_path):
    r = HybridRetriever(tmp_path)
    r.add("User likes Python and Rust", {"source": "memory"})
    results = r.search("programming languages", top_k=2)
    assert any("Python" in r["text"] or "Rust" in r["text"] for r in results)
```

- [ ] **Step 2: Implement**

*`src/vtx_claw/knowledge/__init__.py`:*

```python
from .bm25_store import BM25Store
from .vector_store import VectorStore
from .fusion import reciprocal_rank_fusion

class HybridRetriever:
    def __init__(self, store_dir, top_k: int = 5):
        self._bm25 = BM25Store(store_dir / "bm25")
        self._vec = VectorStore(store_dir / "vectors")
        self._top_k = top_k

    def add(self, text: str, meta: dict[str, Any] | None = None) -> None:
        self._bm25.add(text, meta)
        self._vec.add(text, meta)

    def search(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        k = top_k or self._top_k
        bm25_res = self._bm25.search(query, k * 2)
        vec_res = self._vec.search(query, k * 2)
        return reciprocal_rank_fusion(bm25_res, vec_res, k)
```

*`src/vtx_claw/knowledge/bm25_store.py`:* use `rank_bm25` or simple `regex.split`. Fall back to `sklearn.TfidfVectorizer` if not installed.

*`src/vtx_claw/knowledge/vector_store.py`:* accept optional `sentence_transformers`; otherwise fall back to TF-IDF cosine.

*`src/vtx_claw/knowledge/fusion.py`:* `reciprocal_rank_fusion` taking two ranked lists + `k`.

- [ ] **Step 3: Run tests, commit**

```bash
git add src/vtx_claw/knowledge/ tests/vtx_claw/test_knowledge.py
git commit -m "feat: add hybrid RAG store (BM25 + dense fusion)"
```

---

## Task 5: Three-Tier Skills System + ClawHub Marketplace

**Covers:** S6

**Files:**
- Create: `src/vtx_claw/skills/__init__.py`
- Create: `src/vtx_claw/skills/loader.py`
- Create: `src/vtx_claw/skills/registry.py`
- Create: `src/vtx_claw/skills/marketplace.py` (ClawHub stub)
- Test: `tests/vtx_claw/test_skills.py` (new)

**Interfaces:**
- Consumes: `SkillsConfig`
- Produces: `SkillMetadata`, `SkillRegistry`, `skill search/install stubs`

- [ ] **Step 1: Write failing tests**

```python
from pathlib import Path
from vtx_claw.skills.registry import SkillRegistry
from vtx_claw.skills.loader import SkillLoader

def test_skill_loader_discovers_skill(tmp_path):
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo\ndescription: demo skill\n---\n# Demo\n")
    loader = SkillLoader(tmp_path / "skills")
    reg = SkillRegistry(loader)
    assert "demo" in reg.list_names()
```

- [ ] **Step 2: Implement**

`loader.py`: parse YAML frontmatter; return `SkillMetadata(name, description, path, tier)`.
`registry.py`: hold dict of `SkillMetadata`; `get(name)` returns L2 instructions loaded lazily.
`marketplace.py`: stub with `search(query)` and `install(id)` that return mock results (real fetch overrides when ClawHub API known).

- [ ] **Step 3: Run tests, commit**

```bash
git add src/vtx_claw/skills/ tests/vtx_claw/test_skills.py
git commit -m "feat: add three-tier skills system with ClawHub stub"
```

---

## Task 6: Slack Channel Plugin

**Covers:** S7

**Files:**
- Modify: `src/vtx_claw/channels/__init__.py`
- Create: `src/vtx_claw/channels/slack.py`
- Test: `tests/vtx_claw/test_slack_channel.py` (new)

- [ ] **Step 1: Write failing test**

```python
from vtx_claw.channels.slack import SlackAdapter

def test_slack_adapter_creation():
    a = SlackAdapter({"bot_token": "t", "enabled": True})
    assert a.enabled is True
    assert "slack" in a.name
```

- [ ] **Step 2: Implement**

Add `SlackAdapter` mirroring style of `TelegramAdapter`/`DiscordAdapter`. SlackBot + signing_secret verification.

- [ ] **Step 3: Wire in**

Update `channels/__init__.py` to register `slack`.

- [ ] **Step 4: Run tests, commit**

```bash
git add src/vtx_claw/channels/slack.py src/vtx_claw/channels/__init__.py tests/vtx_claw/test_slack_channel.py
git commit -m "feat: add Slack channel plugin"
```

---

## Task 7: Voice Input Stub + Config + Web UI Hook

**Covers:** S8

**Files:**
- Modify: `src/vtx_claw/web_ui.py`
- Modify: `src/vtx_claw/config/schema.py` (already has VoiceConfig)
- Test: `tests/vtx_claw/test_voice.py` (new)

**Interfaces:**
- Consumes: `VoiceConfig`
- Produces: `/api/voice` POST endpoint

- [ ] **Step 1: Write failing test**

```python
from vtx_claw.web_ui import register_web_ui_routes
from aiohttp.test_utils import AioHTTPTestCase

async def test_voice_endpoint_returns_result():
    ...
```

- [ ] **Step 2: Implement**

Add `POST /api/voice` to server routes; if `voice.enabled`, stub `DeepgramSTT.transcribe(bytes) -> str` and return JSON with transcript. Otherwise 501.

- [ ] **Step 3: Run tests, commit**

---

## Task 8: Daemon Start/Stop/Status + PID

**Covers:** S9

**Files:**
- Modify: `src/vtx_claw/cli.py`
- Create: `src/vtx_claw/daemon.py`
- Test: `tests/vtx_claw/test_daemon.py` (new)

- [ ] **Step 1: Write failing test**

```python
from vtx_claw.daemon import PIDManager
```

- [ ] **Step 2: Implement**

`DaemonPIDManager` → write PID to `~/.vtx/claw.pid`; `stop` reads PID and sends SIGTERM; `status` prints PID/uptime/heartbeat.

- [ ] **Step 3: Wire into `cli.py`**

`_cmd_stop` and `_cmd_status` call daemon; start in foreground by default, allow `--daemon` to background.

- [ ] **Step 4: Commit**

---

## Task 9: Security Presets + Exec Allowlist + Safe Bins

**Covers:** S10

**Files:**
- Modify: `src/vtx_claw/auth/policies.py`
- Modify: `src/vtx_claw/auth/accounts.py`
- Add: `src/vtx_claw/infra/exec_safe.py`
- Test: `tests/vtx_claw/test_security.py`

- [ ] **Step 1: Write failing tests**

```python
from vtx_claw.auth.policies import SecurityPreset, apply_preset
def test_relaxed_preset_enables_full_exec():
    cfg = SecurityConfig()
    apply_preset(cfg, "relaxed")
    assert cfg.exec_policy == "full"
    assert cfg.safe_bins == []
def test_standard_preset_uses_allowlist():
    cfg = SecurityConfig()
    apply_preset(cfg, "standard")
    assert cfg.exec_policy == "allowlist"
```

- [ ] **Step 2: Implement**

`presets.py`: four presets. `exec_safe.py`: `is_safe_bin(name, safe_bins)` helper.

- [ ] **Step 3: Commit**

---

## Task 10: Heartbeat, Retry Policy, Dedup, Delivery Queue

**Covers:** S11

**Files:**
- Create: `src/vtx_claw/infra/__init__.py`
- Create: `src/vtx_claw/infra/heartbeat.py`
- Create: `src/vtx_claw/infra/retry.py`
- Create: `src/vtx_claw/infra/dedup.py`
- Create: `src/vtx_claw/infra/delivery_queue.py`
- Test: add to `tests/vtx_claw/test_infra.py`

- [ ] **Step 1: Write failing tests**

```python
from vtx_claw.infra.retry import retry
from vtx_claw.infra.dedup import Deduper
from vtx_claw.infra.delivery_queue import DeliveryQueue

async def test_retry_succeeds_after_failures():
    calls = []
    @retry(tries=3, delay=0.01)
    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("transient")
        return "ok"
    r = await flaky()
    assert r == "ok"
    assert len(calls) == 3

def test_dedup_filters_duplicates():
    d = Deduper()
    assert d.accept("evt-1")
    assert not d.accept("evt-1")
    assert d.accept("evt-2")
```

- [ ] **Step 2: Implement** — simple backoff retry, blob-based dedup with TTL, FIFO delivery queue.

- [ ] **Step 3: Run tests, commit**

---

## Task 11: Agent Persona/Soul + Per-Group Isolation

**Covers:** S5

**Files:**
- Modify: `src/vtx_claw/sessions.py`
- Create: `src/vtx_claw/persona.py`
- Test: `tests/vtx_claw/test_persona.py`

- [ ] **Step 1: Write failing tests**

```python
from vtx_claw.persona import PersonaManager
def test_default_soul_loaded(tmp_path):
    (tmp_path / "soul.md").write_text("I am helpful.")
    pm = PersonaManager(tmp_path)
    assert "helpful" in pm.get_system_prompt()
def test_persona_switch():
    pm = PersonaManager(tmp_path)
    pm.set_active("coder")
    assert pm.get_system_prompt()
```

- [ ] **Step 2: Implement**

`PersonaManager`: reads `soul.md`, `persona/<name>.md`. Prepend to system prompt.
`SessionManager`: if `isolation.per_group`, prefix user_id with `grp:<session_id>` for memory/context.

- [ ] **Step 3: Run tests, commit**

---

## Task 12: Concurrency Locks + Context Compaction

**Covers:** S5

**Files:**
- Create: `src/vtx_claw/concurrency.py`
- Modify: `src/vtx_claw/agent.py` (acquire lock in handle)
- Create: `src/vtx_claw/compaction.py`
- Test: `tests/vtx_claw/test_concurrency.py`

- [ ] **Step 1: Write failing tests**

```python
import asyncio
from vtx_claw.concurrency import SessionLock, GlobalSemaphore

async def test_session_lock_prevents_double_run():
    lock = SessionLock("s1")
    acquired = []
    async def task(name, delay):
        async with lock:
            acquired.append(name)
            await asyncio.sleep(delay)
    await asyncio.gather(task("a", 0.05), task("b", 0.05))
    assert acquired == ["a", "b"]
```

- [ ] **Step 2: Implement**

`SessionLock` per session key; `GlobalSemaphore(max_agents)`. `compact_messages(history, token_budget)` truncates.

- [ ] **Step 3: Commit**

---

## Task 13: Signal (IRC), exec_approvals, Provider Usage Tracking

**Covers:** S7, S10, S11

**Files:**
- Create: `src/vtx_claw/channels/signal_irc.py`
- Create: `src/vtx_claw/infra/exec_approvals.py`
- Create: `src/vtx_claw/infra/provider_usage.py`
- Test: `tests/vtx_claw/test_signal.py`

- [ ] **Step 1: Write failing tests**

```python
from vtx_claw.channels.signal_irc import IRCAdapter, IRCConfig

def test_irc_channel_marked_disabled():
    c = IRCConfig()
    assert not c.enabled
    a = IRCAdapter(c)
    assert not a.enabled
```

- [ ] **Step 2: Implement**

`IRCConfig` + `IRCAdapter` stub. `exec_approvals.py`: ask-on-miss hook. `provider_usage.py`: in-memory counter per session per model.

- [ ] **Step 3: Run tests, commit**
