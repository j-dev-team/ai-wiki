from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_wiki.schema_v3 import validate_v3_document


NOW = "2026-07-13T00:00:00Z"


def _document():
    return {
        "schema_version": 3,
        "id": "entity-first-case",
        "title": "Entity-first timeline",
        "category": "law/cases",
        "tags": ["case", "timeline"],
        "metadata": {
            "confidence": 0.8, "document_version": 1,
            "created_at": NOW, "modified_at": NOW, "verified_at": NOW,
            "author": "test", "maturity": "review", "completeness": 0.8,
        },
        "sources": [{"id": "src-1", "url": "https://example.com/source"}],
        "relations": [],
        "content": {
            "type": "event",
            "data": {
                "what": "A structured event record with stable participant identities.",
                "period": "2026-07-13",
                "location": ["test environment"],
                "participants": ["suspect", "victim-a"],
                "facts": ["The graph precedes its narrative projection."],
                "timeline_contract": "entity_first",
                "timeline": [{
                    "event_id": "stalking", "entity_ids": ["suspect", "victim-a"],
                    "event": "A씨를 스토킹한 혐의",
                }],
            },
        },
        "verification": [{"path": "/content/data/timeline", "level": "sourced", "source_ids": ["src-1"]}],
        "history": [],
        "extensions": {},
        "entities": [
            {"id": "suspect", "kind": "person", "name": "피고인"},
            {"id": "victim-a", "kind": "person", "name": "20대 베트남 국적 A씨",
             "attributes": {"nationality": "베트남", "relationship": "아르바이트 동료"}},
        ],
        "evidence": [{"id": "ev-1", "source_id": "src-1", "locator": {"type": "section", "value": "facts"}, "observed_at": NOW}],
        "events": [{
            "id": "stalking", "event_type": "alleged_offense", "occurred_at": NOW,
            "participant_ids": ["suspect", "victim-a"], "description": "스토킹 혐의", "evidence_ids": ["ev-1"],
        }],
        "claims": [],
        "transitions": [],
    }


def test_entity_first_timeline_accepts_bound_canonical_entities():
    document = validate_v3_document(_document())
    assert document.content.data["timeline"][0]["entity_ids"] == ["suspect", "victim-a"]


@pytest.mark.parametrize("row, message", [
    ({"event": "unbound"}, "requires a known event_id"),
    ({"event_id": "stalking", "entity_ids": ["other"], "event": "wrong person"}, "unknown entities"),
    ({"event_id": "stalking", "entity_ids": ["suspect", "victim-a", "other"], "event": "wrong person"}, "unknown entities"),
])
def test_entity_first_timeline_rejects_unbound_or_wrong_entities(row, message):
    raw = _document()
    raw["content"]["data"]["timeline"] = [row]
    with pytest.raises(ValidationError, match=message):
        validate_v3_document(raw)


def test_partial_legacy_timeline_cannot_publish_an_invalid_entity_binding():
    raw = _document()
    raw["content"]["data"].pop("timeline_contract")
    raw["content"]["data"]["timeline"][0]["entity_ids"] = ["other"]

    with pytest.raises(ValidationError, match="unknown entities"):
        validate_v3_document(raw)
