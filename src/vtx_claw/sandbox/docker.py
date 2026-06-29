from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SandboxResult:
    success: bool
    output: str = ""
    error: str = ""
    exit_code: int = 0


class DockerSandbox:
    def __init__(self, image: str = "python:3.12-slim", timeout_seconds: int = 300) -> None:
        self.image = image
        self.timeout_seconds = timeout_seconds

    async def run(self, command: str, cwd: str = "/workspace") -> SandboxResult:
        logger.info("Sandbox run: %s (image=%s)", command[:80], self.image)
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "run",
            "--rm",
            "--network=none",
            "-v",
            f"{cwd}:/workspace",
            self.image,
            "sh",
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_seconds
            )
        except TimeoutError:
            proc.kill()
            return SandboxResult(success=False, error="Timed out", exit_code=-1)

        return SandboxResult(
            success=proc.returncode == 0,
            output=stdout.decode(errors="replace"),
            error=stderr.decode(errors="replace"),
            exit_code=proc.returncode or 0,
        )

    async def is_available(self) -> bool:
        proc = await asyncio.create_subprocess_exec(
            "docker", "info", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        return proc.returncode == 0
