"""Deterministic, structure-aware chunks for keyword and vector retrieval."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterator

from ai_wiki.models import Article

MAX_CHUNK_CHARS = 1200
CHUNK_OVERLAP_CHARS = 160


@dataclass(frozen=True)
class ArticleChunk:
    chunk_id: str
    document_id: str
    path: str
    text: str
    indexed_text: str
    content_hash: str
    ordinal: int
    part: int = 0


def _encode_pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _leaf_values(value: Any, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _leaf_values(child, f"{path}/{_encode_pointer_part(str(key))}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from _leaf_values(child, f"{path}/{index}")
        return
    if value is None:
        return
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    if text:
        yield path, text


def _split_text(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + MAX_CHUNK_CHARS)
        if end < len(text):
            boundary = text.rfind(" ", start + MAX_CHUNK_CHARS // 2, end)
            if boundary > start:
                end = boundary
        part = text[start:end].strip()
        if part:
            parts.append(part)
        if end >= len(text):
            break
        start = max(start + 1, end - CHUNK_OVERLAP_CHARS)
    return parts


def article_chunks(article: Article) -> list[ArticleChunk]:
    """Create stable chunks from canonical content leaves without changing YAML."""
    canonical = article.to_yaml_dict()
    content = canonical.get("content", {}).get("data", {})
    metadata_text = " | ".join(filter(None, [
        article.title,
        article.category,
        " ".join(article.tags),
    ]))
    metadata_identity = f"{article.id}\0/title\00"
    metadata_digest = hashlib.sha256(metadata_identity.encode("utf-8")).hexdigest()[:20]
    chunks: list[ArticleChunk] = [ArticleChunk(
        chunk_id=f"{article.id}:{metadata_digest}",
        document_id=article.id,
        path="/title",
        text=article.title,
        indexed_text=metadata_text or article.title,
        content_hash=hashlib.sha256((metadata_text or article.title).encode("utf-8")).hexdigest(),
        ordinal=0,
    )]
    for path, value in _leaf_values(content, "/content/data"):
        label = " ".join(
            part.replace("~1", "/").replace("~0", "~")
            for part in path.split("/")[3:]
        )
        for part_number, part in enumerate(_split_text(value)):
            identity = f"{article.id}\0{path}\0{part_number}"
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
            indexed_text = f"{label} | {part}"
            content_hash = hashlib.sha256(indexed_text.encode("utf-8")).hexdigest()
            chunks.append(ArticleChunk(
                chunk_id=f"{article.id}:{digest}",
                document_id=article.id,
                path=path,
                text=part,
                indexed_text=indexed_text,
                content_hash=content_hash,
                ordinal=len(chunks),
                part=part_number,
            ))
    return chunks
