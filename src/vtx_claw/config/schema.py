"""Claw configuration schema — stored inside vtx's ``~/.vtx/config.yml``.

The claw config lives under a ``claw:`` top-level key in the same YAML
file that vtx uses.  vtx's own schema silently ignores the extra key
(Pydantic v2 ``extra='ignore'`` by default), so both tools share the
same config file without conflict.

All file paths resolve through :func:`vtx.config.get_config_dir` (default
``~/.vtx``) so there is a single storage root.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from vtx.config import get_config_dir


class GatewayConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 18789


class ProviderSecrets(BaseModel):
    api_key: str = ""
    model: str = ""


class LLMConfig(BaseModel):
    default_model: str = "gpt-4o"
    provider: str = "openai"
    openai: dict[str, Any] = Field(default_factory=lambda: {"api_key": "", "model": "gpt-4o"})
    anthropic: dict[str, Any] = Field(
        default_factory=lambda: {"api_key": "", "model": "claude-sonnet-4-20250514"}
    )
    deepseek: dict[str, Any] = Field(
        default_factory=lambda: {"api_key": "", "model": "deepseek-chat"}
    )
    gemini: dict[str, Any] = Field(
        default_factory=lambda: {"api_key": "", "model": "gemini-2.0-flash"}
    )
    grok: dict[str, Any] = Field(default_factory=lambda: {"api_key": "", "model": "grok-3"})
    kimi: dict[str, Any] = Field(
        default_factory=lambda: {"api_key": "", "model": "moonshot-v1-128k"}
    )
    glm: dict[str, Any] = Field(default_factory=lambda: {"api_key": "", "model": "glm-4-flash"})
    custom: dict[str, Any] = Field(
        default_factory=lambda: {"base_url": "", "api_key": "", "model": ""}
    )

    def resolve_api_key(self) -> str:
        """Return the first non-empty API key across all provider blocks."""
        for prov_key in ("openai", "anthropic", "deepseek", "gemini", "grok", "kimi", "glm"):
            prov = getattr(self, prov_key, None) or {}
            if isinstance(prov, dict) and prov.get("api_key"):
                return prov["api_key"]
        return ""


class MemoryConfig(BaseModel):
    daily_logs: bool = True


class IsolationConfig(BaseModel):
    per_group: bool = False


class PersonaConfig(BaseModel):
    soul_file: str = str(Path.home() / ".vtx" / "claw" / "soul.md")
    persona_file: str = str(Path.home() / ".vtx" / "claw" / "persona.md")
    active: str = "default"


class SkillsConfig(BaseModel):
    catalog_refresh_seconds: int = 3600


class SecurityConfig(BaseModel):
    default_policy: str = "pairing"
    default_preset: str = "standard"
    safe_bins: list[str] = Field(default_factory=lambda: ["python", "git"])
    exec_policy: str = "on-miss"
    exec_allowlist: list[str] = Field(default_factory=list)
    allowlist: list[str] = Field(default_factory=list)


class AuthConfig(SecurityConfig):
    default_policy: str = "pairing"
    allowlist: list[str] = Field(default_factory=list)
    default_preset: str = "standard"


class VoiceConfig(BaseModel):
    enabled: bool = False
    deepgram_api_key: str = ""


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
    phone: str = ""


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


class ToolsConfig(BaseModel):
    tools_md: str = str(Path.home() / ".vtx" / "claw" / "TOOLS.md")


class ClawConfig(BaseModel):
    """vtx_claw configuration — embedded in vtx's ``~/.vtx/config.yml``.

    The ``llm`` section feeds into :class:`~vtx.runtime.ConversationRuntime`
    via :class:`~vtx_claw.agent.AgentHandler`, which also reads
    :mod:`vtx.config` for defaults (model, provider, API keys).
    """

    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
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


CHANNEL_FIELD_NAMES: list[str] = ["telegram", "feishu", "discord", "slack", "signal"]


# ── Data directory (shared with vtx) ─────────────────────────────────────


def get_claw_dir() -> Path:
    """Return ``~/.vtx/claw/`` — the claw's storage root under vtx's config dir."""
    return get_config_dir() / "claw"


def get_claw_pid_path() -> Path:
    """Return ``~/.vtx/claw.pid`` — the PID file path."""
    return get_config_dir() / "claw.pid"


# ── Config file (shared with vtx) ────────────────────────────────────────


def _get_config_path() -> Path:
    """Return the shared config file path (same as vtx's)."""
    return get_config_dir() / "config.yml"


def load_claw_config(path: Path | None = None) -> ClawConfig:
    """Load claw config from the ``claw:`` key in vtx's ``config.yml``.

    If the key is absent (fresh install) the built-in defaults are used.
    """
    config_path = path or _get_config_path()
    if config_path.exists():
        raw: dict[str, Any] = yaml.safe_load(config_path.read_text()) or {}
        if "claw" in raw:
            claw_raw = raw["claw"]
        elif any(
            k in raw for k in ("gateway", "channels", "auth", "cron", "sandbox", "llm", "memory")
        ):
            claw_raw = raw
        else:
            claw_raw = {}
        if isinstance(claw_raw, dict):
            return ClawConfig(**claw_raw)
    return ClawConfig()


def save_claw_config(config: ClawConfig, path: Path | None = None) -> None:
    """Write the claw config into the ``claw:`` key of vtx's ``config.yml``.

    Other sections of the file are preserved as-is.
    """
    config_path = path or _get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    raw: dict[str, Any] = {}
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text()) or {}

    raw["claw"] = config.model_dump(mode="python")

    config_path.write_text(
        yaml.dump(raw, default_flow_style=False, sort_keys=False, allow_unicode=True)
    )
