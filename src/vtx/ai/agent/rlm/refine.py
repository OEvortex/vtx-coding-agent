"""Continual-harness refinement executor for the RLM mode.

Port of Prime Agent's ``core/refinement/refinement.ts`` plan/apply pipeline:
an auxiliary LLM proposes JSON ``create``/``update``/``delete`` edits against
the continual harness state, the edits are applied with per-edit error
capture and baseline-conflict detection, a prime-style notice is built for
the model, and the full result (with before/after snapshots) is persisted to
``refinements.jsonl`` so ``/refine rollback <id>`` can invert it later.

Mode-neutral by construction: the executor runs in every runtime mode. Only
the prompt-facing call contracts differ (``await refine.run()`` /
``rlm.spawn`` in ``code_first``, the ``refine`` / ``task`` tools in
``tool_first``), which is why every mode-sensitive prompt is built from
:data:`current_mode`.

Ported/adapted from Prime Agent (MIT) — https://github.com/PrimeIntellect-ai/prime-agent
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vtx.ai.agent.rlm.harness import (
    _MINED_MIN_RUN,
    HarnessKind,
    HarnessState,
    _slug,
    get_harness_state,
    harness_query_terms,
    skill_reference_error,
)
from vtx.ai.base import ProviderConfig
from vtx.core.types import (
    AssistantMessage,
    Message,
    StopReason,
    StreamDone,
    TextPart,
    ToolResultMessage,
    UserMessage,
)

log = logging.getLogger(__name__)

MODE_CODE_FIRST = "code_first"
MODE_TOOL_FIRST = "tool_first"

TRUNCATED_JSON_ERROR = (
    "the model's JSON output was truncated; the refinement output budget was exhausted"
)
_DEFAULT_OVERVIEW_ENTRY_LIMIT = 40
_DEFAULT_OVERVIEW_CONTENT_LIMIT = 180
_HISTORY_LIMIT = 20
_CONVERSATION_TAIL_CHARS = 80_000
_HISTORY_FILE_NAME = "refinements.jsonl"

ACTIONS = ("create", "update", "delete")
KINDS: tuple[HarnessKind, ...] = ("prompt", "memory", "skill", "subagent")

# =================================================================================================
# stale-entry detection
#
# Entries are write-mostly: a refinement pass can update or delete one, but
# nothing re-checks whether an old entry is still true. A global entry recorded
# from one session is read by every later session, so a fact that has since
# changed keeps being asserted with the same confidence. Nothing in the store
# decays it, and the digest's per-kind limit means old entries also stop being
# visible.
#
# Detection folds into the auto-refine gate rather than adding a pass: the gate
# already reads the trajectory and already decides whether a refinement pass is
# worth running, so a contradiction it can see is free to report.
# =================================================================================================

STALE_REASON = "contradicted by the current trajectory"

STALE_REVIEW_INSTRUCTIONS = (
    "Also check whether any existing harness entry is now contradicted by what you "
    "just observed. If one is, update it or delete it; do not add a second entry "
    "that repeats the same subject while leaving the wrong one in place. If nothing "
    "is contradicted, say so and leave the entries alone."
)


@dataclass
class StaleEntry:
    """An existing entry the gate believes the trajectory contradicts."""

    kind: HarnessKind
    id: str
    scope: str
    reason: str = STALE_REASON

    def label(self) -> str:
        return f"[{self.scope}:{self.id}] {self.kind}"


def current_mode() -> str:
    """Active runtime mode, defaulting to the REPL-first one.

    Imported lazily: :mod:`vtx.ai.config` pulls in the provider stack, and
    this module is reachable from the prompt builder above it.
    """
    try:
        from vtx.ai.config import config

        return getattr(config, "mode", MODE_CODE_FIRST) or MODE_CODE_FIRST
    except Exception:
        return MODE_CODE_FIRST


REFINEMENT_SYSTEM_PROMPT = """You are Vtx's /refine continual harness subsystem.

Your job is to improve the editable continual harness state from the current trajectory.
This is similar in spirit to context compaction, but instead of summarizing the
conversation you emit precise Create, Update, or Delete edits to reusable state.
The continual harness is the persistent, editable set of prompt notes, memories,
skills, and subagent specs that lets Vtx improve reusable behavior
outside the token history.
Use "continual harness" for that persistent artifact layer; keep "RLM" for the
runtime, Python REPL kernel, and native call interface that executes those artifacts.

Continual harness components:
- prompt: supplemental prompt notes only. The base system prompt is immutable and MUST NOT be rewritten.
- memory: durable facts, decisions, failures, preferences, and outcomes.
- skill: installed skill entry. Skill create/update edits MUST include a `reference` object saying how to invoke it, plus an `arguments` object describing accepted inputs, required fields, defaults, and constraints. Two reference shapes exist, one per runtime mode: `{"type":"python","import":"package.module","callable":"function_name","call_pattern":"await function_name(...)"}` for a session with a persistent Python kernel, and `{"type":"tool_first","call_pattern":"<tool or shell invocation>"}` for a session without one. Use `{}` for `arguments` only when the skill truly needs no external inputs. A `<mode_contract>` block below states which shape this session requires.
- subagent: reusable delegation specs, including purpose, instructions, and when to invoke. Include the RLM-native call form: compose a concise task prompt and spawn with `handle = await rlm.spawn("sub-task", name="worker")`; admission returns immediately with `rlm_child_id`, `name`, `session_dir`, and `model`, never the child's answer. Results arrive only through explicit `agent_message` replies or files; children reply with `await agent_message.send(message, receiver_role="parent")`. Use `await rlm.list_subagents()` to recover direct child handles and `await agent_message.send(..., receiver_role="child", receiver_name=handle.name)` for follow-ups. Do not invent wrappers like `run_subagent(...)`.

Scope and persistence policy:
- The default editable continual harness store is local to the current Vtx session. Use it for session-specific progress, active task state, current-run coordination notes, temporary blockers, and project facts that should not affect other sessions.
- A caller may explicitly request global refinement. Global edits must be stable cross-session lessons, durable user preferences, reusable skills/subagents, or tool/environment facts that should affect future sessions.
- Entry ids in the harness overview may carry a display-only `local:` or `global:` prefix. Always use the bare id (no prefix) in edits.
- All edits in one refinement apply only to the requested scope's store. During a local refinement, global entries are read-only context: never propose update or delete edits for them; create a local entry instead when a session-specific override is genuinely needed.
- Project/workspace-specific lessons may be persisted globally only when the title, path, or content explicitly names the project/workspace and the lesson is likely to be reused in future sessions for that project. Prefer local edits when the lesson only belongs in the current conversation.
- Use memory for declarative facts and preferences, skill for repeatable procedures exposed as Python calls, prompt for narrow behavioral policy addendums, and subagent for reusable delegation roles.
- Create or update the smallest relevant component: repeated delegation roles should become subagent specs, repeated procedures should become skills, durable facts/preferences should become memories, and narrow behavioral policies should become prompt addendums.
- When an edit is persisted, include metadata such as `{"scope":"local"}` or `{"scope":"global"}` when that helps future review understand the intended blast radius.

Use the trajectory, current continual harness state, and prior refinement history. Prefer
small evidence-backed edits. If prior refinements caused issues, rollback or
replace the faulty editable entries. Never edit source files directly. Output
JSON only with this exact shape:

