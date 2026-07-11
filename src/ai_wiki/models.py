from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

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

    def to_dict(self) -> dict:
        """전체 데이터를 dict로 변환 (JSON/YAML 직렬화용)."""
        meta = self.meta_dict()
        meta["content"] = self.content
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
        """YAML 파일 저장용."""
        d = self.meta_dict()
        d["content"] = self.content
        return d

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
        """content._meta를 반환. 없으면 빈 dict."""
        if isinstance(self.content, dict):
            return self.content.get("_meta", {})
        return {}

    def set_meta(self, meta: dict) -> None:
        """content._meta를 설정."""
        if isinstance(self.content, dict):
            self.content["_meta"] = meta

    # ── _changelog 헬퍼 ──────────────────────────

    def append_changelog(self, action: str, fields: list[str], note: str = "") -> None:
        """content._changelog에 항목 추가."""
        if not isinstance(self.content, dict):
            return
        if "_changelog" not in self.content:
            self.content["_changelog"] = []
        self.content["_changelog"].append({
            "date": self._fmt(_now()),
            "action": action,
            "fields": fields,
            "note": note,
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
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(value, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        return _now()

    @classmethod
    def from_yaml(cls, data: dict) -> Article:
        """YAML dict에서 Article 생성."""
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

        return cls(
            id=data.get("id", ""),
            title=data.get("title", ""),
            category=data.get("category", ""),
            content=content,
            tags=tags,
            confidence=float(data.get("confidence", 0.8)),
            version=int(data.get("version", 1)),
            created_at=cls._parse_dt(data.get("created_at", "")),
            last_modified=cls._parse_dt(data.get("last_modified", "")),
            last_verified=cls._parse_dt(data.get("last_verified", "")),
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
