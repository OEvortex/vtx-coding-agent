"""The search tool: find text or files in one call, fast, on any machine.

The reason this exists as a tool rather than a bash command is the ladder:

    ripgrep  ->  git grep -E  ->  git ls-files | xargs grep  ->  Python

Each rung is a fallback for the rung above, and the last rung is Python itself,
which means the tool can never be *unavailable*: on a machine with nothing
installed it still answers, just slower. That is the whole point -- the model
should not have to know what is installed before it can look for something.

Two rules keep it honest:

* the pattern is a regex, so ``a|b|c`` works on every rung. ``git grep`` speaks
  BRE by default where ``|`` is a literal, so the rung must pass ``-E`` or it
  answers "no matches" for a pattern that matches. That flag is not cosmetic.
* an empty result says what it searched, not just that it found nothing.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import re
import shutil
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ctx.agent.tools.base import BaseTool
from ctx.protocol.types import ToolResult
from pydantic import BaseModel, Field, field_validator, model_validator

#: What the model asks for, and the most it may ask for.
DEFAULT_RESULTS = 50
MAX_RESULTS = 200

#: A search that has not finished in this long is a search with the wrong scope.
TIMEOUT_SECONDS = 20.0
_EXIT_POLL_SECONDS = 0.02

#: Enough for any real answer; a cap so a runaway pattern cannot eat memory.
MAX_OUTPUT_BYTES = 8 * 1024 * 1024

#: Directories no one means when they say "search this repo".
IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        ".env",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".tox",
        ".nox",
        ".cache",
        "dist",
        "build",
        "target",
        ".next",
        ".nuxt",
        ".turbo",
        "coverage",
        "htmlcov",
        ".idea",
        ".vscode",
    }
)

#: Roots where "search everything" is not a search, it is a hang.
WIDE_ROOTS = frozenset(
    {
        "/",
        "/home",
        "/Users",
        "/usr",
        "/etc",
        "/var",
        "/opt",
        "/root",
        "/mnt",
        "/media",
        "/srv",
        "/Volumes",
        "/System",
        "/Library",
    }
)

#: What models call the pattern, and the path.
_PATTERN_ALIASES = ("pattern", "query", "regex", "search", "text", "word", "expression")
_PATH_ALIASES = ("path", "dir", "directory", "root", "root_dir", "cwd", "in", "folder")
_GLOB_ALIASES = ("glob", "include", "file_pattern", "file_glob", "files", "extension", "ext")

_BINARY = re.compile(rb"\x00")


def _first_str(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _which(name: str) -> str | None:
    """Separate so a test can take a tool away without touching PATH."""
    return shutil.which(name)


_GIT_ROOTS: dict[str, Path | None] = {}


def _git_root(path: Path) -> Path | None:
    """The work tree containing `path`, if any. Cached: one fork per directory."""
    key = str(path)
    if key in _GIT_ROOTS:
        return _GIT_ROOTS[key]
    root: Path | None = None
    git = shutil.which("git")
    if git is not None:
        try:
            done = subprocess.run(
                [git, "-C", str(path), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if done.returncode == 0 and done.stdout.strip():
                root = Path(done.stdout.strip())
        except Exception:
            root = None
    _GIT_ROOTS[key] = root
    return root


class SearchParams(BaseModel):
    """One pattern, one scope. Two modes: find text, or find files."""

    pattern: str | list[str] = Field(
        default="",
        description=(
            "Regex to find (content mode) or filename glob (files mode). "
            "Pass a list to search several at once. `a|b|c` also works."
        ),
    )

    mode: Literal["content", "files"] = Field(
        default="content",
        description="`content` finds matching lines, `files` finds matching paths.",
    )
    path: str = Field(default="", description="Where to search. Defaults to the cwd.")
    glob: str = Field(
        default="", description="Only look at files matching this glob, e.g. `*.py`."
    )
    case_sensitive: bool = Field(default=False, description="Match case exactly.")
    literal: bool = Field(
        default=False, description="Treat the pattern as plain text, not a regex."
    )
    context_lines: int = Field(default=0, description="Lines of context per match (0-5).")
    max_results: int = Field(
        default=DEFAULT_RESULTS, description=f"Stop after this many hits (max {MAX_RESULTS})."
    )
    no_ignore: bool = Field(
        default=False, description="Also search ignored files (node_modules, .venv, .gitignore'd)."
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_aliases(cls, value: Any) -> Any:
        """Models name `pattern`/`path`/`glob` differently; take them all."""
        if not isinstance(value, dict):
            return value
        out = dict(value)
        if not out.get("pattern") and isinstance(out.get("patterns"), list):
            # The field used to be called `patterns`; a model that learnt that
            # name should not get "give a pattern" back.
            out["pattern"] = [
                part.strip() for part in out["patterns"] if isinstance(part, str) and part.strip()
            ]
        if not out.get("pattern"):
            found = _first_str(out, _PATTERN_ALIASES)
            if found:
                out["pattern"] = found
        if not out.get("path"):
            found = _first_str(out, _PATH_ALIASES)
            if found:
                out["path"] = found
        if not out.get("glob"):
            found = _first_str(out, _GLOB_ALIASES)
            if found:
                out["glob"] = found
        if out.get("mode") in (None, ""):
            out["mode"] = "content"
        return out

    @field_validator("pattern", mode="before")
    @classmethod
    def _plain_pattern(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [v.strip() if isinstance(v, str) else v for v in value]
        return value.strip() if isinstance(value, str) else value

    @field_validator("path", "glob", mode="before")
    @classmethod
    def _plain(cls, value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    @field_validator("mode", mode="before")
    @classmethod
    def _mode(cls, value: Any) -> str:
        flat = str(value or "content").strip().lower()
        if flat in ("file", "files", "paths", "path", "list", "name"):
            return "files"
        return "content"

    def pattern_list(self) -> list[str]:
        raw = self.pattern
        items = raw if isinstance(raw, list) else ([raw] if raw else [])
        # Only strings: a list with a null in it is not a pattern called "None".
        return [item.strip() for item in items if isinstance(item, str) and item.strip()]

    def literal_single(self) -> bool:
        """`-F` takes one string. An escaped union is already exact as a regex,
        so passing `--fixed-strings` for several items searches for the literal
        text `a|b` and answers "no matches" -- wrong, and instantly."""
        return self.literal and len(self.pattern_list()) < 2

    def query(self) -> str:
        """The pattern actually searched for: literals escaped and joined."""
        items = self.pattern_list()
        if not items:
            return ""
        source = re.escape if self.literal else (lambda item: item)
        return "|".join(source(item) for item in items)

    def scope(self) -> Path:
        return Path(self.path).expanduser() if self.path else Path.cwd()

    def limit(self) -> int:
        return max(1, min(MAX_RESULTS, int(self.max_results or DEFAULT_RESULTS)))

    def context(self) -> int:
        return max(0, min(5, int(self.context_lines or 0)))

    def alternatives(self) -> list[str]:
        """The top-level alternatives of the pattern, or [] if it is one thing.

        Only a clean ``a|b`` splits: a ``|`` inside ``(…)`` or ``[…]`` is not
        something the user is asking to be counted separately.
        """
        if isinstance(self.pattern, list):
            items = self.pattern_list()
            return items if len(items) > 1 else []
        pattern = self.query()
        if self.literal or not pattern:
            if "|" in pattern and self.literal:
                return [part for part in pattern.split("|") if part]
            return []
        parts: list[str] = []
        depth = 0
        current: list[str] = []
        escaped = False
        for char in pattern:
            if escaped:
                current.append(char)
                escaped = False
                continue
            if char == "\\":
                current.append(char)
                escaped = True
                continue
            if char in "([":
                depth += 1
            elif char in ")]":
                depth = max(0, depth - 1)
            elif char == "|" and depth == 0:
                parts.append("".join(current))
                current = []
                continue
            current.append(char)
        parts.append("".join(current))
        if len(parts) < 2 or any(not part for part in parts):
            return []
        for part in parts:
            try:
                re.compile(part)
            except re.error:
                return []
        return parts


@dataclass
class Hit:
    path: str
    line: int = 0
    text: str = ""


@dataclass
class Outcome:
    """What one rung of the ladder managed to find."""

    hits: list[Hit] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    backend: str = ""
    #: Hits the rung produced, before the cap: `+N more` needs the real number.
    total: int = 0
    scanned: int = 0
    truncated: bool = False
    counts: dict[str, int] = field(default_factory=dict)
    error: str = ""
    timed_out: bool = False

    def empty(self) -> bool:
        return not self.hits and not self.files


@dataclass
class Run:
    """One child process, and why it stopped."""

    out: bytes = b""
    err: str = ""
    code: int = 0
    timed_out: bool = False

    @property
    def failed(self) -> bool:
        return self.timed_out or self.code not in (0, 1)


async def _run(argv: list[str], cwd: Path, timeout: float = TIMEOUT_SECONDS) -> Run:
    """Run one backend, bounded in time and bytes.

    The exit code is polled rather than awaited with `communicate()`: a backend
    that spawns a child which inherits the pipe would otherwise hold the call
    open for the whole timeout after the answer was already complete.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return Run(err=str(exc), code=127)

    chunks: list[bytes] = []
    size = 0

    async def drain() -> None:
        nonlocal size
        assert proc.stdout is not None
        while True:
            try:
                chunk = await proc.stdout.read(1 << 16)
            except (asyncio.LimitOverrunError, ValueError):
                continue
            if not chunk:
                return
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_OUTPUT_BYTES:
                return

    async def drain_err() -> str:
        assert proc.stderr is not None
        try:
            data = await asyncio.wait_for(proc.stderr.read(8192), timeout)
        except (TimeoutError, asyncio.LimitOverrunError, ValueError):
            return ""
        return data.decode("utf-8", "replace")

    reader = asyncio.create_task(drain())
    err_reader = asyncio.create_task(drain_err())

    timed_out = False
    deadline = time.monotonic() + timeout
    while proc.returncode is None:
        if time.monotonic() >= deadline:
            timed_out = True
            _kill(proc)
            break
        await asyncio.sleep(_EXIT_POLL_SECONDS)

    with_grace = 0.3
    for task in (reader, err_reader):
        try:
            await asyncio.wait_for(asyncio.shield(task), with_grace)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()

    err_text = ""
    if err_reader.done():
        with suppress(Exception):
            err_text = err_reader.result()
    return Run(
        out=b"".join(chunks),
        err=err_text,
        code=proc.returncode if proc.returncode is not None else -1,
        timed_out=timed_out,
    )


