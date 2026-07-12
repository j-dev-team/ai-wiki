from __future__ import annotations

from copy import deepcopy


NOW = "2026-07-12T00:00:00Z"

CRITERIA = {
    "baseline": "Baseline recorded",
    "detail": "Detail route renders",
    "mobile": "Mobile view has no horizontal overflow",
}

VISIBILITY_MATRIX = {
    "reader": {
        "visible": [
            "mission_summary", "plan_objective", "task_content",
            "criterion_status", "evidence_count", "handoff_presence",
        ],
        "redacted": [
            "evidence.result", "evidence.locator", "evidence.content_hash",
            "evidence.source_ids", "handoff.changed_files", "handoff.blockers",
            "handoff.artifacts",
        ],
    },
    "agent": {
        "visible": [
            "mission_summary", "plan_objective", "task_content",
            "criterion_status", "evidence_metadata", "evidence.result",
            "handoff.current_state", "handoff.remaining_work",
        ],
        "redacted": [
            "evidence.private_locator", "handoff.private_paths",
            "handoff.private_artifacts",
        ],
    },
    "reviewer": {"visible": ["authorized_mission_detail"], "redacted": ["secrets"]},
    "owner": {"visible": ["authorized_mission_detail"], "redacted": ["secrets"]},
}

VIEWPORTS = {
    "desktop": (1440, 900),
    "laptop": (1024, 768),
    "tablet": (768, 1024),
    "mobile": (390, 844),
    "small_mobile": (320, 568),
}


def plan_document() -> dict:
    return {
        "mission_schema_version": 1,
        "kind": "work_plan",
        "id": "plan-mission-control-fixture",
        "revision": 1,
        "status": "proposed",
        "metadata": {
            "created_at": NOW,
            "modified_at": NOW,
            "created_by": "planner",
            "namespace": "plans",
        },
        "payload": {
            "plan_id": "plan-mission-control-fixture",
            "objective": "Make Mission work independently reviewable.",
            "scope": ["Mission overview", "Mission detail"],
            "constraints": ["Read-only UI"],
            "acceptance_criteria": [
                "The exact plan revision is visible",
                "Every task links criteria to evidence",
            ],
            "tasks": [
                {
                    "id": "T0",
                    "title": "Capture baseline",
                    "instructions": "Record the immutable starting point.",
                    "dependencies": [],
                    "acceptance_criteria": [CRITERIA["baseline"]],
                    "verification": ["pytest -q"],
                    "authorization": ["read workspace"],
                    "resources": ["workspace:baseline"],
                },
                {
                    "id": "T1",
                    "title": "Build detail",
                    "instructions": "Render task content and linked proof.",
                    "dependencies": ["T0"],
                    "acceptance_criteria": [CRITERIA["detail"]],
                    "verification": ["Flask client"],
                    "authorization": ["read missions"],
                    "resources": ["templates:mission_detail"],
                },
                {
                    "id": "T2",
                    "title": "Verify mobile",
                    "instructions": "Check the smallest supported viewport.",
                    "dependencies": ["T1"],
                    "acceptance_criteria": [CRITERIA["mobile"]],
                    "verification": ["browser viewport test"],
                    "authorization": ["read UI"],
                    "resources": ["styles:mission_control"],
                },
            ],
            "approval": {"required": True, "status": "pending", "requested_at": NOW},
        },
        "evidence": [],
        "history": [],
    }


def _evidence() -> list[dict]:
    return [
        {
            "evidence_id": "E-file",
            "type": "file_change",
            "locator": "tests/mission_control_fixtures.py",
            "captured_at": NOW,
            "captured_by": "agent-1",
            "result": "Fixture added",
            "criterion_ids": [CRITERIA["baseline"]],
        },
        {
            "evidence_id": "E-test",
            "type": "test_result",
            "locator": "pytest -q tests/test_mission_control_fixtures.py",
            "captured_at": NOW,
            "captured_by": "agent-1",
            "result": "Tests passed",
            "criterion_ids": [CRITERIA["baseline"], CRITERIA["detail"]],
        },
        {
            "evidence_id": "E-private",
            "type": "command",
            "locator": "C:/private/workspace/command.log",
            "captured_at": NOW,
            "captured_by": "agent-1",
            "result": "token=secret:MISSION_TEST_TOKEN",
            "source_ids": ["private-source"],
            "criterion_ids": [CRITERIA["detail"]],
        },
    ]


