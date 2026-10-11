"""The ``.vtx/hooks.yml`` row.

A hook is a declarative event handler: an event name plus a command, a Python
callable, or a prompt. Loading it is a plugin row, so the YAML file is one more
thing a profile can disable or replace, and every handler it registers unwinds
with the row instead of leaking into the next reload.

Ordering matters and is deliberate: project hooks load before user hooks so a
user's own hook sees (and can override) a project's.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from vtx.core.paths import get_config_dir
from vtx.plugins import event_contract as ec
from vtx.plugins.plugin import plugin

log = logging.getLogger("vtx.plugins.hooks")

HOOK_FILENAME = "hooks.yml"


class Config(BaseModel):
    """Whether the row loads hooks at all."""

    enabled: bool = True
    #: Extra ``.vtx``-style directories to read beyond the cwd and the home.
    extra_dirs: list[str] = []


def hook_files(cwd: str, extra_dirs: list[str] | None = None) -> list[Path]:
    """The hook files, project first and user second."""
    roots = [Path(cwd) / ".vtx", get_config_dir()]
    roots.extend(Path(d) for d in (extra_dirs or []))
    return [root / HOOK_FILENAME for root in roots if (root / HOOK_FILENAME).is_file()]


def _run_command(command: str, cwd: str, event: str, payload: dict[str, Any]) -> None:
    tokens = shlex.split(command)
    if not tokens:
        return
    try:
        result = subprocess.run(
            tokens,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "VTX_HOOK_EVENT": event},
        )
        if result.returncode != 0:
            log.warning("hook %s for %s exited %d", command, event, result.returncode)
    except Exception as exc:
        log.warning("hook %s for %s failed: %s", command, event, exc)


def _resolve_callable(spec: str) -> Any:
    module_name, _, attr = spec.partition(":")
    import importlib

    module = importlib.import_module(module_name)
    target = getattr(module, attr or "handle", None)
    if target is None:
        raise AttributeError(f"{spec} exposes no handler")
    return target


@plugin(name="hooks", inject=(), config=Config)
def apply(ctx, config: Config):
    """Load every hook file and register its handlers on the composition's hub."""
    cwd = getattr(config, "cwd", "") or os.getcwd()
    loaded = 0
    for path in hook_files(cwd, config.extra_dirs):
        try:
            import yaml

            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            log.warning("hook file %s is unreadable: %s", path, exc)
            continue
        entries = data.get("hooks") if isinstance(data, dict) else data
        if not isinstance(entries, list):
            log.warning("hook file %s: expected a list of hooks", path)
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            event = entry.get("event")
            if event not in ec.ALL_EVENTS:
                log.warning("hook file %s: unknown event %r", path, event)
                continue
            _register_one(ctx, event, entry, cwd, path)
            loaded += 1
    log.debug("loaded %d hooks from %s", loaded, cwd)


def _register_one(ctx: Any, event: str, entry: dict[str, Any], cwd: str, path: Path) -> None:
    command = entry.get("command")
    if command:

        def handler(_event: str, _payload: Any, *, _command: str = command) -> Any:
            _run_command(_command, cwd, event, {})
            return None

        ctx.on(event, handler)
        return

    callable_spec = entry.get("callable")
    if callable_spec:
        target = _resolve_callable(callable_spec)

        def handler(_event: str, payload: Any, *, _target: Any = target) -> Any:
            result = _target(payload)
            if hasattr(result, "__await__"):
                return result
            return None

        ctx.on(event, handler)
        return

    prompt = entry.get("prompt")
    if prompt:

        def handler(_event: str, payload: Any, *, _prompt: str = prompt) -> Any:
            ui = ctx.get("ui")
            if ui is not None:
                ui.notify(_prompt)
            return None

        ctx.on(event, handler)


PLUGIN = apply