def _kill(proc: asyncio.subprocess.Process) -> None:
    """Kill the whole group: a backend may have children of its own."""
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, 9)
    except (ProcessLookupError, PermissionError, OSError):
        with suppress(ProcessLookupError):
            proc.kill()


def _text(data: bytes) -> str:
    return data.decode("utf-8", "replace")


# =================================================================================================
# Scope
# =================================================================================================


def _ignore_glob(params: SearchParams) -> str:
    """ripgrep and fd speak gitignore, not "build dirs": say it out loud.

    Outside a repo -- or for an untracked `node_modules` -- the ignore rules
    have nothing to say, so a search would descend into 40k vendored files.
    """
    return "!{" + ",".join(sorted(IGNORED_DIRS)) + "}"


def _is_ignored(path: Path, scope: Path) -> bool:
    try:
        parts = path.resolve().relative_to(scope.resolve()).parts[:-1]
    except ValueError:
        parts = path.parts[:-1]
    return any(part in IGNORED_DIRS for part in parts)


def _rel(path: Path, cwd: Path) -> str:
    try:
        return str(path.resolve().relative_to(cwd.resolve()))
    except ValueError:
        return str(path)


def _refusal(scope: Path, params: SearchParams) -> str:
    """Refuse the searches that cannot finish, before spending 20s proving it."""
    try:
        resolved = str(scope.resolve())
    except OSError:
        return ""
    wide = resolved in WIDE_ROOTS or resolved == str(Path.home())
    named = bool(params.glob) or (params.mode == "files" and bool(params.pattern_list()))
    if wide and not named:
        return f"refusing to search {resolved} · give a path or a glob"
    if not str(scope).strip():
        return "no path"
    return ""