def run_document(*, handoff: str = "typed", plan_id: str = "plan-mission-control-fixture") -> dict:
    if handoff == "typed":
        handoff_value = {
            "handoff_schema_version": 1,
            "current_state": "Detail is ready for review",
            "changed_files": ["src/ai_wiki/templates/mission_detail.html"],
            "remaining_work": ["Verify mobile layout"],
            "blockers": ["Independent review pending"],
            "evidence_ids": ["E-file", "E-test"],
            "artifacts": ["screenshots/mission-detail-mobile.png"],
            "recorded_by": "agent-1",
            "recorded_at": NOW,
            "next_owner": "reviewer-1",
        }
    else:
        handoff_value = {"reason": "session ended", "recorded_by": "agent-1"}
    return {
        "mission_schema_version": 1,
        "kind": "work_run",
        "id": f"run-mission-control-{handoff}",
        "revision": 4,
        "status": "running",
        "metadata": {
            "created_at": NOW,
            "modified_at": NOW,
            "created_by": "agent-1",
            "namespace": "runs",
        },
        "payload": {
            "run_id": f"run-mission-control-{handoff}",
            "plan_id": plan_id,
            "plan_revision": 1,
            "started_by": "agent-1",
            "started_at": NOW,
            "task_states": [
                {
                    "task_id": "T0",
                    "status": "completed",
                    "attempt": 1,
                    "assigned_to": "agent-1",
                    "started_at": NOW,
                    "completed_at": NOW,
                    "result": "Baseline captured",
                    "evidence_ids": ["E-file", "E-test"],
                },
                {
                    "task_id": "T1",
                    "status": "in_review",
                    "attempt": 1,
                    "assigned_to": "agent-1",
                    "started_at": NOW,
                    "result": "Awaiting review",
                    "evidence_ids": ["E-test", "E-private"],
                },
                {
                    "task_id": "T2",
                    "status": "blocked",
                    "attempt": 1,
                    "assigned_to": "agent-1",
                    "started_at": NOW,
                    "result": "Waiting for T1",
                    "evidence_ids": [],
                },
            ],
            "execution_events": [],
            "artifacts": ["screenshots/mission-detail-mobile.png"],
            "handoff": handoff_value,
        },
        "evidence": _evidence(),
        "history": [],
    }


def research_document() -> dict:
    return {
        "mission_schema_version": 1,
        "kind": "research_report",
        "id": "research-mission-control-fixture",
        "revision": 1,
        "status": "proposed",
        "metadata": {
            "created_at": NOW,
            "modified_at": NOW,
            "created_by": "researcher",
            "namespace": "artifacts",
        },
        "payload": {
            "workspace_root": "C:/fixture",
            "scope": ["Mission Control"],
            "findings": [{"id": "F1", "title": "Summary only", "detail": "Detail is absent"}],
            "recommendations": ["Add detail"],
            "sufficient": True,
        },
        "evidence": [],
        "history": [],
    }


def candidate_document() -> dict:
    evidence = {
        "evidence_id": "E-candidate",
        "type": "document",
        "locator": "mission:run-mission-control-typed",
        "captured_at": NOW,
        "captured_by": "agent-1",
        "result": "Reusable UI contract",
    }
    return {
        "mission_schema_version": 1,
        "kind": "knowledge_candidate",
        "id": "candidate-mission-control-fixture",
        "revision": 1,
        "status": "pending",
        "metadata": {
            "created_at": NOW,
            "modified_at": NOW,
            "created_by": "agent-1",
            "namespace": "artifacts",
        },
        "payload": {
            "source_run_id": "run-mission-control-typed",
            "source_task_ids": ["T1"],
            "evidence_ids": ["E-candidate"],
            "action": "create",
            "proposed_document": {"title": "Mission Control review pattern"},
            "reusable_summary": "Link criteria to evidence in review UIs.",
            "verification_status": "pending",
        },
        "evidence": [evidence],
        "history": [],
    }


def all_mission_kinds() -> list[dict]:
    return [plan_document(), run_document(), research_document(), candidate_document()]


def large_run(*, task_count: int = 100, evidence_count: int = 1000) -> tuple[dict, dict]:
    plan = plan_document()
    template = plan["payload"]["tasks"][0]
    tasks = []
    states = []
    evidence = []
    for index in range(task_count):
        criterion = f"Criterion {index}"
        task = deepcopy(template)
        task.update({"id": f"T{index}", "title": f"Task {index}", "acceptance_criteria": [criterion]})
        task["dependencies"] = [f"T{index - 1}"] if index else []
        tasks.append(task)
        linked = [f"E{offset}" for offset in range(index, evidence_count, task_count)]
        states.append({"task_id": f"T{index}", "status": "in_review", "attempt": 1,
                       "assigned_to": "agent-1", "evidence_ids": linked})
    plan["payload"]["tasks"] = tasks
    for index in range(evidence_count):
        evidence.append({
            "evidence_id": f"E{index}", "type": "test_result", "locator": f"test:{index}",
            "captured_at": NOW, "captured_by": "agent-1", "result": "passed",
            "criterion_ids": [f"Criterion {index % task_count}"],
        })
    run = run_document()
    run["payload"]["task_states"] = states
    run["payload"]["handoff"] = {}
    run["evidence"] = evidence
    return plan, run
