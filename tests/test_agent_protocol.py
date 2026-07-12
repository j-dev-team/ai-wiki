from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from ai_wiki.agent_protocol import estimate_tokens
from ai_wiki.cli import cli
from ai_wiki.index import WikiIndex
from ai_wiki.models import Article
from ai_wiki.storage import get_relative_path, load_article_with_path, save_article


def _article(article_id: str = "tech-agent-protocol-abc123", *, title: str = "Agent Protocol",
             marker: str = "quantumwidget", sources: bool = True) -> Article:
    source_urls = ["https://example.com/reference"] if sources else []
    article = Article(
        id=article_id,
        title=title,
        category="technology/agents",
        tags=["agents", "protocol"],
        confidence=0.9 if sources else 0.5,
        sources=source_urls,
        author="test-agent",
        content={
            "type": "technology",
            "what": f"{marker} is a synthetic protocol subject used for deterministic evaluation.",
            "facts": [
                f"{marker} supports compact context retrieval with deterministic evidence.",
                "Every returned citation points to an existing document and content path.",
                "Optimistic concurrency prevents stale AI writes from overwriting newer knowledge.",
            ],
            "use_cases": ["Agent retrieval", "Safe writeback", "Evidence tracking"],
            "limitations": ["Synthetic benchmark data only", "No external model is called"],
            "best_practices": ["Use context first", "Patch with if-version"],
        },
    )
    if sources:
        article.verification = [{
            "path": "/content/data/facts",
            "level": "verified",
            "source_ids": ["src-1"],
        }]
    else:
        article.metadata["verification_status"] = "pending"
        article.verification = [{
            "path": "/content/data", "level": "unverified", "source_ids": [],
        }]
    return article


def _index_article(index: WikiIndex, article: Article) -> Path:
    path = save_article(article)
    index.upsert(article, get_relative_path(path))
    return path


def _run(args, wiki_root, input_text=None):
    return CliRunner().invoke(cli, args, input=input_text, env={"AI_WIKI_ROOT": str(wiki_root)})


def test_capabilities_protocol(wiki_root):
    result = _run(["capabilities"], wiki_root)
    data = json.loads(result.output)
    assert result.exit_code == 0
    assert set(data) == {"protocol_version", "status", "data", "meta", "error"}
    assert data["data"]["commands"]["patch"]["if_version_required"] is True


def test_get_compact_full_raw_fields_and_legacy(wiki_root, wiki_index):
    article = _article()
    path = _index_article(wiki_index, article)
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    compact = json.loads(_run(["get", article.id], wiki_root).output)
    assert compact["data"]["document"]["content"]["facts"]
    assert "history" not in compact["data"]["document"]

    projected = json.loads(_run([
        "get", article.id, "--fields", "id,title,content.what",
    ], wiki_root).output)["data"]["document"]
    assert set(projected) == {"id", "title", "content"}
    assert set(projected["content"]) == {"what"}

    full = json.loads(_run(["get", article.id, "--view", "full"], wiki_root).output)
    assert full["data"]["document"]["schema_version"] == 2
    raw = json.loads(_run(["get", article.id, "--view", "raw"], wiki_root).output)
    assert "schema_version: 2" in raw["data"]["document"]["raw_yaml"]
    legacy = json.loads(_run(["get", article.id, "--legacy"], wiki_root).output)
    assert legacy["article"]["id"] == article.id
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_context_budget_citations_and_record_use(wiki_root, wiki_index):
    article = _article()
    _index_article(wiki_index, article)

    result = _run(["context", "quantumwidget", "--max-tokens", "1200"], wiki_root)
    envelope = json.loads(result.output)
    assert result.exit_code == 0
    assert envelope["data"]["documents"][0]["id"] == article.id
    assert estimate_tokens(envelope) <= 1200
    assert envelope["meta"]["budget"]["estimated_tokens"] <= 1200
    citation = envelope["data"]["citations"][0]
    assert citation["document_id"] == article.id
    assert citation["path"].startswith("/content/data/")
    assert citation["source_ids"] == ["src-1"]
    assert envelope["data"]["documents"][0]["evidence"][0]["path"] == citation["path"]
    for issued in envelope["data"]["citations"]:
        document = next(
            item for item in envelope["data"]["documents"]
            if item["id"] == issued["document_id"]
        )
        parts = issued["path"].strip("/").split("/")
        assert parts[:2] == ["content", "data"]
        represented_key = parts[2].replace("~1", "/").replace("~0", "~")
        assert represented_key in document["content"] or any(
            evidence["path"] == issued["path"] for evidence in document["evidence"]
        )

    usage = _run([
        "record-use", envelope["data"]["context_id"],
        "--citation", citation["key"], "--outcome", "answered",
    ], wiki_root)
    assert json.loads(usage.output)["data"]["outcome"] == "answered"

    invalid = _run([
        "record-use", envelope["data"]["context_id"],
        "--citation", "doc:missing#/content/data", "--outcome", "answered",
    ], wiki_root)
    assert invalid.exit_code == 1
    assert json.loads(invalid.output)["error"]["code"] == "invalid_citation"


