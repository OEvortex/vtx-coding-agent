"""``ctx.telemetry`` — the span sink every package reports through."""

from __future__ import annotations

from pydantic import BaseModel

from ctx.plugins.plugin import plugin
from ctx.plugins.services import TelemetryService


class Config(BaseModel):
    """Which exporters are mounted beside the console one."""

    jsonl: bool = False


@plugin(name="telemetry", config=Config)
def apply(ctx, config: Config):
    telemetry = TelemetryService()
    if config.jsonl:
        from ctx.core.paths import get_config_dir
        from ctx.telemetry.exporters.jsonl import JSONLTraceProcessor

        telemetry.add_exporter(JSONLTraceProcessor(get_config_dir() / "telemetry.jsonl"))
    ctx.provide("telemetry", telemetry)


PLUGIN = apply
