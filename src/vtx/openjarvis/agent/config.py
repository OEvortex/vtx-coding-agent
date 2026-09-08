"""OpenJarvis agent config — VTX-native."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from vtx.coding_agent.config import get_config_dir

DMPolicy = Literal["pairing", "allowlist", "open", "disabled"]
GroupPolicy = Literal["allowlist", "open", "disabled"]


class GatewayConfig(BaseModel):
    port: int = 18789
    bind: Literal["loopback", "auto", "0.0.0.0"] = "loopback"
    auth_mode: Literal["token", "password", "none", "trusted-proxy"] = "token"
    token: str | None = None
    verbose: bool = False


class ChannelAccountConfig(BaseModel):
    enabled: bool = True
    dm_policy: DMPolicy = "pairing"
    allow_from: list[str] = Field(default_factory=list)
    group_policy: GroupPolicy = "allowlist"
    group_allow_from: list[str] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)


class ChannelsDefaultsConfig(BaseModel):
    group_policy: GroupPolicy = "allowlist"
    heartbeat: bool = True


class CronDefaultsConfig(BaseModel):
    enabled: bool = True
    max_jobs: int = 100


class SessionConfig(BaseModel):
    dm_scope: Literal["main", "per-peer", "per-channel-peer", "per-account-channel-peer"] = (
        "per-channel-peer"
    )
    workspace: str | None = None


class MemoryConfig(BaseModel):
    skills_enabled: bool = True
    fts5_enabled: bool = True
    honcho_enabled: bool = False
    vector_enabled: bool = True


class OpenJarvisConfig(BaseModel):
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    channels: dict[str, ChannelAccountConfig] = Field(default_factory=dict)
    channels_defaults: ChannelsDefaultsConfig = Field(default_factory=ChannelsDefaultsConfig)
    cron: CronDefaultsConfig = Field(default_factory=CronDefaultsConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    workspace: str = Field(default_factory=lambda: str(Path.cwd()))
    model: str | None = None
    model_provider: str | None = None
    # Per-tool configuration consumed by the openjarvis tool loader, e.g.
    # {"exec": {"sandbox": ""}, "web": {"enable": false}}.
    tools: dict = Field(default_factory=dict)

    @classmethod
    def load(cls) -> OpenJarvisConfig:
        cfg_path = get_config_dir() / "openjarvis.json"
        if cfg_path.exists():
            try:
                import json

                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                return cls.model_validate(data)
            except Exception:
                return cls()
        return cls()

    def save(self) -> None:
        import json

        cfg_path = get_config_dir() / "openjarvis.json"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(self.model_dump(), indent=2), encoding="utf-8")
