"""What a long-running command has to say to the model before it is asked.

A background job that finishes, stalls on a password prompt, or simply outlives
the turn is information the model needs *during* the run, not as a paragraph it
reads on the next tool call. agy solves this with four server-side notifications
(``Background command is still running after %ds: %s``, output-stabilised,
input-required, task-finished). This module is the same four, as one queue.

Each notice is delivered once: the queue deduplicates on ``(job_id, kind)``, so a
watcher that fires every poll cannot turn into a wall of duplicates. Delivery is
the caller's business — :mod:`vx.app` drains into the live agent, the bash tool
drains at the head of its own result.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FINISHED = "finished"
INPUT_REQUIRED = "input_required"
OUTPUT_SETTLED = "output_settled"

TAG = "job-notification"

#: What a shell prints when it is waiting for a human. Narrow on purpose: a
#: false positive interrupts the model to say "it is asking you something" about
#: a log line that merely contains the word "password".
INPUT_HINTS = (
    "password:",
    "passphrase",
    "(y/n)",
    "[y/n]",
    "(yes/no)",
    "[yes/no]",
    "are you sure",
    "press enter",
    "press any key",
    "overwrite?",
    "continue?",
    "proceed?",
    "enter a value",
    "please enter",
)


def looks_like_a_prompt(text: str) -> str | None:
    """The hint line if this output is a question waiting for an answer."""
    tail = text.strip().splitlines()[-3:]
    for line in reversed(tail):
        low = line.strip().lower()
        if not low or len(low) > 400:
            continue
        for hint in INPUT_HINTS:
            if hint in low:
                return line.strip()[:200]
    return None


@dataclass(frozen=True)
class JobNotice:
    """One thing a job wants the model to know, exactly once."""

    kind: str
    job_id: str
    text: str


def format_notice(notice: JobNotice) -> str:
    """The message body delivered to the model, agy's tag style."""
    return f'<{TAG} job="{notice.job_id}" kind="{notice.kind}">\n{notice.text}\n</{TAG}>'


@dataclass
class NoticeQueue:
    """Every notice this process posted and has not delivered yet."""

    _pending: list[JobNotice] = field(default_factory=list)
    _seen: set[tuple[str, str]] = field(default_factory=set)

    def post(self, notice: JobNotice) -> bool:
        """Queue ``notice`` unless this job already said this. True if queued."""
        key = (notice.job_id, notice.kind)
        if key in self._seen:
            return False
        self._seen.add(key)
        self._pending.append(notice)
        return True

    def drain(self) -> list[JobNotice]:
        """Take everything pending, oldest first."""
        drained = self._pending
        self._pending = []
        return drained

    def peek(self) -> list[JobNotice]:
        return list(self._pending)

    def forget(self, job_id: str) -> None:
        """Drop a job's dedup memory (and pending notices) once it is gone."""
        self._pending = [n for n in self._pending if n.job_id != job_id]
        self._seen = {key for key in self._seen if key[0] != job_id}

    def discard(self, job_id: str, kind: str) -> None:
        """Drop one notice: the caller just said the same thing itself.

        A sync call that waited for the command and reported its exit code does
        not also need a ``finished`` notice about that same command.
        """
        self._pending = [n for n in self._pending if (n.job_id, n.kind) != (job_id, kind)]
        self._seen.discard((job_id, kind))

    def clear(self) -> None:
        self._pending = []
        self._seen = set()


#: One queue per process, like the job registry.
NOTICES = NoticeQueue()


def drain_text() -> str:
    """Everything pending, formatted for a model, or an empty string."""
    drained = NOTICES.drain()
    if not drained:
        return ""
    return "\n\n".join(format_notice(notice) for notice in drained)
