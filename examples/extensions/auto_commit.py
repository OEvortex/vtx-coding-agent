"""Auto-commit at the end of every successful agent run.

On ``agent_end`` with ``stop_reason == "stop"``, this extension stages
all changes in the current working directory and creates a single commit
attributed to the extension. If the working tree is clean or there is
no git repo, the extension does nothing.

Skip-turn-ending events (interrupted, error, length) are ignored so we
do not commit partial work the user might want to inspect or revert.
"""

from __future__ import annotations

import asyncio
import shutil

from vtx.extensions import AGENT_END


async def _run_git(*args: str, cwd: str) -> tuple[int, str, str]:
    """Run a git command, return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace").strip(),
        stderr.decode("utf-8", errors="replace").strip(),
    )


def register(api):
    if shutil.which("git") is None:
        api.notify("git not on PATH; auto_commit is a no-op", level="warning")
        return

    @api.on(AGENT_END)
    def _commit(event, payload):
        stop = payload.get("stop_reason", "stop")
        if stop != "stop":
            return None
        # We are in a sync handler so we cannot await directly. We schedule
        # the commit on the running loop (or, if no loop is running, do
        # nothing — there is no way to run async work from a sync event
        # fired at process exit).
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None

        async def _do_commit() -> None:
            cwd = api.cwd
            code, _, _ = await _run_git("rev-parse", "--is-inside-work-tree", cwd=cwd)
            if code != 0:
                return

            code, status, _ = await _run_git("status", "--porcelain", cwd=cwd)
            if code != 0 or not status:
                return

            code, _, err = await _run_git("add", "-A", cwd=cwd)
            if code != 0:
                api.notify(f"git add failed: {err}", level="error")
                return

            summary = "\n".join(status.splitlines()[:8])
            msg = f"vtx auto-commit: {len(status.splitlines())} file(s)\n\n{summary}"
            code, _, err = await _run_git("commit", "-m", msg, "--no-verify", cwd=cwd)
            if code == 0:
                api.notify("auto-committed working-tree changes")
            else:
                # commit can fail benignly (e.g. pre-commit hook rejected).
                api.notify(f"commit skipped: {err[:120]}", level="warning")

        loop.create_task(_do_commit())  # noqa: RUF006
        return None
