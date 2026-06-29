from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class GatewayConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 18789
    web_ui: bool = True


class ProviderSecrets(BaseModel):
    api_key: str = ""
    model: str = ""


class LLMConfig(BaseModel):
    default_model: str = "gpt-4o"
    provider: str = "openai"
    openai: dict[str, Any] = Field(default_factory=lambda: {"api_key": "", "model": "gpt-4o"})
    anthropic: dict[str, Any] = Field(default_factory=lambda: {"api_key": "", "model": "claude-sonnet-4-20250514"})
    deepseek: dict[str, Any] = Field(default_factory=lambda: {"api_key": "", "model": "deepseek-chat"})
    gemini: dict[str, Any] = Field(default_factory=lambda: {"api_key": "", "model": "gemini-2.0-flash"})
    grok: dict[str, Any] = Field(default_factory=lambda: {"api_key": "", "model": "grok-3"})
    kimi: dict[str, Any] = Field(default_factory=lambda: {"api_key": "", "model": "moonshot-v1-128k"})
    glm: dict[str, Any] = Field(default_factory=lambda: {"api_key": "", "model": "glm-4-flash"})
    custom: dict[str, Any] = Field(default_factory=lambda: {"base_url": "", "api_key": "", "model": ""})


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


class ClawConfig(BaseModel):
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


CHANNEL_FIELD_NAMES: list[str] = [
    "telegram",
    "feishu",
    "discord",
    "slack",
    "signal",
]


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
    config_path.write_text(
        yaml.dump(config.model_dump(mode="python"), default_flow_style=False, sort_keys=False)
    )
