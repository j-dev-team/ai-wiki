from __future__ import annotations

from pathlib import Path

import json
import time

import pytest
from click.testing import CliRunner

from ai_wiki.api import AIWikiClient
from ai_wiki.cli import cli
from ai_wiki.missions import MissionStore


NOW = "2026-07-12T18:00:00Z"


def _plan() -> dict:
    return {
        "mission_schema_version": 1,
        "kind": "work_plan",
        "id": "plan-compact",
        "revision": 1,
        "status": "approved",
        "metadata": {
            "created_at": NOW,
            "modified_at": NOW,
            "created_by": "reviewer",
            "namespace": "plans",
            "source_language": "en",
        },
        "payload": {
            "plan_id": "plan-compact",
            "objective": "Execute with compact context",
            "scope": ["Mission execution reads"],
            "constraints": ["Keep the full ledger"],
            "acceptance_criteria": [
                {"id": "G1", "text": "global done", "scope": "global", "order": 0},
            ],
            "tasks": [
                {
                    "id": "T1",
                    "title": "Dependency",
                    "instructions": "Complete the dependency",
                    "dependencies": [],
                    "acceptance_criteria": [
                        {"id": "T1C1", "text": "dependency done", "scope": "task", "order": 0},
                    ],
                    "verification": ["review dependency"],
                    "authorization": ["read source"],
                    "resources": ["src/dependency.py"],
                },
                {
                    "id": "T2",
                    "title": "Next task",
                    "instructions": "Use the dependency result",
                    "dependencies": ["T1"],
                    "acceptance_criteria": [
                        {"id": "T2C1", "text": "next done", "scope": "task", "order": 0},
                    ],
                    "verification": ["pytest"],
                    "authorization": ["change source"],
                    "resources": ["src/next.py"],
                },
                {
                    "id": "T3",
                    "title": "Blocked task",
                    "instructions": "Wait for an external dependency",
                    "dependencies": ["T2"],
                    "acceptance_criteria": [
                        {"id": "T3C1", "text": "blocker cleared", "scope": "task", "order": 0},
                    ],
                    "verification": ["review blocker"],
                    "authorization": ["read state"],
                    "resources": ["external/service"],
                },
            ],
            "approval": {
                "required": True,
                "status": "approved",
                "requested_at": NOW,
                "decided_at": NOW,
                "decided_by": "reviewer",
                "note": "approved fixture",
            },
        },
        "evidence": [],
        "history": [],
    }


def _run() -> dict:
    return {
        "mission_schema_version": 1,
        "kind": "work_run",
        "id": "run-compact",
        "revision": 1,
        "status": "running",
        "metadata": {
            "created_at": NOW,
            "modified_at": NOW,
            "created_by": "agent",
            "namespace": "runs",
            "source_language": "en",
        },
        "payload": {
            "run_id": "run-compact",
            "plan_id": "plan-compact",
            "plan_revision": 1,
            "started_by": "agent",
            "started_at": NOW,
            "task_states": [
                {
                    "task_id": "T1",
                    "status": "completed",
                    "attempt": 1,
                    "assigned_to": "agent",
                    "completed_at": NOW,
                    "result": "dependency result",
                    "evidence_ids": ["ev-task"],
                },
                {"task_id": "T2", "status": "planned", "attempt": 0},
                {
                    "task_id": "T3",
                    "status": "blocked",
                    "attempt": 1,
                    "assigned_to": "agent",
                    "result": "external service unavailable",
                },
            ],
            "execution_events": [],
            "artifacts": [],
            "handoff": {
                "handoff_schema_version": 1,
                "current_state": "Dependency complete",
                "changed_files": ["src/dependency.py"],
                "remaining_work": ["Run T2"],
                "blockers": ["external service unavailable"],
                "evidence_ids": ["ev-task"],
                "artifacts": [],
                "recorded_by": "agent",
                "recorded_at": NOW,
                "next_owner": "agent",
            },
        },
        "evidence": [
            {
                "evidence_id": "ev-global",
                "type": "command",
                "locator": "C:/private/global.log",
                "captured_at": NOW,
                "captured_by": "agent",
                "result": "g" * 400,
                "criterion_ids": ["G1"],
            },
            {
                "evidence_id": "ev-task",
                "type": "test_result",
                "locator": "pytest dependency",
                "captured_at": NOW,
                "captured_by": "agent",
                "result": "passed",
                "criterion_ids": ["T1C1"],
            },
            {
                "evidence_id": "ev-unlinked",
                "type": "command",
                "locator": "command:unlinked",
                "captured_at": NOW,
                "captured_by": "agent",
                "result": "must not cover T2 until linked by its task state",
                "criterion_ids": ["T2C1"],
            },
        ],
        "history": [],
    }


@pytest.fixture
def compact_store(wiki_root: Path):
    store = MissionStore(wiki_root)
    store.create(_plan())
    store.create(_run())
    try:
        yield store
    finally:
        store.close()