# =================================================================================================
# File lists
# =================================================================================================


def _git_files(scope: Path) -> list[Path] | None:
    """Every tracked or untracked file under `scope`, per git's own ignore rules."""
    root = _git_root(scope)
    git = shutil.which("git")
    if root is None or git is None:
        return None
    done = subprocess.run(
        [git, "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        timeout=15,
    )
    if done.returncode != 0:
        return None
    try:
        prefix = scope.resolve().relative_to(root.resolve())
    except ValueError:
        prefix = Path()
    out: list[Path] = []
    for raw in done.stdout.split(b"\0"):
        if not raw:
            continue
        rel = Path(raw.decode("utf-8", "replace"))
        if prefix.parts and rel.parts[: len(prefix.parts)] != prefix.parts:
            continue
        out.append(root / rel)
    return out


def _name_globs(params: SearchParams) -> list[str]:
    """Files mode name filter(s): `footer` should find footer.py.

    Content mode is different: there `glob` is ripgrep's `--glob`, so it is
    passed through untouched -- padding `tests` into `*tests*` here would make
    the Python rung disagree with the rg rung about the same call.
    """
    if params.mode != "files":
        return [params.glob] if params.glob else []
    wants = params.pattern_list() or ([params.glob] if params.glob else [])
    return [want if any(char in want for char in "*?[") else f"*{want}*" for want in wants]


def _wanted(name: str, params: SearchParams) -> bool:
    """The name filter, in one place so every rung agrees on it."""
    globs = _name_globs(params)
    if not globs:
        return True
    return any(fnmatch.fnmatch(name, glob) for glob in globs)


def _walk(scope: Path, params: SearchParams) -> list[Path]:
    """The floor: every rung above may be missing, `os.scandir` is always there."""
    files: list[Path] = []
    skip = frozenset() if params.no_ignore else IGNORED_DIRS
    stack = [scope]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in skip:
                        stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False) and _wanted(entry.name, params):
                    files.append(Path(entry.path))
            except OSError:
                continue
    return files


