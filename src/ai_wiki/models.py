from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError

from ai_wiki.schema_v2 import SCHEMA_VERSION, validate_v2_document

# content 내부에서 검색/인덱싱에서 제외할 예약 키
_RESERVED_KEYS = {"_meta", "_changelog", "_v"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Article:
    """AI Wiki 문서. content는 구조화된 dict (YAML 데이터)."""
    id: str
    title: str
    category: str
    content: dict  # 구조화된 데이터 (type별 자유 스키마)
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.8
    version: int = 1
    created_at: datetime = field(default_factory=_now)
    last_modified: datetime = field(default_factory=_now)
    last_verified: datetime = field(default_factory=_now)
    sources: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    author: str = "unknown"
    schema_version: int = SCHEMA_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)
    source_records: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    verification: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    extensions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Return the backward-compatible API representation."""
        meta = self.meta_dict()
        content = dict(self.content) if isinstance(self.content, dict) else self.content
        if isinstance(content, dict) and self.metadata:
            content["_meta"] = dict(self.metadata)
        meta["content"] = content
        return meta

    def meta_dict(self) -> dict:
        """메타데이터만 반환 (content 제외)."""
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "tags": self.tags,
            "confidence": self.confidence,
            "version": self.version,
            "created_at": self._fmt(self.created_at),
            "last_modified": self._fmt(self.last_modified),
            "last_verified": self._fmt(self.last_verified),
            "sources": self.sources,
            "related": self.related,
            "author": self.author,
        }

    def to_yaml_dict(self) -> dict:
        """Return the canonical schema-v2 YAML mapping."""
        from ai_wiki.schemas import compute_completeness, determine_maturity

        if not isinstance(self.content, dict):
            raise ValueError("content must be a mapping")
        existing_sources = {
            item.get("url"): item for item in self.source_records
            if isinstance(item, dict) and isinstance(item.get("url"), str)
        }
        source_records = []
        invalid_sources = []
        used_source_ids: set[str] = set()
        for index, raw_source in enumerate(self.sources, start=1):
            url = raw_source.get("url") if isinstance(raw_source, dict) else raw_source
            parsed = urlparse(url) if isinstance(url, str) else None
            if not parsed or parsed.scheme not in {"http", "https"} or not parsed.netloc:
                invalid_sources.append(raw_source)
                continue
            record = (
                dict(raw_source) if isinstance(raw_source, dict)
                else dict(existing_sources.get(url, {}))
            )
            source_id = record.get("id") or f"src-{index}"
            while source_id in used_source_ids:
                source_id = f"src-{index}-{len(used_source_ids) + 1}"
            record.update({"id": source_id, "url": url})
            used_source_ids.add(source_id)
            source_records.append(record)
        existing_relations = {
            item.get("target_id"): item for item in self.relations
            if isinstance(item, dict) and isinstance(item.get("target_id"), str)
        }
        relations = [
            dict(existing_relations.get(target, {}), target_id=target)
            for target in self.related if isinstance(target, str) and target
        ]
        from ai_wiki.migration import normalize_legacy_content
        content, legacy_verification, legacy_meta, legacy_history = normalize_legacy_content(
            self.content, source_records
        )
        content_type = content.pop("type", "")
        original_content_type = content_type if isinstance(content_type, str) else str(content_type)
        from ai_wiki.schema_v2 import ContentBlock
        try:
            ContentBlock.model_validate(
                {"type": original_content_type, "data": content}, strict=True
            )
            content_type = original_content_type
        except ValidationError:
            content_type = "legacy"
            content = {"original_type": original_content_type or "unspecified", **content}
        completeness, _, _ = compute_completeness(self.content)
        raw_maturity = self.metadata.get("maturity", legacy_meta.get("maturity"))
        maturity_aliases = {
            "developing": "draft", "reviewed": "review",
            "authoritative": "mature", "complete": "mature",
            "comprehensive": "mature", "detailed": "mature",
            "enriched": "mature", "published": "mature",
            "reference": "mature", "verified": "mature",
        }
        maturity = maturity_aliases.get(raw_maturity, raw_maturity) if isinstance(raw_maturity, str) else None
        if maturity not in {"stub", "draft", "review", "mature"}:
            maturity = determine_maturity(
                completeness, len(source_records), len(relations), self.confidence
            )
        raw_completeness = self.metadata.get(
            "completeness", legacy_meta.get("completeness", completeness)
        )
        try:
            normalized_completeness = min(1.0, max(0.0, float(raw_completeness)))
        except (TypeError, ValueError):
            normalized_completeness = completeness
        metadata = {
            "confidence": self.confidence,
            "document_version": self.version,
            "created_at": self._fmt(self.created_at),
            "modified_at": self._fmt(self.last_modified),
            "verified_at": self._fmt(self.last_verified),
            "author": self.author,
            "maturity": maturity,
            "completeness": normalized_completeness,
        }
        extensions = dict(self.extensions)
        extra_metadata = {
            key: value for key, value in {**legacy_meta, **self.metadata}.items()
            if key not in {"maturity", "completeness"}
        }
        if extra_metadata:
            extensions["system_metadata"] = extra_metadata
        normalization = {}
        if invalid_sources:
            normalization["invalid_sources"] = invalid_sources
        if content_type == "legacy":
            normalization["content_type"] = {
                "original": original_content_type or None,
                "reason": "legacy content did not satisfy the registered v2 type contract",
            }
        if raw_maturity not in (None, maturity):
            normalization["maturity"] = {"original": raw_maturity, "normalized": maturity}

        valid_source_ids = {record["id"] for record in source_records}
        verification = []
        verification_aliases = {
            "confirmed": "verified", "official": "sourced", "reported": "sourced",
            "synthesized": "corroborated", "mixed": "disputed",
        }
        invalid_verification = []
        for raw in self.verification or legacy_verification:
            if not isinstance(raw, dict):
                invalid_verification.append(raw)
                continue
            record = dict(raw)
            original_level = record.get("level", "unverified")
            record["level"] = verification_aliases.get(original_level, original_level)
            if record["level"] not in {
                "unverified", "sourced", "corroborated", "verified", "disputed",
                "human_verified",
            }:
                record["level"] = "unverified"
                record["note"] = (
                    f"Legacy verification level: {original_level}. " + str(record.get("note", ""))
                ).strip()
            path = record.get("path")
            record["path"] = path if isinstance(path, str) and path.startswith("/") else "/content/data"
            source_ids = record.get("source_ids", [])
            record["source_ids"] = [
                value for value in source_ids
                if isinstance(value, str) and value in valid_source_ids
            ] if isinstance(source_ids, list) else []
            if record.get("verified_at"):
                try:
                    record["verified_at"] = self._fmt(self._parse_dt(record["verified_at"]))
                except (TypeError, ValueError):
                    invalid_verification.append(raw)
                    record.pop("verified_at", None)
            verification.append(record)

        history = []
        invalid_history = []
        for raw in self.history or legacy_history:
            if not isinstance(raw, dict):
                invalid_history.append(raw)
                continue
            record = dict(raw)
            try:
                record["at"] = self._fmt(self._parse_dt(record.get("at")))
            except (TypeError, ValueError):
                invalid_history.append(raw)
                record["at"] = self._fmt(self.last_modified)
                record["note"] = (
                    f"Legacy history date was invalid: {raw.get('at')!r}. " + str(record.get("note", ""))
                ).strip()
            record["action"] = str(record.get("action") or "legacy_change")
            fields = record.get("fields", [])
            record["fields"] = [str(value) for value in fields] if isinstance(fields, list) else []
            record["note"] = str(record.get("note", ""))
            history.append(record)
        if invalid_verification:
            normalization["invalid_verification"] = invalid_verification
        if invalid_history:
            normalization["invalid_history"] = invalid_history
        if normalization:
            extensions["legacy_normalization"] = normalization
        document = {
            "schema_version": SCHEMA_VERSION,
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "tags": self.tags,
            "metadata": metadata,
            "sources": source_records,
            "relations": relations,
            "content": {"type": content_type, "data": content},
            "verification": verification,
            "history": history,
            "extensions": extensions,
        }
        validated_v2 = validate_v2_document(document).model_dump(mode="json", exclude_none=True)
        if self.schema_version == 3 or extensions.get("temporal"):
            from ai_wiki.schema_v3 import v2_to_v3_view
            return v2_to_v3_view(validated_v2)
        return validated_v2

    def content_as_text(self) -> str:
        """content dict를 평탄화하여 검색용 텍스트로 변환. 예약키 제외.
        surrogate 문자는 '?'로 치환하여 SQLite UnicodeEncodeError 방지."""
        raw = _flatten_dict(self.content)
        # lone surrogate(\udcXX 등)를 encode→decode round-trip으로 제거
        return raw.encode("utf-8", errors="replace").decode("utf-8", errors="replace")

    def content_keys(self) -> set[str]:
        """예약키(_meta, _changelog, _v) 제외한 실제 데이터 키."""
        if not isinstance(self.content, dict):
            return set()
        return {k for k in self.content if not k.startswith("_")}

    # ── _meta 헬퍼 ────────────────────────────────

    def get_meta(self) -> dict:
        """Return normalized system metadata."""
        return self.metadata

    def set_meta(self, meta: dict) -> None:
        """Set normalized system metadata."""
        self.metadata = dict(meta)

    # ── _changelog 헬퍼 ──────────────────────────

    def append_changelog(self, action: str, fields: list[str], note: str = "") -> None:
        """Append an entry to normalized document history."""
        entry = {
            "at": self._fmt(_now()),
            "action": action,
            "fields": fields,
            "note": note,
        }
        self.history.append(entry)
        # Keep the legacy in-memory view for API compatibility. YAML serialization
        # removes this key and persists only the normalized history record above.
        if isinstance(self.content, dict):
            self.content.setdefault("_changelog", []).append({
                "date": entry["at"], "action": action, "fields": fields, "note": note,
            })

    # ── static/class methods ─────────────────────

    @staticmethod
    def _fmt(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _parse_dt(value: str | datetime) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
        if not isinstance(value, str) or not value.strip():
            raise ValueError("datetime value is required")
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(value, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        raise ValueError(f"invalid datetime: {value!r}")

    @classmethod
    def from_yaml(cls, data: dict) -> Article:
        """YAML dict에서 Article 생성."""
        if data.get("schema_version") == SCHEMA_VERSION:
            from ai_wiki.migration import v2_to_article_fields
            return cls(**v2_to_article_fields(data))
        if data.get("schema_version") == 3:
            from ai_wiki.schema_v3 import v3_to_article_fields
            return cls(**v3_to_article_fields(data))
        if "schema_version" in data:
            raise ValueError(f"unsupported schema_version: {data['schema_version']}")
        tags = data.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        sources = data.get("sources", [])
        if isinstance(sources, str):
            sources = [sources]

        related = data.get("related", [])
        if isinstance(related, str):
            related = [r.strip() for r in related.split(",") if r.strip()]

        content = data.get("content", {})
        if isinstance(content, str):
            content = {"text": content}

        now = _now()
        return cls(
            id=data.get("id", ""),
            title=data.get("title", ""),
            category=data.get("category", ""),
            content=content,
            tags=tags,
            confidence=float(data.get("confidence", 0.8)),
            version=int(data.get("version", 1)),
            created_at=cls._parse_dt(data["created_at"]) if data.get("created_at") else now,
            last_modified=cls._parse_dt(data["last_modified"]) if data.get("last_modified") else now,
            last_verified=cls._parse_dt(data["last_verified"]) if data.get("last_verified") else now,
            sources=sources,
            related=related,
            author=data.get("author", "unknown"),
        )


def _flatten_dict(d, prefix: str = "") -> str:
    """dict/list를 재귀적으로 평탄화. 예약키(_meta, _changelog, _v) 제외."""
    parts = []
    if isinstance(d, dict):
        for k, v in d.items():
            if k in _RESERVED_KEYS:
                continue
            # _v가 value의 형제인 구조: value만 추출
            if isinstance(v, dict) and "_v" in v and "value" in v:
                parts.append(_flatten_dict(v["value"], f"{prefix}{k}: "))
                continue
            parts.append(_flatten_dict(v, f"{prefix}{k}: "))
    elif isinstance(d, list):
        for item in d:
            parts.append(_flatten_dict(item, prefix))
    else:
        parts.append(f"{prefix}{d}")
    return "\n".join(parts)