def test_run_summary_is_compact_revision_pinned_and_deterministic(compact_store: MissionStore):
    before = compact_store.get("run-compact").model_dump(mode="json")
    first = compact_store.run_summary("run-compact")
    second = compact_store.run_summary("run-compact")

    assert first == second
    assert first["run_id"] == "run-compact"
    assert first["run_revision"] == 1
    assert first["pinned_plan"] == {
        "plan_id": "plan-compact",
        "plan_revision": 1,
        "status": "approved",
        "approval_status": "approved",
    }
    assert first["ready_tasks"] == ["T2"]
    assert first["task_counts"]["completed"] == 1
    assert first["criterion_counts"] == {"total": 4, "covered": 2, "missing": 2}
    assert first["blocked_tasks"][0]["task_id"] == "T3"
    assert first["handoff"]["remaining_work"] == ["Run T2"]
    assert "evidence" not in first
    assert "history" not in first
    assert "execution_events" not in first
    assert compact_store.get("run-compact").model_dump(mode="json") == before


def test_next_and_task_context_include_minimum_complete_context(compact_store: MissionStore):
    lease = compact_store.claim("run-compact", "T2", owner="agent")
    context = compact_store.next_task_context("run-compact")

    assert context["task"]["task_id"] == "T2"
    assert context["task"]["instructions"] == "Use the dependency result"
    assert context["task"]["dependencies"] == [{
        "task_id": "T1",
        "status": "completed",
        "result": {"text": "dependency result", "truncated": False},
        "evidence_ids": ["ev-task"],
    }]
    assert context["task"]["criteria"][0]["criterion_id"] == "T2C1"
    assert context["task"]["criteria"][0]["covered"] is False
    assert context["task"]["criteria"][0]["evidence_ids"] == []
    assert context["task"]["lease"]["owner"] == lease["owner"]
    assert context["handoff"]["current_state"] == "Dependency complete"


def test_criterion_evidence_filters_and_marks_truncation(compact_store: MissionStore):
    result = compact_store.criterion_evidence("run-compact", "global done")

    assert result["criterion"]["criterion_id"] == "G1"
    assert result["criterion"]["covered"] is True
    assert [item["evidence_id"] for item in result["evidence"]] == ["ev-global"]
    assert result["evidence"][0]["result"]["truncated"] is True
    assert len(result["evidence"][0]["result"]["text"]) == 320
    assert compact_store.criterion_evidence("run-compact", "T2C1")["evidence"] == []


def test_compact_read_errors_are_explicit(compact_store: MissionStore):
    with pytest.raises(ValueError, match="run_not_found"):
        compact_store.run_summary("missing")
    with pytest.raises(ValueError, match="task_not_found"):
        compact_store.task_context("run-compact", "missing")
    with pytest.raises(ValueError, match="criterion_not_found"):
        compact_store.criterion_evidence("run-compact", "missing")


def _invoke(wiki_root: Path, *args: str):
    result = CliRunner().invoke(cli, list(args), env={"AI_WIKI_ROOT": str(wiki_root)})
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_cli_compact_commands_and_full_fallback(wiki_root: Path, compact_store: MissionStore):
    status = _invoke(wiki_root, "run", "status", "run-compact")
    summary = _invoke(wiki_root, "run", "summary", "run-compact")
    next_task = _invoke(wiki_root, "run", "next", "run-compact")
    task = _invoke(wiki_root, "task", "context", "run-compact", "T2")
    evidence = _invoke(
        wiki_root, "run", "evidence", "run-compact", "--criterion", "G1",
    )
    full = _invoke(wiki_root, "run", "status", "run-compact", "--full")

    assert status["data"]["run"] == summary["data"]["run"]
    assert next_task["data"]["run"]["task"]["task_id"] == "T2"
    assert task["data"]["run"]["task"]["task_id"] == "T2"
    assert [item["evidence_id"] for item in evidence["data"]["run"]["evidence"]] == [
        "ev-global",
    ]
    assert full["data"]["mission"]["id"] == "run-compact"
    assert full["data"]["mission"]["evidence"][0]["result"] == "g" * 400
    assert full["data"]["ready_tasks"] == ["T2"]


def test_client_compact_reads_match_store_and_capabilities(
    wiki_root: Path, compact_store: MissionStore,
):
    with AIWikiClient(wiki_root) as client:
        assert client.mission_run_summary("run-compact")["data"]["run"]["run_revision"] == 1
        assert client.mission_run_next("run-compact")["data"]["run"]["task"]["task_id"] == "T2"
        assert client.mission_task_context("run-compact", "T2")["data"]["run"]["task"][
            "task_id"
        ] == "T2"
        assert client.mission_run_evidence("run-compact", "G1")["data"]["run"][
            "criterion"
        ]["criterion_id"] == "G1"
        contract = client.capabilities()["data"]["commands"]["mission"]["execution_reads"]

    assert contract["status_default"] == "compact"
    assert contract["full_ledger"] == "run status --full"
    cli_contract = _invoke(wiki_root, "capabilities")["data"]["commands"]["mission"][
        "execution_reads"
    ]
    assert cli_contract == contract


