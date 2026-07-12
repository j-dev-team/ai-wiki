"""Stable, model-neutral protocol helpers for AI Wiki agents."""
from __future__ import annotations

import copy
import json
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_wiki.models import Article

PROTOCOL_VERSION = "1.5"
DEFAULT_CONTEXT_TOKENS = 4000
MIN_CONTEXT_TOKENS = 256
MAX_CONTEXT_TOKENS = 100_000

COMPACT_CONTENT_PRIORITY = (
    "what", "summary", "definition", "answer", "facts", "key_principles",
    "solution", "key_provisions", "steps", "use_cases", "applications",
    "limitations", "caveats", "best_practices", "conclusion", "status",
)
PROTECTED_PATCH_PATHS = {
    "/schema_version", "/id", "/metadata/document_version",
    "/metadata/created_at",
}


class ProtocolFailure(ValueError):
    def __init__(self, code: str, message: str, *, details: Any = None,
                 retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.retryable = retryable


def success(data: Any, *, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "status": "ok",
        "data": data,
        "meta": meta or {},
        "error": None,
    }


def failure(code: str, message: str, *, details: Any = None,
            retryable: bool = False, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "status": "error",
        "data": None,
        "meta": meta or {},
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details,
        },
    }


def estimate_tokens(value: Any) -> int:
    """Conservative provider-neutral estimate based on serialized UTF-8 bytes."""
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return max(1, math.ceil(len(raw) / 2))


def canonical_document(article: Article) -> dict[str, Any]:
    return article.to_yaml_dict()


def is_unverified_draft(article: Article) -> bool:
    status = article.metadata.get("verification_status")
    if not status:
        status = article.extensions.get("system_metadata", {}).get("verification_status")
    return status == "pending"


