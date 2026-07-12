from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from ai_wiki.calibration import CalibrationManager
from ai_wiki.connectors import ConnectorManager
from ai_wiki.index import WikiIndex
from ai_wiki.models import Article
from ai_wiki.policy import SecurityPolicy
from ai_wiki.team_security import TeamSecurity

from tests.test_temporal_missions import _temporal_data


class FakeVector:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE vector_state(key TEXT PRIMARY KEY, value TEXT)")
        self.installed = None

    def state(self):
        return {
            "embedding_model": "test", "embedding_version": "1", "dimensions": "3",
            "index_revision": "chunk-v1", "calibration_run_id": "",
        }

    def search(self, query, limit=50):
        number = int(query.removeprefix("q"))
        return [{"id": f"doc-{number}", "vector_similarity": 0.9 if number % 2 == 0 else 0.1}]

    def _calibration(self):
        return None

    def install_calibration(self, a, b, *, samples, run_id):
        backup = {"calibration_run_id": "", "calibration_a": "", "calibration_b": ""}
        self.conn.execute(
            "INSERT INTO vector_state VALUES (?, ?)",
            (f"calibration_backup:{run_id}", json.dumps(backup)),
        )
        self.conn.commit()
        self.installed = (a, b, samples, run_id)
        return {"status": "production", "run_id": run_id}


def test_calibration_candidate_promote_and_rollback(wiki_root):
    wiki = WikiIndex(wiki_root / "data" / "wiki.db")
    vector = FakeVector()
    try:
        for number in range(40):
            context_id = f"ctx-{number}"
            citation = f"doc:doc-{number}#/content/data/facts/0"
            wiki.record_context(
                context_id=context_id, query=f"q{number}", document_ids=[f"doc-{number}"],
                citations=[citation], max_tokens=100, estimated_tokens=10,
            )
            wiki.record_feedback(context_id, {
                "citation": citation,
                "judgment": "accepted" if number % 2 == 0 else "rejected",
                "evidence_type": "external_eval", "evidence_reference": f"fixture:{number}",
            }, principal_id="evaluator", roles=set(), model_scope="test:1:3:chunk-v1")
        manager = CalibrationManager(wiki, vector)
        result = manager.run(thresholds={
            "minimum_labeled_samples": 20, "minimum_positive_samples": 10,
            "minimum_negative_samples": 10, "minimum_distinct_documents": 20,
        })
        assert result["status"] == "candidate"
        assert manager.promote(result["run_id"])["status"] == "production"
        assert manager.rollback(result["run_id"])["status"] == "rolled_back"
    finally:
        vector.conn.close()
        wiki.close()


def test_git_connector_snapshots_are_read_only_and_deduplicated(wiki_root, tmp_path):
    repository = tmp_path / "source-repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.com"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=repository, check=True)
    source = repository / "knowledge.txt"
    source.write_text("read-only evidence", encoding="utf-8")
    subprocess.run(["git", "add", "knowledge.txt"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=repository, check=True)

    manager = ConnectorManager(wiki_root)
    manager.add("local", "git", {"path": str(repository), "visibility": "private"})
    first = manager.sync("local")
    second = manager.sync("local")
    assert first["updated"] == 1
    assert second["skipped"] == 1
    snapshot = next((wiki_root / "sources" / "local").glob("*.json"))
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    assert payload["status"] == "pending_unverified"
    assert payload["permissions"]["visibility"] == "private"
    assert source.read_text(encoding="utf-8") == "read-only evidence"


def test_schema_v3_is_lazy_and_v2_without_temporal_stays_v2(sample_article):
    plain = sample_article.to_yaml_dict()
    assert plain["schema_version"] == 2
    sample_article.extensions["temporal"] = _temporal_data()
    temporal = sample_article.to_yaml_dict()
    assert temporal["schema_version"] == 3
    assert temporal["claims"][0]["id"] == "claim-1"
    assert "temporal" not in temporal["extensions"]


def test_policy_returns_redaction_for_protected_fields(wiki_root):
    policy = SecurityPolicy(wiki_root)
    principal = policy.resolve()
    reader = type(principal)("reader", frozenset({"reader"}))
    decision = policy.decide(reader, "read", "knowledge", {
        "extensions": {"access": {
            "visibility": "public", "field_roles": {"/content/data/private": ["owner"]},
        }}
    })
    assert decision.effect == "redact"
    assert decision.redacted_fields == ("/content/data/private",)


@pytest.mark.skipif(importlib.util.find_spec("argon2") is None, reason="team optional dependency absent")
def test_team_passwords_and_tokens_are_stored_as_hashes(wiki_root):
    security = TeamSecurity(wiki_root)
    try:
        security.create_user("owner", "correct horse battery staple", ["owner"])
        row = security.conn.execute("SELECT password_hash FROM team_users").fetchone()
        assert "correct horse" not in row[0]
        assert security.verify_password("owner", "correct horse battery staple")["id"] == "owner"
        token = security.issue_token("owner", "automation")
        assert security.verify_token(token)["id"] == "owner"
        stored = security.conn.execute("SELECT token_hash FROM api_tokens").fetchone()[0]
        assert token not in stored
    finally:
        security.close()
