"""Config schema — VTX-native, inspired by openjarvis config.schema."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from vtx.openjarvis.agent.config import OpenJarvisConfig


class Base(BaseModel):
    model_config = ConfigDict(extra="allow")


class ProviderConfig(Base):
    """A single provider entry (API credentials and defaults)."""

    name: str = ""
    api_key: str | None = None
    api_base: str | None = None
    model: str | None = None
    extra_headers: dict[str, str] = {}
    extra_body: dict[str, Any] = {}


class ToolsConfig(Base):
    """Tool-level settings (workspace restriction + MCP servers)."""

    restrict_to_workspace: bool = False
    mcp_servers: dict[str, Any] = {}


class Config(Base):
    """VTX-native config — wraps OpenJarvisConfig for channel compatibility."""

    channels: dict[str, Any] = {}
    gateway: dict[str, Any] = {}
    cron: dict[str, Any] = {}
    session: dict[str, Any] = {}
    workspace: str = ""
    tools: ToolsConfig = ToolsConfig()

    @property
    def workspace_path(self) -> str:
        return self.workspace

    @classmethod
    def from_openjarvis(cls, oj: OpenJarvisConfig) -> Config:
        return cls(
            channels={
                k: v.model_dump() if hasattr(v, "model_dump") else v
                for k, v in oj.channels.items()
            },
            gateway=oj.gateway.model_dump(),
            cron=oj.cron.model_dump(),
            session=oj.session.model_dump(),
            workspace=oj.workspace,
        )
