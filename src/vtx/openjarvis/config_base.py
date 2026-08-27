"""Config base — VTX-native, inspired by openjarvis config_base."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Base(BaseModel):
    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)
