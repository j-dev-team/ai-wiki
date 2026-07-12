"""Feedback-driven candidate calibration, promotion, and rollback."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from ai_wiki.vector import VectorIndex


DEFAULT_THRESHOLDS = {
    "minimum_labeled_samples": 200,
    "minimum_positive_samples": 50,
    "minimum_negative_samples": 50,
    "minimum_distinct_documents": 30,
    "holdout_ratio": 0.2,
}


def _probability(score: float, a: float, b: float) -> float:
    value = max(-60.0, min(60.0, a * score + b))
    return 1.0 / (1.0 + math.exp(-value))


def metrics(samples: list[tuple[float, int]], a: float, b: float) -> dict[str, float]:
    if not samples:
        return {"brier": 1.0, "log_loss": 99.0, "ece": 1.0, "accuracy": 0.0}
    probabilities = [_probability(score, a, b) for score, _ in samples]
    labels = [label for _, label in samples]
    brier = sum((p - y) ** 2 for p, y in zip(probabilities, labels)) / len(samples)
    log_loss = -sum(
        y * math.log(max(p, 1e-12)) + (1 - y) * math.log(max(1 - p, 1e-12))
        for p, y in zip(probabilities, labels)
    ) / len(samples)
    accuracy = sum((p >= 0.5) == bool(y) for p, y in zip(probabilities, labels)) / len(samples)
    ece = 0.0
    for lower in [i / 10 for i in range(10)]:
        bucket = [(p, y) for p, y in zip(probabilities, labels) if lower <= p < lower + 0.1]
        if bucket:
            confidence = sum(p for p, _ in bucket) / len(bucket)
            observed = sum(y for _, y in bucket) / len(bucket)
            ece += len(bucket) / len(samples) * abs(confidence - observed)
    return {"brier": brier, "log_loss": log_loss, "ece": ece, "accuracy": accuracy}


class CalibrationManager:
    def __init__(self, wiki_index, vector_index: VectorIndex):
        self.wiki = wiki_index
        self.vector = vector_index

    def status(self) -> dict[str, Any]:
        counts = self.wiki.calibration_feedback_counts()
        rows = self.wiki.conn.execute(
            "SELECT * FROM calibration_runs ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        return {"feedback": counts, "vector": self.vector.state(),
                "runs": [dict(row) for row in rows]}

    def _samples(self) -> list[dict[str, Any]]:
        rows = self.wiki.conn.execute(
            """SELECT e.*, s.query FROM calibration_events e
               JOIN context_sessions s ON s.context_id=e.context_id
               WHERE e.eligible=1 ORDER BY e.created_at"""
        ).fetchall()
        output = []
        for row in rows:
            results = self.vector.search(row["query"], limit=50)
            hit = next((item for item in results if item["id"] == row["document_id"]), None)
            score = float(hit["vector_similarity"]) if hit else -1.0
            output.append({
                "score": score,
                "label": int(row["judgment"] == "accepted"),
                "document_id": row["document_id"],
                "group": hashlib.sha256(
                    f"{row['document_id']}:{row['query'].casefold().strip()}".encode()
                ).hexdigest(),
                "event_id": row["event_id"],
            })
        return output

    def run(self, *, dry_run: bool = False, thresholds: dict[str, Any] | None = None) -> dict[str, Any]:
        limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        samples = self._samples()
        positives = sum(item["label"] for item in samples)
        negatives = len(samples) - positives
        documents = len({item["document_id"] for item in samples})
        failures = []
        if len(samples) < limits["minimum_labeled_samples"]:
            failures.append("insufficient_samples")
        if positives < limits["minimum_positive_samples"]:
            failures.append("insufficient_positive_samples")
        if negatives < limits["minimum_negative_samples"]:
            failures.append("insufficient_negative_samples")
        if documents < limits["minimum_distinct_documents"]:
            failures.append("insufficient_document_coverage")
        if failures:
            return {"status": "not_ready", "reasons": failures, "samples": len(samples),
                    "positives": positives, "negatives": negatives, "documents": documents}
        groups = sorted({item["group"] for item in samples})
        holdout_groups = set(groups[-max(1, int(len(groups) * limits["holdout_ratio"])):])
        train = [(item["score"], item["label"]) for item in samples if item["group"] not in holdout_groups]
        holdout = [(item["score"], item["label"]) for item in samples if item["group"] in holdout_groups]
        a, b = VectorIndex.fit_calibration(train)
        candidate_metrics = metrics(holdout, a, b)
        current = self.vector._calibration()
        baseline_metrics = metrics(holdout, *current) if current else None
        promotable = baseline_metrics is None or (
            candidate_metrics["brier"] <= baseline_metrics["brier"] * 0.98
            or candidate_metrics["ece"] <= baseline_metrics["ece"] - 0.01
        )
        run_id = f"cal-{uuid.uuid4().hex[:16]}"
        dataset_hash = hashlib.sha256(
            json.dumps([item["event_id"] for item in samples], sort_keys=True).encode()
        ).hexdigest()
        scope_hash = hashlib.sha256(json.dumps(self.vector.state(), sort_keys=True).encode()).hexdigest()
        result = {
            "run_id": run_id, "status": "candidate" if promotable else "rejected",
            "scope_hash": scope_hash, "dataset_hash": dataset_hash,
            "train_count": len(train), "holdout_count": len(holdout),
            "metrics": candidate_metrics, "baseline_metrics": baseline_metrics,
            "parameters": {"a": a, "b": b}, "promotable": promotable,
        }
        if not dry_run:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self.wiki.conn.execute(
                """INSERT INTO calibration_runs
                   (run_id, scope_hash, dataset_hash, status, train_count, holdout_count,
                    metrics_json, candidate_parameters, baseline_run_id, created_at,
                    completed_at, rejection_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, scope_hash, dataset_hash, result["status"], len(train), len(holdout),
                 json.dumps(candidate_metrics), json.dumps({"a": a, "b": b}),
                 self.vector.state().get("calibration_run_id"), now, now,
                 None if promotable else "candidate_not_better"),
            )
            self.wiki.conn.commit()
        return result

    def maintain(self) -> dict[str, Any]:
        last = self.wiki.conn.execute(
            "SELECT created_at FROM calibration_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if last:
            created = datetime.fromisoformat(last["created_at"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - created < timedelta(days=7):
                return {"status": "cooldown", "next_after": (created + timedelta(days=7)).isoformat()}
        candidate = self.run()
        if candidate.get("status") == "candidate":
            promoted = self.promote(candidate["run_id"])
            return {"status": "promoted", "candidate": candidate, "production": promoted}
        return candidate

    def promote(self, run_id: str) -> dict[str, Any]:
        row = self.wiki.conn.execute(
            "SELECT * FROM calibration_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if not row or row["status"] != "candidate":
            raise ValueError("calibration_candidate_not_found")
        current_scope = hashlib.sha256(
            json.dumps(self.vector.state(), sort_keys=True).encode()
        ).hexdigest()
        if current_scope != row["scope_hash"]:
            raise ValueError("calibration_scope_changed")
        parameters = json.loads(row["candidate_parameters"])
        result = self.vector.install_calibration(
            parameters["a"], parameters["b"],
            samples=row["train_count"] + row["holdout_count"], run_id=run_id,
        )
        self.wiki.conn.execute(
            "UPDATE calibration_runs SET status='superseded' WHERE status='production' AND run_id<>?",
            (run_id,),
        )
        self.wiki.conn.execute(
            "UPDATE calibration_runs SET status='production' WHERE run_id=?", (run_id,)
        )
        self.wiki.conn.commit()
        keep = [item[0] for item in self.wiki.conn.execute(
            "SELECT run_id FROM calibration_runs WHERE status IN ('production','superseded') "
            "ORDER BY completed_at DESC LIMIT 3"
        ).fetchall()]
        for key, in self.vector.conn.execute(
            "SELECT key FROM vector_state WHERE key LIKE 'calibration_backup:%'"
        ).fetchall():
            if key.split(":", 1)[1] not in keep and key != f"calibration_backup:{run_id}":
                self.vector.conn.execute("DELETE FROM vector_state WHERE key=?", (key,))
        self.vector.conn.commit()
        return result

    def rollback(self, run_id: str) -> dict[str, Any]:
        row = self.vector.conn.execute(
            "SELECT value FROM vector_state WHERE key=?", (f"calibration_backup:{run_id}",)
        ).fetchone()
        if not row:
            raise ValueError("calibration_backup_not_found")
        backup = json.loads(row[0])
        self.vector.conn.execute(
            "DELETE FROM vector_state WHERE key IN ('calibration_a','calibration_b',"
            "'calibration_samples','calibrated_at','calibration_revision','calibration_run_id')"
        )
        values = [(key, value) for key, value in backup.items()]
        if values:
            self.vector.conn.executemany(
                "INSERT OR REPLACE INTO vector_state(key,value) VALUES (?,?)", values,
            )
        self.vector.conn.commit()
        self.wiki.conn.execute(
            "UPDATE calibration_runs SET status='rolled_back' WHERE run_id=?", (run_id,)
        )
        self.wiki.conn.commit()
        return {"status": "rolled_back", "run_id": run_id}