def _candidates(scope: Path, params: SearchParams) -> list[Path]:
    files = _git_files(scope) if not params.no_ignore else None
    if files is None:
        return _walk(scope, params)
    return [
        path
        for path in files
        if _wanted(path.name, params) and (params.no_ignore or not _is_ignored(path, scope))
    ]


# =================================================================================================
# Rungs: content
# =================================================================================================


def _rg_args(params: SearchParams) -> list[str]:
    rg = _which("rg")
    if rg is None:
        return []
    args = [rg, "--line-number", "--no-heading", "--color=never", "--no-messages"]
    if params.literal_single():
        args.append("--fixed-strings")
    if not params.case_sensitive:
        args.append("--ignore-case")
    if params.no_ignore:
        args.append("--no-ignore")
    if params.glob:
        args += ["--glob", params.glob]
    if not params.no_ignore:
        args += ["--glob", _ignore_glob(params)]
    if params.context():
        args += ["--context", str(params.context())]
    return args


def _parse_lines(text: str, scope: Path, cwd: Path) -> list[Hit]:
    hits: list[Hit] = []
    for row in text.splitlines():
        if not row or row == "--":
            continue
        parts = row.split(":", 2)
        if len(parts) != 3 or not parts[1].isdigit():
            continue
        hits.append(
            Hit(
                path=_rel(scope / parts[0].removeprefix("./"), cwd),
                line=int(parts[1]),
                text=parts[2].rstrip()[:400],
            )
        )
    return hits


