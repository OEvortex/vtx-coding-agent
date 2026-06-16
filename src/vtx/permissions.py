import shlex
from dataclasses import dataclass
from enum import Enum

from vtx import config

from .tools.base import BaseTool


class PermissionDecision(Enum):
    ALLOW = "allow"
    PROMPT = "prompt"


class ApprovalResponse(Enum):
    APPROVE = "approve"
    DENY = "deny"


@dataclass(frozen=True)
class AskUserOption:
    """A single option the user can pick when asked a question."""

    label: str
    description: str = ""


@dataclass(frozen=True)
class AskUserResponse:
    """The user's answer to an ``ask_user`` tool call.

    ``selections`` holds the labels of the options the user picked (in
    order). When the user types free text instead, ``custom_text`` is
    set and ``selections`` is empty. An empty response means the user
    dismissed the prompt (e.g. pressed Escape) and the tool call should
    be treated as cancelled.
    """

    selections: tuple[str, ...] = ()
    custom_text: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.selections and not (self.custom_text and self.custom_text.strip())

    def format_for_llm(self, options: list[AskUserOption]) -> str:
        """Render the response as plain text for the LLM tool result."""
        if self.custom_text and self.custom_text.strip():
            return f"User answered with custom text: {self.custom_text.strip()}"
        if not self.selections:
            return "User did not provide an answer."
        return f"User selected: {', '.join(self.selections)}"

    def ui_summary(self) -> str:
        """Short summary for the tool block header (e.g. "(option A, option B)")."""
        if self.custom_text and self.custom_text.strip():
            text = self.custom_text.strip()
            if len(text) > 40:
                text = text[:37] + "..."
            return f"[dim]→ {text}[/dim]"
        if not self.selections:
            return "[dim](no answer)[/dim]"
        return f"[dim]→ {', '.join(self.selections)}[/dim]"


SAFE_COMMANDS: frozenset[str] = frozenset(
    {
        "cat",
        "head",
        "tail",
        "ls",
        "pwd",
        "wc",
        "diff",
        "which",
        "file",
        "stat",
        "du",
        "df",
        "whoami",
        "id",
        "uname",
        "date",
        "realpath",
        "dirname",
        "basename",
    }
)

SAFE_GIT_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "status",
        "diff",
        "log",
        "show",
        "rev-parse",
        "describe",
        "ls-files",
        "ls-tree",
        "blame",
        "shortlog",
    }
)

_PUNCTUATION_CHARS = frozenset(";|&()><")


def check_permission(tool: BaseTool, arguments: dict) -> PermissionDecision:
    if config.permissions.mode == "auto":
        return PermissionDecision.ALLOW
    if not tool.mutating:
        return PermissionDecision.ALLOW
    if tool.name == "bash":
        command = arguments.get("command", "")
        if _is_safe_bash_command(command):
            return PermissionDecision.ALLOW
    return PermissionDecision.PROMPT


def _is_safe_bash_command(command: str) -> bool:
    if "\n" in command or "`" in command or "$(" in command or "<(" in command or ">(" in command:
        return False

    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";|&()><")
        tokens = list(lexer)
    except ValueError:
        return False

    if not tokens:
        return False

    for token in tokens:
        if token and all(c in _PUNCTUATION_CHARS for c in token):
            return False

    base = tokens[0]
    if "/" in base:
        base = base.rsplit("/", 1)[-1]

    if base == "git":
        return _is_safe_git_command(tokens)

    return base in SAFE_COMMANDS


def _is_safe_git_command(tokens: list[str]) -> bool:
    i = 1
    while i < len(tokens):
        if tokens[i] in ("-c", "--config-env") or tokens[i].startswith("--config-env="):
            return False
        if not tokens[i].startswith("-"):
            if tokens[i] not in SAFE_GIT_SUBCOMMANDS:
                return False
            # --output writes diff to a file, making it mutating
            return not any(t == "--output" or t.startswith("--output=") for t in tokens[i + 1 :])
        if tokens[i] in ("-C", "--git-dir", "--work-tree", "--namespace") and i + 1 < len(tokens):
            i += 2
            continue
        i += 1
    return False
