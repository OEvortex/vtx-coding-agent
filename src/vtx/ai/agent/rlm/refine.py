"""Continual-harness refinement executor for the RLM mode.

Port of Prime Agent's ``core/refinement/refinement.ts`` plan/apply pipeline:
an auxiliary LLM proposes JSON ``create``/``update``/``delete`` edits against
the continual harness state, the edits are applied with per-edit error
capture and baseline-conflict detection, a prime-style notice is built for
the model, and the full result (with before/after snapshots) is persisted to
``refinements.jsonl`` so ``/refine rollback <id>`` can invert it later.

Ported/adapted from Prime Agent (MIT) — https://github.com/PrimeIntellect-ai/prime-agent
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vtx.ai.agent.rlm.harness import HarnessKind, HarnessState, _slug, get_harness_state
from vtx.core.types import AssistantMessage, Message, TextPart, ToolResultMessage, UserMessage

log = logging.getLogger(__name__)

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
- skill: installed Python REPL skill. Skill create/update edits MUST include a `reference` object with `{"type":"python"}`, a Python import, and a callable or call pattern; they also MUST include an `arguments` object describing accepted inputs, required fields, defaults, and constraints. Use `{}` for `arguments` only when the Python callable truly needs no external inputs. Include the RLM-native call form `await <skill_import>(...)`.
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
        return json.loads(candidate)
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
        try:
            return json.loads(trimmed[start : end + 1])
        except json.JSONDecodeError:
            return _parse_json_candidate(trimmed[start:])
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
        reference = edit.get("reference")
        if not reference:
            return f"{action} skill requires python reference"
        if reference.get("type") != "python":
            return f"{action} skill reference.type must be python"
        has_import = bool(
            isinstance(reference.get("import"), str) and reference.get("import")
        ) or bool(
            isinstance(reference.get("python_import"), str) and reference.get("python_import")
        )
        has_callable = bool(
            isinstance(reference.get("callable"), str) and reference.get("callable")
        ) or bool(isinstance(reference.get("call_pattern"), str) and reference.get("call_pattern"))
        if not has_import:
            return f"{action} skill requires python import"
        if not has_callable:
            return f"{action} skill requires callable or call_pattern"
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


def _merge_states(*states: HarnessState) -> tuple[dict[HarnessKind, dict[str, Any]], list[Any]]:
    """Merge stores for digest rendering; later states override earlier ones
    for the same kind/id (call with global first, local last)."""
    merged: dict[HarnessKind, dict[str, Any]] = {}
    refinements: list[Any] = []
    for state in states:
        state.list()  # sync from disk
        for kind in KINDS:
            bucket = merged.setdefault(kind, {})
            bucket.update(state.entries.get(kind, {}))
        refinements.extend(state.refinements)
    refinements.sort(key=lambda event: getattr(event, "created_at", ""))
    return merged, refinements


def harness_digest_for_prompt(session_id: str, cwd: str) -> str:
    """`# Continual Harness State` digest for the rlm system prompt.

    Returns ``""`` when there is nothing to show or rendering fails — the
    caller omits the section rather than failing prompt construction.
    """
    try:
        local_state = get_harness_state(local_state_dir(session_id, cwd))
        global_state = get_harness_state(global_=True)
        merged, refinements = _merge_states(global_state, local_state)

        lines = [
            "# Continual Harness State",
            "",
            "Local continual harness entries belong to this Vtx session. Global continual harness entries persist across Vtx sessions.",
            "The continual harness entries below are compact summaries, not full descriptions. Use them as routing/context hints; inspect or refine the underlying continual harness entry only when detail matters.",
            "Default to local continual harness refinement for current task progress, temporary blockers, and session coordination. Use global continual harness refinement only for stable cross-session lessons, durable user preferences, reusable skills/subagents, or explicitly project-qualified facts.",
            "Use these continual harness prompt notes, memories, skills, and subagent specs when they are relevant. The base system prompt is immutable; prompt entries below are supplemental notes only.",
            "",
            "When to call `await refine.run()`: after a repeated failure, a reusable tactic emerges, a repeated delegation role should become a subagent spec, a repeated procedure should become a skill, a durable fact/preference should become a memory, a narrow behavioral policy should become a prompt addendum, a user corrects behavior that should persist locally or globally, validation shows a continual harness entry is wrong, or a skill/subagent/memory/prompt note should be created, updated, deleted, or rolled back. Keep `await refine.run()` continual harness edits small and evidence-backed.",
            "",
            "Call contract: read each installed Python skill's SKILL.md and call its documented module function in the Python REPL; do not assume a `.run` entrypoint. Use `<skill_import> ...` in shell when a CLI exists. Continual harness skill entries are Python REPL skills with an explicit Python `reference` and `arguments` contract. Spawn a continual harness subagent spec by composing a concise task prompt and calling `handle = await rlm.spawn('sub-task', name='worker')`; admission returns immediately with `rlm_child_id`, `name`, `session_dir`, and `model`, never the child's answer. Results arrive only through explicit `agent_message` replies or files; children reply with `await agent_message.send(message, receiver_role='parent')`. Use `await rlm.list_subagents()` to recover direct child handles and `await agent_message.send(..., receiver_role='child', receiver_name=handle.name)` for follow-ups. Do not invent wrappers such as `call_skill(...)`, `run_subagent(...)`, or named subagent registries.",
            "",
        ]

        total_entries = 0
        for kind in KINDS:
            bucket = merged.get(kind, {})
            entries = sorted(bucket.values(), key=lambda e: (e.path, e.title, e.id))
            total_entries += len(entries)
            if kind == "subagent" and entries:
                lines.append(
                    f"{kind}: {len(entries)} (invoke a spec by turning it into a concise task prompt and spawning with `await rlm.spawn('<task>', name='<worker>')`; admission returns a child handle, never the answer)"
                )
            else:
                lines.append(f"{kind}: {len(entries)}")
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
            changes = ", ".join(event.changes) if event.changes else "no applied edits"
            outcome = f"; outcome: {compact_text(event.outcome)}" if event.outcome else ""
            lines.append(f"- [{event.id}] {compact_text(event.trigger)}: {changes}{outcome}")
        refinement_overflow = len(refinements) - min(len(refinements), _DIGEST_REFINEMENT_LIMIT)
        if refinement_overflow > 0:
            lines.append(f"- +{refinement_overflow} older refinement events")

        return "\n".join(lines).strip()
    except Exception:
        log.exception("harness digest rendering failed")
        return ""


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


async def plan_refinement(
    *,
    messages: list[Message],
    provider: Any,
    states: tuple[HarnessState, ...],
    history: list[dict[str, Any]],
    instructions: str | None = None,
    global_: bool = False,
    cancel_event: Any = None,
) -> dict[str, Any]:
    conversation = serialize_conversation(messages)[-_CONVERSATION_TAIL_CHARS:]
    scope_instruction = _GLOBAL_SCOPE_INSTRUCTION if global_ else _LOCAL_SCOPE_INSTRUCTION
    blocks = [
        f"<current_harness_state>\n{overview_for_prompt(*states)}\n</current_harness_state>",
        f"<refinement_history>\n{history_for_prompt(history)}\n</refinement_history>",
        f"<conversation>\n{conversation}\n</conversation>",
        f"<scope_policy>\n{scope_instruction}\n</scope_policy>",
    ]
    if instructions:
        blocks.append(f"<user_refine_instructions>\n{instructions}\n</user_refine_instructions>")
    blocks.append(
        "Return only JSON edits. If no useful edit is justified, return an empty edits "
        "array with a rationale."
    )
    user_prompt = "\n\n".join(blocks)

    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("Refinement cancelled before planning")

    stream = await provider.stream(
        [UserMessage(content=user_prompt)], system_prompt=REFINEMENT_SYSTEM_PROMPT, tools=None
    )
    text_parts: list[str] = []
    async for part in stream:
        if isinstance(part, TextPart):
            text_parts.append(part.text)

    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("Refinement cancelled during planning")

    return normalize_proposal(extract_json_object("\n".join(text_parts)))


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
) -> RefinementOutcome:
    """Full plan → apply → record → notice pipeline for one refinement.

    ``rollback_id`` skips the LLM pass and inverts a recorded result's
    before/after snapshots instead (``/refine rollback <id>``).
    """
    fallback_scope = "global" if global_ else "local"
    result_id = generate_refinement_id()

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
        history = load_history(state)
        proposal = await plan_refinement(
            messages=messages,
            provider=provider,
            states=overview_states,
            history=history,
            instructions=instructions,
            global_=global_,
            cancel_event=cancel_event,
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
    "REFINEMENT_SYSTEM_PROMPT",
    "RefineCommandOptions",
    "RefinementOutcome",
    "apply_refinement",
    "create_notice",
    "extract_json_object",
    "format_notice_body",
    "generate_refinement_id",
    "harness_digest_for_prompt",
    "history_for_prompt",
    "load_history",
    "normalize_proposal",
    "overview_for_prompt",
    "parse_refine_command_options",
    "plan_refinement",
    "resolve_states",
    "rollback_proposal",
    "run_refinement",
    "serialize_conversation",
    "snapshot_baseline",
    "validate_edit",
]