async def _rg_search(params: SearchParams, scope: Path, cwd: Path) -> Outcome | None:
    args = _rg_args(params)
    if not args:
        return None
    alternatives = params.alternatives()
    wants_counts = len(alternatives) > 1
    if wants_counts:
        args.append("--json")
    run = await _run([*args, "--", params.query(), "."], scope)
    if run.timed_out:
        return Outcome(backend="rg", timed_out=True)
    if run.failed:
        return None
    text = _text(run.out)
    if wants_counts:
        hits, counts = _parse_json(text, scope, cwd)
    else:
        hits, counts = _parse_lines(text, scope, cwd), {}
    return _trim(params, Outcome(hits=hits, backend="rg", counts=counts))


def _parse_json(text: str, scope: Path, cwd: Path) -> tuple[list[Hit], dict[str, int]]:
    hits: list[Hit] = []
    counts: dict[str, int] = {}
    for row in text.splitlines():
        try:
            event = json.loads(row)
        except ValueError:
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data", {})
        path = (data.get("path") or {}).get("text") or ""
        line = data.get("line_number") or 0
        body = ((data.get("lines") or {}).get("text") or "").rstrip("\n")
        hits.append(
            Hit(
                path=_rel(scope / path.removeprefix("./"), cwd),
                line=line,
                text=body.rstrip()[:400],
            )
        )
        # One per line, like the row's own "3 matches": a line that says
        # `needle = needle_fn(1)` is one match, not two, and the counts have to
        # add up to the number next to them.
        seen: set[str] = set()
        for submatch in data.get("submatches") or []:
            found = ((submatch.get("match") or {}).get("text") or "").strip()
            if found and found not in seen:
                seen.add(found)
                counts[found] = counts.get(found, 0) + 1
    return hits, counts


async def _git_grep(params: SearchParams, scope: Path, cwd: Path) -> Outcome | None:
    """`git grep`, for machines without ripgrep.

    `-E` is the flag this rung lives or dies by: BRE treats `|` as a literal
    character, so `a|b` would answer "no matches" -- a wrong answer, not a slow
    one. `--untracked` is the other one: without it a file written a minute ago
    is invisible and the answer is again silently wrong.
    """
    git = _which("git")
    root = _git_root(scope)
    if git is None or root is None:
        return None
    try:
        prefix = scope.resolve().relative_to(root.resolve())
    except ValueError:
        prefix = Path()
    args = [git, "-C", str(root), "grep", "--line-number", "--color=never", "--untracked"]
    args.append("-F" if params.literal_single() else "-E")
    args.append("-I")
    if not params.case_sensitive:
        args.append("--ignore-case")
    excludes = [] if params.no_ignore else [f":(exclude){name}" for name in sorted(IGNORED_DIRS)]
    pathspec = [str(prefix) if prefix.parts else ".", *excludes]
    args += ["-e", params.query(), "--", *pathspec]
    run = await _run(args, scope)
    if run.timed_out:
        return Outcome(backend="git grep", timed_out=True)
    if run.failed:
        return None
    hits = [hit for hit in _parse_lines(_text(run.out), root, cwd)]
    if params.glob:
        hits = [hit for hit in hits if fnmatch.fnmatch(Path(hit.path).name, params.glob)]
    return _trim(params, Outcome(hits=hits, backend="git grep"))


