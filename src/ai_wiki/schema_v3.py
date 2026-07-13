"""Schema v3 promotes temporal knowledge objects to first-class document fields."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from ai_wiki.schema_v2 import (
    ContentBlock, DocumentMetadata, HistoryRecord, RelationRecord, SourceRecord,
    VerificationRecord, WikiDocumentV2,
)
from ai_wiki.temporal_contracts import (
    TemporalClaim, TemporalEntity, TemporalEvent, TemporalEvidence, TemporalExtension,
    TemporalTransition,
)


SCHEMA_VERSION_V3 = 3


class WikiDocumentV3(WikiDocumentV2):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[3]
    entities: list[TemporalEntity] = Field(default_factory=list)
    events: list[TemporalEvent] = Field(default_factory=list)
    claims: list[TemporalClaim] = Field(default_factory=list)
    evidence: list[TemporalEvidence] = Field(default_factory=list)
    transitions: list[TemporalTransition] = Field(default_factory=list)

    @model_validator(mode="after")
    def temporal_references_exist(self):
        temporal = TemporalExtension(
            entities=self.entities, evidence=self.evidence, events=self.events,
            claims=self.claims, transitions=self.transitions,
        )
        source_ids = {source.id for source in self.sources}
        unknown = {item.source_id for item in temporal.evidence} - source_ids
        if unknown:
            raise ValueError(f"unknown temporal source IDs referenced: {sorted(unknown)}")
        if self.extensions.get("temporal") is not None:
            raise ValueError("schema v3 temporal data must use top-level fields")
        # Existing v3 ledgers remain readable. A full entity-first contract
        # binds every row; partially migrated ledgers still validate every row
        # that claims an event/entity reference.
        timeline = self.content.data.get("timeline")
        if self.content.data.get("timeline_contract") is not None or (
            isinstance(timeline, list)
            and any(isinstance(row, dict) and ({"event_id", "entity_ids"} & set(row)) for row in timeline)
        ):
            self._validate_narrative_timeline(temporal)
        return self

    def _validate_narrative_timeline(self, temporal: TemporalExtension) -> None:
        """Bind human-readable timeline rows to the canonical event graph.

        A v3 document may still have free-form content, but once it publishes a
        ``content.data.timeline`` each row must point at one temporal event and
        name only that event's participants through stable entity IDs.  This
        prevents prose from silently splitting one participant into two people.
        """
        timeline = self.content.data.get("timeline")
        contract = self.content.data.get("timeline_contract")
        if contract not in (None, "entity_first"):
            raise ValueError("timeline_contract must be 'entity_first'")
        if timeline is None:
            return
        if not isinstance(timeline, list):
            raise ValueError("content.data.timeline must be a list")
        events = {event.id: event for event in temporal.events}
        entities = {entity.id for entity in temporal.entities}
        for index, row in enumerate(timeline):
            if not isinstance(row, dict):
                raise ValueError(f"timeline row {index} must be an object")
            has_binding = "event_id" in row or "entity_ids" in row
            if contract is None and not has_binding:
                continue
            event_id = row.get("event_id")
            if not isinstance(event_id, str) or event_id not in events:
                raise ValueError(f"timeline row {index} requires a known event_id")
            entity_ids = row.get("entity_ids")
            if not isinstance(entity_ids, list) or not entity_ids or not all(
                isinstance(entity_id, str) for entity_id in entity_ids
            ):
                raise ValueError(f"timeline row {index} requires non-empty entity_ids")
            if len(entity_ids) != len(set(entity_ids)):
                raise ValueError(f"timeline row {index} has duplicate entity_ids")
            unknown = set(entity_ids) - entities
            if unknown:
                raise ValueError(f"timeline row {index} has unknown entities: {sorted(unknown)}")
            unrelated = set(entity_ids) - set(events[event_id].participant_ids)
            if unrelated:
                raise ValueError(
                    f"timeline row {index} entities are not participants of {event_id}: {sorted(unrelated)}"
                )


def validate_v3_document(data: dict[str, Any]) -> WikiDocumentV3:
    return WikiDocumentV3.model_validate(data, strict=True)


def v2_to_v3_view(data: dict[str, Any]) -> dict[str, Any]:
    document = WikiDocumentV2.model_validate(data, strict=True)
    raw = document.model_dump(mode="json")
    temporal = raw.get("extensions", {}).pop("temporal", None) or {}
    raw["schema_version"] = 3
    raw["entities"] = temporal.get("entities", [])
    raw["events"] = temporal.get("events", [])
    raw["claims"] = temporal.get("claims", [])
    raw["evidence"] = temporal.get("evidence", [])
    raw["transitions"] = temporal.get("transitions", [])
    return validate_v3_document(raw).model_dump(mode="json", exclude_none=True)


def v3_to_article_fields(data: dict[str, Any]) -> dict[str, Any]:
    document = validate_v3_document(data)
    raw = document.model_dump(mode="python")
    content = {"type": raw["content"]["type"], **raw["content"]["data"]}
    temporal = {
        "entities": raw["entities"], "evidence": raw["evidence"],
        "events": raw["events"], "claims": raw["claims"],
        "transitions": raw["transitions"],
    }
    extensions = dict(raw["extensions"])
    extensions["temporal"] = temporal
    return {
        "schema_version": 3,
        "id": raw["id"], "title": raw["title"], "category": raw["category"],
        "tags": raw["tags"], "content": content,
        "confidence": raw["metadata"]["confidence"],
        "version": raw["metadata"]["document_version"],
        "created_at": raw["metadata"]["created_at"],
        "last_modified": raw["metadata"]["modified_at"],
        "last_verified": raw["metadata"]["verified_at"],
        "author": raw["metadata"]["author"],
        "sources": [source["url"] for source in raw["sources"]],
        "related": [relation["target_id"] for relation in raw["relations"]],
        "metadata": {
            "maturity": raw["metadata"]["maturity"],
            "completeness": raw["metadata"]["completeness"],
            **extensions.get("system_metadata", {}),
        },
        "source_records": raw["sources"], "relations": raw["relations"],
        "verification": raw["verification"], "history": raw["history"],
        "extensions": extensions,
    }


def document_v3_json_schema() -> dict[str, Any]:
    return WikiDocumentV3.model_json_schema()
