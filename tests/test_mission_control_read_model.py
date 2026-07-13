from __future__ import annotations

from copy import deepcopy

import pytest
import yaml

from ai_wiki.mission_contracts import MissionDocument
from ai_wiki.missions import MissionControlReader, MissionStore
from ai_wiki.policy import Principal, SecurityPolicy
from tests.mission_control_fixtures import CRITERIA, plan_document, run_document


def _principal(role: str) -> Principal:
    return Principal(f"{role}-1", frozenset({role}))


def _seed_run(store: MissionStore):
    plan = plan_document()
    run = run_document()
    store.create(plan)
    store.create(run)
    return plan, run


def test_run_detail_joins_exact_pinned_plan_revision_and_derives_counts(wiki_root):
    store = MissionStore(wiki_root)
    try:
        plan, run = _seed_run(store)
        newer = deepcopy(plan)
        newer["revision"] = 2
        newer["payload"]["tasks"][1]["title"] = "Title from a newer plan"
        newer["payload"]["tasks"][1]["instructions"] = "Do not leak this instruction"
        newer["payload"]["acceptance_criteria"][0] = "Do not leak this newer global criterion"
        store._write(MissionDocument.model_validate(newer, strict=True))

        run_with_global_evidence = deepcopy(run)
        run_with_global_evidence["evidence"][0]["criterion_ids"].append(
            plan["payload"]["acceptance_criteria"][0]
        )
        store._write(MissionDocument.model_validate(run_with_global_evidence, strict=True))

        detail = MissionControlReader(
            store, SecurityPolicy(wiki_root), _principal("reviewer"),
        ).detail(run["id"])

        assert detail["plan"]["id"] == plan["id"]
        assert detail["plan"]["revision"] == 1
        assert len(detail["plan"]["global_criteria"]) == 2
        assert detail["plan"]["global_criteria"][0]["text"] == "The exact plan revision is visible"
        assert detail["plan"]["global_criteria"][0]["coverage_status"] == "evidence_attached"
        assert detail["plan"]["global_criteria"][0]["evidence_ids"] == ["E-file"]
        assert detail["plan"]["global_criteria"][1]["coverage_status"] == "missing"
        task = next(item for item in detail["tasks"] if item["id"] == "T1")
        assert task["title"] == "Build detail"
        assert task["instructions"] == "Render task content and linked proof."
        assert task["dependencies"] == ["T0"]
        assert task["verification"] == ["Flask client"]
        assert task["criteria"][0]["text"] == CRITERIA["detail"]
        assert detail["task_counts"] == {
            "completed": 1, "in_review": 1, "blocked": 1, "ready": 0,
            "total": 3, "in_progress": 0, "skipped": 0,
        }
        assert detail["criterion_counts"]["total"] == 5
        assert detail["criterion_counts"]["covered"] == 1
        assert detail["criterion_counts"]["evidence_attached"] == 1
        assert detail["criterion_counts"]["pending_review"] == 1
        assert detail["criterion_counts"]["missing"] == 2
        assert detail["evidence_count"] == 3
        assert detail["handoff_count"] == 1
        assert detail["control"] == {
            "run_id": run["id"],
            "run_revision": 4,
            "run_status": "running",
            "pinned_plan_id": plan["id"],
            "pinned_plan_revision": 1,
            "approval_status": "pending",
            "next_task": {
                "id": "T1", "title": "Build detail", "state": "in_review",
                "instruction": "Render task content and linked proof.",
                "reason": "in_review",
            },
            "blocked_task_ids": ["T2"],
            "handoff_present": True,
            "audit_href": f"/missions/{run['id']}?revision=4",
        }
    finally:
        store.close()


@pytest.mark.parametrize("corrupt", [False, True])
def test_missing_or_corrupt_pinned_plan_is_explicit_and_actionable(wiki_root, corrupt):
    store = MissionStore(wiki_root)
    try:
        if corrupt:
            plan, run = _seed_run(store)
            path = store._path(MissionDocument.model_validate(plan, strict=True))
            path.write_text("not: a valid Mission", encoding="utf-8")
        else:
            run = run_document(plan_id="missing-plan")
            store.create(run)
        detail = MissionControlReader(
            store, SecurityPolicy(wiki_root), _principal("reviewer"),
        ).detail(run["id"])
        code = "corrupt_pinned_plan" if corrupt else "missing_pinned_plan"
        assert detail["degraded"][0]["code"] == code
        assert "recovery" in detail["degraded"][0]
        assert "newer plan" in detail["degraded"][0]["recovery"].lower() or corrupt
        assert detail["summary"]["objective"] == "Pinned plan unavailable"
    finally:
        store.close()


def test_role_matrix_redacts_reader_agent_and_all_secrets(wiki_root):
    store = MissionStore(wiki_root)
    try:
        plan, run = _seed_run(store)
        revised = deepcopy(run)
        revised["evidence"][0]["criterion_ids"].append(
            plan["payload"]["acceptance_criteria"][0]
        )
        store._write(MissionDocument.model_validate(revised, strict=True))
        policy = SecurityPolicy(wiki_root)
        reader = MissionControlReader(store, policy, _principal("reader")).detail(run["id"])
        agent = MissionControlReader(store, policy, _principal("agent")).detail(run["id"])
        reviewer = MissionControlReader(store, policy, _principal("reviewer")).detail(run["id"])
        owner = MissionControlReader(store, policy, _principal("owner")).detail(run["id"])

        assert reader["policy"]["visibility"] == "reader"
        assert all(item["locator"] == "[redacted]" for item in reader["evidence"])
        assert reader["handoff"]["changed_files"] == []
        assert reader["plan"]["global_criteria"][0]["coverage_status"] == "redacted"
        assert agent["policy"]["visibility"] == "agent"
        assert agent["evidence"][2]["locator"] == "[redacted]"
        assert agent["evidence"][0]["result"] == "Fixture added"
        assert reviewer["policy"]["visibility"] == "reviewer"
        assert owner["policy"]["visibility"] == "owner"
        assert reviewer["evidence"][2]["result"] == "[redacted]"
        assert owner["evidence"][2]["result"] == "[redacted]"
        assert owner["plan"]["global_criteria"][0]["coverage_status"] == "evidence_attached"
        assert "/evidence/2/result" in owner["policy"]["redacted_fields"]
    finally:
        store.close()