def _scan(params: SearchParams, files: list[Path], cwd: Path, backend: str) -> Outcome:
    """The Python rung: also the third rung, given git's file list."""
    flags = 0 if params.case_sensitive else re.IGNORECASE
    if params.literal:
        items = params.pattern_list()
        source = (
            "|".join(re.escape(item) for item in items) if items else re.escape(params.query())
        )
    else:
        source = params.query()
    try:
        regex = re.compile(source, flags)
    except re.error as exc:
        return Outcome(backend=backend, error=f"bad pattern: {exc}")
    hits: list[Hit] = []
    limit = params.limit()
    for path in files:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if _BINARY.search(data[:2048]):
            continue
        text = data.decode("utf-8", "replace")
        for number, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                hits.append(Hit(path=_rel(path, cwd), line=number, text=line.rstrip()[:400]))
                if len(hits) > limit:
                    return Outcome(
                        hits=hits[:limit], backend=backend, truncated=True, total=len(hits)
                    )
    return Outcome(hits=hits, backend=backend)


def _trim(params: SearchParams, outcome: Outcome) -> Outcome:
    limit = params.limit()
    outcome.total = len(outcome.hits) or len(outcome.files)
    if len(outcome.hits) > limit:
        outcome.hits = outcome.hits[:limit]
        outcome.truncated = True
    if len(outcome.files) > limit:
        outcome.files = outcome.files[:limit]
        outcome.truncated = True
    # A stable order: two searches for the same thing read the same way, and the
    # panel groups by path instead of by whichever file the walker met first.
    outcome.hits.sort(key=lambda hit: (hit.path, hit.line))
    return outcome


def _count_alternatives(params: SearchParams, outcome: Outcome) -> None:
    """Count hits per alternative when the backend could not do it for us."""
    alternatives = params.alternatives()
    if len(alternatives) < 2:
        outcome.counts = {}
        return
    if outcome.counts and set(outcome.counts) <= set(alternatives):
        outcome.counts = {
            alternative: outcome.counts.get(alternative, 0) for alternative in alternatives
        }
        return
    flags = 0 if params.case_sensitive else re.IGNORECASE
    outcome.counts = {
        alternative: sum(1 for hit in outcome.hits if re.search(alternative, hit.text, flags))
        for alternative in alternatives
    }


# =================================================================================================
# Rungs: files
# =================================================================================================


async def _fd_files(params: SearchParams, scope: Path, cwd: Path) -> Outcome | None:
    fd = _which("fd")
    if fd is None:
        return None
    # `--glob` is a *flag* in fd, and the pattern is one positional argument:
    # a second one is read as another search path. So the globs are unioned
    # here, one run each, instead of being handed over as a list fd cannot take.
    args = [fd, "--type", "f", "--color", "never", "--glob"]
    if params.no_ignore:
        args.append("--unrestricted")
    else:
        args += ["--exclude", _ignore_glob(params).removeprefix("!")]
    if params.case_sensitive:
        args.append("--case-sensitive")
    globs = _name_globs(params) or [""]
    found: set[str] = set()
    for want in globs:
        run = await _run([*args, "--", *([want] if want else []), "."], scope)
        if run.timed_out:
            return Outcome(backend="fd", timed_out=True)
        if run.failed:
            return None
        for row in _text(run.out).splitlines():
            if row.strip():
                found.add(_rel(scope / row.removeprefix("./"), cwd))
    return _trim(params, Outcome(files=sorted(found), backend="fd"))


async def _rg_files(params: SearchParams, scope: Path, cwd: Path) -> Outcome | None:
    rg = _which("rg")
    if rg is None:
        return None
    args = [rg, "--files", "--color", "never"]
    for want in _name_globs(params):
        args += ["--glob", want]
    if params.no_ignore:
        args.append("--no-ignore")
    else:
        args += ["--glob", _ignore_glob(params)]
    run = await _run([*args, "."], scope)
    if run.timed_out:
        return Outcome(backend="rg --files", timed_out=True)
    if run.failed:
        return None
    files = [
        _rel(scope / row.removeprefix("./"), cwd)
        for row in _text(run.out).splitlines()
        if row.strip()
    ]
    return _trim(params, Outcome(files=sorted(files), backend="rg --files"))


