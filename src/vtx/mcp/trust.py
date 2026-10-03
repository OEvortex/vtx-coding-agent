"""Whether a project-local ``.vtx/mcp.json`` may be read.

A project MCP server is a command vtx would run. Reading one out of a repository
the user merely opened means executing code they did not ask to execute, so the
default is not trusted and there is no way to become trusted implicitly -- not on
first use, not because the file is small, not because the directory looks
familiar. Trust is granted by a person, once, for one path.

This lives under :mod:`vtx.mcp` rather than somewhere more general because a
project ``mcp.json`` is the only thing gated on it. If another feature starts
gating on the same decision, this is the place to lift it from.

The store is keyed by resolved path, so ``~/proj`` and ``~/proj/`` and a symlink
to it are one project, and a decision cannot be smuggled in by spelling a path
differently.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from vtx.core.paths import get_config_dir

log = logging.getLogger("mcp.trust")

TRUST_STORE_FILENAME = "trusted-projects.json"


def project_key(cwd: str | Path) -> str:
    """The canonical identity of a project directory.

    ``resolve()`` is what makes this a security boundary rather than a string
    comparison: without it, ``/repo`` and ``/repo/.`` would be two entries and
    trusting one would not cover the other, which is exactly the confusion a
    trust decision must not have.
    """
    try:
        return str(Path(cwd).expanduser().resolve())
    except OSError:
        # An unresolvable path is still a usable key; it just will not match a
        # differently-spelled one.
        return str(Path(cwd).expanduser())


@dataclass
class TrustRecord:
    path: str
    trusted_at: str


class ProjectTrustStore:
    """The set of projects whose ``.vtx/mcp.json`` vtx will read."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (get_config_dir() / TRUST_STORE_FILENAME)

    @property
    def path(self) -> Path:
        return self._path

    def is_trusted(self, cwd: str | Path) -> bool:
        return project_key(cwd) in self._records()

    def trusted_projects(self) -> list[TrustRecord]:
        return sorted(self._records().values(), key=lambda r: r.path)

    def trust(self, cwd: str | Path) -> TrustRecord:
        """Record trust for a project, replacing any earlier record."""
        record = TrustRecord(
            path=project_key(cwd), trusted_at=datetime.now(UTC).isoformat(timespec="seconds")
        )
        data = self._read()
        data[record.path] = {"trusted_at": record.trusted_at}
        self._write(data)
        return record

    def untrust(self, cwd: str | Path) -> bool:
        """Revoke trust. Returns whether there was anything to revoke."""
        data = self._read()
        if data.pop(project_key(cwd), None) is None:
            return False
        self._write(data)
        return True

    # ---- storage ----------------------------------------------------------

    def _records(self) -> dict[str, TrustRecord]:
        return {
            key: TrustRecord(path=key, trusted_at=str(value.get("trusted_at") or ""))
            for key, value in self._read().items()
            if isinstance(value, dict)
        }

    def _read(self) -> dict[str, dict]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            log.warning("Could not read the project trust store at %s: %s", self._path, exc)
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # A corrupt store must fail closed, not open: treating it as empty
            # makes every project untrusted, which is the safe direction.
            log.warning("Discarding corrupt project trust store at %s", self._path)
            return {}
        projects = parsed.get("projects") if isinstance(parsed, dict) else None
        if not isinstance(projects, dict):
            return {}
        return {k: v for k, v in projects.items() if isinstance(k, str) and isinstance(v, dict)}

    def _write(self, projects: dict[str, dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp_name = tempfile.mkstemp(
            prefix=f"{self._path.name}.", suffix=".tmp", dir=self._path.parent
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as f:
                json.dump({"projects": projects}, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise


__all__ = ["TRUST_STORE_FILENAME", "ProjectTrustStore", "TrustRecord", "project_key"]