def test_context_includes_query_relevant_nonstandard_field(wiki_root, wiki_index):
    article = _article(marker="architecturemarker")
    article.content["architecture"] = {
        "layers": ["YAML source", "SQLite retrieval", "vector retrieval"],
    }
    article.verification.append({
        "path": "/content/data/architecture",
        "level": "verified",
        "source_ids": ["src-1"],
    })
    _index_article(wiki_index, article)

    envelope = json.loads(_run(["context", "architecture"], wiki_root).output)
    document = envelope["data"]["documents"][0]

    assert document["evidence"][0]["path"].startswith("/content/data/architecture/")
    assert document["evidence"][0]["text"] in {"YAML source", "SQLite retrieval", "vector retrieval"}
    assert any(
        item["path"] == "/content/data/architecture"
        or item["path"].startswith("/content/data/architecture/")
        for item in envelope["data"]["citations"]
    )


def test_context_excludes_pending_draft_by_default(wiki_root, wiki_index):
    draft = _article("tech-pending-draft-abc123", title="Pending Draft", marker="pendingmarker", sources=False)
    _index_article(wiki_index, draft)

    normal = json.loads(_run(["context", "pendingmarker"], wiki_root).output)
    assert normal["data"]["documents"] == []
    included = json.loads(_run([
        "context", "pendingmarker", "--include-unverified",
    ], wiki_root).output)
    assert included["data"]["documents"][0]["verification_status"] == "pending"


def test_context_is_root_isolated(wiki_root, wiki_index, tmp_path):
    article = _article()
    _index_article(wiki_index, article)
    envelope = json.loads(_run(["context", "quantumwidget"], wiki_root).output)

    other_root = tmp_path / "other"
    (other_root / "articles").mkdir(parents=True)
    (other_root / "data").mkdir()
    other = _run([
        "record-use", envelope["data"]["context_id"], "--outcome", "insufficient",
    ], other_root)
    assert other.exit_code == 1
    assert json.loads(other.output)["error"]["code"] == "context_not_found"


def test_recall_at_five_is_perfect_for_synthetic_exact_queries(wiki_root, wiki_index):
    expected = {}
    for number in range(8):
        marker = f"uniquemarker{number}"
        article = _article(
            f"tech-recall-{number}-abc123", title=f"Recall Subject {number}", marker=marker,
        )
        _index_article(wiki_index, article)
        expected[marker] = article.id
    for marker, article_id in expected.items():
        results = wiki_index.search(marker, limit=5)
        assert article_id in [item["id"] for item in results]


def test_context_interleaves_related_documents_before_limit(wiki_root, wiki_index):
    related = _article(
        "tech-related-detail-abc123", title="Related Detail", marker="zzrelationquery",
    )
    hub = _article(
        "tech-related-hub-abc123", title="zzrelationquery",
        marker=" ".join(["zzrelationquery"] * 8),
    )
    hub.related = [related.id]
    _index_article(wiki_index, related)
    _index_article(wiki_index, hub)
    for number in range(10):
        distractor = _article(
            f"tech-relation-distractor-{number}-abc123",
            title=f"Distractor {number}", marker=f"zzrelationquery distractor {number}",
        )
        _index_article(wiki_index, distractor)

    envelope = json.loads(_run([
        "context", "zzrelationquery", "--limit", "3", "--max-tokens", "4000",
    ], wiki_root).output)
    documents = envelope["data"]["documents"]

    assert hub.id in [item["id"] for item in documents]
    relation = next(item for item in documents if item["id"] == related.id)
    assert relation["selection_reason"] == f"related:{hub.id}"


