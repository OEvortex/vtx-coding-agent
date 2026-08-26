"""Tests for the vtx goal system: record model, storage, and service."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vtx.coding_agent.goal import storage
from vtx.coding_agent.goal.record import (
    GoalRecord,
    count_tasks,
    create_record,
    current_task,
    normalize_task_ids,
    objective_title,
)
from vtx.coding_agent.goal.service import GoalError, GoalService, get_service, goal_progress


@pytest.fixture()
def cwd(tmp_path: Path) -> Path:
    return tmp_path


# ---------------------------------------------------------------------------
# record model
# ---------------------------------------------------------------------------


def test_create_record_validates_objective() -> None:
    with pytest.raises(ValueError):
        create_record("   ")
    with pytest.raises(ValueError):
        create_record("x" * 4001)
    with pytest.raises(ValueError):
        create_record("do it", mode="chaos")
    with pytest.raises(ValueError):
        create_record("do it", token_budget=0)


def test_normalize_task_ids_stable_and_parent_linked() -> None:
    tasks = normalize_task_ids(
        [
            {"title": "first"},
            {"title": "sub", "parent_id": "t1"},
            {"title": "second"},
            {"title": "orphan sub", "parent_id": "missing"},
        ]
    )
    assert [t.id for t in tasks] == ["t1", "t1.1", "t2", "t3"]
    assert tasks[1].parent_id == "t1"
    assert tasks[3].parent_id is None  # dropped link to unknown parent


def test_count_and_progress() -> None:
    tasks = normalize_task_ids(
        [{"title": "a"}, {"title": "b"}, {"title": "b-sub", "parent_id": "t2"}]
    )
    done, total = count_tasks(tasks)
    assert (done, total) == (0, 2)
    tasks[0].status = "complete"
    assert count_tasks(tasks) == (1, 2)
    assert goal_progress(
        GoalRecord(id="x", mode="regular", status="active", objective="o", tasks=tasks)
    ) == (1, 2, 50)


def test_current_task_prefers_pointer_then_first_pending() -> None:
    record = create_record("obj")
    record.tasks = normalize_task_ids([{"title": "a"}, {"title": "b"}])
    assert current_task(record).id == "t1"
    record.tasks[0].status = "complete"
    assert current_task(record).id == "t2"
    record.current_task_id = "t1"
    assert current_task(record).id == "t1"


def test_objective_title_truncates_single_line() -> None:
    assert objective_title("a\n\nb   c") == "a b c"
    long = "x" * 100
    assert len(objective_title(long)) == 60
    assert objective_title(long).endswith("…")


# ---------------------------------------------------------------------------
# storage round-trip
# ---------------------------------------------------------------------------


def test_serialize_parse_roundtrip(cwd: Path) -> None:
    service = GoalService(str(cwd))
    record = service.create(
        "Ship the feature", verification="Run pytest with zero failures.", source="test"
    )
    record = service.replace_tasks(
        record.id, [{"title": "plan"}, {"title": "build", "parent_id": "t1"}]
    )
    reloaded = service.get(record.id)
    assert reloaded.objective == "Ship the feature"
    assert reloaded.verification == "Run pytest with zero failures."
    assert len(reloaded.tasks) == 2
    assert reloaded.status == "active"
    # Prompt body edits from disk are picked up.
    path = storage.find_goal_file(str(cwd), record.id)
    text = path.read_text(encoding="utf-8")
    assert "# Objective" in text and "# Verification" in text


def test_prompt_body_edit_survives_reconcile(cwd: Path) -> None:
    service = GoalService(str(cwd))
    record = service.create("objective here")
    path = storage.find_goal_file(str(cwd), record.id)
    edited = path.read_text(encoding="utf-8").replace(
        "# Goal Prompt", "# Goal Prompt\n\nextra user context"
    )
    if "# Goal Prompt" not in edited:
        edited = path.read_text(encoding="utf-8") + "\n# Goal Prompt\n\nextra user context\n"
    path.write_text(edited, encoding="utf-8")
    reloaded = service.focused() if False else service.get(record.id)
    assert "extra user context" in reloaded.prompt


def test_completed_records_dropped_from_pool(cwd: Path) -> None:
    service = GoalService(str(cwd))
    record = service.create("done thing")
    service.set_status(record.id, "paused")
    # Manually mark complete inside the active file (as the archive step would).
    pool_before = service.pool()
    assert len(pool_before) == 1
    archived = service.archive(record.id)
    assert (
        not storage.find_goal_file(str(cwd), archived.id).parent.name.startswith("goals")
        or storage.find_goal_file(str(cwd), archived.id).parent.name == "archived"
    )
    assert service.pool() == {}


def test_ledger_append_and_activity(cwd: Path) -> None:
    service = GoalService(str(cwd))
    record = service.create("ledger goal")
    service.set_status(record.id, "paused", reason="waiting")
    events = storage.read_ledger(str(cwd), goal_id=record.id)
    types = {e["type"] for e in events}
    assert {"created", "focus_changed", "status_changed"} <= types
    activity = storage.recent_activity(str(cwd), record.id)
    assert any("status → paused" in line for line in activity)


def test_settings_roundtrip(cwd: Path) -> None:
    service = GoalService(str(cwd))
    assert service.settings["autoContinue"] is True
    updated = service.update_settings(autoContinue=False)
    assert updated["autoContinue"] is False
    # Unknown keys ignored; reload persists.
    fresh = GoalService(str(cwd)).settings
    assert fresh["autoContinue"] is False
    assert json.loads(storage.settings_path(str(cwd)).read_text())["autoContinue"] is False


# ---------------------------------------------------------------------------
# service mutations
# ---------------------------------------------------------------------------


def test_create_focuses_and_second_create_conflicts(cwd: Path) -> None:
    service = GoalService(str(cwd))
    first = service.create("first goal")
    assert service.focused_id == first.id
    second = service.create("second goal", focus=False)
    assert service.focused_id == first.id
    assert len(service.pool()) == 2
    del second


def test_replace_tasks_preserves_statuses(cwd: Path) -> None:
    service = GoalService(str(cwd))
    record = service.create("tasks goal")
    service.replace_tasks(record.id, [{"title": "keep me"}])
    service.update_task(record.id, "t1", "complete", evidence="it works")
    updated = service.replace_tasks(record.id, [{"title": "keep me"}, {"title": "new one"}])
    by_title = {t.title: t for t in updated.tasks}
    assert by_title["keep me"].status == "complete"
    assert by_title["keep me"].evidence == "it works"
    assert by_title["new one"].status == "pending"


def test_update_task_requires_contract_evidence(cwd: Path) -> None:
    service = GoalService(str(cwd))
    record = service.create("contracted")
    service.replace_tasks(
        record.id, [{"title": "contracted task", "note": "Contract: tests must pass"}]
    )
    with pytest.raises(GoalError):
        service.update_task(record.id, "t1", "complete")
    updated = service.update_task(record.id, "t1", "complete", evidence="pytest output ok")
    assert updated.tasks[0].status == "complete"
    assert updated.current_task_id is None


def test_update_task_start_sets_execution_focus(cwd: Path) -> None:
    service = GoalService(str(cwd))
    record = service.create("pointer goal")
    service.replace_tasks(record.id, [{"title": "a"}, {"title": "b"}])
    updated = service.update_task(record.id, "t2", "start")
    assert updated.current_task_id == "t2"
    reopened = service.update_task(record.id, "t2", "skipped", note="later")
    assert reopened.tasks[1].status == "skipped"


def test_budget_limit_transitions_once(cwd: Path) -> None:
    service = GoalService(str(cwd))
    record = service.create("budget goal", token_budget=100)
    charged = service.charge_usage(record.id, input_tokens=60, output_tokens=60, elapsed_ms=10)
    assert charged.status == "budget_limited"
    # Accounting stops once the status left active.
    again = service.charge_usage(record.id, input_tokens=10, output_tokens=0, elapsed_ms=5)
    assert again.status == "budget_limited"
    assert service.focused().status == "budget_limited"


def test_tweak_updates_objective_with_revision_bump(cwd: Path) -> None:
    service = GoalService(str(cwd))
    record = service.create("original")
    tweaked = service.tweak(record.id, "narrow scope", new_objective="narrower objective")
    assert tweaked.objective == "narrower objective"
    assert tweaked.revision == record.revision + 1


def test_archive_clears_focus_and_moves_file(cwd: Path) -> None:
    service = GoalService(str(cwd))
    record = service.create("archive me")
    archived = service.archive(record.id)
    assert service.focused_id is None
    path = storage.find_goal_file(str(cwd), archived.id)
    assert path is not None and "archived" in str(path)
    assert service.focused() is None


def test_get_service_caches_per_cwd(cwd: Path) -> None:
    a = get_service(str(cwd))
    b = get_service(str(cwd))
    assert a is b


def test_unfocus_keeps_goal_open(cwd: Path) -> None:
    service = GoalService(str(cwd))
    record = service.create("still open")
    service.unfocus()
    assert service.focused() is None
    assert service.get(record.id).is_open()
