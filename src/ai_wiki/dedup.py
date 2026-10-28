"""Merge-candidate detection before a new document is created.

Agents tend to create a second document for a topic the wiki already covers,
usually with a reworded title. Whitespace-token title overlap misses those
(Korean particles and spacing change every token), so candidates are found by
two signals:

- character-bigram Dice similarity of titles, which tolerates spacing and
  particle changes;
- embedding similarity of ``title + summary`` against existing chunks.

A candidate at ``review`` or ``duplicate`` level blocks creation until the
agent either patches the existing document or acknowledges it explicitly with
``distinct_from`` (id + reason), which is stored on the new document.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable

# Raw cosine thresholds per embedding model. multilingual-e5 scores are
# compressed (unrelated wiki pages still reach ~0.85), so they are measured on
# a real Korean wiki: reworded duplicates scored 0.912-0.948 and related but
# distinct topics 0.853-0.896. Other models get candidates listed but no
# vector-based level until they are measured.
VECTOR_THRESHOLDS: dict[str, dict[str, float]] = {
    "intfloat/multilingual-e5-small": {"review": 0.905, "duplicate": 0.95},
}
TITLE_THRESHOLDS = {"review": 0.5, "duplicate": 0.8}
_LEVEL_ORDER = {"clear": 0, "review": 1, "duplicate": 2}
_SUMMARY_KEYS = ("what", "summary", "definition", "purpose", "error_message")


def _bigrams(text: str) -> Counter:
    compact = re.sub(r"[\W_]+", "", text.casefold())
    if len(compact) < 2:
        return Counter([compact]) if compact else Counter()
    return Counter(compact[index:index + 2] for index in range(len(compact) - 1))


def title_similarity(left: str, right: str) -> float:
    """Dice coefficient over character bigrams, ignoring spacing and punctuation."""
    a, b = _bigrams(left), _bigrams(right)
    total = sum(a.values()) + sum(b.values())
    if not total:
        return 0.0
    return round(2 * sum((a & b).values()) / total, 4)


def candidate_query(article: Any) -> str:
    """Title plus the document's one-line summary: the text the thresholds were measured on."""
    content = getattr(article, "content", None) or {}
    data = content.get("data") if isinstance(content.get("data"), dict) else content
    summary = next((str(data[key]) for key in _SUMMARY_KEYS if data.get(key)), "")
    return f"{article.title} {summary}".strip()[:600]


def _default_vector_search(query: str, limit: int) -> list[dict]:
    import contextlib
    import io

    from ai_wiki.vector import VectorIndex

    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        vector_index = VectorIndex()
        try:
            return vector_index.search(query, limit=limit, chunks_per_document=1)
        finally:
            vector_index.close()


def _level(score: float | None, thresholds: dict[str, float] | None) -> str:
    if score is None or not thresholds:
        return "clear"
    if score >= thresholds["duplicate"]:
        return "duplicate"
    if score >= thresholds["review"]:
        return "review"
    return "clear"


def find_merge_candidates(
    article: Any,
    index: Any,
    *,
    limit: int = 5,
    vector_search: Callable[[str, int], list[dict]] | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Return existing documents the new ``article`` may duplicate, with a decision level."""
    if model_name is None:
        from ai_wiki.vector import _MODEL_NAME as model_name  # noqa: N811
    vector_thresholds = VECTOR_THRESHOLDS.get(model_name)
    rows = index.conn.execute("SELECT id, title, category FROM articles_meta").fetchall()
    existing = {row["id"]: {"title": row["title"], "category": row["category"]} for row in rows}
    existing.pop(article.id, None)

    candidates: dict[str, dict[str, Any]] = {}

    def entry(document_id: str) -> dict[str, Any]:
        meta = existing[document_id]
        return candidates.setdefault(document_id, {
            "id": document_id, "title": meta["title"], "category": meta["category"],
            "same_category": meta["category"] == article.category,
            "title_similarity": title_similarity(article.title, meta["title"]),
            "vector_similarity": None, "matched_path": None, "matched_text": None,
        })

    for document_id, meta in existing.items():
        if title_similarity(article.title, meta["title"]) >= TITLE_THRESHOLDS["review"] - 0.15:
            entry(document_id)

    query = candidate_query(article)
    vector_status, vector_error = "ready", None
    try:
        hits = (vector_search or _default_vector_search)(query, limit + 1)
    except Exception as exc:  # degraded: title signal still applies
        hits, vector_status, vector_error = [], "unavailable", str(exc)[:300]
    for hit in hits:
        if hit.get("id") not in existing:
            continue
        item = entry(hit["id"])
        item["vector_similarity"] = round(float(hit["vector_similarity"]), 4)
        chunk = (hit.get("matched_chunks") or [{}])[0]
        item["matched_path"] = chunk.get("path")
        item["matched_text"] = (chunk.get("text") or "")[:240] or None
    if vector_status == "ready" and vector_thresholds is None:
        vector_status = "uncalibrated_model"

    for item in candidates.values():
        reasons = []
        title_level = _level(item["title_similarity"], TITLE_THRESHOLDS)
        vector_level = _level(item["vector_similarity"], vector_thresholds)
        if title_level != "clear":
            reasons.append(f"title_similarity {item['title_similarity']} >= {TITLE_THRESHOLDS[title_level]}")
        if vector_level != "clear":
            reasons.append(f"vector_similarity {item['vector_similarity']} >= {vector_thresholds[vector_level]}")
        item["level"] = max(title_level, vector_level, key=_LEVEL_ORDER.__getitem__)
        item["reasons"] = reasons

    ranked = sorted(
        candidates.values(),
        key=lambda item: (_LEVEL_ORDER[item["level"]], item["vector_similarity"] or 0.0, item["title_similarity"]),
        reverse=True,
    )[:limit]
    status = max((item["level"] for item in ranked), key=_LEVEL_ORDER.__getitem__, default="clear")
    return {
        "status": status,
        "query": query,
        "vector_status": vector_status,
        "vector_error": vector_error,
        "model": model_name,
        "thresholds": {"title": TITLE_THRESHOLDS, "vector": vector_thresholds},
        "candidates": ranked,
    }


def normalize_distinct_from(value: Any) -> list[dict[str, str]]:
    """Accept ``[{"id", "reason"}]``; reject entries without a non-empty reason."""
    if value in (None, []):
        return []
    if not isinstance(value, list):
        raise ValueError("distinct_from must be a list of {id, reason} objects")
    normalized = []
    for item in value:
        if not isinstance(item, dict) or not str(item.get("id") or "").strip():
            raise ValueError("each distinct_from entry needs an id")
        reason = str(item.get("reason") or "").strip()
        if not reason:
            raise ValueError(f"distinct_from entry {item['id']} needs a reason")
        normalized.append({"id": str(item["id"]).strip(), "reason": reason})
    return normalized


def unacknowledged(report: dict[str, Any], distinct_from: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Flagged candidates the agent has not explicitly declared distinct."""
    acknowledged = {item["id"] for item in distinct_from}
    return [
        item for item in report["candidates"]
        if item["level"] != "clear" and item["id"] not in acknowledged
    ]
