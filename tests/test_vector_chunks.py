from __future__ import annotations

from unittest.mock import patch

import pytest

from ai_wiki.agent_protocol import ProtocolFailure, build_context
from ai_wiki.chunking import MAX_CHUNK_CHARS, article_chunks
from ai_wiki.index import WikiIndex
from ai_wiki.models import Article
from ai_wiki.storage import get_relative_path, save_article
from ai_wiki.vector import VectorIndex, VectorSearchUnavailable


class FakeEmbeddingModel:
    def encode(self, texts, **_kwargs):
        vectors = []
        for text in texts:
            vector = [0.0] * 384
            if "후반부고유근거" in text:
                vector[0] = 1.0
            elif "다른주제" in text:
                vector[1] = 1.0
            else:
                vector[2] = 1.0
            vectors.append(vector)
        return vectors


def _article(article_id: str = "tech-long-vector-abc123") -> Article:
    article = Article(
        id=article_id,
        title="장문 벡터 검색 검증",
        category="technology/search",
        tags=["검색", "벡터"],
        confidence=0.95,
        sources=["https://example.com/vector-chunks"],
        author="test",
        content={
            "type": "technology",
            "what": "구조화 청크 검색을 검증한다.",
            "facts": [
                "앞부분 " + ("일반 설명 " * 400),
                "후반부고유근거는 장문의 끝에만 존재한다.",
            ],
            "limitations": ["격리된 테스트 데이터"],
        },
    )
    article.verification = [{
        "path": "/content/data/facts",
        "level": "verified",
        "source_ids": ["src-1"],
    }]
    return article


def test_structure_aware_chunks_preserve_paths_and_split_long_values():
    chunks = article_chunks(_article())

    long_parts = [chunk for chunk in chunks if chunk.path == "/content/data/facts/0"]
    tail = [chunk for chunk in chunks if chunk.path == "/content/data/facts/1"]

    assert len(long_parts) > 1
    assert all(len(chunk.text) <= MAX_CHUNK_CHARS for chunk in long_parts)
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)
    assert tail[0].text.startswith("후반부고유근거")


def test_chunk_vector_search_finds_long_document_tail(wiki_root):
    target = _article()
    distractor = _article("tech-vector-distractor-abc123")
    distractor.title = "다른주제 문서"
    distractor.content["facts"] = ["다른주제에 관한 설명만 존재한다."]

    with patch("ai_wiki.vector._model", FakeEmbeddingModel()):
        index = VectorIndex(wiki_root / "data" / "vectors.db")
        try:
            details = index.upsert_many([target, distractor], rebuild=True)
            results = index.search("후반부고유근거를 찾아줘", limit=2)
            unchanged = index.upsert_many([target, distractor])
        finally:
            index.close()

    assert details["embedded_chunks"] > 2
    assert results[0]["id"] == target.id
    assert results[0]["matched_chunks"][0]["path"] == "/content/data/facts/1"
    assert unchanged["updated_documents"] == 0
    assert unchanged["skipped_documents"] == 2


def test_rebuild_encoding_failure_keeps_previous_vector_index(wiki_root):
    article = _article()
    with patch("ai_wiki.vector._model", FakeEmbeddingModel()):
        index = VectorIndex(wiki_root / "data" / "vectors.db")
        try:
            index.upsert(article)
            before = index.state()
            with patch("ai_wiki.vector._encode_texts", side_effect=RuntimeError("encode failed")):
                with pytest.raises(RuntimeError, match="encode failed"):
                    index.upsert_many([article], rebuild=True)
            after = index.state()
        finally:
            index.close()

    assert after == before