def test_vector_failure_rolls_back_ai_create(wiki_root, tmp_path):
    payload = {
        "title": "Vector Rollback Subject",
        "category": "technology/agents",
        "tags": ["vector", "rollback"],
        "confidence": 0.9,
        "sources": ["https://example.com/vector-rollback"],
        "content": _article(marker="vectorrollback").content,
    }
    document = tmp_path / "vector-rollback.json"
    document.write_text(json.dumps(payload), encoding="utf-8")

    with patch("ai_wiki.cli._vector_upsert", side_effect=RuntimeError("injected vector failure")):
        result = _run(["create", "--document-file", str(document)], wiki_root)

    response = json.loads(result.output)
    assert result.exit_code == 1
    assert response["error"]["code"] == "storage_failed"
    assert list((wiki_root / "articles").rglob("*.yaml")) == []
    index = WikiIndex(wiki_root / "data" / "wiki.db")
    try:
        assert index.count() == 0
    finally:
        index.close()


def test_vector_failure_rolls_back_ai_patch(wiki_root, wiki_index, tmp_path):
    article = _article()
    path = _index_article(wiki_index, article)
    before = path.read_bytes()
    operations = tmp_path / "vector-patch.json"
    operations.write_text(json.dumps([
        {"op": "add", "path": "/content/data/facts/-", "value": "Must be rolled back"},
    ]), encoding="utf-8")

    with patch(
        "ai_wiki.cli._vector_upsert",
        side_effect=[RuntimeError("injected vector failure"), None],
    ):
        result = _run([
            "patch", article.id, "--operations-file", str(operations), "--if-version", "1",
        ], wiki_root)

    response = json.loads(result.output)
    assert result.exit_code == 1
    assert response["error"]["code"] == "storage_failed"
    assert path.read_bytes() == before
    restored = load_article_with_path(article.id)[0]
    assert restored is not None
    assert restored.version == 1
    assert "Must be rolled back" not in restored.content["facts"]


def test_patch_dry_run_apply_conflict_and_protected_field(wiki_root, wiki_index, tmp_path):
    article = _article()
    path = _index_article(wiki_index, article)
    before = path.read_bytes()
    operations = tmp_path / "patch.json"
    operations.write_text(json.dumps([
        {"op": "test", "path": "/metadata/document_version", "value": 1},
        {"op": "add", "path": "/content/data/facts/-", "value": "A fourth verified synthetic fact."},
    ]), encoding="utf-8")

    dry = _run([
        "patch", article.id, "--operations-file", str(operations),
        "--if-version", "1", "--dry-run",
    ], wiki_root)
    assert dry.exit_code == 0
    assert path.read_bytes() == before

    with patch("ai_wiki.cli._vector_upsert"):
        applied = _run([
            "patch", article.id, "--operations-file", str(operations), "--if-version", "1",
        ], wiki_root)
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)["data"]["version"] == 2
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["schema_version"] == 2

    stale = _run([
        "patch", article.id, "--operations-file", str(operations), "--if-version", "1",
    ], wiki_root)
    assert stale.exit_code == 1
    assert json.loads(stale.output)["error"]["code"] == "version_conflict"

    protected = tmp_path / "protected.json"
    protected.write_text(json.dumps([
        {"op": "replace", "path": "/id", "value": "changed"},
    ]), encoding="utf-8")
    rejected = _run([
        "patch", article.id, "--operations-file", str(protected), "--if-version", "2",
    ], wiki_root)
    assert json.loads(rejected.output)["error"]["code"] == "protected_field"

    protected.write_text(json.dumps([
        {"op": "replace", "path": "/metadata", "value": {}},
    ]), encoding="utf-8")
    ancestor = _run([
        "patch", article.id, "--operations-file", str(protected), "--if-version", "2",
    ], wiki_root)
    assert json.loads(ancestor.output)["error"]["code"] == "protected_field"


