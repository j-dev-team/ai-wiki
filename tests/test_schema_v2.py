from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from pydantic import ValidationError
from click.testing import CliRunner

from ai_wiki.migration import migrate_article_files, migrate_v1_to_v2
from ai_wiki.models import Article
from ai_wiki.schema_v2 import document_json_schema, validate_v2_document
from ai_wiki.storage import (
    _mark_index_pending,
    _mark_vector_pending,
    atomic_update,
    load_article,
    save_article,
)
from ai_wiki.index import WikiIndex
from ai_wiki.yaml_loader import load_yaml_text
from ai_wiki.cli import cli

FIXTURE = Path(__file__).parent / "fixtures" / "schema_v1_article.yaml"


def _legacy_fixture() -> dict:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def test_v1_golden_fixture_migrates_to_normalized_v2():
    migrated = migrate_v1_to_v2(_legacy_fixture())

    assert migrated["schema_version"] == 2
    assert migrated["metadata"]["document_version"] == 3
    assert migrated["content"]["type"] == "technology"
    assert "facts_v" not in migrated["content"]["data"]
    assert "_meta" not in migrated["content"]["data"]
    assert "_changelog" not in migrated["content"]["data"]
    assert migrated["verification"] == [{
        "path": "/content/data/facts",
        "level": "verified",
        "source_ids": ["src-1"],
        "note": "",
    }]
    assert migrated["relations"][0]["target_id"] == "test-related-def456"
    validate_v2_document(migrated)


def test_migration_is_idempotent():
    migrated = migrate_v1_to_v2(_legacy_fixture())
    assert migrate_v1_to_v2(migrated) == migrated


def test_missing_legacy_dates_are_recorded_not_hidden():
    legacy = _legacy_fixture()
    del legacy["last_verified"]
    migrated = migrate_v1_to_v2(
        legacy, now=datetime(2026, 2, 1, tzinfo=timezone.utc)
    )
    assert migrated["metadata"]["verified_at"] == "2026-02-01T00:00:00Z"
    assert migrated["extensions"]["migration"]["defaulted_fields"] == ["last_verified"]


def test_invalid_legacy_date_is_rejected():
    legacy = _legacy_fixture()
    legacy["created_at"] = "not-a-date"
    with pytest.raises(ValidationError):
        migrate_v1_to_v2(legacy)


def test_unknown_content_type_and_wrong_field_types_are_rejected():
    migrated = migrate_v1_to_v2(_legacy_fixture())
    migrated["content"]["type"] = "not-registered"
    with pytest.raises(ValidationError):
        validate_v2_document(migrated)

    migrated = migrate_v1_to_v2(_legacy_fixture())
    migrated["content"]["data"]["facts"] = "must be a list"
    with pytest.raises(ValidationError):
        validate_v2_document(migrated)


def test_duplicate_yaml_keys_are_rejected():
    with pytest.raises(yaml.constructor.ConstructorError, match="duplicate key"):
        load_yaml_text("id: first\nid: second\n")


def test_json_schema_is_versioned_and_closed():
    schema = document_json_schema()
    assert schema["properties"]["schema_version"]["const"] == 2
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["SourceRecord"]["properties"]["url"]["pattern"].startswith("^https?")
    assert "technology" in schema["$defs"]["ContentBlock"]["properties"]["type"]["enum"]
    assert schema["x-ai-wiki-content-types"]["technology"]["required"]


def test_save_writes_canonical_v2_and_roundtrips(wiki_root, sample_article):
    sample_article.content["facts_v"] = {
        "level": "verified", "sources": ["https://example.com"],
    }
    path = save_article(sample_article)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert raw["schema_version"] == 2
    assert set(raw["content"]) == {"type", "data"}
    assert "facts_v" not in raw["content"]["data"]
    assert raw["verification"][0]["source_ids"] == ["src-1"]
    assert load_article(sample_article.id).content["facts"] == ["fact 1", "fact 2"]


def test_atomic_replace_failure_preserves_existing_file(wiki_root, sample_article):
    path = save_article(sample_article)
    original = path.read_bytes()
    sample_article.title = "Changed title"

    with patch("ai_wiki.storage.os.replace", side_effect=OSError("simulated failure")):
        with pytest.raises(OSError, match="simulated failure"):
            save_article(sample_article)

    assert path.read_bytes() == original
    assert list(path.parent.glob(".*.tmp")) == []


def test_index_failure_restores_previous_yaml(wiki_root, sample_article):
    path = save_article(sample_article)
    original = path.read_bytes()
    sample_article.title = "Changed title"
    index = MagicMock()
    index.upsert.side_effect = RuntimeError("simulated DB failure")

    with pytest.raises(RuntimeError, match="simulated DB failure"):
        atomic_update(sample_article, path, index)

    assert path.read_bytes() == original


def test_pending_index_marker_is_reconciled_on_startup(wiki_root, sample_article):
    path = save_article(sample_article)
    marker = _mark_index_pending(sample_article, path)

    index = WikiIndex(wiki_root / "data" / "wiki.db")
    try:
        assert index.count() == 1
        assert not marker.exists()
    finally:
        index.close()


def test_pending_vector_marker_is_reconciled_on_startup(wiki_root, sample_article):
    path = save_article(sample_article)
    marker = _mark_vector_pending(sample_article, path)
    vector = MagicMock()

    with patch("ai_wiki.vector.VectorIndex", return_value=vector):
        index = WikiIndex(wiki_root / "data" / "wiki.db")
    try:
        vector.upsert.assert_called_once()
        assert vector.upsert.call_args.args[0].id == sample_article.id
        assert not marker.exists()
    finally:
        index.close()


def test_bulk_migration_dry_run_and_backup(tmp_path):
    articles = tmp_path / "articles" / "technology" / "testing"
    articles.mkdir(parents=True)
    target = articles / "sample.yaml"
    target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    original = target.read_bytes()

    dry = migrate_article_files(tmp_path, dry_run=True)
    assert dry["migrated"] == 1
    assert target.read_bytes() == original

    applied = migrate_article_files(tmp_path, dry_run=False, backup=True)
    assert applied["migrated"] == 1
    assert applied["failed"] == []
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["schema_version"] == 2
    backup = Path(applied["backup_dir"]) / target.relative_to(tmp_path)
    assert backup.read_bytes() == original

    second = migrate_article_files(tmp_path, dry_run=False)
    assert second["migrated"] == 0
    assert second["already_current"] == 1


def test_migrate_schema_cli_rebuilds_index(tmp_path, monkeypatch):
    articles = tmp_path / "articles" / "technology" / "testing"
    articles.mkdir(parents=True)
    (tmp_path / "data").mkdir()
    target = articles / "sample.yaml"
    target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("AI_WIKI_ROOT", str(tmp_path))

    result = CliRunner().invoke(cli, ["migrate-schema", "--apply"])

    assert result.exit_code == 0, result.output
    assert '"migrated": 1' in result.output
    loaded = load_article("test-schema-migration-abc123")
    assert loaded is not None
    assert loaded.schema_version == 2
