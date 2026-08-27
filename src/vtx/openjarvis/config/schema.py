"""Config schema — VTX-native, inspired by openjarvis config.schema."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from vtx.openjarvis.agent.config import OpenJarvisConfig


class Base(BaseModel):
    model_config = ConfigDict(extra="allow")


class Config(Base):
    """VTX-native config — wraps OpenJarvisConfig for channel compatibility."""

    channels: dict[str, Any] = {}
    gateway: dict[str, Any] = {}
    cron: dict[str, Any] = {}
    session: dict[str, Any] = {}
    workspace: str = ""

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
