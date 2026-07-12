"""Temporal query views over the derived SQLite claim ledger."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def parse_time(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("temporal query time must include a timezone")
    return result


class TemporalQueries:
    def __init__(self, index):
        self.index = index

    def _claims(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        rows = self.index.conn.execute(sql, params).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def current(self, document_id: str, at: str | datetime | None = None) -> list[dict[str, Any]]:
        when = parse_time(at).isoformat()
        return self._claims(
            """SELECT payload FROM temporal_claims
               WHERE document_id=? AND status='current'
                 AND (valid_from IS NULL OR valid_from<=?)
                 AND (valid_to IS NULL OR valid_to>?)
               ORDER BY subject_id, predicate, recorded_at""",
            (document_id, when, when),
        )

    def as_of(self, document_id: str, at: str | datetime) -> list[dict[str, Any]]:
        when = parse_time(at).isoformat()
        return self._claims(
            """SELECT payload FROM temporal_claims
               WHERE document_id=? AND status NOT IN ('invalidated','proposed')
                 AND (valid_from IS NULL OR valid_from<=?)
                 AND (valid_to IS NULL OR valid_to>?)
               ORDER BY subject_id, predicate, recorded_at""",
            (document_id, when, when),
        )

    def known_as_of(self, document_id: str, at: str | datetime) -> list[dict[str, Any]]:
        when = parse_time(at).isoformat()
        return self._claims(
            """SELECT payload FROM temporal_claims
               WHERE document_id=? AND recorded_at<=?
                 AND (retired_at IS NULL OR retired_at>?)
               ORDER BY recorded_at""".replace("retired_at", "json_extract(payload, '$.retired_at')"),
            (document_id, when, when),
        )

    def disputed(self, document_id: str) -> list[dict[str, Any]]:
        return self._claims(
            "SELECT payload FROM temporal_claims WHERE document_id=? AND status='disputed' "
            "ORDER BY subject_id, predicate, recorded_at",
            (document_id,),
        )

    def timeline(self, document_id: str) -> list[dict[str, Any]]:
        events = [
            {"kind": "event", "at": row["at"], "data": json.loads(row["payload"])}
            for row in self.index.conn.execute(
                """SELECT COALESCE(occurred_at, started_at) AS at, payload
                   FROM temporal_events WHERE document_id=?""", (document_id,),
            ).fetchall()
        ]
        transitions = [
            {"kind": "transition", "at": row["recorded_at"], "data": json.loads(row["payload"])}
            for row in self.index.conn.execute(
                """SELECT recorded_at, payload FROM temporal_transitions
                   WHERE document_id=? AND status='approved'""", (document_id,),
            ).fetchall()
        ]
        return sorted([*events, *transitions], key=lambda item: item["at"] or "")

    def why_changed(self, claim_id: str) -> dict[str, Any] | None:
        transition = self.index.conn.execute(
            """SELECT document_id, payload FROM temporal_transitions
               WHERE (from_claim_id=? OR to_claim_id=?) AND status='approved'
               ORDER BY recorded_at DESC LIMIT 1""", (claim_id, claim_id),
        ).fetchone()
        if not transition:
            return None
        data = json.loads(transition["payload"])
        claim_ids = [value for value in (data.get("from_claim_id"), data.get("to_claim_id")) if value]
        claims = []
        for target in claim_ids:
            row = self.index.conn.execute(
                "SELECT payload FROM temporal_claims WHERE document_id=? AND claim_id=?",
                (transition["document_id"], target),
            ).fetchone()
            if row:
                claims.append(json.loads(row["payload"]))
        event = None
        if data.get("triggered_by_event_id"):
            row = self.index.conn.execute(
                "SELECT payload FROM temporal_events WHERE document_id=? AND event_id=?",
                (transition["document_id"], data["triggered_by_event_id"]),
            ).fetchone()
            event = json.loads(row["payload"]) if row else None
        return {"document_id": transition["document_id"], "transition": data, "claims": claims, "event": event}

    def propose_conflicts(self, document_id: str) -> list[dict[str, Any]]:
        rows = self.index.conn.execute(
            """SELECT subject_id, predicate, COUNT(*) AS count
               FROM temporal_claims
               WHERE document_id=? AND status IN ('current','disputed')
               GROUP BY subject_id, predicate HAVING COUNT(*)>1""", (document_id,),
        ).fetchall()
        return [
            {
                "subject_id": row["subject_id"], "predicate": row["predicate"],
                "relation": "contradicts", "status": "proposed",
                "reason": "multiple active claims share subject and predicate",
            }
            for row in rows
        ]