def test_strict_local_compact_reads_authorize_before_data_and_redact(
    wiki_root: Path, compact_store: MissionStore,
):
    (wiki_root / ".ai-wiki.yaml").write_text(
        """security:
  mode: strict-local
  secret_policy: references-only
  principals:
    - id: owner
      roles: [owner]
    - id: agent
      roles: [agent]
    - id: reader
      roles: [reader]
""",
        encoding="utf-8",
    )
    denied = CliRunner().invoke(
        cli, ["run", "summary", "run-compact"],
        env={"AI_WIKI_ROOT": str(wiki_root)},
    )
    assert denied.exit_code != 0
    assert '"data": null' in denied.output
    assert '"run_id": "run-compact"' not in denied.output

    reader = _invoke(
        wiki_root, "run", "evidence", "run-compact", "--criterion", "G1",
        "--principal", "reader",
    )["data"]["run"]
    assert reader["evidence"][0]["locator"] == "[redacted]"
    assert reader["evidence"][0]["result"] == "[redacted]"
    assert reader["policy"]["effect"] == "redact"

    agent = _invoke(
        wiki_root, "run", "evidence", "run-compact", "--criterion", "G1",
        "--principal", "agent",
    )["data"]["run"]
    assert agent["evidence"][0]["locator"] == "[redacted]"
    assert agent["evidence"][0]["result"]["text"]


def test_mission_skill_template_and_installed_copy_use_compact_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    import ai_wiki.cli as cli_module

    template = (
        Path(cli_module.__file__).parent / "mission_skill_templates" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "ai-wiki run next <run-id>" in template
    assert "ai-wiki task context <run-id> <task-id>" in template
    assert "ai-wiki run evidence <run-id> --criterion <criterion-id>" in template
    assert "ai-wiki run status <run-id> --full" in template
    assert "Do not call every compact command" in template
    assert "claim the lease with `run_revision`" in template
    assert "not the submitting agent" in template

    monkeypatch.setitem(
        cli_module._AGENT_SKILL_PATHS, "codex", lambda name: tmp_path / name,
    )
    cli_module._install_skills_for_agents(["codex"], "sample-wiki", [])
    installed = (tmp_path / "sample-wiki-missions" / "SKILL.md").read_text(encoding="utf-8")
    assert installed == template


def test_large_run_summary_has_constant_queries_and_small_projection(wiki_root: Path):
    plan = _plan()
    plan["id"] = plan["payload"]["plan_id"] = "plan-large"
    plan["payload"]["tasks"] = []
    states = []
    evidence = []
    for index in range(100):
        task_id = f"T{index + 1}"
        criterion_id = f"{task_id}C1"
        plan["payload"]["tasks"].append({
            "id": task_id,
            "title": f"Task {index + 1}",
            "instructions": f"Execute task {index + 1}",
            "dependencies": [f"T{index}"] if index else [],
            "acceptance_criteria": [{
                "id": criterion_id,
                "text": f"Task {index + 1} complete",
                "scope": "task",
                "order": 0,
            }],
            "verification": ["verify"],
            "authorization": [],
            "resources": [],
        })
        evidence_id = f"ev-task-{index + 1}"
        completed = index < 99
        states.append({
            "task_id": task_id,
            "status": "completed" if completed else "planned",
            "attempt": 1 if completed else 0,
            "result": "done" if completed else "",
            "evidence_ids": [evidence_id] if completed else [],
        })
        if completed:
            evidence.append({
                "evidence_id": evidence_id,
                "type": "test_result",
                "locator": f"test:{task_id}",
                "captured_at": NOW,
                "captured_by": "agent",
                "result": "passed " + ("x" * 500),
                "criterion_ids": [criterion_id],
            })
    for index in range(901):
        evidence.append({
            "evidence_id": f"ev-global-{index}",
            "type": "command",
            "locator": f"command:{index}",
            "captured_at": NOW,
            "captured_by": "agent",
            "result": "output " + ("y" * 500),
            "criterion_ids": ["G1"],
        })
    run = _run()
    run["id"] = run["payload"]["run_id"] = "run-large"
    run["payload"]["plan_id"] = "plan-large"
    run["payload"]["task_states"] = states
    run["payload"]["handoff"] = {}
    run["evidence"] = evidence

    store = MissionStore(wiki_root)
    try:
        store.create(plan)
        store.create(run)
        selects: list[str] = []
        store.conn.set_trace_callback(
            lambda statement: selects.append(statement)
            if statement.lstrip().upper().startswith("SELECT") else None
        )
        started = time.perf_counter()
        summary = store.run_summary("run-large")
        elapsed = time.perf_counter() - started
        store.conn.set_trace_callback(None)
        full = store.get("run-large").model_dump(mode="json")
    finally:
        store.close()

    compact_bytes = len(json.dumps(summary, ensure_ascii=False).encode("utf-8"))
    full_bytes = len(json.dumps(full, ensure_ascii=False).encode("utf-8"))
    assert summary["ready_tasks"] == ["T100"]
    assert summary["evidence_count"] == 1000
    assert len(selects) <= 3
    assert compact_bytes < full_bytes * 0.05
    assert elapsed < 5.0
