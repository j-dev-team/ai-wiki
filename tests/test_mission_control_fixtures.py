from __future__ import annotations

from ai_wiki.mission_contracts import MissionDocument
from tests.mission_control_fixtures import (
    VIEWPORTS,
    VISIBILITY_MATRIX,
    all_mission_kinds,
    large_run,
    run_document,
)


def test_all_mission_control_fixture_kinds_validate():
    documents = [MissionDocument.model_validate(item, strict=True) for item in all_mission_kinds()]
    assert {item.kind for item in documents} == {
        "research_report", "work_plan", "work_run", "knowledge_candidate",
    }


def test_typed_legacy_and_missing_plan_run_fixtures_validate():
    for raw in (
        run_document(handoff="typed"),
        run_document(handoff="legacy"),
        run_document(handoff="missing", plan_id="plan-does-not-exist"),
    ):
        assert MissionDocument.model_validate(raw, strict=True).kind == "work_run"


def test_visibility_matrix_is_conservative_and_complete():
    assert set(VISIBILITY_MATRIX) == {"reader", "agent", "reviewer", "owner"}
    assert "evidence.result" in VISIBILITY_MATRIX["reader"]["redacted"]
    assert VISIBILITY_MATRIX["owner"]["redacted"] == ["secrets"]


def test_required_responsive_viewports_are_pinned():
    assert VIEWPORTS == {
        "desktop": (1440, 900),
        "laptop": (1024, 768),
        "tablet": (768, 1024),
        "mobile": (390, 844),
        "small_mobile": (320, 568),
    }


def test_large_fixture_covers_planned_performance_shape():
    plan, run = large_run(task_count=100, evidence_count=1000)
    assert len(plan["payload"]["tasks"]) == 100
    assert len(run["payload"]["task_states"]) == 100
    assert len(run["evidence"]) == 1000