def test_patch_changes_only_touched_legacy_file(wiki_root, wiki_index, tmp_path):
    now = "2026-01-01T00:00:00Z"
    legacy = _article("tech-legacy-one-abc123", title="Legacy One", marker="legacymarker")
    raw = legacy.meta_dict()
    raw["content"] = legacy.content
    raw.update({"created_at": now, "last_modified": now, "last_verified": now})
    first = wiki_root / "articles" / "technology" / "agents" / "legacy-one.yaml"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    wiki_index.upsert(Article.from_yaml(raw), get_relative_path(first))

    untouched = wiki_root / "articles" / "untouched.yaml"
    untouched.write_text(yaml.safe_dump({**raw, "id": "tech-legacy-two-abc123", "title": "Legacy Two"}), encoding="utf-8")
    untouched_hash = hashlib.sha256(untouched.read_bytes()).hexdigest()
    operations = tmp_path / "legacy-patch.json"
    operations.write_text(json.dumps([
        {"op": "add", "path": "/content/data/facts/-", "value": "Legacy touched fact"},
    ]), encoding="utf-8")
    with patch("ai_wiki.cli._vector_upsert"):
        result = _run([
            "patch", legacy.id, "--operations-file", str(operations), "--if-version", "1",
        ], wiki_root)
    assert result.exit_code == 0, result.output
    _, migrated_path = load_article_with_path(legacy.id)
    assert migrated_path is not None
    assert yaml.safe_load(migrated_path.read_text(encoding="utf-8"))["schema_version"] == 2
    assert "schema_version" not in yaml.safe_load(untouched.read_text(encoding="utf-8"))
    assert hashlib.sha256(untouched.read_bytes()).hexdigest() == untouched_hash


def test_legacy_view_preserves_custom_content_and_records_normalization():
    raw = {
        "id": "legacy-custom-abc123",
        "title": "Legacy Custom",
        "category": "custom",
        "confidence": 0.8,
        "version": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "last_modified": "2026-01-02T00:00:00Z",
        "last_verified": "2026-01-02T00:00:00Z",
        "sources": ["internal memo without a URL"],
        "author": "legacy-agent",
        "content": {
            "type": "customer_record",
            "when_to_use": "free-form legacy value",
            "facts": ["Preserve this field"],
            "_meta": {"maturity": "authoritative"},
            "_v": {"level": "mixed", "sources": [{"name": "legacy"}]},
            "_changelog": [{"date": "unknown", "action": "created"}],
        },
    }

    canonical = Article.from_yaml(raw).to_yaml_dict()

    assert canonical["content"]["type"] == "legacy"
    assert canonical["content"]["data"]["original_type"] == "customer_record"
    assert canonical["content"]["data"]["facts"] == ["Preserve this field"]
    assert canonical["metadata"]["maturity"] == "mature"
    assert canonical["verification"][0]["level"] == "disputed"
    normalization = canonical["extensions"]["legacy_normalization"]
    assert normalization["invalid_sources"] == ["internal memo without a URL"]
    assert normalization["invalid_history"][0]["at"] == "unknown"


def test_ai_document_create_draft_apply_and_duplicate(wiki_root, wiki_index, tmp_path):
    payload = {
        "title": "Autonomous Draft",
        "category": "technology/agents",
        "tags": ["agents", "draft"],
        "confidence": 0.9,
        "content": _article(marker="draftcreation").content,
    }
    document = tmp_path / "document.json"
    document.write_text(json.dumps(payload), encoding="utf-8")

    dry = _run(["create", "--document-file", str(document), "--dry-run"], wiki_root)
    dry_data = json.loads(dry.output)
    assert dry.exit_code == 0, dry.output
    assert dry_data["data"]["verification_status"] == "pending"
    assert dry_data["data"]["document"]["confidence"] == 0.5
    assert not list((wiki_root / "articles").rglob("*.yaml"))

    with patch("ai_wiki.cli._vector_upsert"):
        created = _run(["create", "--document-file", str(document)], wiki_root)
    assert created.exit_code == 0, created.output
    created_data = json.loads(created.output)["data"]
    assert Path(wiki_root / created_data["file_path"]).exists()

    duplicate = _run(["create", "--document-file", str(document)], wiki_root)
    assert duplicate.exit_code == 1
    assert json.loads(duplicate.output)["error"]["code"] == "duplicate_conflict"