def compact_document(article: Article, *, score: float | None = None,
                      selection_reason: str | None = None,
                      query: str | None = None,
                      evidence_chunks: list[dict[str, Any]] | None = None,
                      evidence_limit: int = 3,
                      compact_content_bytes: int | None = None,
                      ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    canonical = canonical_document(article)
    content_data = canonical["content"]["data"]
    compact_content: dict[str, Any] = {}
    raw_evidence = [
        chunk for chunk in (evidence_chunks or [])
        if str(chunk.get("path", "")).startswith("/content/data")
    ]
    verification_paths = [
        item.get("path", "/content/data")
        for item in canonical.get("verification", [])
        if item.get("level", "unverified") != "unverified"
    ]
    raw_evidence.sort(key=lambda chunk: not any(
        chunk.get("path") == path or str(chunk.get("path", "")).startswith(path.rstrip("/") + "/")
        for path in verification_paths
    ))
    evidence_chunks = []
    evidence_bytes = 0
    for chunk in raw_evidence:
        size = len(str(chunk.get("text", "")).encode("utf-8"))
        if evidence_chunks and evidence_bytes + size > 2400:
            continue
        evidence_chunks.append(chunk)
        evidence_bytes += size
        if len(evidence_chunks) >= evidence_limit:
            break
    evidence_keys: list[str] = []
    for chunk in evidence_chunks:
        parts = _decode_pointer(chunk.get("path", ""))
        if len(parts) >= 3 and parts[:2] == ["content", "data"]:
            evidence_keys.append(parts[2])
    candidate_keys = [
        *_query_relevant_content_keys(content_data, query),
        *evidence_keys,
        *COMPACT_CONTENT_PRIORITY,
    ]
    compact_bytes = 0
    compact_limit = (
        compact_content_bytes if compact_content_bytes is not None
        else (800 if evidence_chunks else 2400)
    )
    compact_field_limit = 0 if evidence_chunks and compact_limit <= 0 else (2 if evidence_chunks else 6)
    for key in dict.fromkeys(candidate_keys):
        if key not in content_data or len(compact_content) >= compact_field_limit:
            continue
        value = copy.deepcopy(content_data[key])
        value_bytes = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if evidence_chunks and compact_bytes + value_bytes > compact_limit:
            continue
        compact_content[key] = value
        compact_bytes += value_bytes
    if not compact_content and not evidence_chunks:
        for key in list(content_data)[:5]:
            compact_content[key] = copy.deepcopy(content_data[key])

    sources = [
        {key: source[key] for key in ("id", "url", "title") if key in source}
        for source in canonical.get("sources", [])
    ]
    source_ids = {source["id"] for source in sources}
    citations: list[dict[str, Any]] = []
    seen: set[str] = set()
    def pointer_exists(path: str) -> bool:
        current: Any = canonical
        try:
            for part in _decode_pointer(path):
                current = current[int(part)] if isinstance(current, list) else current[part]
            return True
        except (KeyError, IndexError, ValueError, TypeError, ProtocolFailure):
            return False

    if evidence_chunks:
        for chunk in evidence_chunks:
            path = chunk.get("path", "")
            if not pointer_exists(path):
                continue
            matching = []
            for verification in canonical.get("verification", []):
                verified_path = verification.get("path", "/content/data")
                if path == verified_path or path.startswith(verified_path.rstrip("/") + "/"):
                    matching.append(verification)
            verification = matching[0] if matching else {}
            valid_ids = [item for item in verification.get("source_ids", []) if item in source_ids]
            if not valid_ids and sources:
                valid_ids = sorted(source_ids)
            key = f"doc:{article.id}#{path}"
            if key in seen:
                continue
            seen.add(key)
            citations.append({
                "key": key,
                "document_id": article.id,
                "path": path,
                "level": verification.get("level", "sourced" if sources else "unverified"),
                "source_ids": valid_ids,
                "chunk_id": chunk.get("chunk_id"),
            })
    for verification in ([] if evidence_chunks else canonical.get("verification", [])):
        valid_ids = [item for item in verification.get("source_ids", []) if item in source_ids]
        path = verification.get("path", "/content/data")
        if path == "/content/data":
            paths = [f"/content/data/{_encode_pointer_part(key)}" for key in compact_content]
        else:
            parts = _decode_pointer(path)
            paths = [path] if (
                len(parts) >= 3
                and parts[:2] == ["content", "data"]
                and parts[2] in compact_content
            ) else []
        for represented_path in paths:
            if not pointer_exists(represented_path):
                continue
            key = f"doc:{article.id}#{represented_path}"
            if key in seen:
                continue
            seen.add(key)
            citations.append({
                "key": key,
                "document_id": article.id,
                "path": represented_path,
                "level": verification.get("level", "unverified"),
                "source_ids": valid_ids,
            })
    if not citations and sources and not evidence_chunks:
        for content_key in compact_content:
            path = f"/content/data/{_encode_pointer_part(content_key)}"
            citations.append({
                "key": f"doc:{article.id}#{path}",
                "document_id": article.id,
                "path": path,
                "level": "sourced",
                "source_ids": sorted(source_ids),
            })

    document = {
        "id": article.id,
        "title": article.title,
        "category": article.category,
        "type": canonical["content"]["type"],
        "tags": list(article.tags),
        "confidence": article.confidence,
        "version": article.version,
        "modified_at": canonical["metadata"]["modified_at"],
        "verification_status": "pending" if is_unverified_draft(article) else "active",
        "content": compact_content,
        "evidence": [
            {
                key: chunk[key] for key in (
                    "chunk_id", "path", "text", "vector_similarity", "retrieval_source",
                ) if key in chunk
            }
            for chunk in evidence_chunks
        ],
        "sources": sources,
        "citations": [item["key"] for item in citations],
    }
    if score is not None:
        document["score"] = score
    if selection_reason:
        document["selection_reason"] = selection_reason
    return document, citations


def project_fields(document: dict[str, Any], fields: str | None) -> dict[str, Any]:
    if not fields:
        return document
    requested = [item.strip() for item in fields.split(",") if item.strip()]
    result: dict[str, Any] = {}
    for dotted in requested:
        source: Any = document
        parts = dotted.split(".")
        try:
            for part in parts:
                source = source[part]
        except (KeyError, TypeError):
            raise ProtocolFailure("unknown_field", f"Unknown field projection: {dotted}")
        target = result
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = copy.deepcopy(source)
    return result


def build_context(index, query: str, *, max_tokens: int = DEFAULT_CONTEXT_TOKENS,
                  limit: int = 8, category: str | None = None,
                  tags: list[str] | None = None,
                  include_unverified: bool = False,
                  require_vector: bool = False) -> dict[str, Any]:
    from ai_wiki.storage import load_article

    if not MIN_CONTEXT_TOKENS <= max_tokens <= MAX_CONTEXT_TOKENS:
        raise ProtocolFailure(
            "invalid_token_budget",
            f"max_tokens must be between {MIN_CONTEXT_TOKENS} and {MAX_CONTEXT_TOKENS}",
        )
    if not 1 <= limit <= 50:
        raise ProtocolFailure("invalid_limit", "limit must be between 1 and 50")

    try:
        ranked = index.search(
            query, category=category, tags=tags, limit=20,
            require_vector=require_vector,
        )
    except Exception as exc:
        from ai_wiki.vector import VectorSearchUnavailable
        if isinstance(exc, VectorSearchUnavailable):
            raise ProtocolFailure(
                "vector_unavailable", str(exc),
                details={"reason": exc.code}, retryable=True,
            ) from exc
        raise
    direct_candidates: list[tuple[Article, float, str]] = []
    chunks_by_id: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for result in ranked:
        article = load_article(result["id"])
        if not article or article.id in seen:
            continue
        seen.add(article.id)
        chunks_by_id[article.id] = list(result.get("matched_chunks", []))
        direct_candidates.append((article, float(result.get("hybrid_score", 0.0)), "hybrid"))

    direct_by_id = {article.id: (article, score, reason) for article, score, reason in direct_candidates}
    direct_rank = {article.id: rank for rank, (article, _, _) in enumerate(direct_candidates)}
    related_by_parent: dict[str, list[tuple[Article, float, str]]] = {}
    promoted_relations: set[str] = set()
    for parent_rank, (article, score, _) in enumerate(direct_candidates[:5]):
        for relation_id in article.related:
            if relation_id in promoted_relations or relation_id == article.id:
                continue
            direct_relation = direct_by_id.get(relation_id)
            # A weaker result must not demote a stronger direct match merely because
            # it links to that document. Relations may only pull later results forward.
            if direct_relation and direct_rank[relation_id] < parent_rank:
                continue
            related = direct_relation[0] if direct_relation else load_article(relation_id)
            if related:
                promoted_relations.add(related.id)
                related_by_parent.setdefault(article.id, []).append(
                    (
                        related,
                        max(score * 0.75, direct_relation[1] if direct_relation else 0.0),
                        f"related:{article.id}",
                    )
                )

    candidates: list[tuple[Article, float, str]] = []
    for article, score, reason in direct_candidates:
        if article.id in promoted_relations:
            continue
        candidates.append((article, score, reason))
        candidates.extend(related_by_parent.get(article.id, []))

    context_id = uuid.uuid4().hex
    documents: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    excluded_unverified = 0
    truncated = False
    for article, score, reason in candidates:
        if len(documents) >= limit:
            truncated = True
            break
        if is_unverified_draft(article) and not include_unverified:
            excluded_unverified += 1
            continue
        document, document_citations = compact_document(
            article, score=round(score, 6), selection_reason=reason, query=query,
            evidence_chunks=chunks_by_id.get(article.id),
            evidence_limit=1,
            compact_content_bytes=0,
        )
        def make_trial() -> dict[str, Any]:
            trial_data = {
                "context_id": context_id,
                "query": query,
                "documents": documents + [document],
                "citations": citations + document_citations,
            }
            return success(trial_data, meta={
                "budget": {"max_tokens": max_tokens, "estimated_tokens": 0, "truncated": False},
                "excluded_unverified": excluded_unverified,
            })

        trial = make_trial()
        while estimate_tokens(trial) > max_tokens and document.get("content"):
            document["content"].pop(next(reversed(document["content"])))
            truncated = True
            trial = make_trial()
        while estimate_tokens(trial) > max_tokens and len(document.get("evidence", [])) > 1:
            removed_evidence = document["evidence"].pop()
            removed_chunk_id = removed_evidence.get("chunk_id")
            document_citations[:] = [
                item for item in document_citations if item.get("chunk_id") != removed_chunk_id
            ]
            document["citations"] = [item["key"] for item in document_citations]
            truncated = True
            trial = make_trial()
        if estimate_tokens(trial) > max_tokens:
            truncated = True
            continue
        documents.append(document)
        citations.extend(document_citations)

    data = {"context_id": context_id, "query": query, "documents": documents, "citations": citations}
    meta = {
        "budget": {"max_tokens": max_tokens, "estimated_tokens": 0, "truncated": truncated},
        "excluded_unverified": excluded_unverified,
        "candidate_count": len(candidates),
        "retrieval": dict(getattr(index, "last_retrieval_status", {})),
    }
    envelope = success(data, meta=meta)
    while True:
        for _ in range(5):
            estimated = estimate_tokens(envelope)
            if envelope["meta"]["budget"]["estimated_tokens"] == estimated:
                break
            envelope["meta"]["budget"]["estimated_tokens"] = estimated
        actual = estimate_tokens(envelope)
        envelope["meta"]["budget"]["estimated_tokens"] = actual
        if actual <= max_tokens or not documents:
            break
        removed = documents.pop()
        allowed = set(removed["citations"])
        citations[:] = [item for item in citations if item["key"] not in allowed]
        envelope["meta"]["budget"]["truncated"] = True
    index.record_context(
        context_id=context_id,
        query=query,
        document_ids=[item["id"] for item in documents],
        citations=[item["key"] for item in citations],
        max_tokens=max_tokens,
        estimated_tokens=envelope["meta"]["budget"]["estimated_tokens"],
    )
    return envelope


def _decode_pointer(path: str) -> list[str]:
    if path == "":
        return []
    if not path.startswith("/"):
        raise ProtocolFailure("invalid_patch", f"JSON Pointer must start with '/': {path}")
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def _encode_pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _query_relevant_content_keys(content: dict[str, Any], query: str | None) -> list[str]:
    if not query:
        return []
    tokens = [token for token in re.findall(r"[\w-]+", query.casefold()) if len(token) >= 3]
    if not tokens:
        return []
    ranked: list[tuple[int, int, str]] = []
    for position, (key, value) in enumerate(content.items()):
        key_text = key.casefold()
        value_text = json.dumps(value, ensure_ascii=False, separators=(",", ":")).casefold()
        score = sum(4 for token in tokens if token in key_text)
        score += sum(1 for token in tokens if token in value_text)
        if score:
            ranked.append((score, -position, key))
    ranked.sort(reverse=True)
    return [key for _, _, key in ranked[:3]]


def _parent_for(document: Any, path: str) -> tuple[Any, str]:
    parts = _decode_pointer(path)
    if not parts:
        raise ProtocolFailure("invalid_patch", "Replacing the document root is not allowed")
    current = document
    for part in parts[:-1]:
        try:
            current = current[int(part)] if isinstance(current, list) else current[part]
        except (KeyError, IndexError, ValueError, TypeError):
            raise ProtocolFailure("invalid_patch", f"Path does not exist: {path}")
    return current, parts[-1]


def _get_pointer(document: Any, path: str) -> Any:
    current = document
    for part in _decode_pointer(path):
        try:
            current = current[int(part)] if isinstance(current, list) else current[part]
        except (KeyError, IndexError, ValueError, TypeError):
            raise ProtocolFailure("patch_test_failed", f"Path does not exist: {path}")
    return current


def apply_json_patch(document: dict[str, Any], operations: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(operations, list) or not operations:
        raise ProtocolFailure("invalid_patch", "Patch must be a non-empty JSON array")
    result = copy.deepcopy(document)
    changed: list[str] = []
    for position, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise ProtocolFailure("invalid_patch", f"Operation {position} must be an object")
        op = operation.get("op")
        path = operation.get("path")
        if op not in {"test", "add", "replace", "remove"} or not isinstance(path, str):
            raise ProtocolFailure("invalid_patch", f"Unsupported operation at index {position}")
        if op != "test" and any(
            path == protected or path.startswith(protected + "/") or protected.startswith(path + "/")
            for protected in PROTECTED_PATCH_PATHS
        ):
            raise ProtocolFailure("protected_field", f"Field cannot be patched: {path}")
        if op == "test":
            if "value" not in operation or _get_pointer(result, path) != operation["value"]:
                raise ProtocolFailure("patch_test_failed", f"Test operation failed: {path}")
            continue
        parent, key = _parent_for(result, path)
        if op == "remove":
            try:
                parent.pop(int(key)) if isinstance(parent, list) else parent.pop(key)
            except (KeyError, IndexError, ValueError, TypeError):
                raise ProtocolFailure("invalid_patch", f"Cannot remove missing path: {path}")
        elif isinstance(parent, list):
            if "value" not in operation:
                raise ProtocolFailure("invalid_patch", f"Operation requires value: {path}")
            if op == "add" and key == "-":
                parent.append(copy.deepcopy(operation["value"]))
            else:
                try:
                    index = int(key)
                    if op == "add":
                        parent.insert(index, copy.deepcopy(operation["value"]))
                    else:
                        parent[index] = copy.deepcopy(operation["value"])
                except (IndexError, ValueError):
                    raise ProtocolFailure("invalid_patch", f"Invalid array path: {path}")
        else:
            if "value" not in operation:
                raise ProtocolFailure("invalid_patch", f"Operation requires value: {path}")
            if op == "replace" and key not in parent:
                raise ProtocolFailure("invalid_patch", f"Cannot replace missing path: {path}")
            parent[key] = copy.deepcopy(operation["value"])
        changed.append(path)
    return result, changed


def load_json_input(path: str) -> Any:
    import sys
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolFailure("invalid_json", f"Invalid JSON: {exc.msg}") from exc


def utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
