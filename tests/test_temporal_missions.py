from __future__ import annotations

from datetime import datetime, timezone

import pytest
import yaml
from pydantic import ValidationError

from ai_wiki.index import WikiIndex
from ai_wiki.mission_contracts import MissionDocument
from ai_wiki.missions import MissionStore
from ai_wiki.models import Article
from ai_wiki.policy import PolicyDenied, SecurityPolicy
from ai_wiki.temporal import TemporalQueries
from ai_wiki.temporal_contracts import TemporalExtension


NOW = "2026-07-12T00:00:00Z"


def _temporal_data():
    return {
        "entities": [{"id": "entity-1", "kind": "product", "name": "AI Wiki"}],
        "evidence": [{
            "id": "ev-1", "source_id": "src-1",
            "locator": {"type": "section", "value": "release"},
            "observed_at": NOW,
        }],
        "events": [{
            "id": "event-1", "event_type": "release", "occurred_at": NOW,
            "participant_ids": ["entity-1"], "description": "Version released",
            "evidence_ids": ["ev-1"],
        }],
        "claims": [{
            "id": "claim-1", "subject_id": "entity-1", "predicate": "version",
            "object": "1.0.0", "status": "current", "valid_from": NOW,
            "observed_at": NOW, "recorded_at": NOW, "evidence_ids": ["ev-1"],
        }],
        "transitions": [],
    }


def _plan(created_by="planner"):
    return {
        "mission_schema_version": 1,
        "kind": "work_plan",
        "id": "plan-1",
        "revision": 1,
        "status": "proposed",
        "metadata": {
            "created_at": NOW, "modified_at": NOW,
            "created_by": created_by, "namespace": "plans",
        },
        "payload": {
            "plan_id": "plan-1", "objective": "Implement one verified change",
            "scope": ["src"], "constraints": ["Do not modify real documents"],
            "acceptance_criteria": ["all tasks complete"],
            "tasks": [{
                "id": "T1", "title": "Change", "instructions": "Perform change",
                "dependencies": [], "acceptance_criteria": ["tests pass"],
                "verification": ["pytest"], "resources": ["src/shared.py"],
            }],
            "approval": {"required": True, "status": "pending"},
        },
        "evidence": [], "history": [],
    }


def test_temporal_contract_rejects_invalid_references_and_naive_time():
    raw = _temporal_data()
    raw["claims"][0]["subject_id"] = "missing"
    with pytest.raises(ValidationError, match="unknown claim subject"):
        TemporalExtension.model_validate(raw)

    raw = _temporal_data()
    raw["claims"][0]["recorded_at"] = "2026-07-12T00:00:00"
    with pytest.raises(ValidationError, match="timezone"):
        TemporalExtension.model_validate(raw)


def test_temporal_index_and_queries_are_derived_and_rebuild_removes_stale(wiki_root):
    article = Article(
        id="temporal-doc", title="Temporal document", category="technology",
        content={"type": "technology", "what": "Version history", "facts": ["released"]},
        sources=["https://example.com/release"], extensions={"temporal": _temporal_data()},
    )
    index = WikiIndex(wiki_root / "data" / "wiki.db")
    try:
        index.upsert(article, "articles/temporal-doc.yaml")
        query = TemporalQueries(index)
        assert query.current(article.id)[0]["object"] == "1.0.0"
        assert query.as_of(article.id, "2026-07-12T01:00:00Z")[0]["id"] == "claim-1"
        assert query.timeline(article.id)[0]["kind"] == "event"
        index.rebuild([])
        assert query.current(article.id) == []
    finally:
        index.close()


def test_mission_plan_approval_revision_pin_and_evidence_gate(wiki_root):
    store = MissionStore(wiki_root)
    try:
        store.create(_plan())
        operations = [
            {"op": "replace", "path": "/status", "value": "approved"},
            {"op": "replace", "path": "/payload/approval/status", "value": "approved"},
            {"op": "replace", "path": "/payload/approval/decided_by", "value": "planner"},
            {"op": "replace", "path": "/payload/approval/decided_at", "value": NOW},
        ]
        with pytest.raises(PermissionError, match="own plan"):
            store.patch("plan-1", operations, if_revision=1, actor="planner", roles={"owner"})

        operations[2]["value"] = "reviewer"
        approved = store.patch(
            "plan-1", operations, if_revision=1, actor="reviewer", roles={"reviewer"},
        )
        assert approved.revision == 2
        assert store.get("plan-1", revision=1).status == "proposed"

        run = store.start_run("plan-1", actor="agent-1", run_id="run-1")
        assert run.payload["plan_revision"] == 2
        assert store.get("plan-1").status == "active"
        assert store.ready_tasks("run-1") == ["T1"]
        lease = store.claim("run-1", "T1", owner="agent-1")
        assert lease["owner"] == "agent-1"
        with pytest.raises(ValueError, match="already_claimed"):
            store.claim("run-1", "T1", owner="agent-2")
    finally:
        store.close()


def test_mission_dependency_cycle_is_rejected():
    raw = _plan()
    raw["payload"]["tasks"].append({
        "id": "T2", "title": "Second", "instructions": "Second change",
        "dependencies": ["T1"], "acceptance_criteria": ["done"],
        "verification": ["review"],
    })
    raw["payload"]["tasks"][0]["dependencies"] = ["T2"]
    with pytest.raises(ValidationError, match="cycle"):
        MissionDocument.model_validate(raw, strict=True)


def test_strict_local_policy_and_secret_references(wiki_root):
    config = {
        "security": {
            "mode": "strict-local", "default_principal": "owner",
            "principals": [
                {"id": "owner", "roles": ["owner"]},
                {"id": "reader", "roles": ["reader"]},
            ],
            "secret_policy": "references-only",
        }
    }
    (wiki_root / ".ai-wiki.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    policy = SecurityPolicy(wiki_root)
    with pytest.raises(PolicyDenied, match="requires a principal"):
        policy.resolve()
    reader = policy.resolve("reader")
    with pytest.raises(PolicyDenied, match="cannot patch"):
        policy.authorize(reader, "patch", "knowledge")
    with pytest.raises(PolicyDenied, match="secret value"):
        policy.validate_secrets({"api_token": "plaintext"})
    policy.validate_secrets({"api_token": "env:AI_WIKI_TOKEN"})
    policy.validate_secrets({"token_estimation": "UTF-8 byte based"})