def test_strict_local_resolves_every_role_to_the_same_visibility_matrix(wiki_root):
    config = {
        "security": {
            "mode": "strict-local",
            "principals": [
                {"id": role, "roles": [role]}
                for role in ("reader", "agent", "reviewer", "owner")
            ],
        },
    }
    (wiki_root / ".ai-wiki.yaml").write_text(
        yaml.safe_dump(config), encoding="utf-8",
    )
    store = MissionStore(wiki_root)
    try:
        _, run = _seed_run(store)
        policy = SecurityPolicy(wiki_root)
        for role in ("reader", "agent", "reviewer", "owner"):
            detail = MissionControlReader(store, policy, policy.resolve(role)).detail(run["id"])
            assert detail["policy"]["visibility"] == role
        reader = MissionControlReader(store, policy, policy.resolve("reader")).detail(run["id"])
        assert reader["evidence"][0]["result"] == "[redacted]"
    finally:
        store.close()


def test_team_mode_principal_is_applied_before_view_model_serialization(wiki_root, monkeypatch):
    from flask import g
    from ai_wiki import web

    store = MissionStore(wiki_root)
    try:
        _, run = _seed_run(store)
    finally:
        store.close()
    monkeypatch.setattr(web, "_TEAM_MODE", True)
    with web.app.test_request_context("/api/missions"):
        g.principal = {"id": "team-agent", "roles": ["agent"]}
        store, reader = web._mission_reader()
        try:
            detail = reader.detail(run["id"])
        finally:
            store.close()
    assert detail["policy"]["visibility"] == "agent"
    assert detail["evidence"][2]["locator"] == "[redacted]"


def test_overview_filters_and_paginates_without_loading_documents(wiki_root, monkeypatch):
    store = MissionStore(wiki_root)
    try:
        plan, run = _seed_run(store)
        monkeypatch.setattr(store, "get", lambda *args, **kwargs: pytest.fail("N+1 get"))
        reader = MissionControlReader(store, SecurityPolicy(wiki_root), _principal("reader"))
        page = reader.list(kind="work_run", plan_id=plan["id"], limit=1, offset=0)
        assert page["total"] == 1
        assert page["has_more"] is False
        assert page["items"][0]["id"] == run["id"]
        assert "file_path" not in page["items"][0]
        assert page["items"][0]["summary"]["task_counts"]["blocked"] == 1
        assert page["items"][0]["summary"]["criterion_counts"] == {
            "total": 5, "covered": 2, "missing": 3,
        }
        assert page["items"][0]["summary"]["evidence_count"] == 3
        assert page["items"][0]["summary"]["handoff_present"] is True
        with pytest.raises(ValueError, match="limit"):
            reader.list(limit=101)
        with pytest.raises(ValueError, match="offset"):
            reader.list(offset=-1)
    finally:
        store.close()


def test_api_contract_filters_exact_revision_and_validates_input(wiki_root):
    from ai_wiki.web import app

    store = MissionStore(wiki_root)
    try:
        plan, run = _seed_run(store)
    finally:
        store.close()
    app.config.update(TESTING=True)
    client = app.test_client()

    response = client.get(f"/api/missions?kind=work_run&plan={plan['id']}&limit=1&offset=0")
    body = response.get_json()
    assert response.status_code == 200
    assert body["count"] == body["total"] == 1
    assert body["has_more"] is False
    assert body["missions"][0]["id"] == run["id"]
    assert body["missions"][0]["summary"]["task_counts"]["in_review"] == 1

    response = client.get(f"/api/missions/{run['id']}?revision=4")
    body = response.get_json()
    assert response.status_code == 200
    assert body["mission"]["revision"] == 4
    assert body["mission"]["plan"]["revision"] == 1
    assert body["meta"]["policy"]["redacted_fields"] == body["mission"]["policy"]["redacted_fields"]

    assert client.get("/api/missions?kind=invalid").status_code == 400
    assert client.get("/api/missions?status=unknown").status_code == 400
    assert client.get("/api/missions?limit=0").status_code == 400
    assert client.get("/api/missions?offset=-1").status_code == 400
    assert client.get(f"/api/missions/{run['id']}?revision=0").status_code == 400
    assert client.get("/api/missions/not-found").status_code == 404


def test_client_service_returns_stable_mission_envelopes(wiki_root):
    from ai_wiki.api import AIWikiClient

    store = MissionStore(wiki_root)
    try:
        plan, run = _seed_run(store)
    finally:
        store.close()
    with AIWikiClient(wiki_root) as client:
        listing = client.mission_list(kind="work_run", plan_id=plan["id"])
        detail = client.mission_detail(run["id"], revision=4)
    assert listing["status"] == "ok"
    assert listing["data"]["total"] == 1
    assert detail["status"] == "ok"
    assert detail["data"]["mission"]["plan"]["revision"] == 1
    assert detail["meta"]["policy"]["visibility"] == "owner"
