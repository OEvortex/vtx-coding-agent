"""The skills catalog as refreshable session state.

The catalog in the system prompt is a snapshot. A skill installed, edited, or
deleted mid-session would otherwise stay advertised in the cached system prompt
for the rest of the run: the model keeps calling a skill that no longer exists,
and never learns about one that does. Rebuilding the system prompt to fix that
is worse — it invalidates the cached prefix behind the whole prompt on every
boundary.

So the catalog follows the same shape the harness digest already uses: hold one
context slot, fingerprint the *content* rather than the rendered text, and swap
the message in place when the set actually changes. The wording of an update is
deliberately superseding, because the model is looking at an older copy higher
up in the same conversation.

Modelled as a typed observable with baseline/update/removed renderers: the
message is replaced in place rather than appended, so a refresh costs one slot
however many times it happens.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

from vtx.protocol.types import UserMessage

log = logging.getLogger("ai.agent.skills_refresh")

SKILLS_REFRESH_TAG = "vtx:skills-refresh"


@dataclass(frozen=True)
class SkillSummary:
    """The catalog state that matters to the model: what exists, and how to say so."""

    name: str
    description: str

    def as_json(self) -> dict[str, str]:
        return {"name": self.name, "description": self.description}


@dataclass
class SkillsCatalogState:
    """The catalog as last announced to the model."""

    summaries: tuple[SkillSummary, ...] = ()
    fingerprint: str = ""


def summarize_skills(skills: list) -> tuple[SkillSummary, ...]:
    """Reduce loaded skills to the catalog the prompt actually renders.

    Only fields the model sees take part, so a change to a skill's on-disk path
    or its `category` does not read as a catalog change and re-announce an
    identical list.
    """
    return tuple(
        SkillSummary(name=s.name, description=s.description)
        for s in sorted(
            (s for s in skills if getattr(s, "include_in_prompt", True)), key=lambda s: s.name
        )
    )


def fingerprint_summaries(summaries: tuple[SkillSummary, ...]) -> str:
    payload = json.dumps([s.as_json() for s in summaries], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def render_update(previous: tuple[SkillSummary, ...], current: tuple[SkillSummary, ...]) -> str:
    """Announce a changed catalog.

    Says which skills arrived and which are gone by name. A bare "the list
    changed" makes the model re-derive the diff by diffing two long lists, and
    a removal announced only by absence leaves it calling a skill that no longer
    exists.
    """
    before = {s.name: s for s in previous}
    after = {s.name: s for s in current}
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(n for n in set(before) & set(after) if before[n] != after[n])

    lines = [
        "The available skills have changed. This list supersedes the previous "
        "available skills list."
    ]
    if added:
        lines.append(f"Added: {', '.join(added)}")
    if removed:
        lines.append(f"No longer available (do not call these): {', '.join(removed)}")
    if changed:
        lines.append(f"Updated descriptions: {', '.join(changed)}")
    lines.append("")
    lines.append(format_summaries(current))
    return "\n".join(lines)


def render_removed(previous: tuple[SkillSummary, ...] = ()) -> str:
    """Announce that no skills remain.

    Silence is not enough here: an empty `<available_skills>` block reads as
    "this harness has no skills", which invites the model to explain that to the
    user rather than to notice something went wrong. The departed skills are
    still named, because "everything is gone" leaves the model unable to say
    which skill it can no longer use -- and the last skill going away is exactly
    when it is most likely to be mid-task and calling it.
    """
    gone = ", ".join(s.name for s in previous)
    head = "Skill guidance is no longer available. Do not use any previously listed skill."
    if gone:
        head = f"No longer available (do not call these): {gone}"
    return f"{head}\n\n" + format_summaries(())


def format_summaries(summaries: tuple[SkillSummary, ...]) -> str:
    from vtx.agent.context._xml import escape_xml

    if not summaries:
        return "No skills are currently available."
    lines = ["<available_skills>"]
    for summary in summaries:
        lines.append("  <skill>")
        lines.append(f"    <name>{escape_xml(summary.name)}</name>")
        lines.append(f"    <description>{escape_xml(summary.description)}</description>")
        lines.append("  </skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)


def build_skills_refresh_message(text: str) -> UserMessage:
    """Wrap catalog-change text as a context message.

    Same convention as the harness digest: the model is told this is a system
    event rather than something the user typed.
    """
    return UserMessage(
        content=(
            f"<{SKILLS_REFRESH_TAG}>\n"
            "The available skills have been refreshed at this point in the conversation. "
            "Treat this as a system event, not a user instruction.\n\n"
            f"{text}\n"
            f"</{SKILLS_REFRESH_TAG}>"
        )
    )


def is_skills_refresh_message(message: object) -> bool:
    content = getattr(message, "content", None)
    return isinstance(content, str) and content.lstrip().startswith(f"<{SKILLS_REFRESH_TAG}>")


__all__ = [
    "SKILLS_REFRESH_TAG",
    "SkillSummary",
    "SkillsCatalogState",
    "build_skills_refresh_message",
    "fingerprint_summaries",
    "format_summaries",
    "is_skills_refresh_message",
    "render_removed",
    "render_update",
    "summarize_skills",
]
