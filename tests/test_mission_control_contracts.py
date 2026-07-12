from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from ai_wiki.mission_contracts import (
    Criterion,
    MissionDocument,
    MissionHandoff,
    WorkPlanPayload,
    WorkRunPayload,
    criterion_coverage_keys,
    criterion_records,
    handoff_view,
    legacy_criterion_id,
    mission_json_schema,
)
from ai_wiki.missions import MissionStore
from tests.mission_control_fixtures import CRITERIA, plan_document, run_document


def test_legacy_criteria_keep_source_shape_and_get_stable_display_ids():
    raw = plan_document()["payload"]
    plan = WorkPlanPayload.model_validate(raw, strict=True)
    dumped = plan.model_dump(mode="json")
    assert dumped["tasks"][0]["acceptance_criteria"] == [CRITERIA["baseline"]]

    first = criterion_records(
        plan.tasks[0].acceptance_criteria,
        scope="task",
        owner_id=f"{plan.plan_id}:T0",
    )[0]
    second = criterion_records(
        plan.tasks[0].acceptance_criteria,
        scope="task",
        owner_id=f"{plan.plan_id}:T0",
    )[0]
    assert first.id == second.id == legacy_criterion_id(
        CRITERIA["baseline"], scope="task", owner_id=f"{plan.plan_id}:T0",
    )
    assert criterion_coverage_keys(first) >= {first.id, first.text}


def test_explicit_criterion_roundtrips_with_stable_identity():
    raw = plan_document()["payload"]
    raw["tasks"][0]["acceptance_criteria"] = [{
        "id": "criterion-baseline",
        "text": CRITERIA["baseline"],
        "scope": "task",
        "order": 4,
        "legacy_aliases": ["Old baseline wording"],
    }]
    plan = WorkPlanPayload.model_validate(raw, strict=True)
    criterion = plan.tasks[0].acceptance_criteria[0]
    assert isinstance(criterion, Criterion)
    assert criterion.id == "criterion-baseline"
    assert plan.model_dump(mode="json")["tasks"][0]["acceptance_criteria"][0]["order"] == 4


def test_duplicate_and_wrong_scope_criterion_ids_are_rejected():
    raw = plan_document()["payload"]
    explicit = {"id": "same", "text": "One", "scope": "task"}
    raw["tasks"][0]["acceptance_criteria"] = [explicit]
    raw["tasks"][1]["acceptance_criteria"] = [{**explicit, "text": "Two"}]
    with pytest.raises(ValidationError, match="criterion IDs must be unique"):
        WorkPlanPayload.model_validate(raw, strict=True)

    raw = plan_document()["payload"]
    raw["tasks"][0]["acceptance_criteria"] = [{
        "id": "wrong-scope", "text": "Wrong", "scope": "global",
    }]
    with pytest.raises(ValidationError, match="expected task"):
        WorkPlanPayload.model_validate(raw, strict=True)


def test_completion_coverage_accepts_legacy_text_and_stable_id(tmp_path):
    store = MissionStore(tmp_path)
    try:
        plan_raw = plan_document()
        plan_raw["payload"]["tasks"] = [plan_raw["payload"]["tasks"][0]]
        plan = store.create(plan_raw)

        legacy_run = run_document()
        legacy_run["payload"]["task_states"] = [legacy_run["payload"]["task_states"][0]]
        legacy_run["payload"]["handoff"] = {}
        legacy_run["evidence"] = legacy_run["evidence"][:2]
        store._validate_completion_coverage(MissionDocument.model_validate(legacy_run, strict=True))

        plan_raw = plan_document()
        plan_raw["id"] = plan_raw["payload"]["plan_id"] = "plan-stable"
        plan_raw["payload"]["tasks"] = [plan_raw["payload"]["tasks"][0]]
        plan_raw["payload"]["tasks"][0]["acceptance_criteria"] = [{
            "id": "criterion-stable", "text": "Stable criterion", "scope": "task",
        }]
        store.create(plan_raw)
        stable_run = run_document(plan_id="plan-stable")
        stable_run["id"] = stable_run["payload"]["run_id"] = "run-stable"
        stable_run["payload"]["task_states"] = [stable_run["payload"]["task_states"][0]]
        stable_run["payload"]["task_states"][0]["evidence_ids"] = ["E-stable"]
        stable_run["payload"]["handoff"] = {}
        stable_run["evidence"] = [{
            "evidence_id": "E-stable", "type": "test_result", "locator": "pytest",
            "captured_at": "2026-07-12T00:00:00Z", "captured_by": "agent",
            "result": "passed", "criterion_ids": ["criterion-stable"],
        }]
        store._validate_completion_coverage(
            MissionDocument.model_validate(stable_run, strict=True),
        )
        assert plan.kind == "work_plan"
    finally:
        store.close()


def test_typed_handoff_validates_timezone_and_evidence_references():
    raw = run_document(handoff="typed")
    document = MissionDocument.model_validate(raw, strict=True)
    parsed = WorkRunPayload.model_validate(document.payload, strict=True)
    assert isinstance(parsed.handoff, MissionHandoff)
    assert handoff_view(parsed.handoff)["mode"] == "typed"

    naive = deepcopy(raw)
    naive["payload"]["handoff"]["recorded_at"] = "2026-07-12T00:00:00"
    with pytest.raises(ValidationError, match="timezone"):
        MissionDocument.model_validate(naive, strict=True)

    unknown = deepcopy(raw)
    unknown["payload"]["handoff"]["evidence_ids"] = ["E-does-not-exist"]
    with pytest.raises(ValidationError, match="unknown mission evidence IDs"):
        MissionDocument.model_validate(unknown, strict=True)


def test_typed_marker_cannot_fall_back_to_legacy_dictionary():
    raw = run_document(handoff="typed")
    del raw["payload"]["handoff"]["recorded_by"]
    with pytest.raises(ValidationError, match="recorded_by"):
        MissionDocument.model_validate(raw, strict=True)


def test_legacy_handoff_is_preserved_and_adapted_deterministically():
    raw = run_document(handoff="legacy")
    document = MissionDocument.model_validate(raw, strict=True)
    assert document.payload["handoff"] == {"reason": "session ended", "recorded_by": "agent-1"}
    view = handoff_view(document.payload["handoff"])
    assert view["mode"] == "legacy"
    assert view["current_state"] == "session ended"
    assert view["recorded_by"] == "agent-1"


def test_mission_schemas_expose_criterion_and_typed_handoff_contracts():
    plan_schema = mission_json_schema("work-plan")
    run_schema = mission_json_schema("work-run")
    assert "Criterion" in plan_schema["$defs"]
    assert "MissionHandoff" in run_schema["$defs"]
