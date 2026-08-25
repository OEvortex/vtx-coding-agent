import shlex
from dataclasses import dataclass, field
from enum import Enum

from vtx.core.abc import BaseTool


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
    preview: str = ""


@dataclass(frozen=True)
class AskUserQuestion:
    """One question inside an ``ask_user`` questionnaire."""

    question: str
    header: str = ""
    options: list[AskUserOption] = field(default_factory=list)
    multi_select: bool = False


@dataclass(frozen=True)
class AskUserAnswer:
    """The user's resolved answer to a single question of a questionnaire.

    ``kind`` mirrors the rpiv envelope: ``option`` (picked a listed
    choice, ``answer`` holds its label), ``custom`` (typed free text in
    ``answer``), or ``multi`` (``selected`` holds the checked labels).
    ``notes`` is optional side-band commentary the user attached with
    the ``n`` key; it never marks a question answered on its own.
    """

    question: str
    kind: str
    answer: str | None = None
    selected: tuple[str, ...] = ()
    notes: str | None = None
    preview: str | None = None

    def scalar(self) -> str:
        """The answer as one flat string (for the LLM envelope)."""
        if self.kind == "multi":
            return ", ".join(self.selected) if self.selected else ""
        return self.answer or ""

    def segment(self) -> str:
        """Render this answer as one `"question"="answer"` envelope segment."""
        parts = [f'"{self.question}"="{self.scalar()}"']
        if self.preview:
            parts.append(f"selected preview: {self.preview}")
        if self.notes:
            parts.append(f"user notes: {self.notes}")
        return f"{'. '.join(parts)}."


DECLINE_MESSAGE = "User declined to answer questions"
ENVELOPE_PREFIX = "User has answered your questions:"
ENVELOPE_SUFFIX = "You can now continue with the user's answers in mind."


@dataclass(frozen=True)
class AskUserResponse:
    """The user's answer to an ``ask_user`` tool call.

    Two shapes share this class. The single-question picker fills the
    legacy ``selections``/``custom_text`` fields. The rpiv-style
    questionnaire fills ``answers`` (one :class:`AskUserAnswer` per
    answered question) and optionally ``global_note``. An empty
    response means the user dismissed the prompt (e.g. pressed Escape)
    and the tool call should be treated as cancelled.
    """

    selections: tuple[str, ...] = ()
    custom_text: str | None = None
    answers: tuple[AskUserAnswer, ...] = ()
    global_note: str | None = None

    @property
    def is_empty(self) -> bool:
        return not (
            self.selections
            or (self.custom_text and self.custom_text.strip())
            or self.answers
            or (self.global_note and self.global_note.strip())
        )

    def format_for_llm(self, options: list[AskUserOption] | None = None) -> str:
        """Render the response as plain text for the LLM tool result."""
        del options  # kept for call-compatibility; the envelope is self-contained
        if self.answers or (self.global_note and self.global_note.strip()):
            segments = [answer.segment() for answer in self.answers]
            if self.global_note and self.global_note.strip():
                segments.append(f"global note: {self.global_note.strip()}.")
            if segments:
                return f"{ENVELOPE_PREFIX} {' '.join(segments)} {ENVELOPE_SUFFIX}"
        if self.custom_text and self.custom_text.strip():
            return f"User answered with custom text: {self.custom_text.strip()}"
        if not self.selections:
            return DECLINE_MESSAGE
        return f"User selected: {', '.join(self.selections)}"

    def ui_summary(self) -> str:
        """Short summary for the tool block header (e.g. "(option A, option B)").

        Questionnaire responses compress to ``Header: answer`` pairs so a
        four-question dialog still fits on one header line.
        """
        if self.answers:
            parts: list[str] = []
            for answer in self.answers[:4]:
                text = answer.scalar()
                if len(text) > 30:
                    text = text[:27] + "..."
                parts.append(f"{text}")
            summary = "; ".join(parts)
            if len(summary) > 60:
                summary = summary[:57] + "..."
            return f"[dim]→ {summary}[/dim]"
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


def check_permission(
    tool: BaseTool, arguments: dict, config: object | None = None
) -> PermissionDecision:
    if config is None:
        from vtx.coding_agent.config import config as _config

        config = _config
    permissions = getattr(config, "permissions", None)
    if permissions is not None and permissions.mode == "auto":
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
