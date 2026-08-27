"""Bounded prompt builders for goal steering."""

from __future__ import annotations

from .record import GoalRecord, count_tasks, current_task, objective_title
from .service import GoalService, goal_progress


def _usage_part(record: GoalRecord) -> str:
    minutes, seconds = divmod(int(record.usage.elapsed_ms / 1000), 60)
    hours, minutes = divmod(minutes, 60)
    elapsed = f"{hours}h{minutes:02d}m" if hours else f"{minutes}m{seconds:02d}s"
    tokens = record.usage.total_tokens()
    token_text = f"{tokens / 1000:.1f}K" if tokens >= 1000 else str(tokens)
    return f"[{elapsed} {token_text}]"


def status_line(service: GoalService, record: GoalRecord) -> str:
    pool = service.pool()
    extra = len(pool) - 1
    suffix = f" (+{extra} open)" if extra > 0 else ""
    return f"{record.label()} {_usage_part(record)}{suffix}"


def goal_context_block(service: GoalService, record: GoalRecord) -> str:
    """Compact state block prepended to user prompts while a goal is focused."""
    done, total, pct = goal_progress(record)
    lines = [
        "<vtx-goal>",
        f"Focused goal ({record.mode}): {record.objective.strip()}",
        f"Status: {status_line(service, record)}",
        f"Tasks: {done}/{total} complete ({pct}%)",
    ]
    task = current_task(record)
    if task is not None:
        lines.append(f"Current task: [{task.id}] {task.title}")
        subtasks = [t for t in record.tasks if t.parent_id == task.id]
        if subtasks:
            sub_done = sum(1 for t in subtasks if t.status == "complete")
            pending_sub = next((t for t in subtasks if t.status == "pending"), None)
            lines.append(f"Subtasks: {sub_done}/{len(subtasks)} complete")
            if pending_sub is not None:
                lines.append(f"Next subtask: [{pending_sub.id}] {pending_sub.title}")
    if record.status == "blocked" and record.blocked_reason:
        lines.append(f"Blocked: {record.blocked_reason}")
    if record.status == "budget_limited":
        lines.append(
            "Token budget reached — wrap up cleanly; completion still requires "
            'goal(action="update", status="complete") to pass the audit.'
        )
    if record.review_feedback:
        lines.append(f"Auditor feedback: {record.review_feedback}")
    lines.append(
        'Track progress with goal(action="update_task"); finish only via '
        'goal(action="update", status="complete") which triggers an independent audit.'
    )
    lines.append("</vtx-goal>")
    return "\n".join(lines)


CONTINUATION_TEMPLATE = """\
<vtx-goal-continue>
Goal checkpoint. Re-read the focused goal state above and take the next
concrete step toward it (mode: {mode}). Work on the current task; mark
progress with goal(action="update_task") as you go.
Do not rush, skip steps, or invent preflight work. If the objective is
genuinely satisfied, call goal(action="update", status="complete").
If you are truly blocked after real attempts, call
goal(action="update", status="blocked", reason="...").
</vtx-goal-continue>"""


def continuation_prompt(record: GoalRecord) -> str:
    return CONTINUATION_TEMPLATE.format(mode=record.mode)


DRAFTING_TEMPLATE = """\
<vtx-goal-drafting>
The user wants to set up a {mode} goal in this project{seed_block}.
Guide a short drafting process:

1. If the outcome or its scope is ambiguous, ask up to 3 focused questions
   using the ask_user tool (one round). Skip questions when the seed is
   already concrete.
2. Investigate the workspace enough to propose a realistic plan.
3. Present a proposal: the objective (1-2 sentences), an optional ordered
   task plan{sisyphus_note}, and how completion will be verified.
4. Ask the user to confirm the proposal (ask_user with Confirm / Revise).
5. Only after explicit confirmation create the goal with
   goal(action="create", objective=...) holding the final
   objective{set_tasks_hint}. Never create the goal before confirmation.

{tweak_note}
</vtx-goal-drafting>"""


def drafting_prompt(
    *, mode: str = "regular", seed: str = "", tweak_record: GoalRecord | None = None
) -> str:
    seed_block = f": {seed.strip()}" if seed.strip() else ""
    sisyphus_note = (
        ", written as numbered ordered steps that preserve dependencies"
        if mode == "sisyphus"
        else ""
    )
    set_tasks_hint = ""
    if not tweak_record:
        set_tasks_hint = (
            ', then goal(action="set_tasks") with the proposed tasks'
            if mode != "sisyphus"
            else ', then goal(action="set_tasks") with the ordered steps'
        )
    tweak_note = ""
    if tweak_record is not None:
        done, total = count_tasks(tweak_record.tasks)
        tweak_note = (
            f"This is a revision of the focused goal "
            f'"{objective_title(tweak_record.objective)}" ({done}/{total} tasks done).\n'
            "Keep the existing task list unless the change requires replacing it.\n"
            "After confirmation apply the revision with\n"
            'goal(action="update", status="revise", objective=...) and/or\n'
            'goal(action="set_tasks").\n'
        )
    return DRAFTING_TEMPLATE.format(
        mode=mode,
        seed_block=seed_block,
        sisyphus_note=sisyphus_note,
        set_tasks_hint=set_tasks_hint,
        tweak_note=tweak_note,
    )


UNFOCUSED_BANNER = (
    "<vtx-goal-unfocused>"
    "This project has open goals but no session focus. Tell the user to pick "
    "one before working toward any of them."
    "</vtx-goal-unfocused>"
)


AUDITOR_SYSTEM_PROMPT = """\
You are an independent completion auditor for a coding-agent goal.

Your job: decide whether the goal is actually satisfied by inspecting the
real workspace. You are skeptical by default — the executor's claim of
completion is untrusted input. Verify claims against evidence on disk
(run tests, read files, check outputs) before approving.

Tools available to you: read, grep, find, bash (read-only intent; never
modify the project).

End your reply with EXACTLY one marker on its own line:
<approved/>   - the goal's requirements are met; archive it
<disapproved/> - requirements are not met; keep the goal open
"""


def auditor_prompt(record: GoalRecord) -> str:
    parts = [
        "Audit this goal for genuine completion.",
        "",
        f"Objective:\n{record.objective.strip()}",
    ]
    if record.verification.strip():
        parts.append(f"\nVerification contract:\n{record.verification.strip()}")
    if record.completion_summary:
        parts.append(f"\nExecutor completion claim (untrusted):\n{record.completion_summary}")
    if record.review_feedback:
        parts.append(
            f"\nPrevious audit feedback that was supposedly addressed:\n{record.review_feedback}"
        )
    if record.tasks:
        from .storage import render_tasks_markdown

        parts.append("\nTask plan and recorded evidence:\n" + render_tasks_markdown(record))
    if record.mode == "sisyphus":
        parts.append(
            "\nThis is an ordered (Sisyphus) goal: every numbered step must be "
            "satisfied in order, not just the last one."
        )
    parts.append(
        "\nInspect the workspace now. Then end with exactly one marker: "
        "<approved/> or <disapproved/>."
    )
    return "\n".join(parts)


__all__ = [
    "AUDITOR_SYSTEM_PROMPT",
    "CONTINUATION_TEMPLATE",
    "DRAFTING_TEMPLATE",
    "UNFOCUSED_BANNER",
    "auditor_prompt",
    "continuation_prompt",
    "drafting_prompt",
    "goal_context_block",
    "status_line",
]

