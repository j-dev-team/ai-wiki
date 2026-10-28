"""Merge-candidate detection blocks near-duplicate creates until resolved."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from ai_wiki import dedup
from ai_wiki.cli import cli
from ai_wiki.storage import save_article

E5 = "intfloat/multilingual-e5-small"
RICH_CONTENT = {
    "type": "technology",
    "what": "dedupwidget is a synthetic subject used to exercise merge-candidate detection end to end.",
    "facts": [
        "dedupwidget compares a new document against existing titles and embedded summaries.",
        "Every flagged candidate must be patched or declared distinct with a written reason.",
        "The declared reason is stored on the new document so later reviews can audit it.",
    ],
    "use_cases": ["Duplicate prevention", "Merge review", "Audit trail"],
    "limitations": ["Synthetic test data only", "No external model is called"],
    "best_practices": ["Search before create", "Patch the matching document"],
}


def _doc(title: str, *, category: str = "business/ecommerce", what: str = "") -> SimpleNamespace:
    return SimpleNamespace(id="new-doc", title=title, category=category,
                           content={"type": "concept", "what": what})


def _hit(document_id: str, similarity: float) -> dict:
    return {"id": document_id, "vector_similarity": similarity,
            "matched_chunks": [{"path": "/content/data/what", "text": "matched evidence"}]}


def test_title_similarity_ignores_spacing_and_punctuation():
    assert dedup.title_similarity("파이썬 패키지 배포 절차", "파이썬패키지 배포절차!") == 1.0
    assert dedup.title_similarity("세로영상 제작방법", "세로 영상 제작 방법") == 1.0
    assert dedup.title_similarity("파이썬 테스트 도구", "주방 수납 정리법") < 0.2


def test_candidate_query_uses_title_and_summary_from_either_content_shape():
    flat = _doc("제목", what="한 줄 요약")
    nested = SimpleNamespace(title="제목", content={"type": "concept", "data": {"summary": "중첩 요약"}})
    assert dedup.candidate_query(flat) == "제목 한 줄 요약"
    assert dedup.candidate_query(nested) == "제목 중첩 요약"


def test_levels_from_vector_and_title(wiki_index, sample_article):
    save_article(sample_article)
    wiki_index.upsert(sample_article)
    target = sample_article.id

    def search(score):
        return lambda query, limit: [_hit(target, score)]

    assert dedup.find_merge_candidates(_doc("Unrelated"), wiki_index, vector_search=search(0.88),
                                       model_name=E5)["status"] == "clear"
    review = dedup.find_merge_candidates(_doc("Unrelated"), wiki_index, vector_search=search(0.92),
                                         model_name=E5)
    assert review["status"] == "review"
    assert review["candidates"][0]["id"] == target
    assert review["candidates"][0]["matched_text"] == "matched evidence"
    assert dedup.find_merge_candidates(_doc("Unrelated"), wiki_index, vector_search=search(0.96),
                                       model_name=E5)["status"] == "duplicate"
    same_title = dedup.find_merge_candidates(_doc(sample_article.title), wiki_index,
                                             vector_search=search(0.5), model_name=E5)
    assert same_title["status"] == "duplicate"


def test_unmeasured_model_and_unavailable_vectors_fall_back_to_titles(wiki_index, sample_article):
    save_article(sample_article)
    wiki_index.upsert(sample_article)
    other_model = dedup.find_merge_candidates(
        _doc("Unrelated"), wiki_index, vector_search=lambda q, n: [_hit(sample_article.id, 0.99)],
        model_name="some/other-model",
    )
    assert other_model["status"] == "clear"
    assert other_model["vector_status"] == "uncalibrated_model"

    def broken(query, limit):
        raise RuntimeError("model missing")

    degraded = dedup.find_merge_candidates(_doc(sample_article.title), wiki_index,
                                           vector_search=broken, model_name=E5)
    assert degraded["vector_status"] == "unavailable"
    assert degraded["status"] == "duplicate"


def test_distinct_from_requires_reason_and_covers_flagged_ids():
    with pytest.raises(ValueError, match="needs a reason"):
        dedup.normalize_distinct_from([{"id": "a", "reason": " "}])
    report = {"candidates": [{"id": "a", "level": "review"}, {"id": "b", "level": "clear"}]}
    assert [item["id"] for item in dedup.unacknowledged(report, [])] == ["a"]
    assert dedup.unacknowledged(report, [{"id": "a", "reason": "different market"}]) == []


def _run(args, wiki_root):
    return CliRunner().invoke(cli, args, env={"AI_WIKI_ROOT": str(wiki_root)})


def test_create_requires_patch_or_distinct_from_for_vector_match(wiki_root, wiki_index, sample_article, tmp_path):
    save_article(sample_article)
    wiki_index.upsert(sample_article)
    payload = {
        "title": "Completely Reworded Topic", "category": "technology/testing",
        "confidence": 0.9, "content": RICH_CONTENT,
        "sources": [{"id": "src-1", "url": "https://example.com/dedup"}],
    }
    document = tmp_path / "document.json"
    document.write_text(json.dumps(payload), encoding="utf-8")
    fake = lambda query, limit: [_hit(sample_article.id, 0.93)]  # noqa: E731

    with patch("ai_wiki.dedup._default_vector_search", fake), \
         patch("ai_wiki.vector._MODEL_NAME", E5):
        blocked = _run(["create", "--document-file", str(document), "--dry-run"], wiki_root)
        error = json.loads(blocked.output)["error"]
        assert blocked.exit_code == 1
        assert error["code"] == "duplicate_conflict"
        assert error["details"]["unresolved"] == [sample_article.id]
        assert error["details"]["candidates"][0]["level"] == "review"

        payload["distinct_from"] = [{"id": sample_article.id, "reason": "covers a different test runner"}]
        document.write_text(json.dumps(payload), encoding="utf-8")
        with patch("ai_wiki.cli._vector_upsert"):
            created = _run(["create", "--document-file", str(document)], wiki_root)
        assert created.exit_code == 0, created.output
        data = json.loads(created.output)["data"]
        assert data["merge_candidates"]["status"] == "review"
        saved = yaml.safe_load((wiki_root / data["file_path"]).read_text(encoding="utf-8"))
        assert saved["extensions"]["system_metadata"]["distinct_from"] == payload["distinct_from"]


def test_create_rejects_distinct_from_without_reason(wiki_root, tmp_path, sample_article):
    payload = {"title": "New Topic", "category": "technology/testing", "confidence": 0.9,
               "content": sample_article.content, "distinct_from": [{"id": "x"}]}
    document = tmp_path / "document.json"
    document.write_text(json.dumps(payload), encoding="utf-8")
    result = _run(["create", "--document-file", str(document), "--dry-run"], wiki_root)
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "validation_failed"


def test_skills_and_schema_document_distinct_from():
    from pathlib import Path

    from ai_wiki.schema_v2 import document_json_schema
    from ai_wiki.variant import VariantSpec, _render_skill, load_builtin_preset

    package = Path(dedup.__file__).parent
    for text in (
        (package / "skill_templates" / "SKILL.md").read_text(encoding="utf-8"),
        (package / "skill_templates" / "references" / "templates.md").read_text(encoding="utf-8"),
        (package / "skill_templates" / "references" / "protocol.md").read_text(encoding="utf-8"),
        _render_skill(VariantSpec.from_mapping(load_builtin_preset("tax"))),
    ):
        assert "distinct_from" in text and "merge_candidates" in text
    create_request = document_json_schema()["x-ai-wiki-create-request"]
    assert create_request["properties"]["distinct_from"]["items"]["required"] == ["id", "reason"]