{
  "summary": "one sentence",
  "rationale": "why these edits are justified by trajectory evidence",
  "expectedOutcome": "what should improve and how to validate it",
  "edits": [
    {
      "action": "create|update|delete",
      "kind": "prompt|memory|skill|subagent",
      "id": "stable id for update/delete, optional for create",
      "title": "required for create/update except delete",
      "content": "required for create/update except delete",
      "path": "optional grouping path",
      "reference": {"type": "python", "import": "package.module", "callable": "function_name", "call_pattern": "await function_name(...)"},
      "arguments": {"name": {"type": "string", "required": true, "description": "accepted input"}},
      "metadata": {},
      "reason": "why this edit is useful"
    }
  ]
}"""

_GLOBAL_SCOPE_INSTRUCTION = (
    "Requested refinement scope: global. Only propose stable cross-session continual "
    "harness edits, durable user preferences, reusable skills/subagents, or explicitly "
    "project-qualified facts that should affect future Vtx sessions. Do not persist "
    "session-only progress, temporary blockers, or current-run coordination globally."
)
_LOCAL_SCOPE_INSTRUCTION = (
    "Requested refinement scope: local. Prefer local continual harness edits for current "
    "task progress, temporary blockers, current-run coordination, and project facts that "
    "are not clearly reusable across Vtx sessions. Global entries in the overview are "
    "read-only context: do not propose update or delete edits for them; create a local "
    "entry instead if an override is needed."
)

_CODE_FIRST_TRIGGER_HEAD = (
    "When to call `await refine.run()`: after a repeated failure, a reusable tactic "
    "emerges, a repeated delegation role should become a subagent spec, a repeated "
    "procedure should become a skill, a durable fact/preference should become a "
    "memory, a narrow behavioral policy should become a prompt addendum, a user "
    "corrects behavior that should persist locally or globally, validation shows a "
    "continual harness entry is wrong, or a skill/subagent/memory/prompt note should "
    "be created, updated, deleted, or rolled back."
)
_TOOL_FIRST_TRIGGER_HEAD = (
    "When to call the `refine` tool: after a repeated failure, a reusable tactic "
    "emerges, a repeated delegation role should become a subagent spec, a repeated "
    "procedure should become a skill, a durable fact/preference should become a "
    "memory, a narrow behavioral policy should become a prompt addendum, a user "
    "corrects behavior that should persist locally or globally, validation shows a "
    "continual harness entry is wrong, or a skill/subagent/memory/prompt note should "
    "be created, updated, deleted, or rolled back."
)

_MODE_DIGEST_LINES: dict[str, tuple[str, ...]] = {
    MODE_CODE_FIRST: (
        f"{_CODE_FIRST_TRIGGER_HEAD} Keep `await refine.run()` continual harness "
        "edits small and evidence-backed.",
        "Call contract: read each installed Python skill's SKILL.md and call its documented "
        "module function in the Python REPL; do not assume a `.run` entrypoint. Use "
        "`<skill_import> ...` in shell when a CLI exists. Continual harness skill entries "
        "are Python REPL skills with an explicit Python `reference` and `arguments` "
        "contract. Spawn a continual harness subagent spec by composing a concise task "
        "prompt and calling `handle = await rlm.spawn('sub-task', name='worker')`; "
        "admission returns immediately with `rlm_child_id`, `name`, `session_dir`, and "
        "`model`, never the child's answer. Results arrive only through explicit "
        "`agent_message` replies or files; children reply with "
        "`await agent_message.send(message, receiver_role='parent')`. Use "
        "`await rlm.list_subagents()` to recover direct child handles and "
        "`await agent_message.send(..., receiver_role='child', receiver_name=handle.name)` "
        "for follow-ups. Do not invent wrappers such as `call_skill(...)`, "
        "`run_subagent(...)`, or named subagent registries.",
    ),
    MODE_TOOL_FIRST: (
        f"{_TOOL_FIRST_TRIGGER_HEAD} Use `refine(instructions=...)` to "
        "focus one pass and `refine(global_=true)` for cross-session entries; "
        "`refine(action='status')` reports whether a pass is already queued. Keep continual "
        "harness edits small and evidence-backed.",
        "Call contract: read each installed skill's SKILL.md and follow its documented call "
        "form (a tool call, or a CLI/shell invocation). There is no persistent Python "
        "kernel in this mode, so never write `await <module>.<func>(...)` Python import "
        "call forms. Continual harness skill entries still carry an explicit `reference` "
        "and `arguments` contract, but the call pattern must be the tool or CLI "
        "invocation that works without a REPL. Invoke a continual harness subagent spec "
        "with the `task` tool: `task(description=..., subagent_type='<spec title>', "
        "prompt='<concise task>')`, whose result is the subagent's final text returned as "
        "the tool result. Do not invent wrappers such as `rlm.spawn(...)`, "
        "`agent_message.send(...)`, `call_skill(...)`, or `run_subagent(...)`.",
    ),
}

#: Shown when relevance ranking trims a kind below its display limit. The
#: reachability claim is per-mode because it differs: only ``code_first`` has
#: the REPL ``harness`` object to read the remainder through.
_RANKED_NOTE: dict[str, str] = {
    MODE_CODE_FIRST: (
        "(entries ranked by relevance to the current task; the rest are "
        "readable with harness.list(...) and harness.search(...) in the REPL)"
    ),
    MODE_TOOL_FIRST: (
        "(entries ranked by relevance to the current task; only the top entries "
        "are shown, so an entry you need but cannot see is not retrievable here)"
    ),
}

_MODE_SUBAGENT_HINT: dict[str, str] = {
    MODE_CODE_FIRST: (
        "invoke a spec by turning it into a concise task prompt and spawning with "
        "`await rlm.spawn('<task>', name='<worker>')`; admission returns a child handle, "
        "never the answer"
    ),
    MODE_TOOL_FIRST: (
        "invoke a spec with the `task` tool as `task(description=..., "
        "subagent_type='<spec title>', prompt='<concise task>')`; the subagent's final "
        "text comes back as the tool result"
    ),
}

# Appended to the plan-pass prompt when the session is not REPL-first: the base
# REFINEMENT_SYSTEM_PROMPT documents the RLM-native call forms, which the
# tool-first surface cannot execute.
_MODE_PLAN_INSTRUCTION: dict[str, str] = {
    MODE_TOOL_FIRST: (
        "Mode contract: this session has no persistent Python kernel and no "
        "`rlm`/`agent_message` bridge. Every `skill` edit MUST use the "
        '`{"type": "tool_first", "call_pattern": "<the tool or shell invocation that runs it>"}` '
        "reference shape -- this overrides the `python` example in the output shape above, and "
        "a reference carrying a Python import is rejected. Write every `subagent` edit as a "
        "`task`-tool delegation spec (purpose, instructions, when to invoke) rather than an "
        "`rlm.spawn` call. Keep emitting the `arguments` object so the contract stays "
        "machine-checkable. The agent triggers refinement with the `refine` tool in this mode."
    )
}


@dataclass
class RefinementOutcome:
    """Result of one plan/apply pass, as consumed by the agent loop."""

    id: str
    summary: str
    applied: int
    total: int
    scope: str
    notice: str | None = None
    rollback_of: str | None = None
    #: Per-edit records (action, kind, id, scope, before, after, error). The TUI
    #: renders the diff from these; the model only ever sees `notice`.
    edits: list[dict[str, Any]] = field(default_factory=list)
    #: Model the pass ran on, so the cost is attributable when it was routed away
    #: from the session model.
    model: str = ""


# =================================================================================================
# id / text helpers
# =================================================================================================


def generate_refinement_id() -> str:
    """Mint a refinement id in the canonical ``refine_<timestamp>`` format."""
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")[:17]
    return f"refine_{stamp}"


def compact_text(text: str, limit: int = _DEFAULT_OVERVIEW_CONTENT_LIMIT) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[: limit - 3]}..."


def harness_entry_malformation(entry: Any) -> str | None:
    """Return a reason string when a persisted entry cannot be rendered, else None."""
    if not isinstance(getattr(entry, "id", None), str) or not getattr(entry, "id", ""):
        return "missing id"
    if not isinstance(getattr(entry, "title", None), str) or not getattr(entry, "title", ""):
        return "missing title"
    if not isinstance(getattr(entry, "content", None), str):
        return "content is not a string"
    return None


# =================================================================================================
# JSON extraction / proposal normalization (ports of extractJsonObject et al.)
# =================================================================================================


def _is_incomplete_json(candidate: str) -> bool:
    """Whether a JSON candidate ends mid-value (unterminated string / unclosed braces)."""
    depth = 0
    in_string = False
    escaped = False
    for char in candidate:
        if escaped:
            escaped = False
            continue
        if in_string:
            if char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
    return in_string or depth > 0


def _parse_json_candidate(candidate: str) -> Any:
    try:
        # strict=False: models routinely emit raw newlines/tabs inside string values.
        return json.loads(candidate, strict=False)
    except json.JSONDecodeError as error:
        if _is_incomplete_json(candidate):
            raise ValueError(TRUNCATED_JSON_ERROR) from error
        raise ValueError(f"the model did not return valid JSON: {error.msg}") from error


def extract_json_object(text: str) -> Any:
    trimmed = text.strip()
    if trimmed.startswith("{") and trimmed.endswith("}"):
        return _parse_json_candidate(trimmed)
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", trimmed)
    if fenced:
        return _parse_json_candidate(fenced.group(1).strip())
    start = trimmed.find("{")
    end = trimmed.rfind("}")
    if start != -1 and end > start:
        return _parse_json_candidate(trimmed[start : end + 1])
    if _is_incomplete_json(trimmed):
        raise ValueError(TRUNCATED_JSON_ERROR)
    raise ValueError("Refiner did not return a JSON object")


def _object_record(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    return None


def normalize_proposal(value: Any) -> dict[str, Any]:
    """Normalize an untrusted refinement proposal while preserving invalid edit
    fields for apply-time validation."""
    record = value if isinstance(value, dict) else {}
    edits = record.get("edits")
    edit_list = edits if isinstance(edits, list) else []
    normalized: list[dict[str, Any]] = []
    for edit in edit_list:
        if not isinstance(edit, dict):
            continue
        normalized.append(
            {
                "action": edit.get("action"),
                "kind": edit.get("kind"),
                "id": edit.get("id") if isinstance(edit.get("id"), str) else None,
                "title": edit.get("title") if isinstance(edit.get("title"), str) else None,
                "content": edit.get("content") if isinstance(edit.get("content"), str) else None,
                "path": edit.get("path") if isinstance(edit.get("path"), str) else None,
                "reference": _object_record(edit.get("reference")),
                "arguments": _object_record(edit.get("arguments")),
                "metadata": _object_record(edit.get("metadata")),
                "reason": edit.get("reason") if isinstance(edit.get("reason"), str) else None,
            }
        )
    return {
        "summary": (
            record.get("summary")
            if isinstance(record.get("summary"), str)
            else "Refined continual harness state"
        ),
        "rationale": record.get("rationale") if isinstance(record.get("rationale"), str) else "",
        "expectedOutcome": (
            record.get("expectedOutcome") if isinstance(record.get("expectedOutcome"), str) else ""
        ),
        "edits": normalized,
    }


def validate_edit(edit: dict[str, Any], computed_id: str | None = None) -> str | None:
    """Return an error string when the edit is not applicable, else None."""
    action = edit.get("action")
    kind = edit.get("kind")
    edit_id = edit.get("id")
    if action not in ACTIONS:
        return f"unsupported action {action}"
    if kind not in KINDS:
        return f"unsupported kind {kind}"
    if kind == "prompt" and (
        edit_id == "base_system_prompt" or computed_id == "base_system_prompt"
    ):
        return "base system prompt is not editable"
    if action != "create" and not edit_id:
        return f"{action} requires id"
    if action != "delete" and (not edit.get("title") or not edit.get("content")):
        return f"{action} requires title and content"
    if edit_id is not None and (not isinstance(edit_id, str) or not edit_id):
        return f"{action} requires id to be a non-empty string when provided"
    path = edit.get("path")
    if path is not None and (not isinstance(path, str) or not path):
        return f"{action} requires path to be a non-empty string when provided"
    if action != "delete":
        title = edit.get("title")
        content = edit.get("content")
        if not isinstance(title, str) or not isinstance(content, str) or not title or not content:
            return f"{action} requires title and content to be non-empty strings"
    if edit.get("reference") is not None and not isinstance(edit.get("reference"), dict):
        return f"{action} requires reference to be an object when provided"
    if edit.get("arguments") is not None and not isinstance(edit.get("arguments"), dict):
        return f"{action} requires arguments to be an object when provided"
    if edit.get("metadata") is not None and not isinstance(edit.get("metadata"), dict):
        return f"{action} requires metadata to be an object when provided"
    if action != "delete" and kind == "skill" and edit.get("arguments") is None:
        return f"{action} skill requires arguments"
    if action != "delete" and kind == "skill":
        # The harness owns the reference contract; a bad shape is reported as a
        # per-edit error so the plan pass can repair it on the next attempt.
        reference_error = skill_reference_error(edit.get("reference"), edit.get("id") or "")
        if reference_error is not None:
            # The harness messages already name the skill, so only the action is
            # prefixed: "create skill reference.type must be one of: ...".
            return f"{action} {reference_error}"
    return None


# =================================================================================================
# prompt fragments
# =================================================================================================


def overview_for_prompt(*states: HarnessState) -> str:
    """Compact ``kind: N`` + entry-line digest (prime's overviewForPrompt)."""
    lines: list[str] = []
    for kind in KINDS:
        entries = []
        for state in states:
            entries.extend(state.list(kind))
        lines.append(f"{kind}: {len(entries)}")
        for entry in entries[:_DEFAULT_OVERVIEW_ENTRY_LIMIT]:
            malformation = harness_entry_malformation(entry)
            if malformation:
                lines.append(f"- harness: skipped malformed entry {entry.id} ({malformation})")
                continue
            content = re.sub(r"\s+", " ", entry.content).strip()[:240]
            arguments_text = ""
            if entry.kind == "skill" and entry.arguments:
                arguments_text = f" args={json.dumps(entry.arguments, ensure_ascii=False)[:240]}"
            reference_text = ""
            if entry.kind == "skill" and entry.reference:
                reference_text = f" ref={json.dumps(entry.reference, ensure_ascii=False)[:240]}"
            lines.append(
                f"- [{entry.scope}:{entry.id}] {entry.title} ({entry.path}, v{entry.version})"
                f"{reference_text}{arguments_text}: {content}"
            )
        if len(entries) > _DEFAULT_OVERVIEW_ENTRY_LIMIT:
            lines.append(f"- +{len(entries) - _DEFAULT_OVERVIEW_ENTRY_LIMIT} more {kind} entries")
    return "\n".join(lines)


def history_for_prompt(results: list[dict[str, Any]]) -> str:
    if not results:
        return "No prior refinement history."
    rendered: list[str] = []
    for item in results[-_HISTORY_LIMIT:]:
        edits = ", ".join(
            f"{'applied' if edit.get('applied') else 'failed'} {edit.get('action')} "
            f"{edit.get('kind')}:{edit.get('id')}"
            for edit in item.get("appliedEdits", [])
        )
        rollback = f" rollbackOf={item['rollbackOf']}" if item.get("rollbackOf") else ""
        rendered.append(
            f"[{item.get('id', '?')}]{rollback} {item.get('summary', '')}\n{edits}\n"
            f"Expected outcome: {item.get('expectedOutcome', '')}"
        )
    return "\n\n".join(rendered)


def format_notice_body(result: dict[str, Any]) -> str:
    """Notice body in digest notation: summary line plus applied-edit lines."""
    lines = [compact_text(result.get("summary", ""))]
    for edit in result.get("appliedEdits", []):
        if not edit.get("applied"):
            continue
        entry = edit.get("after") or edit.get("before")
        scope = (entry or {}).get("scope") or result.get("scope") or "local"
        malformation = entry and harness_entry_malformation(_entry_from_dict(entry))
        if malformation:
            lines.append(
                f"- {edit.get('action')} {edit.get('kind')} [{scope}:{edit.get('id')}] "
                f"{edit.get('id')}:  (skipped malformed entry: {malformation})"
            )
            continue
        title = (entry or {}).get("title") or edit.get("id")
        content = compact_text((entry or {}).get("content") or "")
        lines.append(
            f"- {edit.get('action')} {edit.get('kind')} [{scope}:{edit.get('id')}] {title}: {content}"
        )
    return "\n".join(lines)


def _entry_from_dict(data: dict[str, Any]):
    from dataclasses import fields as dataclass_fields

    from vtx.ai.agent.rlm.harness import HarnessEntry

    allowed = {f.name for f in dataclass_fields(HarnessEntry)}
    return HarnessEntry(**{k: v for k, v in data.items() if k in allowed})


def create_notice(result: dict[str, Any], source: str) -> str:
    """Model-facing refinement notice (prime's createRefinementNoticeMessage)."""
    return f"[{source}-refinement]\n\n{format_notice_body(result)}"


# =================================================================================================
# system-prompt digest (prime's formatHarnessStateForPrompt, rendered over the
# merged global + local store)
# =================================================================================================

_DIGEST_ENTRY_LIMIT = 6
_DIGEST_REFINEMENT_LIMIT = 5

#: Marker tag wrapping the delivered digest context message. The digest is
#: delivered as conversation context rather than in the system prompt so that
#: relevance re-ranking (which changes as the task moves) cannot invalidate the
#: provider's cached system-prompt prefix every turn.
HARNESS_DIGEST_TAG = "vtx:harness-digest"

#: Bump when the fingerprinted material or its canonical serialization changes,
#: so fingerprints minted under different versions never compare equal.
#: Normalizing a render-ignored flag out of the material does not need a bump:
#: fingerprint equality still implies identical renders.
_DIGEST_FINGERPRINT_VERSION = 1

#: Hard cap on ranked query terms. Scoring is O(terms x entries) and the
#: ranked corpus is small, so an unbounded map only adds latency.
_MAX_QUERY_TERMS = 48


def _merge_states(*states: HarnessState) -> tuple[dict[HarnessKind, dict[str, Any]], list[Any]]:
    """Merge stores for digest rendering (call with global first, local last).

    A bare ``dict.update`` would drop an earlier store's entry whenever a later
    store held the same kind/id, which is the common case: ``create_memory``
    slugs titles, so ``notes`` in two scopes collides easily and the global
    entry would silently vanish from the digest. A colliding later entry is
    therefore keyed by ``<scope>:<id>`` so both survive. The key is internal
    only — the digest renders ``[<scope>:<id>]`` from the entry's own fields,
    which already disambiguate the two scopes from the model's point of view.
    """
    merged: dict[HarnessKind, dict[str, Any]] = {}
    refinements: list[Any] = []
    for state in states:
        state.list()  # sync from disk
        for kind in KINDS:
            bucket = merged.setdefault(kind, {})
            for entry_id, entry in state.entries.get(kind, {}).items():
                bucket[entry_id if entry_id not in bucket else f"{state.scope}:{entry_id}"] = entry
        refinements.extend(state.refinements)
    refinements.sort(key=lambda event: _created_at(event))
    return merged, refinements


def _created_at(event: Any) -> str:
    """Sortable created_at for a refinement event of any provenance.

    A hand-edited store can hold a non-string here; sorting must not raise.
    """
    value = (
        event.get("created_at") if isinstance(event, dict) else getattr(event, "created_at", "")
    )
    return value if isinstance(value, str) else ""


def harness_refinement_malformation(event: Any) -> str | None:
    """Return why a persisted refinement event cannot be rendered, else None.

    Mirrors :func:`harness_entry_malformation`: the digest joins ``changes``
    with ``str.join`` and truncates ``trigger``/``outcome`` with string
    slicing, so a non-string member of either store would raise. Render paths
    skip the event with a diagnostic instead, so one corrupt element cannot
    break prompt construction.
    """
    if not isinstance(event, dict):
        changes = getattr(event, "changes", None)
        event_id = getattr(event, "id", None)
        trigger = getattr(event, "trigger", None)
        outcome = getattr(event, "outcome", None)
    else:
        changes = event.get("changes")
        event_id = event.get("id")
        trigger = event.get("trigger")
        outcome = event.get("outcome")
    if not isinstance(event_id, str):
        return "id not a string"
    if not isinstance(trigger, str):
        return "trigger not a string"
    if not isinstance(changes, list) or not all(isinstance(c, str) for c in changes):
        return "changes is not a list of strings"
    if outcome is not None and not isinstance(outcome, str):
        return "outcome not a string"
    return None


def _malformed_event_label(event: Any) -> str:
    """Bounded label for a skipped malformed event.

    A corrupt element is labeled by type, never by value: arbitrary unbounded
    text from a hand-edited store must not reach every session's prompt.
    """
    if event is None:
        return "null"
    if not isinstance(event, dict) and not hasattr(event, "id"):
        return f"a {type(event).__name__}"
    event_id = event.get("id") if isinstance(event, dict) else getattr(event, "id", None)
    return event_id if isinstance(event_id, str) else f"a {type(event_id).__name__} id"


# -------------------------------------------------------------------------------------------------
# Relevance ranking
#
# The digest renders a handful of entries per kind. Ordered alphabetically, a
# store holding more entries than the limit hides the ones that matter for the
# task at hand, so the model cannot even tell they exist. Ranking by weighted
# term overlap against the current task keeps the relevant entries in the
# visible window.
# -------------------------------------------------------------------------------------------------

#: term -> weight
HarnessQueryTerms = dict[str, float]


def harness_query_term_idf(entries: list[Any], terms: HarnessQueryTerms) -> dict[str, float]:
    """Inverse document frequency per term over *entries*.

    ``log(1 + documents / matches)``: a term present in every entry still
    weighs ``log(2)``, while a term in one entry of N weighs ``log(1 + N)``, so
    rare distinctive terms outrank ubiquitous ones. Terms matching no entry are
    omitted — they cannot score anything.
    """
    idf: dict[str, float] = {}
    if not terms:
        return idf
    matches: dict[str, int] = {}
    for entry in entries:
        title = _searchable(entry.title)
        content = _searchable(entry.content)
        identifier = f"{_searchable(entry.path)} {_searchable(entry.id)}"
        for term in terms:
            if term in title or term in content or term in identifier:
                matches[term] = matches.get(term, 0) + 1
    for term, document_frequency in matches.items():
        idf[term] = math.log(1 + len(entries) / document_frequency)
    return idf


def _searchable(value: Any) -> str:
    """Lowercase a possibly malformed persisted field."""
    return value.lower() if isinstance(value, str) else ""


def score_harness_entry(
    entry: Any, terms: HarnessQueryTerms, idf: dict[str, float] | None = None
) -> float:
    """Weighted term overlap between one entry and the query terms.

    One match per field counts once per term, so coverage over distinct fields
    beats repetition inside a single field. Path and id form one identifier
    slot: the id is often embedded in the path, so matching both is one signal.
    """
    if not terms:
        return 0.0
    title = _searchable(entry.title)
    content = _searchable(entry.content)
    identifier = f"{_searchable(entry.path)} {_searchable(entry.id)}"
    score = 0.0
    for term, weight in terms.items():
        fields = 0
        if term in title:
            fields += 1
        if term in content:
            fields += 1
        if term in identifier:
            fields += 1
        if fields:
            score += weight * (idf or {}).get(term, 1.0) * (1 + (fields - 1) * 0.5)
    return score


def _rank_entries(entries: list[Any], terms: HarnessQueryTerms | None) -> list[Any]:
    """Order entries for the digest: by relevance when terms are given.

    Ties break on ``(path, title, id)`` so touching one entry never reshuffles
    equal-score siblings, which keeps the rendered digest a stable prefix for
    provider prompt-cache reuse.
    """
    if not terms:
        return sorted(entries, key=lambda e: (e.path, e.title, e.id))

    idf = harness_query_term_idf(entries, terms)
    return sorted(
        entries,
        key=lambda e: (
            -score_harness_entry(e, terms, idf),
            "\0".join((e.path or "", e.title or "", e.id or "")),
        ),
    )


def build_digest_query_terms(
    *, objective: str | None = None, messages: list[Message] | None = None
) -> HarnessQueryTerms:
    """Relevance signal mined from the current task.

    The active goal objective is the strongest signal; the last few
    user/assistant messages rank below it, weighted newest-first and floored at
    1 so a long tail of recency cannot outweigh the stated objective. Terms are
    mined with the raised floor that keeps function words out of the ranking.
    """
    terms: HarnessQueryTerms = {}

    def add(text: str | None, weight: float) -> None:
        if not text:
            return
        for raw in harness_query_terms(text, min_run=_MINED_MIN_RUN):
            if raw not in terms:
                if len(terms) >= _MAX_QUERY_TERMS:
                    return
                terms[raw] = weight

    add(objective, 3.0)
    recency_weight = 2.0
    for message in reversed((messages or [])[-4:]):
        if isinstance(message, (UserMessage, AssistantMessage)):
            add(_message_text(message), recency_weight)
        recency_weight = max(1.0, recency_weight - 0.5)
    return terms


def harness_digest_for_prompt(
    session_id: str,
    cwd: str,
    *,
    mode: str | None = None,
    query_terms: HarnessQueryTerms | None = None,
) -> str:
    """`# Continual Harness State` digest for the model.

    Rendered in every runtime mode; ``mode`` only selects the call contracts
    the model is told to use (default: the active config mode).

    ``query_terms`` selects entries by relevance to the current task instead of
    alphabetical order. Callers pass terms mined once per cold boundary: the
    digest is delivered as context (see :data:`HARNESS_DIGEST_TAG`) rather than
    rebuilt in the system prompt every turn, so re-ranking cannot invalidate the
    cached system-prompt prefix.

    Returns ``""`` when there is nothing to show or rendering fails — the
    caller omits the section rather than failing prompt construction.
    """
    try:
        return _render_digest(
            *load_merged_harness(session_id, cwd), mode or current_mode(), query_terms
        )
    except Exception:
        log.exception("harness digest rendering failed")
        return ""


def _render_digest(
    merged: dict[HarnessKind, dict[str, Any]],
    refinements: list[Any],
    resolved_mode: str,
    query_terms: HarnessQueryTerms | None,
) -> str:
    trigger_line, contract_line = _MODE_DIGEST_LINES.get(
        resolved_mode, _MODE_DIGEST_LINES[MODE_CODE_FIRST]
    )
    lines = [
        "# Continual Harness State",
        "",
        "Local continual harness entries belong to this Vtx session. Global continual harness entries persist across Vtx sessions.",
        "The continual harness entries below are compact summaries, not full descriptions. Use them as routing/context hints; inspect or refine the underlying continual harness entry only when detail matters.",
        "Default to local continual harness refinement for current task progress, temporary blockers, and session coordination. Use global continual harness refinement only for stable cross-session lessons, durable user preferences, reusable skills/subagents, or explicitly project-qualified facts.",
        "Use these continual harness prompt notes, memories, skills, and subagent specs when they are relevant. The base system prompt is immutable; prompt entries below are supplemental notes only.",
        "",
        trigger_line,
        "",
        contract_line,
        "",
    ]

    subagent_hint = _MODE_SUBAGENT_HINT.get(resolved_mode, _MODE_SUBAGENT_HINT[MODE_CODE_FIRST])
    total_entries = 0
    for kind in KINDS:
        bucket = merged.get(kind, {})
        # Document frequency is scoped to the kind's own entries: they
        # compete for the same visible slots, so the discount should
        # reflect terms ubiquitous within the kind, not across kinds.
        entries = _rank_entries(list(bucket.values()), query_terms)
        total_entries += len(entries)
        if kind == "subagent" and entries:
            lines.append(f"{kind}: {len(entries)} ({subagent_hint})")
        else:
            lines.append(f"{kind}: {len(entries)}")
        if query_terms and len(entries) > _DIGEST_ENTRY_LIMIT:
            lines.append(_RANKED_NOTE.get(resolved_mode, _RANKED_NOTE[MODE_CODE_FIRST]))
        for entry in entries[:_DIGEST_ENTRY_LIMIT]:
            malformation = harness_entry_malformation(entry)
            if malformation:
                lines.append(f"harness: skipped malformed entry {entry.id} ({malformation})")
                continue
            arguments_text = ""
            if entry.kind == "skill" and entry.arguments:
                arguments_text = (
                    f" args={compact_text(json.dumps(entry.arguments, ensure_ascii=False))}"
                )
            reference_text = ""
            if entry.kind == "skill" and entry.reference:
                reference_text = (
                    f" ref={compact_text(json.dumps(entry.reference, ensure_ascii=False))}"
                )
            lines.append(
                f"- [{entry.scope}:{entry.id}] {entry.title} ({entry.path}, v{entry.version})"
                f"{reference_text}{arguments_text}: {compact_text(entry.content)}"
            )
        overflow = len(entries) - min(len(entries), _DIGEST_ENTRY_LIMIT)
        if overflow > 0:
            lines.append(f"- +{overflow} more {kind} entries")
        lines.append("")

    if total_entries == 0 and not refinements:
        return ""

    if total_entries == 0:
        lines.append("No saved harness entries yet.")
        lines.append("")

    lines.append(f"recent refinements: {len(refinements)}")
    for event in refinements[-_DIGEST_REFINEMENT_LIMIT:]:
        malformation = harness_refinement_malformation(event)
        if malformation:
            lines.append(
                f"harness: skipped malformed refinement event "
                f"{_malformed_event_label(event)} ({malformation})"
            )
            continue
        changes = ", ".join(event.changes) if event.changes else "no applied edits"
        outcome = f"; outcome: {compact_text(event.outcome)}" if event.outcome else ""
        lines.append(f"- [{event.id}] {compact_text(event.trigger)}: {changes}{outcome}")
    refinement_overflow = len(refinements) - min(len(refinements), _DIGEST_REFINEMENT_LIMIT)
    if refinement_overflow > 0:
        lines.append(f"- +{refinement_overflow} older refinement events")

    return "\n".join(lines).strip()


def load_merged_harness(
    session_id: str, cwd: str
) -> tuple[dict[HarnessKind, dict[str, Any]], list[Any]]:
    """Load the global store and this session's local store, merged."""
    return _merge_states(
        get_harness_state(global_=True), get_harness_state(local_state_dir(session_id, cwd))
    )


def harness_digest_fingerprint(
    merged: dict[HarnessKind, dict[str, Any]], refinements: list[Any], mode: str
) -> str:
    """Stable fingerprint of the material a rendered digest actually prints.

    Equal fingerprints imply byte-identical digests, so a cold boundary can
    decide staleness by comparing state rather than by comparing rendered text.
    Text comparison is unusable here: relevance ranking makes the rendered
    digest depend on query terms, which change as the task moves, so text
    equality would report a stale digest on nearly every boundary and re-append
    an identical-prefix message for nothing.

    Covered: entry identity and content, sorted so entry order is normalized
    away; the call contract on non-skill entries, which the formatter never
    prints; the mode, which selects the trigger and contract lines; and each
    refinement's printed fields in stored order (the formatter renders a
    positional newest tail, so order is material here). A malformed event
    renders as a skip line rather than its fields, so its label and reason are
    the fingerprint material for it.

    Excluded: ``metadata``, ``source``, the invisible ``created_at``/
    ``updated_at`` bookkeeping, and the query terms themselves — the digest is
    frozen for one delivery, so ranking is not state.
    """
    entries = sorted(
        (
            {
                "scope": entry.scope or "global",
                "kind": entry.kind,
                "id": entry.id,
                "title": entry.title,
                "path": entry.path,
                "version": entry.version,
                "content": entry.content,
                # Only skills render the call contract, so another kind can
                # change these without changing a single digest byte.
                "reference": entry.reference if entry.kind == "skill" else None,
                "arguments": entry.arguments if entry.kind == "skill" else None,
            }
            for bucket in merged.values()
            for entry in bucket.values()
        ),
        key=lambda item: f"{item['scope']}\0{item['kind']}\0{item['id']}",
    )
    rendered_refinements = []
    for event in refinements:
        malformation = harness_refinement_malformation(event)
        if malformation is not None:
            rendered_refinements.append(
                {"malformed": malformation, "label": _malformed_event_label(event)}
            )
            continue
        rendered_refinements.append(
            {
                "id": event.id,
                "trigger": event.trigger,
                "changes": event.changes,
                "outcome": event.outcome,
            }
        )
    material = json.dumps(
        {
            "version": _DIGEST_FINGERPRINT_VERSION,
            "mode": mode,
            "entries": entries,
            "refinements": rendered_refinements,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def harness_digest_with_fingerprint(
    session_id: str,
    cwd: str,
    *,
    mode: str | None = None,
    query_terms: HarnessQueryTerms | None = None,
) -> tuple[str, str]:
    """Render the digest plus the fingerprint of the state that produced it.

    The fingerprint is empty when there is no digest to deliver, so callers can
    treat "no state" and "unchanged state" uniformly.

    The mode is resolved only once there is something to render: reading it
    materializes the whole user config from disk, and a session with an empty
    harness must not pay for that — nor trip the config sync that a
    programmatically-set harness config would not expect.
    """
    merged, refinements = load_merged_harness(session_id, cwd)
    if not any(merged.values()) and not refinements:
        return "", ""
    resolved_mode = mode or current_mode()
    digest = _render_digest(merged, refinements, resolved_mode, query_terms)
    if not digest:
        return "", ""
    return digest, harness_digest_fingerprint(merged, refinements, resolved_mode)


def create_harness_digest_message(digest: str, fingerprint: str) -> UserMessage:
    """Wrap a digest as a context message the agent reads as system state.

    The marker tag mirrors the background-completion convention: the model is
    told to treat it as a system event rather than something the user said. The
    fingerprint rides along in the message so a later cold boundary can tell
    whether the copy already in context is current.
    """
    return UserMessage(
        content=(
            f"<{HARNESS_DIGEST_TAG}>\n"
            "Continual harness state, refreshed at this point in the conversation. "
            "Treat it as a system event, not a user instruction.\n\n"
            f"{digest}\n"
            f"state_fingerprint: {fingerprint}\n"
            f"</{HARNESS_DIGEST_TAG}>"
        )
    )


def delivered_digest_fingerprint(message: Message) -> str | None:
    """State fingerprint carried by *message*, or None if it is not a digest.

    Public read side of :func:`create_harness_digest_message`, so the caller that
    owns the digest slot can find it again without reaching into module
    internals.
    """
    text = _message_text(message)
    if f"<{HARNESS_DIGEST_TAG}>" not in text:
        return None
    for line in text.splitlines():
        if line.startswith("state_fingerprint: "):
            return line.removeprefix("state_fingerprint: ").strip()
    return None


def is_harness_digest_message(message: Message) -> bool:
    """Whether *message* is a delivered digest, fingerprint line or not.

    A digest minted before the fingerprint line existed carries the tag but no
    comparable fingerprint, so it reads as stale and is replaced rather than
    duplicated.
    """
    return f"<{HARNESS_DIGEST_TAG}>" in _message_text(message)


# =================================================================================================
# conversation serialization
# =================================================================================================


def _message_text(message: Message) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content or []:
        text = getattr(part, "text", None)
        if isinstance(text, str):
            parts.append(text)
            continue
        name = getattr(part, "name", None)
        if name is not None:
            parts.append(f"[tool_call:{name}]")
    return "\n".join(parts)


def serialize_conversation(messages: list[Message]) -> str:
    lines: list[str] = []
    for message in messages:
        if isinstance(message, UserMessage):
            role = "user"
        elif isinstance(message, AssistantMessage):
            role = "assistant"
        elif isinstance(message, ToolResultMessage):
            role = "tool"
        else:
            role = getattr(message, "role", "user")
        text = _message_text(message)
        if text:
            lines.append(f"[{role}]\n{text}")
    return "\n".join(lines)


# =================================================================================================
# refinements.jsonl history (rollback source)
# =================================================================================================


def history_path(state: HarnessState) -> Path | None:
    if state.file_path is None:
        return None
    return state.file_path.parent / _HISTORY_FILE_NAME


def load_history(state: HarnessState) -> list[dict[str, Any]]:
    path = history_path(state)
    if path is None or not path.exists():
        return []
    results: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and "id" in value and "appliedEdits" in value:
                results.append(value)
    except OSError:
        log.exception("failed to read refinement history %s", path)
    return results


def merge_refinement_history(
    global_results: list[dict[str, Any]], session_results: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge cross-session and session-local history, de-duplicating by id.

    The plan pass needs both: a local refinement that cannot see global history
    will re-learn a lesson another session already tried, and may even recreate
    an entry that was tried globally and then rolled back. Session entries win
    on id conflict so a pass that is still resolving its own latest result sees
    the fresher copy, while keeping any scope the session copy lacks.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for result in global_results:
        result_id = result.get("id")
        if isinstance(result_id, str):
            by_id[result_id] = result
    for result in session_results:
        result_id = result.get("id")
        if not isinstance(result_id, str):
            continue
        existing = by_id.get(result_id)
        if result.get("scope") or existing is None or not existing.get("scope"):
            by_id[result_id] = result
        else:
            by_id[result_id] = {**result, "scope": existing["scope"]}
    return list(by_id.values())


def load_merged_history(session_id: str, cwd: str) -> list[dict[str, Any]]:
    """Refinement history visible to a plan pass: global plus this session's."""
    return merge_refinement_history(
        load_history(get_harness_state(global_=True)),
        load_history(get_harness_state(local_state_dir(session_id, cwd))),
    )


def append_history(state: HarnessState, result: dict[str, Any]) -> None:
    path = history_path(state)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
    except OSError:
        log.exception("failed to append refinement history %s", path)


# =================================================================================================
# baseline snapshot / rollback
# =================================================================================================


def snapshot_baseline(state: HarnessState) -> dict[str, dict[str, str | None]]:
    """Snapshot of the target store taken before the (slow) LLM plan pass."""
    state.list()  # syncs from disk
    baseline: dict[str, dict[str, str | None]] = {}
    for kind in KINDS:
        baseline[kind] = {
            entry_id: _entry_snapshot(asdict(entry))
            for entry_id, entry in state.entries.get(kind, {}).items()
        }
    return baseline


def rollback_proposal(target: dict[str, Any]) -> dict[str, Any]:
    edits: list[dict[str, Any]] = []
    for edit in reversed(target.get("appliedEdits", [])):
        if not edit.get("applied"):
            continue
        before = edit.get("before")
        after = edit.get("after")
        if before:
            edits.append(
                {
                    "action": "update" if after else "create",
                    "kind": edit.get("kind"),
                    "id": edit.get("id"),
                    "title": before.get("title"),
                    "content": before.get("content"),
                    "path": before.get("path"),
                    "reference": before.get("reference"),
                    "arguments": before.get("arguments"),
                    "metadata": before.get("metadata"),
                    "reason": f"Rollback {target.get('id')}",
                }
            )
        elif after:
            edits.append(
                {
                    "action": "delete",
                    "kind": edit.get("kind"),
                    "id": edit.get("id"),
                    "reason": f"Rollback {target.get('id')}",
                }
            )
    return {
        "summary": f"Rollback refinement {target.get('id')}",
        "rationale": f"Restores continual harness state snapshots from refinement {target.get('id')}.",
        "expectedOutcome": "Faulty refinement edits are reverted.",
        "edits": edits,
    }


# =================================================================================================
# apply
# =================================================================================================


def _entry_snapshot(entry: dict[str, Any] | None) -> str | None:
    if entry is None:
        return None
    return json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str)


def apply_refinement(
    state: HarnessState,
    proposal: dict[str, Any],
    *,
    id: str,
    scope: str,
    baseline: dict[str, dict[str, str | None]] | None = None,
    rollback_of: str | None = None,
) -> dict[str, Any]:
    """Apply a proposal via HarnessState CRUD with per-edit error capture.

    Never raises for an individual edit: each failure lands in that edit's
    ``error`` field (prime's applyRefinementProposal semantics), including the
    baseline-conflict check that guards against kernel writes that landed
    while the LLM plan pass was in flight.
    """
    applied_edits: list[dict[str, Any]] = []
    proposal_modified_keys: set[str] = set()
    for edit in proposal.get("edits", []):
        kind = edit.get("kind")
        action = edit.get("action")
        computed_id = edit.get("id")
        if not computed_id and action == "create":
            computed_id = _slug(edit.get("title") or str(kind), str(kind))
        computed_id = computed_id or ""
        validation_error = validate_edit(edit, computed_id)
        if validation_error:
            applied_edits.append(
                {**edit, "id": computed_id, "applied": False, "error": validation_error}
            )
            continue

        # Snapshot immediately: state.get returns the live entry and _upsert
        # mutates it in place on update, so asdict() taken later would show
        # the post-edit values (prime clones entries for the same reason).
        before = state.get(kind, computed_id)
        before_dict = asdict(before) if before is not None else None
        entry_key = f"{kind}:{computed_id}"
        if (
            baseline is not None
            and entry_key not in proposal_modified_keys
            and _entry_snapshot(before_dict) != baseline.get(kind, {}).get(computed_id)
        ):
            applied_edits.append(
                {
                    **edit,
                    "id": computed_id,
                    "before": before_dict,
                    "applied": False,
                    "error": "entry changed during refinement planning",
                }
            )
            continue

        if action == "delete":
            if before_dict is None:
                applied_edits.append(
                    {**edit, "id": computed_id, "applied": False, "error": "entry not found"}
                )
                continue
            try:
                state.delete(kind, computed_id)
            except (ValueError, RuntimeError) as error:
                applied_edits.append(
                    {**edit, "id": computed_id, "applied": False, "error": str(error)}
                )
                continue
            proposal_modified_keys.add(entry_key)
            applied_edits.append(
                {**edit, "id": computed_id, "before": before_dict, "applied": True}
            )
            continue

        if action == "create" and before_dict is not None:
            applied_edits.append(
                {**edit, "id": computed_id, "applied": False, "error": "entry already exists"}
            )
            continue
        if action == "update" and before_dict is None:
            applied_edits.append(
                {**edit, "id": computed_id, "applied": False, "error": "entry not found"}
            )
            continue

        try:
            if action == "create":
                entry = state.create(
                    kind,
                    edit["title"],
                    edit["content"],
                    id=computed_id,
                    path=edit.get("path") or "general",
                    reference=edit.get("reference"),
                    arguments=edit.get("arguments"),
                    metadata=edit.get("metadata"),
                    source="refine",
                )
            else:
                entry = state.update(
                    kind,
                    computed_id,
                    edit["title"],
                    edit["content"],
                    path=edit.get("path"),
                    reference=edit.get("reference"),
                    arguments=edit.get("arguments"),
                    metadata=edit.get("metadata"),
                    source="refine",
                )
        except (ValueError, RuntimeError) as error:
            applied_edits.append(
                {**edit, "id": computed_id, "applied": False, "error": str(error)}
            )
            continue

        proposal_modified_keys.add(entry_key)
        applied_edits.append(
            {
                **edit,
                "id": computed_id,
                "before": before_dict,
                "after": asdict(entry),
                "applied": True,
            }
        )

    changes = [
        f"{edit['action']} {edit['kind']}:{edit['id']}"
        for edit in applied_edits
        if edit.get("applied")
    ]
    try:
        state.record_refinement(
            proposal.get("summary", ""),
            changes,
            evidence=proposal.get("rationale", ""),
            outcome=proposal.get("expectedOutcome", ""),
            id=id,
        )
    except (ValueError, RuntimeError):
        # The edits themselves landed; a rejected history record must not
        # discard the applied result or its notice.
        log.exception("failed to record refinement %s", id)

    return {
        "id": id,
        "summary": proposal.get("summary", ""),
        "rationale": proposal.get("rationale", ""),
        "expectedOutcome": proposal.get("expectedOutcome", ""),
        "appliedEdits": applied_edits,
        "harnessStatePath": str(state.file_path or ""),
        "rollbackOf": rollback_of,
        "scope": scope,
    }


# =================================================================================================
# plan (LLM pass)
# =================================================================================================

#: Output cap for the plan pass. A refinement proposal is a handful of small
#: JSON edits, so a large budget buys nothing and a truncated reply is a total
#: loss of the pass.
REFINEMENT_MAX_OUTPUT_TOKENS = 32_000

#: Slack added on top of the system prompt's token bound, covering the message
#: envelope and the provider's own framing.
_CONTEXT_OVERHEAD_TOKENS = 1_024

#: Assumed context window when the provider's model is not in the catalog. Only
#: the fitting math depends on this; an unknown model still gets a bounded
#: request rather than an unbounded one.
_FALLBACK_CONTEXT_WINDOW = 200_000

#: Assumed output ceiling when the model is not in the catalog.
_FALLBACK_MAX_TOKENS = 8_192


def _model_limits(provider: Any) -> tuple[int, int]:
    """Return ``(context_window, max_tokens)`` for the provider's model.

    Falls back to conservative constants for an unlisted model so budgeting
    still applies. Reading the live catalog keeps this honest when the model is
    known: an uncapped request would otherwise overflow a small-context model.
    """
    from vtx.ai.models import get_model

    model_id = getattr(provider, "model", None)
    model = get_model(model_id) if isinstance(model_id, str) and model_id else None
    context_window = getattr(model, "context_window", None)
    max_tokens = getattr(model, "max_tokens", None)
    return (
        context_window
        if isinstance(context_window, int) and context_window > 0
        else _FALLBACK_CONTEXT_WINDOW,
        max_tokens if isinstance(max_tokens, int) and max_tokens > 0 else _FALLBACK_MAX_TOKENS,
    )


def _token_bound(text: str) -> int:
    """Upper bound on tokens for *text*.

    One token per UTF-8 byte bounds byte-based tokenizers, including dense or
    unusual text. Erring high is safe here: the bound only decides how much
    trajectory to drop, and dropping too little is the failure this prevents.
    """
    return len(text.encode("utf-8"))


def _thinking_off(provider: Any) -> bool:
    """Whether the provider runs this call without reasoning tokens."""
    config = getattr(provider, "config", None)
    return getattr(config, "thinking_level", "off") in ("off", "", None)


def refinement_request(
    *,
    system_prompt: str,
    conversation: str,
    build_prompt: Callable[[str], str],
    output_reserve: int,
    context_window: int,
    model_max_tokens: int,
) -> tuple[str, int]:
    """Fit a refinement request to the model; return ``(user_prompt, max_tokens)``.

    The trajectory and the reply have to fit in the same window, and a fixed
    character tail cannot do that: on a small-context model an 80k-character
    trajectory overflows before a reply is ever generated. So the input budget is
    ``context_window`` minus the smallest of the model's output ceiling, this
    call's reserve, and half the window; the longest tail of trajectory that
    fits inside it is found by binary search; and whatever context is left over
    becomes ``max_tokens``.

    Drops from the tail, because the newest turns are what a refinement pass
    reasons about. Raises when the prompt alone leaves no room for a reply,
    which is a configuration problem worth naming rather than surfacing later
    as an empty or cut-off completion.
    """
    system_reserve = _token_bound(system_prompt) + _CONTEXT_OVERHEAD_TOKENS
    output_cap = min(model_max_tokens, output_reserve, context_window // 2)
    input_budget = context_window - output_cap

    def prompt_for_length(length: int) -> str:
        start = max(0, len(conversation) - length)
        # Never split a surrogate pair: a lone surrogate is not encodable and
        # would raise inside the token bound.
        if start < len(conversation) and "\ud800" <= conversation[start] <= "\udfff":
            start += 1
        return build_prompt(
            "[Earlier conversation omitted to fit the model context.\n" + conversation[start:]
        )

    user_prompt = build_prompt(conversation)
    if conversation and system_reserve + _token_bound(user_prompt) > input_budget:
        low, high = 0, len(conversation)
        while low < high:
            length = (low + high + 1) // 2
            if system_reserve + _token_bound(prompt_for_length(length)) <= input_budget:
                low = length
            else:
                high = length - 1
        user_prompt = prompt_for_length(low)

    # The reserve caps the reply as well as the input budget: a small call (the
    # review gate) must not be handed the model's full ceiling just because the
    # window had room to spare.
    max_tokens = min(output_cap, context_window - system_reserve - _token_bound(user_prompt))
    if max_tokens <= 0:
        raise ValueError(
            "Refinement prompt leaves no room for output in the model's context window; "
            "retry with a smaller request."
        )
    return user_prompt, max_tokens


async def _complete_refinement_call(
    *,
    provider: Any,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    cancel_event: Any = None,
    label: str,
) -> str:
    """Run one text-only refinement call and return its reply text.

    A ``length`` stop is reported as a truncation error rather than handed to
    the JSON extractor. The extractor can only diagnose a reply that visibly
    stops mid-value; a stream that stopped for output budget tells us the cause
    directly, even when the partial text happens to parse as balanced JSON.
    """
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError(f"{label} cancelled before the call")

    stream = await provider.stream(
        [UserMessage(content=user_prompt)],
        system_prompt=system_prompt,
        tools=None,
        max_tokens=max_tokens,
    )
    text_parts: list[str] = []
    stop_reason = None
    async for part in stream:
        if isinstance(part, TextPart):
            text_parts.append(part.text)
        elif isinstance(part, StreamDone):
            stop_reason = part.stop_reason

    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError(f"{label} cancelled during the call")
    if stop_reason is StopReason.LENGTH:
        raise ValueError(f"{label}: {TRUNCATED_JSON_ERROR}")
    return "\n".join(text_parts)


def resolve_refine_provider(provider: Any) -> tuple[Any, str]:
    """Return ``(provider, label)`` for a refinement call.

    Refinement reads the trajectory and emits a small JSON proposal, so it can
    run on a cheaper model than the session's. ``refine.model`` selects it as
    ``provider/model``; anything that would make the pass worse or impossible
    falls back to the session provider, and the returned label says which was
    used so the TUI and logs can attribute the cost.

    Falls back rather than raising because refinement is a background nicety: a
    bad selector must not break the turn. The three fallback causes are an
    unparsable selector, a model that is not in the catalog, and a model whose
    context window cannot hold the request — the last matters because a small
    window would fail over-limit on the wire after the trajectory was already
    truncated to fit it.
    """
    session_label = str(getattr(provider, "model", "") or "session model")
    selector = _refine_model_selector()
    if not selector:
        return provider, session_label

    resolved = _build_auxiliary_provider(selector)
    if resolved is None:
        log.warning(
            "refine.model %r is unusable; using the session model for refinement", selector
        )
        return provider, session_label
    aux_provider, model_id, provider_name = resolved
    return aux_provider, f"{provider_name}/{model_id}"


def _refine_model_selector() -> str:
    """``refine.model`` as a non-empty string, or "" when unset.

    A hand-edited config can hold a non-string here; treat that as unset so the
    pass falls back rather than failing.
    """
    try:
        from vtx.ai.config import config

        selector = getattr(config.refine, "model", None)
    except Exception:
        return ""
    return selector.strip() if isinstance(selector, str) else ""


def _build_auxiliary_provider(selector: str) -> tuple[Any, str, str] | None:
    """Build a provider for a ``provider/model`` selector, or None if unusable."""
    from vtx.ai.agent.runtime import create_provider
    from vtx.ai.models import get_model

    provider_name, separator, model_id = selector.rpartition("/")
    if not separator or not provider_name or not model_id:
        return None
    info = get_model(model_id, provider_name)
    if info is None:
        return None
    api_key = _auxiliary_api_key(info.provider)
    if not api_key and not _provider_allows_anonymous(info.provider):
        return None
    try:
        aux = create_provider(
            info.api,
            ProviderConfig(
                api_key=api_key,
                base_url=info.base_url,
                model=info.effective_id,
                max_tokens=info.max_tokens,
                thinking_level="off",
                provider=info.provider,
                thinking_level_map=getattr(info, "thinking_level_map", None),
            ),
        )
    except Exception:
        log.warning("refine.model %r could not be instantiated", selector, exc_info=True)
        return None
    return aux, model_id, info.provider


def _auxiliary_api_key(provider_name: str) -> str | None:
    """Credential for *provider_name* from its catalog env var, if configured.

    Resolution is deferred to the provider constructor, which reads its own
    configured env var; this only needs to know whether one exists, so an
    unauthenticated selector is rejected here rather than at the first call.
    """
    try:
        from vtx.ai.provider_catalog import get as get_provider
        from vtx.ai.provider_catalog import is_provider_configured

        entry = get_provider(provider_name)
        if entry is None or not is_provider_configured(entry):
            return None
        import os

        env_var = getattr(entry, "api_key_env", None)
        return os.getenv(env_var) if isinstance(env_var, str) and env_var else None
    except Exception:
        return None


def _provider_allows_anonymous(provider_name: str) -> bool:
    """Whether *provider_name* serves without a credential (local gateways)."""
    try:
        from vtx.ai.provider_catalog import get as get_provider

        entry = get_provider(provider_name)
    except Exception:
        return False
    return bool(entry is not None and (entry.is_local or entry.api_key_optional))


def _output_reserve(provider: Any, cap: int) -> int:
    """Output room to reserve for a refinement call.

    Reasoning tokens and the JSON reply share the model's output budget, so with
    reasoning on the model needs its full ceiling and this call must not compete
    for it. With reasoning off the call only needs the JSON, so the small cap
    applies and the rest of the window stays available for trajectory.
    """
    if _thinking_off(provider):
        return cap
    return _model_limits(provider)[1]


async def plan_refinement(
    *,
    messages: list[Message],
    provider: Any,
    states: tuple[HarnessState, ...],
    history: list[dict[str, Any]],
    instructions: str | None = None,
    global_: bool = False,
    cancel_event: Any = None,
    mode: str | None = None,
) -> dict[str, Any]:
    conversation = serialize_conversation(messages)[-_CONVERSATION_TAIL_CHARS:]
    scope_instruction = _GLOBAL_SCOPE_INSTRUCTION if global_ else _LOCAL_SCOPE_INSTRUCTION
    state_block = (
        f"<current_harness_state>\n{overview_for_prompt(*states)}\n</current_harness_state>"
    )
    history_block = f"<refinement_history>\n{history_for_prompt(history)}\n</refinement_history>"
    tail_blocks = [f"<scope_policy>\n{scope_instruction}\n</scope_policy>"]
    mode_instruction = _MODE_PLAN_INSTRUCTION.get(mode or current_mode())
    if mode_instruction:
        tail_blocks.append(f"<mode_contract>\n{mode_instruction}\n</mode_contract>")
    if instructions:
        tail_blocks.append(
            f"<user_refine_instructions>\n{instructions}\n</user_refine_instructions>"
        )
    tail_blocks.append(
        "Return only JSON edits. If no useful edit is justified, return an empty edits "
        "array with a rationale."
    )

    # The trajectory is the only block the fit rewrites, so build_prompt
    # substitutes just it and leaves the fixed blocks byte-identical.
    def build_prompt(trimmed: str) -> str:
        return "\n\n".join(
            [
                state_block,
                history_block,
                f"<conversation>\n{trimmed}\n</conversation>",
                *tail_blocks,
            ]
        )

    context_window, model_max_tokens = _model_limits(provider)
    user_prompt, max_tokens = refinement_request(
        system_prompt=REFINEMENT_SYSTEM_PROMPT,
        conversation=conversation,
        build_prompt=build_prompt,
        output_reserve=_output_reserve(provider, REFINEMENT_MAX_OUTPUT_TOKENS),
        context_window=context_window,
        model_max_tokens=model_max_tokens,
    )
    text = await _complete_refinement_call(
        provider=provider,
        system_prompt=REFINEMENT_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        cancel_event=cancel_event,
        label="Refinement failed",
    )
    return normalize_proposal(extract_json_object(text))


# auto-refine review gate (prime's reviewAutoRefine)
#
# The gate is the only auto-spent call: it reads the trajectory and answers
# shouldRefine/rationale/instructions. An approved gate then runs one normal
# plan/apply pass with those instructions, so the harness only changes when a
# reviewer saw evidence worth persisting.
# =================================================================================================

AUTO_REFINE_REASON_TURN_INTERVAL = "turn_interval"
AUTO_REFINE_REASON_COMPACT = "compact"
AUTO_REFINE_REASONS = (AUTO_REFINE_REASON_TURN_INTERVAL, AUTO_REFINE_REASON_COMPACT)

AUTO_REFINE_REVIEW_MAX_OUTPUT_TOKENS = 4_096
_AUTO_REVIEW_CONVERSATION_CHARS = 40_000

AUTO_REFINE_REVIEW_SYSTEM_PROMPT = """You are Vtx's automatic /refine review gate.

Decide whether this checkpoint should run /refine. Auto /refine writes local continual harness state by default, so approve when the trajectory contains evidence useful to this session's future turns.
Reject one-off noise, unsupported hypotheses, and transient tool outputs. Ask for global refinement only for durable cross-session lessons or explicitly project-qualified lessons likely to be reused in future sessions.

Also report entries the current harness state already holds that the trajectory now contradicts. Entries are never re-checked once written, so an entry that a later run invalidated would otherwise keep being asserted. Only report a contradiction you can point at in the trajectory; leave the list empty when the trajectory does not bear on an entry. A correct-but-unused entry is not stale.

Return JSON only:
{
  "shouldRefine": true|false,
  "rationale": "short reason",
  "instructions": "optional concise instructions for /refine if shouldRefine is true",
  "staleEntries": [{"kind": "prompt|memory|skill|subagent", "id": "existing entry id", "reason": "what in the trajectory contradicts it"}]
}"""


@dataclass
class AutoRefineReview:
    """Verdict from the auto-refine gate."""

    should_refine: bool = False
    rationale: str = ""
    instructions: str | None = None
    stale_entries: list[StaleEntry] = field(default_factory=list)


def parse_auto_refine_review(
    text: str, *, known: dict[tuple[str, str], str] | None = None
) -> AutoRefineReview:
    """Parse a gate verdict, resolving reported stale ids against *known*.

    ``known`` maps ``(kind, id)`` to scope. A reported id that does not resolve
    is dropped rather than trusted: the gate is a cheap model that can hallucinate
    an entry, and acting on a phantom would have the plan pass editing something
    that does not exist — or worse, creating a duplicate of a real entry under a
    name the gate invented.
    """
    value = extract_json_object(text)
    if not isinstance(value, dict):
        raise ValueError("Auto-refine review JSON must be an object")
    return AutoRefineReview(
        should_refine=value.get("shouldRefine") is True,
        rationale=(
            value["rationale"]
            if isinstance(value.get("rationale"), str)
            else "No rationale provided."
        ),
        instructions=(
            value["instructions"] if isinstance(value.get("instructions"), str) else None
        ),
        stale_entries=_parse_stale_entries(value.get("staleEntries"), known),
    )


def _parse_stale_entries(raw: Any, known: dict[tuple[str, str], str] | None) -> list[StaleEntry]:
    if not known or not isinstance(raw, list):
        return []
    found: list[StaleEntry] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        entry_id = item.get("id")
        if kind not in KINDS or not isinstance(entry_id, str) or not entry_id:
            continue
        scope = known.get((kind, entry_id))
        if scope is None or (kind, entry_id) in seen:
            continue
        seen.add((kind, entry_id))
        reason = item.get("reason")
        found.append(
            StaleEntry(
                kind=kind,
                id=entry_id,
                scope=scope,
                reason=reason.strip()
                if isinstance(reason, str) and reason.strip()
                else STALE_REASON,
            )
        )
    return found


def known_entry_index(session_id: str, cwd: str) -> dict[tuple[str, str], str]:
    """``(kind, id) -> scope`` for every entry visible to a session.

    Both scopes, because a global entry is read by this session and by every
    other one, so a contradiction to it is worth reporting even from a local pass.
    """
    index: dict[tuple[str, str], str] = {}
    for state in (
        get_harness_state(global_=True),
        get_harness_state(local_state_dir(session_id, cwd)),
    ):
        for kind in KINDS:
            for entry_id, entry in state.entries.get(kind, {}).items():
                index.setdefault((kind, str(entry_id)), entry.scope or state.scope)
    return index


def stale_instructions(review: AutoRefineReview) -> str:
    """Instruction block naming the entries the gate flagged as contradicted."""
    lines = [STALE_REVIEW_INSTRUCTIONS, f"Flagged as contradicted: {review.rationale}"]
    for entry in review.stale_entries:
        lines.append(f"- {entry.label()}: {entry.reason}")
    return "\n".join(lines)


def auto_refine_instructions(reason: str, review: AutoRefineReview) -> str:
    """Instruction block handed to the approved plan pass."""
    detail = f"\nReviewer instructions: {review.instructions}" if review.instructions else ""
    return (
        f"Automatic refine review triggered by {reason}. Only create/update/delete local "
        "harness entries if there is clear evidence that should help this session continue. "
        "Prefer an empty edits array over speculative or one-off memories. Do not promote "
        f"anything global unless explicitly requested. Reviewer rationale: {review.rationale}"
        f"{detail}"
    )


async def review_auto_refine(
    *,
    messages: list[Message],
    provider: Any,
    states: tuple[HarnessState, ...],
    history: list[dict[str, Any]],
    reason: str,
    turns_since_last_review: int,
    session_id: str = "",
    cwd: str = "",
    cancel_event: Any = None,
) -> AutoRefineReview:
    """Ask the cheap gate whether this checkpoint warrants a refinement pass.

    Raises on transport/parse failure; the caller turns that into a cooldown so
    a broken provider does not retry a review on every turn.
    """
    conversation = serialize_conversation(messages)[-_AUTO_REVIEW_CONVERSATION_CHARS:]
    state_block = (
        f"<current_harness_state>\n{overview_for_prompt(*states)}\n</current_harness_state>"
    )
    history_block = f"<refinement_history>\n{history_for_prompt(history)}\n</refinement_history>"
    trigger_block = (
        f"<trigger>\n{reason}; {turns_since_last_review} assistant turns since last "
        "auto-refine review\n</trigger>"
    )
    tail_block = (
        "Return shouldRefine=true when the trajectory contains evidence useful to this "
        "session's future turns. Prefer local harness edits for current task progress, "
        "temporary blockers, and current-run coordination. Ask for global refinement only "
        "for durable cross-session lessons or explicitly project-qualified facts likely "
        "to be reused in future sessions."
    )

    def build_prompt(trimmed: str) -> str:
        return "\n\n".join(
            [
                trigger_block,
                state_block,
                history_block,
                f"<conversation>\n{trimmed}\n</conversation>",
                tail_block,
            ]
        )

    context_window, model_max_tokens = _model_limits(provider)
    user_prompt, max_tokens = refinement_request(
        system_prompt=AUTO_REFINE_REVIEW_SYSTEM_PROMPT,
        conversation=conversation,
        build_prompt=build_prompt,
        output_reserve=_output_reserve(provider, AUTO_REFINE_REVIEW_MAX_OUTPUT_TOKENS),
        context_window=context_window,
        model_max_tokens=model_max_tokens,
    )
    text = await _complete_refinement_call(
        provider=provider,
        system_prompt=AUTO_REFINE_REVIEW_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        cancel_event=cancel_event,
        label="Auto-refine review failed",
    )
    # Reported ids are resolved against the live store, so a hallucinated id
    # cannot send the plan pass after an entry that does not exist.
    known = known_entry_index(session_id, cwd)
    return parse_auto_refine_review(text, known=known)


# =================================================================================================
# state resolution + orchestration
# =================================================================================================


def local_state_dir(session_id: str, cwd: str) -> Path:
    from vtx.ai.agent.ipython_manager import session_harness_dir

    return Path(session_harness_dir(session_id, cwd)) / "harness"


def resolve_states(
    session_id: str, cwd: str, *, global_: bool
) -> tuple[HarnessState, tuple[HarnessState, ...], str]:
    """Return (target state, overview states, scope) for a refinement pass.

    Overview always shows global first then local (local entries are the
    editable target for local scope; global is read-only context there).
    """
    global_state = get_harness_state(global_=True)
    local_state = get_harness_state(local_state_dir(session_id, cwd))
    if global_:
        return global_state, (global_state,), "global"
    return local_state, (global_state, local_state), "local"


async def run_refinement(
    *,
    messages: list[Message],
    provider: Any,
    session_id: str,
    cwd: str,
    instructions: str | None = None,
    global_: bool = False,
    source: str = "self",
    cancel_event: Any = None,
    rollback_id: str | None = None,
    mode: str | None = None,
) -> RefinementOutcome:
    """Full plan → apply → record → notice pipeline for one refinement.

    ``rollback_id`` skips the LLM pass and inverts a recorded result's
    before/after snapshots instead (``/refine rollback <id>``).
    """
    fallback_scope = "global" if global_ else "local"
    result_id = generate_refinement_id()
    # Rollback inverts recorded snapshots and never calls the model, so there is
    # nothing to route away from the session model.
    plan_provider, model_label = (
        (provider, "") if rollback_id else resolve_refine_provider(provider)
    )

    if rollback_id:
        # The recorded result decides its own scope when known (prime parity).
        target_result: dict[str, Any] | None = None
        target_state: HarnessState | None = None
        for scope_name, state in (
            ("local", get_harness_state(local_state_dir(session_id, cwd))),
            ("global", get_harness_state(global_=True)),
        ):
            for item in load_history(state):
                if item.get("id") == rollback_id:
                    target_result = item
                    target_state = state
                    fallback_scope = item.get("scope") or scope_name
                    break
            if target_result is not None:
                break
        if target_result is None or target_state is None:
            raise ValueError(f"Refinement {rollback_id} not found")
        proposal = rollback_proposal(target_result)
        rollback_of = rollback_id
        overview_states: tuple[HarnessState, ...] = (target_state,)
        baseline = None
        state = target_state
        scope = fallback_scope
    else:
        state, overview_states, scope = resolve_states(session_id, cwd, global_=global_)
        baseline = snapshot_baseline(state)
        # Merged, not just the target store's: a pass that cannot see global
        # history re-learns lessons another session already tried, including
        # entries that were tried globally and then rolled back.
        history = load_merged_history(session_id, cwd)
        proposal = await plan_refinement(
            messages=messages,
            provider=plan_provider,
            states=overview_states,
            history=history,
            instructions=instructions,
            global_=global_,
            cancel_event=cancel_event,
            mode=mode,
        )
        rollback_of = None

    result = apply_refinement(
        state, proposal, id=result_id, scope=scope, baseline=baseline, rollback_of=rollback_of
    )
    try:
        append_history(state, result)
    except Exception:
        log.exception("failed to persist refinement result %s", result_id)

    applied = sum(1 for edit in result["appliedEdits"] if edit.get("applied"))
    notice = create_notice(result, source) if applied else None
    return RefinementOutcome(
        id=result_id,
        summary=result["summary"],
        applied=applied,
        total=len(result["appliedEdits"]),
        scope=scope,
        notice=notice,
        rollback_of=rollback_of,
        edits=result["appliedEdits"],
        model=model_label,
    )


# =================================================================================================
# /refine slash-command option parsing (prime's parseRefineCommandOptions)
# =================================================================================================


@dataclass
class RefineCommandOptions:
    instructions: str | None = None
    rollback_id: str | None = None
    global_: bool = False
    errors: list[str] = field(default_factory=list)


def parse_refine_command_options(args: str) -> RefineCommandOptions:
    """Parse ``/refine`` args with prime's ``parseRefineCommandOptions`` semantics.

    Usage errors are collected in ``errors`` (prime throws them); the caller
    surfaces each string to the user.
    """
    rest = args.strip()
    global_flag = False
    if re.match(r"^--global(?=\s|$)", rest):
        global_flag = True
        rest = re.sub(r"^--global(?=\s|$)", "", rest).strip()
    if rest == "rollback":
        return RefineCommandOptions(errors=["Usage: /refine rollback <refinement-id>"])
    rollback_match = re.match(
        r"^rollback[\t \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]", rest
    )
    if rollback_match:
        rollback_id = rest[rollback_match.end() :].strip()
        if rollback_id == "--global":
            return RefineCommandOptions(errors=["Usage: /refine rollback <refinement-id>"])
        if re.search(r"\s--global$", rollback_id):
            global_flag = True
            rollback_id = re.sub(r"\s--global$", "", rollback_id).strip()
        if not rollback_id:
            return RefineCommandOptions(errors=["Usage: /refine rollback <refinement-id>"])
        return RefineCommandOptions(rollback_id=rollback_id, global_=global_flag)
    return RefineCommandOptions(instructions=rest or None, global_=global_flag)


__all__ = [
    "AUTO_REFINE_REASONS",
    "AUTO_REFINE_REASON_COMPACT",
    "AUTO_REFINE_REASON_TURN_INTERVAL",
    "AUTO_REFINE_REVIEW_MAX_OUTPUT_TOKENS",
    "AUTO_REFINE_REVIEW_SYSTEM_PROMPT",
    "HARNESS_DIGEST_TAG",
    "MODE_CODE_FIRST",
    "MODE_TOOL_FIRST",
    "REFINEMENT_MAX_OUTPUT_TOKENS",
    "REFINEMENT_SYSTEM_PROMPT",
    "AutoRefineReview",
    "HarnessQueryTerms",
    "RefineCommandOptions",
    "RefinementOutcome",
    "apply_refinement",
    "auto_refine_instructions",
    "build_digest_query_terms",
    "create_harness_digest_message",
    "create_notice",
    "current_mode",
    "delivered_digest_fingerprint",
    "extract_json_object",
    "format_notice_body",
    "generate_refinement_id",
    "harness_digest_fingerprint",
    "harness_digest_for_prompt",
    "harness_digest_with_fingerprint",
    "harness_query_term_idf",
    "harness_refinement_malformation",
    "history_for_prompt",
    "is_harness_digest_message",
    "load_history",
    "load_merged_harness",
    "load_merged_history",
    "merge_refinement_history",
    "normalize_proposal",
    "overview_for_prompt",
    "parse_auto_refine_review",
    "parse_refine_command_options",
    "plan_refinement",
    "refinement_request",
    "resolve_states",
    "review_auto_refine",
    "rollback_proposal",
    "run_refinement",
    "score_harness_entry",
    "serialize_conversation",
    "snapshot_baseline",
    "validate_edit",
]
