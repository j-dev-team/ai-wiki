"""Versioned, validated document contract for AI Wiki YAML files."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_wiki.schemas import TYPE_SCHEMAS, _DICT_FIELDS, _LIST_FIELDS

SCHEMA_VERSION = 2
VERIFICATION_LEVELS = {
    "unverified", "sourced", "corroborated", "verified", "disputed",
    "human_verified",
}


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value


def _parse_datetime(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid ISO 8601 datetime: {value!r}") from exc
    return value


class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    url: str = Field(pattern=r"^https?://[^\s/]+(?:/[^\s]*)?$")
    title: str = ""
    retrieved_at: datetime | None = None

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("source URL must be an absolute HTTP(S) URL")
        return value

    @field_validator("retrieved_at", mode="before")
    @classmethod
    def parse_retrieved_at(cls, value):
        return _parse_datetime(value)

    @field_validator("retrieved_at")
    @classmethod
    def retrieved_at_aware(cls, value: datetime | None) -> datetime | None:
        return _aware(value, "retrieved_at") if value else None


class RelationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1)
    type: str = Field(default="related_to", min_length=1)
    direction: Literal["outgoing", "incoming", "bidirectional"] = "outgoing"
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    source_ids: list[str] = Field(default_factory=list)


class VerificationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(pattern=r"^/")
    level: str
    source_ids: list[str] = Field(default_factory=list)
    verified_at: datetime | None = None
    note: str = ""

    @field_validator("level")
    @classmethod
    def valid_level(cls, value: str) -> str:
        if value not in VERIFICATION_LEVELS:
            raise ValueError(f"unknown verification level: {value}")
        return value

    @field_validator("verified_at", mode="before")
    @classmethod
    def parse_verified_at(cls, value):
        return _parse_datetime(value)

    @field_validator("verified_at")
    @classmethod
    def verified_at_aware(cls, value: datetime | None) -> datetime | None:
        return _aware(value, "verified_at") if value else None


class HistoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at: datetime
    action: str = Field(min_length=1)
    fields: list[str] = Field(default_factory=list)
    note: str = ""

    @field_validator("at", mode="before")
    @classmethod
    def parse_at(cls, value):
        return _parse_datetime(value)

    @field_validator("at")
    @classmethod
    def at_aware(cls, value: datetime) -> datetime:
        return _aware(value, "history.at")


class DocumentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confidence: float = Field(ge=0.0, le=1.0)
    document_version: int = Field(ge=1)
    created_at: datetime
    modified_at: datetime
    verified_at: datetime
    author: str = Field(min_length=1)
    maturity: Literal["stub", "draft", "review", "mature"] = "stub"
    completeness: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("created_at", "modified_at", "verified_at", mode="before")
    @classmethod
    def parse_dates(cls, value):
        return _parse_datetime(value)

    @field_validator("created_at", "modified_at", "verified_at")
    @classmethod
    def dates_are_aware(cls, value: datetime, info) -> datetime:
        return _aware(value, info.field_name)

    @model_validator(mode="after")
    def chronological(self):
        if self.modified_at < self.created_at:
            raise ValueError("modified_at cannot precede created_at")
        return self


class ContentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    data: dict[str, Any]

    @model_validator(mode="after")
    def known_type_and_required_fields(self):
        schema = TYPE_SCHEMAS.get(self.type)
        if schema is None:
            raise ValueError(
                f"unknown content type '{self.type}'; register it in custom_types first"
            )
        missing = [
            key for key in schema["required"]
            if key != "type" and (key not in self.data or self.data[key] in (None, "", [], {}))
        ]
        if missing:
            raise ValueError(f"missing required content fields: {', '.join(missing)}")
        if self.type == "legacy":
            return self
        wrong_lists = [key for key in _LIST_FIELDS if key in self.data and not isinstance(self.data[key], list)]
        wrong_dicts = [key for key in _DICT_FIELDS if key in self.data and not isinstance(self.data[key], dict)]
        if wrong_lists:
            raise ValueError(f"content fields must be lists: {', '.join(sorted(wrong_lists))}")
        if wrong_dicts:
            raise ValueError(f"content fields must be mappings: {', '.join(sorted(wrong_dicts))}")
        if "base_url" in self.data:
            parsed = urlparse(str(self.data["base_url"]))
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("content.data.base_url must be an absolute HTTP(S) URL")
        return self


class WikiDocumentV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2]
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    category: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    metadata: DocumentMetadata
    sources: list[SourceRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
    content: ContentBlock
    verification: list[VerificationRecord] = Field(default_factory=list)
    history: list[HistoryRecord] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def references_exist(self):
        source_ids = {source.id for source in self.sources}
        if len(source_ids) != len(self.sources):
            raise ValueError("source IDs must be unique")
        relation_targets = [relation.target_id for relation in self.relations]
        if len(set(relation_targets)) != len(relation_targets):
            raise ValueError("relation targets must be unique")
        for record in [*self.relations, *self.verification]:
            unknown = set(record.source_ids) - source_ids
            if unknown:
                raise ValueError(f"unknown source IDs referenced: {sorted(unknown)}")
        return self


def validate_v2_document(data: dict[str, Any]) -> WikiDocumentV2:
    """Validate and return the canonical v2 model."""
    return WikiDocumentV2.model_validate(data, strict=True)


def document_json_schema() -> dict[str, Any]:
    """Return the machine-readable JSON Schema for integrations."""
    import copy

    schema = WikiDocumentV2.model_json_schema()
    content_block = schema.get("$defs", {}).get("ContentBlock", {})
    type_property = content_block.get("properties", {}).get("type")
    if isinstance(type_property, dict):
        type_property["enum"] = sorted(TYPE_SCHEMAS)
    schema["x-ai-wiki-content-types"] = copy.deepcopy(TYPE_SCHEMAS)
    return schema