async def _files_ladder(params: SearchParams, scope: Path, cwd: Path) -> Outcome:
    for rung in (_fd_files, _rg_files):
        outcome = await rung(params, scope, cwd)
        if outcome is not None:
            return outcome
    backend = "git files" if _git_root(scope) is not None else "python"
    files = [_rel(path, cwd) for path in _candidates(scope, params)]
    return _trim(params, Outcome(files=sorted(files), backend=backend))


# =================================================================================================
# The ladder
# =================================================================================================


async def _content_ladder(params: SearchParams, scope: Path, cwd: Path) -> Outcome:
    for rung in (_rg_search, _git_grep):
        outcome = await rung(params, scope, cwd)
        if outcome is not None:
            break
    else:
        backend = "git files" if _git_root(scope) is not None else "python"
        outcome = _scan(params, _candidates(scope, params), cwd, backend)
    _count_alternatives(params, outcome)
    if outcome.empty():
        # The count is the answer to "did you even look where I think you did".
        outcome.scanned = len(_candidates(scope, params))
    return outcome


# =================================================================================================
# Rendering
# =================================================================================================


def _ms(elapsed: float) -> str:
    return f"{round(elapsed * 1000)}ms" if elapsed < 1 else f"{elapsed:.1f}s"


def _files_word(count: int) -> str:
    return f"{count} file" if count == 1 else f"{count} files"


def _pattern_label(params: SearchParams) -> str:
    # `a|b` is the union in content mode; in files mode these are names, so a
    # comma-word list reads as one.
    label = (", " if params.mode == "files" else "|").join(params.pattern_list())
    return label if len(label) <= 80 else label[:77] + "..."


