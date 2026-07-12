"""Validated temporal knowledge contracts used by schema v2 extensions and v3."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _parse_datetime(value: Any) -> Any:
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


class TemporalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TemporalLocator(TemporalModel):
    type: Literal["page", "paragraph", "section", "table", "line", "json_pointer"]
    value: str = Field(min_length=1)


class TemporalEntity(TemporalModel):
    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)


class TemporalEvidence(TemporalModel):
    id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    locator: TemporalLocator
    content_hash: str | None = None
    published_at: datetime | None = None
    observed_at: datetime
    retrieved_at: datetime | None = None

    @field_validator("published_at", "observed_at", "retrieved_at", mode="before")
    @classmethod
    def parse_dates(cls, value):
        return _parse_datetime(value)

    @field_validator("published_at", "observed_at", "retrieved_at")
    @classmethod
    def aware_dates(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("temporal timestamps must include a timezone")
        return value


class TemporalClaim(TemporalModel):
    id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object: Any
    status: Literal["proposed", "current", "disputed", "retired", "invalidated"]
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    observed_at: datetime
    recorded_at: datetime
    retired_at: datetime | None = None
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator(
        "valid_from", "valid_to", "observed_at", "recorded_at", "retired_at", mode="before",
    )
    @classmethod
    def parse_dates(cls, value):
        return _parse_datetime(value)

    @field_validator("valid_from", "valid_to", "observed_at", "recorded_at", "retired_at")
    @classmethod
    def aware_dates(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("temporal timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def valid_interval_and_evidence(self):
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to cannot precede valid_from")
        if self.status == "current" and not self.evidence_ids:
            raise ValueError("current claims require evidence")
        if self.status in {"retired", "invalidated"} and self.retired_at is None:
            raise ValueError("retired and invalidated claims require retired_at")
        return self


class TemporalEvent(TemporalModel):
    id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    occurred_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    participant_ids: list[str] = Field(default_factory=list)
    description: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("occurred_at", "started_at", "ended_at", mode="before")
    @classmethod
    def parse_dates(cls, value):
        return _parse_datetime(value)

    @field_validator("occurred_at", "started_at", "ended_at")
    @classmethod
    def aware_dates(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("temporal timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def valid_event_time(self):
        if self.occurred_at is None and self.started_at is None:
            raise ValueError("event requires occurred_at or started_at")
        if self.occurred_at is not None and (self.started_at is not None or self.ended_at is not None):
            raise ValueError("occurred_at cannot be combined with an interval")
        if self.started_at and self.ended_at and self.ended_at < self.started_at:
            raise ValueError("event ended_at cannot precede started_at")
        return self


class TemporalTransition(TemporalModel):
    id: str = Field(min_length=1)
    from_claim_id: str | None = None
    to_claim_id: str | None = None
    relation: Literal[
        "supersedes", "corrects", "invalidates", "contradicts",
        "corroborates", "narrows", "extends",
    ]
    triggered_by_event_id: str | None = None
    reason: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    status: Literal["proposed", "approved", "rejected"] = "proposed"
    recorded_at: datetime

    @field_validator("recorded_at", mode="before")
    @classmethod
    def parse_recorded_at(cls, value):
        return _parse_datetime(value)

    @field_validator("recorded_at")
    @classmethod
    def recorded_at_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must include a timezone")
        return value

    @model_validator(mode="after")
    def has_endpoint(self):
        if self.from_claim_id is None and self.to_claim_id is None:
            raise ValueError("transition requires a claim endpoint")
        return self


class TemporalExtension(TemporalModel):
    entities: list[TemporalEntity] = Field(default_factory=list)
    evidence: list[TemporalEvidence] = Field(default_factory=list)
    events: list[TemporalEvent] = Field(default_factory=list)
    claims: list[TemporalClaim] = Field(default_factory=list)
    transitions: list[TemporalTransition] = Field(default_factory=list)

    @model_validator(mode="after")
    def references_exist(self):
        groups = {
            "entity": [item.id for item in self.entities],
            "evidence": [item.id for item in self.evidence],
            "event": [item.id for item in self.events],
            "claim": [item.id for item in self.claims],
            "transition": [item.id for item in self.transitions],
        }
        for label, ids in groups.items():
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {label} IDs")
        entity_ids = set(groups["entity"])
        evidence_ids = set(groups["evidence"])
        event_ids = set(groups["event"])
        claim_ids = set(groups["claim"])
        for claim in self.claims:
            if claim.subject_id not in entity_ids:
                raise ValueError(f"unknown claim subject: {claim.subject_id}")
            if set(claim.evidence_ids) - evidence_ids:
                raise ValueError(f"unknown claim evidence: {claim.id}")
        for event in self.events:
            if set(event.participant_ids) - entity_ids:
                raise ValueError(f"unknown event participant: {event.id}")
            if set(event.evidence_ids) - evidence_ids:
                raise ValueError(f"unknown event evidence: {event.id}")
        claims = {item.id: item for item in self.claims}
        for transition in self.transitions:
            for claim_id in (transition.from_claim_id, transition.to_claim_id):
                if claim_id and claim_id not in claim_ids:
                    raise ValueError(f"unknown transition claim: {claim_id}")
            if transition.triggered_by_event_id and transition.triggered_by_event_id not in event_ids:
                raise ValueError(f"unknown transition event: {transition.triggered_by_event_id}")
            if set(transition.evidence_ids) - evidence_ids:
                raise ValueError(f"unknown transition evidence: {transition.id}")
            if transition.status == "approved" and transition.relation == "supersedes":
                previous = claims.get(transition.from_claim_id or "")
                replacement = claims.get(transition.to_claim_id or "")
                if previous is None or replacement is None:
                    raise ValueError("approved supersedes requires both claims")
                if previous.status != "retired" or replacement.status != "current":
                    raise ValueError("approved supersedes must retire the old claim and activate the new claim")
        return self


def temporal_json_schema() -> dict[str, Any]:
    return TemporalExtension.model_json_schema()