def test_chunk_fts_and_context_issue_exact_tail_citation(wiki_root, wiki_index):
    article = _article()
    path = save_article(article)
    wiki_index.upsert(article, get_relative_path(path))
    with patch("ai_wiki.vector._model", FakeEmbeddingModel()):
        vector = VectorIndex(wiki_root / "data" / "vectors.db")
        try:
            vector.upsert(article)
        finally:
            vector.close()
        envelope = build_context(
            wiki_index, "후반부고유근거", max_tokens=1200, require_vector=True,
        )

    document = envelope["data"]["documents"][0]
    citation = envelope["data"]["citations"][0]
    assert document["id"] == article.id
    assert document["evidence"][0]["text"].startswith("후반부고유근거")
    assert citation["path"] == "/content/data/facts/1"
    assert citation["chunk_id"] == document["evidence"][0]["chunk_id"]
    assert envelope["meta"]["retrieval"]["vector_status"] == "ready"


def test_vector_degradation_is_visible_and_can_be_required(wiki_root, wiki_index):
    article = _article()
    path = save_article(article)
    wiki_index.upsert(article, get_relative_path(path))

    fallback = wiki_index.search("후반부고유근거")
    assert fallback[0]["id"] == article.id
    assert wiki_index.last_retrieval_status == {
        "mode": "keyword_only",
        "vector_status": "degraded",
        "vector_error": "vector_index_missing",
    }
    with pytest.raises(VectorSearchUnavailable):
        wiki_index.search("후반부고유근거", require_vector=True)
    with pytest.raises(ProtocolFailure) as failure:
        build_context(wiki_index, "후반부고유근거", require_vector=True)
    assert failure.value.code == "vector_unavailable"


def test_acceptance_calibration_is_corpus_revision_bound(wiki_root):
    article = _article()
    with patch("ai_wiki.vector._model", FakeEmbeddingModel()):
        index = VectorIndex(wiki_root / "data" / "vectors.db")
        try:
            index.upsert(article)
            calibration = index.calibrate([(0.9, 1)] * 10 + [(-0.1, 0)] * 10)
            calibrated = index.search("후반부고유근거", limit=1)[0]
            article.version += 1
            article.content["limitations"].append("새로운 제한")
            index.upsert(article)
            invalidated = index.search("후반부고유근거", limit=1)[0]
        finally:
            index.close()

    assert calibration["samples"] == 20
    assert calibrated["calibration_status"] == "calibrated"
    assert calibrated["acceptance_score"] is not None
    assert invalidated["calibration_status"] == "uncalibrated"
    assert invalidated["acceptance_score"] is None


def test_model_change_uses_atomic_sibling_rebuild(wiki_root):
    article = _article()
    db_path = wiki_root / "data" / "vectors.db"
    with patch("ai_wiki.vector._MODEL_NAME", "test-model-a"), \
         patch("ai_wiki.vector._model", FakeEmbeddingModel()):
        original = VectorIndex(db_path)
        original.upsert(article)
        original.close()

    with patch("ai_wiki.vector._MODEL_NAME", "test-model-b"), \
         patch("ai_wiki.vector._model", FakeEmbeddingModel()):
        incompatible = VectorIndex(db_path)
        assert incompatible.state()["rebuild_required"] is True
        assert incompatible.count() == 1
        incompatible.close()
        with patch("ai_wiki.vector._encode_texts", side_effect=RuntimeError("new model failed")):
            with pytest.raises(RuntimeError, match="new model failed"):
                VectorIndex.rebuild_atomic([article], db_path)

    with patch("ai_wiki.vector._MODEL_NAME", "test-model-a"), \
         patch("ai_wiki.vector._model", FakeEmbeddingModel()):
        preserved = VectorIndex(db_path)
        assert preserved.state()["rebuild_required"] is False
        assert preserved.count() == 1
        preserved.close()

    with patch("ai_wiki.vector._MODEL_NAME", "test-model-b"), \
         patch("ai_wiki.vector._model", FakeEmbeddingModel()):
        details = VectorIndex.rebuild_atomic([article], db_path)
        replaced = VectorIndex(db_path)
        assert details["documents"] == 1
        assert replaced.state()["rebuild_required"] is False
        assert replaced.count() == 1
        replaced.close()