def _result(params: SearchParams, outcome: Outcome, elapsed: float) -> ToolResult:
    pattern = _pattern_label(params)
    scope = _rel(params.scope(), Path.cwd()) or "."
    base: dict[str, Any] = {
        "mode": params.mode,
        "pattern": pattern,
        "glob": params.glob,
        "scope": scope,
        "backend": outcome.backend,
        "scanned": outcome.scanned,
        "truncated": outcome.truncated,
        "counts": [[key, value] for key, value in outcome.counts.items()],
        "elapsed": round(elapsed, 3),
    }
    rows = [{"path": hit.path, "line": hit.line, "text": hit.text} for hit in outcome.hits[:40]]
    total = outcome.total or (len(outcome.files) if params.mode == "files" else len(outcome.hits))
    shown = len(rows) if params.mode != "files" else min(len(outcome.files), 40)
    ui_data = {**base, "rows": rows, "files": outcome.files[:40], "total": total, "shown": shown}

    if outcome.error:
        return ToolResult(
            success=False,
            result=f"Search failed: {outcome.error}",
            ui_summary=f"Couldn't search {pattern}",
            ui_data={**ui_data, "error": outcome.error},
        )
    if outcome.timed_out:
        partial = f", {len(outcome.hits)} found first" if outcome.hits else ""
        return ToolResult(
            success=False,
            result=(
                f"Search timed out after {TIMEOUT_SECONDS:.0f}s{partial} · "
                f"narrow the path or add a glob"
            ),
            ui_summary=f"Timed out after {TIMEOUT_SECONDS:.0f}s",
            ui_data={**ui_data, "error": "timed out"},
        )

    if params.mode == "files":
        shown = len(outcome.files)
        total = outcome.total or shown
        summary = (
            f"{shown} of {_files_word(total)} · {_ms(elapsed)}"
            if total > shown
            else f"{_files_word(total)} · {_ms(elapsed)}"
        )
        if not total:
            hint = " Give a path, a glob, or a wider pattern."
            ui_summary = f"No files in {outcome.scanned} scanned"
            return ToolResult(
                success=True,
                result=(
                    f"No files matching {pattern!r} in {_files_word(outcome.scanned)} · "
                    f"{outcome.backend}.{hint}"
                ),
                ui_summary=ui_summary,
                ui_data=ui_data,
            )
        body = "\n".join(outcome.files)
        if outcome.truncated:
            body += f"\n… showing {shown} of {total}; raise max_results for more."
        return ToolResult(
            success=True,
            result=(f"{_files_word(shown)} matching {pattern!r} · {outcome.backend}\n{body}"),
            ui_summary=summary,
            ui_data=ui_data,
        )

    total = len(outcome.hits)
    found = outcome.total or total
    file_count = len(dict.fromkeys(hit.path for hit in outcome.hits))
    if not total:
        # A file scope scans no files, so it is named instead of counted, and
        # ignore rules have nothing to do with it.
        single = params.scope().is_file()
        where = params.scope().name if single else _files_word(outcome.scanned)
        hint = ""
        if not params.no_ignore and not single:
            hint = " Ignored files were skipped; pass no_ignore=true to include them."
        return ToolResult(
            success=True,
            result=f"No matches for {pattern!r} in {where} · {outcome.backend}.{hint}",
            ui_summary=f"No matches in {where}",
            ui_data=ui_data,
        )
    lines = [f"{hit.path}:{hit.line}:{hit.text}" for hit in outcome.hits]
    if outcome.counts:
        counts = " · ".join(f"{key} {value}" for key, value in outcome.counts.items())
        lines.insert(0, counts)
    if outcome.truncated:
        lines.append(
            f"… showing {total} of {found} matches; narrow the pattern or raise max_results."
        )
    lines.insert(
        0,
        (
            f"{total} match{'es' if total != 1 else ''} in "
            f"{_files_word(file_count)} · {outcome.backend}"
        ),
    )
    plural = "es" if total != 1 else ""
    summary = f"{total} match{plural} in {_files_word(file_count)} · {_ms(elapsed)}"
    return ToolResult(success=True, result="\n".join(lines), ui_summary=summary, ui_data=ui_data)


def _failed(params: SearchParams, message: str) -> ToolResult:
    return ToolResult(
        success=False,
        result=f"Search failed: {message}",
        ui_summary=message,
        ui_data={"mode": params.mode, "pattern": _pattern_label(params), "error": message},
    )


class SearchTool(BaseTool[SearchParams]):
    name = "search"
    params = SearchParams
    description = (
        "Search file contents (mode='content') or file paths (mode='files'). "
        "Respects ignore files; `a|b|c` searches several patterns in one call."
    )
    mutating = False
    parallel_safe = True

    def format_call(self, params: SearchParams) -> str:
        pattern = _pattern_label(params)
        if params.mode == "files":
            return f"files {pattern}"
        return pattern

    async def execute(
        self, params: SearchParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        started = time.monotonic()
        if cancel_event is not None and cancel_event.is_set():
            return ToolResult(success=False, result="Search cancelled.", ui_summary="Cancelled")
        if not params.query().strip():
            return _failed(params, "give a pattern")
        scope = params.scope()
        refusal = _refusal(scope, params)
        if refusal:
            return _failed(params, refusal)
        if not scope.exists():
            return _failed(params, f"no such path: {params.path or scope}")
        cwd = Path.cwd()

        if scope.is_file():
            outcome = (
                _scan(params, [scope], cwd, "python")
                if params.mode == "content"
                else Outcome(files=[_rel(scope, cwd)], backend="path")
            )
        elif params.mode == "files":
            outcome = await _files_ladder(params, scope, cwd)
        else:
            outcome = await _content_ladder(params, scope, cwd)
        return _result(params, outcome, time.monotonic() - started)
