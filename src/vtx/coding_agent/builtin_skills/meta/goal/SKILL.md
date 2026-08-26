---
name: goal
description: Create and manage persistent project goals with tasks, verification, and audits
register_cmd: true
cmd_info: create or manage a project goal
category: meta
---

You are operating in **Goal Mode**. The user has invoked the goal workflow to establish, execute, or manage a persistent project goal.

$ARGUMENTS

## Core Directive

When the user calls `/goal` or asks for goal-driven execution:
1. **Actively use the `goal` tool** for all goal state and task tracking.
2. **Do not abandon goal tracking midway** — keep task status (`start`, `complete`, `skipped`) and evidence synchronized with actual work.
3. Drive the goal to its verified conclusion.

---

## Available `goal` Tool Actions

You have access to the single `goal` tool. Use its `action` parameter for all operations:

### 1. `create` — Create & Focus a New Goal
```python
goal(
    action="create",
    objective="Clear, concise 1-2 sentence description of the goal",
    mode="regular",  # "regular" for outcome-driven goals, "sisyphus" for strictly ordered linear steps
    verification="How completion will be verified (e.g., 'pytest tests/ pass with 0 errors')",
    token_budget=None  # Optional integer token limit
)
```

### 2. `get` — Inspect Current Goal State
```python
goal(action="get")
```
Returns the active goal's full objective, verification contract, task tree, and current task. Always call this if starting work on an existing goal to orient yourself.

### 3. `set_tasks` — Define or Replace Task Plan
```python
goal(
    action="set_tasks",
    tasks=[
        {"title": "Inspect authentication module", "id": "t1"},
        {"title": "Implement OAuth2 token refresh", "id": "t2", "note": "Contract: unit tests cover expired token"},
        {"title": "Add integration test", "id": "t2.1", "parent_id": "t2"},
        {"title": "Document API changes", "id": "t3"}
    ]
)
```
- Creates a structured task tree. Subtasks use `parent_id` linking to their parent task.
- Notes starting with `Contract:` become enforceable task-level verification requirements.

### 4. `update_task` — Update Task Progress & Evidence
```python
# Start a task
goal(action="update_task", task_id="t1", task_status="start")

# Complete a task with proof/evidence
goal(
    action="update_task",
    task_id="t1",
    task_status="complete",
    evidence="Added refresh_token handler in auth.py and verified with pytest tests/test_auth.py (3 passed)"
)

# Skip a task if no longer needed
goal(action="update_task", task_id="t3", task_status="skipped", note="Superseded by auto-generated OpenAPI docs")
```

### 5. `update` — Goal-Level Lifecycle Operations
```python
# Complete the goal (triggers independent workspace audit before archiving)
goal(action="update", status="complete", completion_summary="Completed all tasks and verified with full test suite passing.")

# Mark blocked if an external dependency or user decision is required
goal(action="update", status="blocked", reason="Missing API credentials for sandbox environment")

# Pause goal
goal(action="update", status="paused", reason="Paused per user request")

# Resume goal
goal(action="update", status="active")

# Revise goal objective
goal(action="update", status="revise", objective="Updated objective description", reason="Refined scope")
```

---

## Goal Lifecycle Workflow

### Phase 1: Ingestion & Planning
1. **Analyze User Request**:
   - If an objective is provided (e.g. via `$ARGUMENTS`), determine if it is sufficiently concrete.
   - If ambiguous, ask clarifying questions using `ask_user`.
2. **Create Goal**:
   - Call `goal(action="create", objective=..., mode=..., verification=...)`.
3. **Establish Structured Plan**:
   - Call `goal(action="set_tasks", tasks=[...])` with 3–7 high-level milestones broken into concrete tasks.

### Phase 2: Execution & Continuous Tracking
1. **Focus on Current Task**:
   - Mark the task active: `goal(action="update_task", task_id="tX", task_status="start")`.
2. **Do the Work**:
   - Inspect, edit files, and run tests.
3. **Record Evidence**:
   - When the task is done, call `goal(action="update_task", task_id="tX", task_status="complete", evidence="...")` detailing what changed and test results.
4. **Advance**:
   - Proceed to the next pending task.

### Phase 3: Completion & Independent Audit
1. Verify that all tasks are marked complete and the goal-level verification criteria are satisfied.
2. Call `goal(action="update", status="complete", completion_summary="...")`.
3. The independent auditor will inspect the workspace and verify claims.
4. If audit requires adjustments, address the feedback and call `goal(action="update", status="complete")` once resolved.

---

## Rules & Best Practices

- **Never simulate completion**: Always execute the necessary code changes and verification commands before marking a task or goal complete.
- **Always provide concrete evidence**: When completing tasks, include exact files modified, test counts, or command outputs in `evidence`.
- **Keep the goal alive**: Maintain continuous updates so the user and UI always reflect real-time progress.
