"""Mission YAML source-of-truth storage and external-agent workflow state."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from ai_wiki.mission_contracts import (
    MissionDocument, MissionEvent, MissionEvidence, ResearchReportPayload, TaskState, WorkPlanPayload,
    WorkRunPayload, criterion_coverage_keys, criterion_records, handoff_view,
    localization_values,
    validate_mission_authoring_quality, validate_transition,
)
from ai_wiki.storage import _atomic_write_bytes


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime | None = None) -> str:
    return (value or utc_now()).strftime("%Y-%m-%dT%H:%M:%SZ")


class MissionStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.missions_root = self.root / "missions"
        self.paths = {
            "research_report": self.missions_root / "research",
            "work_plan": self.missions_root / "plans",
            "work_run": self.missions_root / "runs",
            "knowledge_candidate": self.missions_root / "candidates",
        }
        for path in self.paths.values():
            path.mkdir(parents=True, exist_ok=True)
        data_dir = self.root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = data_dir / "missions.db"
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.initialize()

    def initialize(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS mission_documents (
                id TEXT NOT NULL,
                kind TEXT NOT NULL,
                revision INTEGER NOT NULL,
                status TEXT NOT NULL,
                file_path TEXT NOT NULL,
                modified_at TEXT NOT NULL,
                plan_id TEXT,
                run_id TEXT,
                summary_json TEXT,
                PRIMARY KEY (id, revision)
            );
            CREATE INDEX IF NOT EXISTS idx_mission_latest
                ON mission_documents(id, revision DESC);
            CREATE INDEX IF NOT EXISTS idx_mission_kind_status
                ON mission_documents(kind, status);
            CREATE TABLE IF NOT EXISTS task_leases (
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                owner TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                PRIMARY KEY (run_id, task_id)
            );
            CREATE TABLE IF NOT EXISTS resource_locks (
                resource TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                owner TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
        """)
        columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(mission_documents)")
        }
        for name in ("plan_id", "run_id", "summary_json"):
            if name not in columns:
                self.conn.execute(f"ALTER TABLE mission_documents ADD COLUMN {name} TEXT")
        self.conn.commit()
        self._backfill_index_fields()

    @staticmethod
    def _relation_values(document: MissionDocument) -> tuple[str | None, str | None]:
        if document.kind == "work_plan":
            return str(document.payload.get("plan_id") or document.id), None
        if document.kind == "work_run":
            return (
                str(document.payload.get("plan_id") or "") or None,
                str(document.payload.get("run_id") or document.id),
            )
        return None, None

    def _backfill_index_fields(self) -> None:
        rows = self.conn.execute(
            "SELECT id, revision, file_path, plan_id, run_id, summary_json "
            "FROM mission_documents"
        ).fetchall()
        for row in rows:
            try:
                existing_summary = json.loads(row["summary_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                existing_summary = {}
            if "source_language" in existing_summary:
                continue
            path = self.root / row["file_path"]
            if not path.exists():
                continue
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                document = MissionDocument.model_validate(raw, strict=True)
            except Exception:
                continue
            plan_id, run_id = self._relation_values(document)
            self.conn.execute(
                "UPDATE mission_documents SET plan_id=?, run_id=?, summary_json=? "
                "WHERE id=? AND revision=?",
                (
                    plan_id, run_id,
                    json.dumps(self._index_summary(document), ensure_ascii=False),
                    row["id"], row["revision"],
                ),
            )
        self.conn.commit()

    def _index_summary(self, document: MissionDocument) -> dict[str, Any]:
        """Return the non-sensitive summary persisted for constant-query overview reads."""
        summary: dict[str, Any] = {
            "objective": "",
            "source_language": document.metadata.source_language,
            "objective_localizations": {},
            "localization_source_revisions": {},
            "approval_status": None,
            "task_counts": {
                "total": 0, "in_progress": 0, "blocked": 0,
                "in_review": 0, "completed": 0, "skipped": 0,
            },
            "criterion_counts": {"total": 0, "covered": 0, "missing": 0},
            "evidence_count": len(document.evidence),
            "handoff_present": False,
            "degraded": False,
        }
        if document.kind == "work_plan":
            plan = WorkPlanPayload.model_validate(document.payload)
            summary["objective"] = plan.objective
            summary["objective_localizations"] = {
                item.language: item.values["objective"]
                for item in document.localizations if "objective" in item.values
            }
            summary["localization_source_revisions"] = {
                item.language: item.source_revision for item in document.localizations
            }
            summary["approval_status"] = plan.approval.status
            summary["task_counts"]["total"] = len(plan.tasks)
            summary["criterion_counts"]["total"] = len(plan.acceptance_criteria) + sum(
                len(task.acceptance_criteria) for task in plan.tasks
            )
            summary["criterion_counts"]["missing"] = summary["criterion_counts"]["total"]
            return summary
        if document.kind == "work_run":
            run = WorkRunPayload.model_validate(document.payload)
            summary["plan_revision"] = run.plan_revision
            state_counts = Counter(state.status for state in run.task_states)
            summary["task_counts"].update({
                key: int(state_counts.get(key, 0))
                for key in ("in_progress", "blocked", "in_review", "completed", "skipped")
            })
            summary["task_counts"]["total"] = len(run.task_states)
            summary["handoff_present"] = bool(run.handoff)
            try:
                pinned = self.get(run.plan_id, run.plan_revision)
                if pinned is None or pinned.kind != "work_plan":
                    raise ValueError("pinned plan unavailable")
                plan = WorkPlanPayload.model_validate(pinned.payload)
                summary["objective"] = plan.objective
                summary["source_language"] = pinned.metadata.source_language
                summary["objective_localizations"] = {
                    item.language: item.values["objective"]
                    for item in pinned.localizations if "objective" in item.values
                }
                summary["localization_source_revisions"] = {
                    item.language: item.source_revision for item in pinned.localizations
                }
                summary["approval_status"] = plan.approval.status
                evidence = {item.evidence_id: item for item in document.evidence}
                states = {item.task_id: item for item in run.task_states}
                total = covered = 0
                for criterion in criterion_records(
                    plan.acceptance_criteria, scope="global", owner_id=plan.plan_id,
                ):
                    total += 1
                    if any(
                        not criterion_coverage_keys(criterion).isdisjoint(item.criterion_ids)
                        for item in evidence.values()
                    ):
                        covered += 1
                for task in plan.tasks:
                    state = states.get(task.id)
                    linked = [
                        evidence[evidence_id] for evidence_id in (state.evidence_ids if state else [])
                        if evidence_id in evidence
                    ]
                    for criterion in criterion_records(
                        task.acceptance_criteria, scope="task",
                        owner_id=f"{plan.plan_id}:{task.id}",
                    ):
                        total += 1
                        if any(
                            not criterion_coverage_keys(criterion).isdisjoint(item.criterion_ids)
                            for item in linked
                        ):
                            covered += 1
                summary["criterion_counts"] = {
                    "total": total, "covered": covered, "missing": total - covered,
                }
            except Exception:
                summary["objective"] = "Pinned plan unavailable"
                summary["degraded"] = True
            return summary
        if document.kind == "research_report":
            scope = document.payload.get("scope") or []
            summary["objective"] = str(scope[0]) if scope else document.id
            summary["objective_localizations"] = {
                item.language: (
                    item.values.get("summary") or item.values.get("scope.0")
                )
                for item in document.localizations
                if item.values.get("summary") or item.values.get("scope.0")
            }
            summary["localization_source_revisions"] = {
                item.language: item.source_revision for item in document.localizations
            }
            return summary
        if document.kind == "knowledge_candidate":
            summary["objective"] = str(document.payload.get("reusable_summary") or document.id)
            summary["objective_localizations"] = {
                item.language: item.values["reusable_summary"]
                for item in document.localizations if "reusable_summary" in item.values
            }
            summary["localization_source_revisions"] = {
                item.language: item.source_revision for item in document.localizations
            }
            return summary
        return summary

    def _path(self, document: MissionDocument) -> Path:
        base = self.paths[document.kind]
        target = base / document.id / f"r{document.revision}.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _write(self, document: MissionDocument) -> Path:
        target = self._path(document)
        serialized = yaml.safe_dump(
            document.model_dump(mode="json"), allow_unicode=True,
            default_flow_style=False, sort_keys=False,
        ).encode("utf-8")
        _atomic_write_bytes(target, serialized)
        relative = target.relative_to(self.root).as_posix()
        plan_id, run_id = self._relation_values(document)
        summary_json = json.dumps(self._index_summary(document), ensure_ascii=False)
        self.conn.execute(
            """INSERT OR REPLACE INTO mission_documents
               (id, kind, revision, status, file_path, modified_at, plan_id, run_id, summary_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (document.id, document.kind, document.revision, document.status,
             relative, document.metadata.modified_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
             plan_id, run_id, summary_json),
        )
        self.conn.commit()
        return target

    def create(self, raw: dict[str, Any], *, dry_run: bool = False) -> MissionDocument:
        document = MissionDocument.model_validate(raw, strict=True)
        validate_mission_authoring_quality(document)
        if self.get(document.id) is not None:
            raise ValueError("mission_already_exists")
        if not dry_run:
            self._write(document)
        return document

    def get(self, mission_id: str, revision: int | None = None) -> MissionDocument | None:
        if revision is None:
            row = self.conn.execute(
                "SELECT file_path FROM mission_documents WHERE id=? ORDER BY revision DESC LIMIT 1",
                (mission_id,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT file_path FROM mission_documents WHERE id=? AND revision=?",
                (mission_id, revision),
            ).fetchone()
        if not row:
            return None
        path = self.root / row["file_path"]
        if not path.exists():
            return None
        return MissionDocument.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")), strict=True)

    def list(self, *, kind: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        clauses = ["d.revision=(SELECT MAX(x.revision) FROM mission_documents x WHERE x.id=d.id)"]
        params: list[Any] = []
        if kind:
            clauses.append("d.kind=?")
            params.append(kind)
        if status:
            clauses.append("d.status=?")
            params.append(status)
        rows = self.conn.execute(
            f"SELECT d.* FROM mission_documents d WHERE {' AND '.join(clauses)} "
            "ORDER BY d.modified_at DESC", params,
        ).fetchall()
        return [dict(row) for row in rows]

    def list_page(
        self, *, kind: str | None = None, status: str | None = None,
        plan_id: str | None = None, run_id: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> dict[str, Any]:
        if limit < 1 or limit > 100:
            raise ValueError("mission_limit_out_of_range")
        if offset < 0:
            raise ValueError("mission_offset_out_of_range")
        clauses = ["d.revision=(SELECT MAX(x.revision) FROM mission_documents x WHERE x.id=d.id)"]
        params: list[Any] = []
        for column, value in (
            ("kind", kind), ("status", status), ("plan_id", plan_id), ("run_id", run_id),
        ):
            if value:
                clauses.append(f"d.{column}=?")
                params.append(value)
        if kind is None and status is None and plan_id is None and run_id is None:
            clauses.append(
                "(d.kind!='work_run' OR d.plan_id IS NULL OR NOT EXISTS ("
                "SELECT 1 FROM mission_documents p WHERE p.kind='work_plan' "
                "AND p.plan_id=d.plan_id))"
            )
        where = " AND ".join(clauses)
        total = int(self.conn.execute(
            f"SELECT COUNT(*) FROM mission_documents d WHERE {where}", params,
        ).fetchone()[0])
        rows = self.conn.execute(
            f"SELECT d.*, CASE WHEN d.kind='work_plan' THEN ("
            "SELECT COUNT(*) FROM mission_documents r WHERE r.kind='work_run' "
            "AND r.plan_id=d.plan_id AND r.revision=(SELECT MAX(rr.revision) "
            "FROM mission_documents rr WHERE rr.id=r.id)) ELSE 0 END AS linked_run_count "
            f"FROM mission_documents d WHERE {where} "
            "ORDER BY d.modified_at DESC, d.id ASC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        items = [dict(row) for row in rows]
        return {
            "items": items, "total": total, "limit": limit, "offset": offset,
            "has_more": offset + len(items) < total,
        }

    def representative_run_record(self, plan_id: str) -> dict[str, Any] | None:
        """Return the latest completed run, or otherwise the latest run, for a plan."""
        row = self.conn.execute(
            "SELECT r.* FROM mission_documents r "
            "WHERE r.kind='work_run' AND r.plan_id=? AND "
            "r.revision=(SELECT MAX(rr.revision) FROM mission_documents rr WHERE rr.id=r.id) "
            "ORDER BY CASE WHEN r.status='completed' THEN 0 ELSE 1 END, "
            "r.modified_at DESC, r.id ASC LIMIT 1",
            (plan_id,),
        ).fetchone()
        return dict(row) if row else None

    def index_record(self, mission_id: str, revision: int | None = None) -> dict[str, Any] | None:
        if revision is None:
            row = self.conn.execute(
                "SELECT * FROM mission_documents WHERE id=? ORDER BY revision DESC LIMIT 1",
                (mission_id,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM mission_documents WHERE id=? AND revision=?",
                (mission_id, revision),
            ).fetchone()
        return dict(row) if row else None

    def patch(self, mission_id: str, operations: list[dict[str, Any]], *, if_revision: int,
              actor: str, roles: set[str], dry_run: bool = False) -> MissionDocument:
        from ai_wiki.agent_protocol import apply_json_patch

        before = self.get(mission_id)
        if before is None:
            raise ValueError("mission_not_found")
        if before.revision != if_revision:
            raise ValueError(f"mission_revision_conflict:{before.revision}")
        raw, _ = apply_json_patch(before.model_dump(mode="json"), operations)
        raw["revision"] = before.revision + 1
        raw["metadata"]["modified_at"] = utc_text()
        after = MissionDocument.model_validate(raw, strict=True)
        validate_mission_authoring_quality(after)
        validate_transition(before, after, roles=roles)
        event = MissionEvent(
            event_id=uuid.uuid4().hex,
            actor=actor,
            at=utc_now(),
            previous_status=before.status,
            new_status=after.status,
            reason="mission JSON patch",
            plan_revision=after.revision if after.kind == "work_plan" else None,
        )
        event.event_hash = event.canonical_hash()
        after.history.append(event)
        if after.kind == "work_run":
            after.payload.setdefault("execution_events", []).append(event.model_dump(mode="python"))
        # Validate once more after appending the immutable authorization event.
        after = MissionDocument.model_validate(after.model_dump(mode="python"), strict=True)
        if after.kind == "work_run":
            self._validate_completion_coverage(after)
        if not dry_run:
            self._write(after)
        return after

    def _validate_completion_coverage(self, run: MissionDocument) -> None:
        payload = WorkRunPayload.model_validate(run.payload)
        plan = self.get(payload.plan_id, payload.plan_revision)
        if plan is None:
            raise ValueError("pinned_plan_revision_missing")
        plan_payload = WorkPlanPayload.model_validate(plan.payload)
        criteria = {
            task.id: criterion_records(
                task.acceptance_criteria, scope="task",
                owner_id=f"{plan_payload.plan_id}:{task.id}",
            )
            for task in plan_payload.tasks
        }
        evidence = {item.evidence_id: item for item in run.evidence}
        for task in payload.task_states:
            if task.status != "completed":
                continue
            linked = [evidence[item] for item in task.evidence_ids if item in evidence]
            covered = {criterion for item in linked for criterion in item.criterion_ids}
            missing = [
                criterion.id for criterion in criteria.get(task.task_id, [])
                if criterion_coverage_keys(criterion).isdisjoint(covered)
            ]
            if missing:
                raise ValueError(
                    f"completed task lacks criterion evidence: {task.task_id}:{sorted(missing)}"
                )

    def start_run(self, plan_id: str, *, actor: str, run_id: str | None = None,
                  dry_run: bool = False) -> MissionDocument:
        plan = self.get(plan_id)
        if plan is None or plan.kind != "work_plan":
            raise ValueError("plan_not_found")
        if plan.status not in {"approved", "active"}:
            raise ValueError("plan_not_approved")
        payload = WorkPlanPayload.model_validate(plan.payload)
        now = utc_now()
        run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        raw = {
            "mission_schema_version": 1,
            "kind": "work_run",
            "id": run_id,
            "revision": 1,
            "status": "created",
            "metadata": {
                "created_at": now, "modified_at": now,
                "created_by": actor, "namespace": "runs",
                "source_language": plan.metadata.source_language,
            },
            "payload": {
                "run_id": run_id, "plan_id": plan.id, "plan_revision": plan.revision,
                "started_by": actor, "started_at": None,
                "task_states": [
                    {"task_id": task.id, "status": "planned", "attempt": 0}
                    for task in payload.tasks
                ],
                "execution_events": [], "artifacts": [], "handoff": {},
            },
            "evidence": [], "history": [],
        }
        run = self.create(raw, dry_run=dry_run)
        if not dry_run and plan.status == "approved":
            self.patch(
                plan.id, [{"op": "replace", "path": "/status", "value": "active"}],
                if_revision=plan.revision, actor=actor, roles={"agent"},
            )
        return run

    def ready_tasks(self, run_id: str) -> list[str]:
        run = self.get(run_id)
        if run is None or run.kind != "work_run":
            raise ValueError("run_not_found")
        payload = WorkRunPayload.model_validate(run.payload)
        plan = self.get(payload.plan_id, payload.plan_revision)
        if plan is None:
            raise ValueError("pinned_plan_revision_missing")
        plan_payload = WorkPlanPayload.model_validate(plan.payload)
        states = {item.task_id: item.status for item in payload.task_states}
        ready = []
        for task in plan_payload.tasks:
            if states.get(task.id) not in {"planned", "ready"}:
                continue
            if all(states.get(dep) == "completed" for dep in task.dependencies):
                ready.append(task.id)
        return ready

    def _expire_leases(self) -> None:
        now = utc_text()
        self.conn.execute("DELETE FROM resource_locks WHERE expires_at<=?", (now,))
        self.conn.execute("DELETE FROM task_leases WHERE expires_at<=?", (now,))
        self.conn.commit()

    def claim(self, run_id: str, task_id: str, *, owner: str, ttl_seconds: int = 900) -> dict[str, Any]:
        if task_id not in self.ready_tasks(run_id):
            raise ValueError("task_not_ready")
        run = self.get(run_id)
        payload = WorkRunPayload.model_validate(run.payload)
        plan = self.get(payload.plan_id, payload.plan_revision)
        plan_payload = WorkPlanPayload.model_validate(plan.payload)
        task = next(item for item in plan_payload.tasks if item.id == task_id)
        now = utc_now()
        expires = now + timedelta(seconds=ttl_seconds)
        self._expire_leases()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            existing = self.conn.execute(
                "SELECT owner FROM task_leases WHERE run_id=? AND task_id=?",
                (run_id, task_id),
            ).fetchone()
            if existing:
                raise ValueError("task_already_claimed")
            for resource in task.resources:
                lock = self.conn.execute(
                    "SELECT owner FROM resource_locks WHERE resource=?", (resource,),
                ).fetchone()
                if lock:
                    raise ValueError(f"resource_locked:{resource}")
            self.conn.execute(
                "INSERT INTO task_leases VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, task_id, owner, utc_text(now), utc_text(now), utc_text(expires)),
            )
            for resource in task.resources:
                self.conn.execute(
                    "INSERT INTO resource_locks VALUES (?, ?, ?, ?, ?)",
                    (resource, run_id, task_id, owner, utc_text(expires)),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return {"run_id": run_id, "task_id": task_id, "owner": owner, "expires_at": utc_text(expires)}

    def heartbeat(self, run_id: str, task_id: str, *, owner: str, ttl_seconds: int = 900) -> dict[str, Any]:
        now = utc_now()
        expires = now + timedelta(seconds=ttl_seconds)
        updated = self.conn.execute(
            """UPDATE task_leases SET heartbeat_at=?, expires_at=?
               WHERE run_id=? AND task_id=? AND owner=?""",
            (utc_text(now), utc_text(expires), run_id, task_id, owner),
        ).rowcount
        if not updated:
            raise ValueError("lease_not_owned")
        self.conn.execute(
            "UPDATE resource_locks SET expires_at=? WHERE run_id=? AND task_id=? AND owner=?",
            (utc_text(expires), run_id, task_id, owner),
        )
        self.conn.commit()
        return {"run_id": run_id, "task_id": task_id, "expires_at": utc_text(expires)}

    def release(self, run_id: str, task_id: str, *, owner: str) -> None:
        self.conn.execute(
            "DELETE FROM task_leases WHERE run_id=? AND task_id=? AND owner=?",
            (run_id, task_id, owner),
        )
        self.conn.execute(
            "DELETE FROM resource_locks WHERE run_id=? AND task_id=? AND owner=?",
            (run_id, task_id, owner),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


class MissionControlReader:
    """Build revision-pinned, policy-filtered Mission Control read models."""

    def __init__(self, store: MissionStore, policy, principal, display_language: str | None = None):
        from ai_wiki.language import SUPPORTED_WIKI_LANGUAGES, wiki_language

        self.store = store
        self.policy = policy
        self.principal = principal
        self.display_language = (
            display_language if display_language in SUPPORTED_WIKI_LANGUAGES
            else wiki_language(store.root)
        )

    def _language_state(
        self, source_language: str, localizations: dict[str, str],
        source_revisions: dict[str, int | None] | None = None,
    ) -> dict[str, Any]:
        localized = bool(localizations.get(self.display_language))
        if localized:
            mode = "localized"
        elif source_language == self.display_language:
            mode = "source"
        elif source_language == "und":
            mode = "legacy_source"
        else:
            mode = "fallback_source"
        return {
            "source_language": source_language,
            "display_language": self.display_language,
            "mode": mode,
            "fallback": mode in {"legacy_source", "fallback_source"},
            "available_localizations": sorted(localizations),
            "source_available": True,
            "localization_source_revision": (source_revisions or {}).get(
                self.display_language,
            ),
        }

    @staticmethod
    def _namespace(kind: str) -> str:
        from ai_wiki.policy import namespace_for_kind
        return namespace_for_kind(kind)

    def list(
        self, *, kind: str | None = None, status: str | None = None,
        plan_id: str | None = None, run_id: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> dict[str, Any]:
        page = self.store.list_page(
            kind=kind, status=status, plan_id=plan_id, run_id=run_id,
            limit=limit, offset=offset,
        )
        items = []
        for row in page["items"]:
            self.policy.authorize(self.principal, "read", self._namespace(row["kind"]))
            try:
                summary = json.loads(row.get("summary_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                summary = {"degraded": True}
            if row["kind"] == "work_plan":
                summary["linked_run_count"] = int(row.get("linked_run_count") or 0)
                representative = self.store.representative_run_record(
                    str(row.get("plan_id") or row["id"]),
                )
                if representative is not None:
                    try:
                        run_summary = json.loads(
                            representative.get("summary_json") or "{}",
                        )
                    except (TypeError, json.JSONDecodeError):
                        run_summary = {"degraded": True}
                    for key in (
                        "task_counts", "criterion_counts", "evidence_count",
                        "handoff_present", "degraded",
                    ):
                        if key in run_summary:
                            summary[key] = run_summary[key]
                    summary["representative_run"] = {
                        "id": representative["id"],
                        "revision": representative["revision"],
                        "status": representative["status"],
                    }
            source_objective = summary.get("objective", "")
            localized = summary.get("objective_localizations") or {}
            language = self._language_state(
                str(summary.get("source_language") or "und"), localized,
                summary.get("localization_source_revisions") or {},
            )
            summary["source_objective"] = source_objective
            summary["objective"] = localized.get(self.display_language, source_objective)
            summary["language"] = language
            items.append({
                "id": row["id"], "kind": row["kind"], "revision": row["revision"],
                "status": row["status"], "modified_at": row["modified_at"],
                "plan_id": row.get("plan_id"), "run_id": row.get("run_id"),
                "summary": summary,
            })
        return {**page, "items": items}

    def detail(self, mission_id: str, revision: int | None = None) -> dict[str, Any] | None:
        record = self.store.index_record(mission_id, revision)
        if record is None:
            return None
        self.policy.authorize(self.principal, "read", self._namespace(record["kind"]))
        try:
            document = self.store.get(mission_id, revision)
        except Exception as exc:
            return self._degraded_index_record(record, "corrupt_mission", str(exc))
        if document is None:
            return self._degraded_index_record(
                record, "indexed_file_missing", "Mission index points to a missing revision file",
            )

        detail: dict[str, Any] = {
            "id": document.id,
            "kind": document.kind,
            "revision": document.revision,
            "status": document.status,
            "metadata": document.metadata.model_dump(mode="json"),
            "degraded": [],
            "summary": {},
            "plan": None,
            "run": None,
            "research": None,
            "task_counts": {},
            "criterion_counts": {},
            "tasks": [],
            "evidence": [item.model_dump(mode="json") for item in document.evidence],
            "handoff": handoff_view({}),
            "history": [item.model_dump(mode="json") for item in document.history],
            "execution_events": [],
            "review_decisions": [],
            "payload": deepcopy(document.payload),
            "language": self._language_state(
                document.metadata.source_language,
                {item.language: "available" for item in document.localizations},
                {item.language: item.source_revision for item in document.localizations},
            ),
        }
        if document.kind == "work_plan":
            self._add_plan_detail(detail, document)
            self._localize_plan_detail(detail, document)
        elif document.kind == "work_run":
            self._add_run_detail(detail, document)
        elif document.kind == "research_report":
            self._add_research_detail(detail, document)
            self._localize_research_detail(detail, document)
        elif document.kind == "knowledge_candidate":
            detail["summary"] = {
                "objective": document.payload.get("reusable_summary", ""),
                "action": document.payload.get("action"),
                "verification_status": document.payload.get("verification_status"),
            }
            values = localization_values(document, self.display_language)
            if values.get("reusable_summary"):
                detail["summary"]["objective"] = values["reusable_summary"]
        self._prepare_evidence_and_history(detail)
        output, _ = self.policy.redact_mission_detail(self.principal, detail)
        return output

    def _degraded_index_record(
        self, record: dict[str, Any], code: str, message: str,
    ) -> dict[str, Any]:
        detail = {
            "id": record["id"], "kind": record["kind"],
            "revision": record["revision"], "status": record["status"],
            "metadata": {"modified_at": record["modified_at"]},
            "degraded": [{
                "code": code,
                "message": message,
                "recovery": "Restore or repair this exact indexed Mission revision, validate it, and retry the read-only request.",
            }],
            "summary": {}, "plan": None, "run": None, "research": None, "task_counts": {},
            "criterion_counts": {}, "tasks": [], "evidence": [],
            "handoff": handoff_view({}), "history": [], "execution_events": [],
            "review_decisions": [],
            "payload": {},
            "language": self._language_state("und", {}),
        }
        output, _ = self.policy.redact_mission_detail(self.principal, detail)
        return output

    def _prepare_evidence_and_history(self, detail: dict[str, Any]) -> None:
        criterion_links: dict[str, list[dict[str, str]]] = {}
        finding_links: dict[str, list[dict[str, str]]] = {}
        plan = detail.get("plan") or {}
        for criterion in plan.get("global_criteria", []):
            link = {
                "criterion_id": criterion["id"],
                "task_id": "GLOBAL",
                "text": criterion["text"],
                "anchor": f"criterion-global-{criterion['id']}",
            }
            for evidence_id in criterion.get("evidence_ids", []):
                criterion_links.setdefault(evidence_id, []).append(link)
        for task in detail.get("tasks", []):
            for criterion in task.get("criteria", []):
                link = {
                    "criterion_id": criterion["id"],
                    "task_id": task["id"],
                    "text": criterion["text"],
                    "anchor": f"criterion-{task['id']}-{criterion['id']}",
                }
                for evidence_id in criterion.get("evidence_ids", []):
                    criterion_links.setdefault(evidence_id, []).append(link)
        for finding in (detail.get("research") or {}).get("findings", []):
            link = {
                "finding_id": finding["id"],
                "title": finding["title"],
                "anchor": f"finding-{finding['id']}",
            }
            for evidence_ref in finding.get("evidence_refs", []):
                if evidence_ref.get("state") == "available":
                    finding_links.setdefault(evidence_ref["value"], []).append(link)
        for evidence in detail.get("evidence", []):
            evidence["criterion_links"] = sorted(
                criterion_links.get(evidence["evidence_id"], []),
                key=lambda item: (item["task_id"], item["criterion_id"]),
            )
            evidence["finding_links"] = sorted(
                finding_links.get(evidence["evidence_id"], []),
                key=lambda item: item["finding_id"],
            )

        event_key = lambda item: (str(item.get("at") or ""), str(item.get("event_id") or ""))
        detail["history"] = sorted(detail.get("history", []), key=event_key)
        detail["execution_events"] = sorted(detail.get("execution_events", []), key=event_key)
        reviewer_ids = {
            principal.id for principal in self.policy.principals.values()
            if principal.roles.intersection({"owner", "reviewer"})
        }
        detail["review_decisions"] = [
            item for item in detail["history"] if item.get("actor") in reviewer_ids
        ]

    @staticmethod
    def _criterion_dict(criterion, *, status: str, evidence_ids: list[str]) -> dict[str, Any]:
        return {
            **criterion.model_dump(mode="json"),
            "coverage_status": status,
            "evidence_ids": evidence_ids,
            "evidence_count": len(evidence_ids),
        }

    def _plan_tasks(
        self, plan: WorkPlanPayload, *, states: dict[str, TaskState] | None = None,
        evidence: dict[str, MissionEvidence] | None = None,
    ) -> list[dict[str, Any]]:
        states = states or {}
        evidence = evidence or {}
        tasks: list[dict[str, Any]] = []
        for task in plan.tasks:
            state = states.get(task.id)
            linked_ids = list(state.evidence_ids) if state else []
            linked = [evidence[item] for item in linked_ids if item in evidence]
            criteria = []
            for criterion in criterion_records(
                task.acceptance_criteria, scope="task",
                owner_id=f"{plan.plan_id}:{task.id}",
            ):
                matched = [
                    item.evidence_id for item in linked
                    if not criterion_coverage_keys(criterion).isdisjoint(item.criterion_ids)
                ]
                if matched and state and state.status == "completed":
                    coverage = "covered"
                elif matched and state and state.status == "in_review":
                    coverage = "pending_review"
                elif matched:
                    coverage = "evidence_attached"
                else:
                    coverage = "missing"
                criteria.append(self._criterion_dict(
                    criterion, status=coverage, evidence_ids=matched,
                ))
            task_data = task.model_dump(mode="json")
            task_data.pop("acceptance_criteria", None)
            tasks.append({
                **task_data,
                "status": state.status if state else "planned",
                "attempt": state.attempt if state else 0,
                "assigned_to": state.assigned_to if state else None,
                "started_at": state.started_at.isoformat() if state and state.started_at else None,
                "completed_at": state.completed_at.isoformat() if state and state.completed_at else None,
                "result": state.result if state else "",
                "evidence_ids": linked_ids,
                "criteria": criteria,
            })
        return tasks

    def _global_criteria(
        self, plan: WorkPlanPayload, *, evidence: dict[str, MissionEvidence] | None = None,
        run_status: str | None = None,
    ) -> list[dict[str, Any]]:
        evidence = evidence or {}
        criteria: list[dict[str, Any]] = []
        for criterion in criterion_records(
            plan.acceptance_criteria, scope="global", owner_id=plan.plan_id,
        ):
            matched = [
                item.evidence_id for item in evidence.values()
                if not criterion_coverage_keys(criterion).isdisjoint(item.criterion_ids)
            ]
            if not matched and run_status is None:
                coverage = "not_evaluated"
            elif matched and run_status == "completed":
                coverage = "covered"
            elif matched and run_status == "in_review":
                coverage = "pending_review"
            elif matched:
                coverage = "evidence_attached"
            else:
                coverage = "missing"
            criteria.append(self._criterion_dict(
                criterion, status=coverage, evidence_ids=matched,
            ))
        return criteria

    @staticmethod
    def _counts(
        tasks: list[dict[str, Any]], global_criteria: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, int], dict[str, int]]:
        task_counts = Counter(task["status"] for task in tasks)
        task_counts["ready"] = sum(bool(task.get("ready")) for task in tasks)
        task_counts["total"] = len(tasks)
        for status in (
            "in_progress", "blocked", "in_review", "completed", "skipped",
        ):
            task_counts.setdefault(status, 0)
        criterion_counts = Counter(
            criterion["coverage_status"]
            for criterion in [
                *(global_criteria or []),
                *(criterion for task in tasks for criterion in task["criteria"]),
            ]
        )
        criterion_counts["total"] = len(global_criteria or []) + sum(
            len(task["criteria"]) for task in tasks
        )
        criterion_counts["covered"] = criterion_counts.get("covered", 0)
        criterion_counts["missing"] = criterion_counts.get("missing", 0)
        return dict(task_counts), dict(criterion_counts)

    def _add_plan_detail(self, detail: dict[str, Any], document: MissionDocument) -> None:
        plan = WorkPlanPayload.model_validate(document.payload)
        tasks = self._plan_tasks(plan)
        global_criteria = self._global_criteria(plan)
        task_counts, criterion_counts = self._counts(tasks, global_criteria)
        runs = self.store.list_page(kind="work_run", plan_id=plan.plan_id, limit=100)["items"]
        representative_record = self.store.representative_run_record(plan.plan_id)
        representative = None
        representative_handoff_present = False
        if representative_record is not None:
            try:
                run_document = self.store.get(
                    representative_record["id"], representative_record["revision"],
                )
                if run_document is not None and run_document.kind == "work_run":
                    run = WorkRunPayload.model_validate(run_document.payload)
                    states = {item.task_id: item for item in run.task_states}
                    evidence = {item.evidence_id: item for item in run_document.evidence}
                    tasks = self._plan_tasks(plan, states=states, evidence=evidence)
                    global_criteria = self._global_criteria(
                        plan, evidence=evidence, run_status=run_document.status,
                    )
                    task_counts, criterion_counts = self._counts(tasks, global_criteria)
                    detail["evidence"] = [
                        item.model_dump(mode="json") for item in run_document.evidence
                    ]
                    detail["handoff"] = handoff_view(run.handoff)
                    representative_handoff_present = bool(run.handoff)
                    detail["execution_events"] = [
                        item.model_dump(mode="json") for item in run.execution_events
                    ]
                    representative = {
                        "id": run.run_id, "revision": run_document.revision,
                        "status": run_document.status,
                        "plan_revision": run.plan_revision,
                    }
            except Exception as exc:
                detail["degraded"].append({
                    "code": "representative_run_unavailable",
                    "message": str(exc),
                    "recovery": "Open the linked WorkRun directly and repair its latest indexed revision.",
                })
        detail.update({
            "summary": {
                "objective": plan.objective,
                "approval": plan.approval.model_dump(mode="json"),
                "scope": plan.scope,
                "constraints": plan.constraints,
                "linked_run_count": len(runs),
                "representative_run": representative,
            },
            "plan": {
                "id": plan.plan_id, "revision": document.revision,
                "global_criteria": global_criteria,
                "linked_runs": [
                    {"id": row["id"], "revision": row["revision"], "status": row["status"]}
                    for row in runs
                ],
            },
            "tasks": tasks, "task_counts": task_counts,
            "criterion_counts": criterion_counts,
            "evidence_count": len(detail["evidence"]),
            "handoff_count": int(representative_handoff_present),
        })
        if representative is not None:
            self._prepare_handoff(detail, run_document)

    def _add_research_detail(
        self, detail: dict[str, Any], document: MissionDocument,
    ) -> None:
        report = ResearchReportPayload.model_validate(document.payload)
        known_evidence = {item.evidence_id for item in document.evidence}
        findings = []
        for finding in report.findings:
            evidence_refs = []
            for evidence_id in finding.evidence_ids:
                available = evidence_id in known_evidence
                evidence_refs.append({
                    "value": evidence_id,
                    "state": "available" if available else "unavailable",
                    "href": f"#evidence-{evidence_id}" if available else None,
                    "reason": "" if available else "evidence_not_found",
                })
            findings.append({
                **finding.model_dump(mode="json"),
                "evidence_refs": evidence_refs,
            })
        detail.update({
            "summary": {
                "objective": report.scope[0],
                "finding_count": len(findings),
                "sufficient": report.sufficient,
            },
            "research": {
                "workspace_root": report.workspace_root,
                "scope": report.scope,
                "excluded_scope": report.excluded_scope,
                "findings": findings,
                "recommendations": report.recommendations,
                "uncertainties": report.uncertainties,
                "sufficient": report.sufficient,
            },
            "evidence_count": len(document.evidence),
            "handoff_count": 0,
        })

    @staticmethod
    def _replace_indexed(values: dict[str, str], prefix: str, items: list[str]) -> list[str]:
        return [values.get(f"{prefix}.{index}", value) for index, value in enumerate(items)]

    def _localize_research_detail(
        self, detail: dict[str, Any], document: MissionDocument,
    ) -> None:
        values = localization_values(document, self.display_language)
        if not values:
            return
        research = detail["research"]
        research["scope"] = self._replace_indexed(values, "scope", research["scope"])
        research["excluded_scope"] = self._replace_indexed(
            values, "excluded_scope", research["excluded_scope"],
        )
        for finding in research["findings"]:
            finding["title"] = values.get(f"findings.{finding['id']}.title", finding["title"])
            finding["detail"] = values.get(f"findings.{finding['id']}.detail", finding["detail"])
        research["recommendations"] = self._replace_indexed(
            values, "recommendations", research["recommendations"],
        )
        research["uncertainties"] = self._replace_indexed(
            values, "uncertainties", research["uncertainties"],
        )
        detail["summary"]["objective"] = values.get(
            "summary", values.get("scope.0", detail["summary"]["objective"]),
        )

    def _localize_plan_detail(
        self, detail: dict[str, Any], document: MissionDocument,
    ) -> None:
        values = localization_values(document, self.display_language)
        detail["plan_language"] = self._language_state(
            document.metadata.source_language,
            {item.language: "available" for item in document.localizations},
            {item.language: item.source_revision for item in document.localizations},
        )
        if not values:
            return
        detail["summary"]["objective"] = values.get(
            "objective", detail["summary"]["objective"],
        )
        detail["summary"]["scope"] = self._replace_indexed(
            values, "scope", detail["summary"].get("scope", []),
        )
        detail["summary"]["constraints"] = self._replace_indexed(
            values, "constraints", detail["summary"].get("constraints", []),
        )
        for criterion in (detail.get("plan") or {}).get("global_criteria", []):
            criterion["text"] = values.get(
                f"global_criteria.{criterion['id']}", criterion["text"],
            )
        for task in detail.get("tasks", []):
            prefix = f"tasks.{task['id']}"
            task["title"] = values.get(f"{prefix}.title", task["title"])
            task["instructions"] = values.get(f"{prefix}.instructions", task["instructions"])
            task["verification"] = self._replace_indexed(
                values, f"{prefix}.verification", task.get("verification", []),
            )
            task["authorization"] = self._replace_indexed(
                values, f"{prefix}.authorization", task.get("authorization", []),
            )
            for criterion in task.get("criteria", []):
                criterion["text"] = values.get(
                    f"{prefix}.criteria.{criterion['id']}", criterion["text"],
                )

    def _localize_run_detail(
        self, detail: dict[str, Any], document: MissionDocument,
    ) -> None:
        values = localization_values(document, self.display_language)
        if not values:
            return
        for task in detail.get("tasks", []):
            task["result"] = values.get(f"tasks.{task['id']}.result", task["result"])
        handoff = detail.get("handoff") or {}
        handoff["current_state"] = values.get(
            "handoff.current_state", handoff.get("current_state", ""),
        )
        handoff["remaining_work"] = self._replace_indexed(
            values, "handoff.remaining_work", handoff.get("remaining_work", []),
        )
        handoff["blockers"] = self._replace_indexed(
            values, "handoff.blockers", handoff.get("blockers", []),
        )

    def _add_run_detail(self, detail: dict[str, Any], document: MissionDocument) -> None:
        run = WorkRunPayload.model_validate(document.payload)
        detail["run"] = {
            "id": run.run_id, "plan_id": run.plan_id,
            "plan_revision": run.plan_revision, "started_by": run.started_by,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "artifacts": run.artifacts,
        }
        detail["handoff"] = handoff_view(run.handoff)
        self._prepare_handoff(detail, document)
        detail["execution_events"] = [
            item.model_dump(mode="json") for item in run.execution_events
        ]
        detail["evidence_count"] = len(document.evidence)
        detail["handoff_count"] = int(bool(run.handoff))
        pinned_error = False
        try:
            pinned = self.store.get(run.plan_id, run.plan_revision)
        except Exception as exc:
            pinned = None
            pinned_error = True
            detail["degraded"].append({
                "code": "corrupt_pinned_plan", "message": str(exc),
                "recovery": "Restore or repair the indexed pinned revision, then re-open this read-only view. Do not substitute the latest plan.",
            })
        if pinned is None and not pinned_error:
            detail["degraded"].append({
                "code": "missing_pinned_plan",
                "message": f"Pinned plan revision is unavailable: {run.plan_id}@{run.plan_revision}",
                "recovery": "Restore the exact pinned plan revision or create a newly approved run; never infer task instructions from a newer plan.",
            })
        if pinned is None:
            tasks = [
                {
                    "id": state.task_id, "title": state.task_id,
                    "instructions": "", "dependencies": [], "verification": [],
                    "authorization": [], "resources": [], "status": state.status,
                    "attempt": state.attempt, "assigned_to": state.assigned_to,
                    "started_at": state.started_at.isoformat() if state.started_at else None,
                    "completed_at": state.completed_at.isoformat() if state.completed_at else None,
                    "result": state.result, "evidence_ids": state.evidence_ids,
                    "criteria": [],
                }
                for state in run.task_states
            ]
            detail["summary"] = {"objective": "Pinned plan unavailable"}
            detail["tasks"] = tasks
            detail["task_counts"], detail["criterion_counts"] = self._counts(tasks)
            self._localize_run_detail(detail, document)
            return
        if pinned.kind != "work_plan":
            detail["degraded"].append({
                "code": "invalid_pinned_plan_kind", "message": pinned.kind,
                "recovery": "Repair the run reference so it points to an approved WorkPlan revision.",
            })
            return
        try:
            plan = WorkPlanPayload.model_validate(pinned.payload)
        except Exception as exc:
            detail["degraded"].append({
                "code": "corrupt_pinned_plan", "message": str(exc),
                "recovery": "Repair the exact pinned plan revision and validate it before review.",
            })
            detail["summary"] = {"objective": "Pinned plan is corrupt"}
            return
        states = {item.task_id: item for item in run.task_states}
        evidence = {item.evidence_id: item for item in document.evidence}
        tasks = self._plan_tasks(plan, states=states, evidence=evidence)
        completed = {task["id"] for task in tasks if task["status"] == "completed"}
        for task in tasks:
            if task["status"] in {"planned", "ready"} and all(
                dependency in completed for dependency in task["dependencies"]
            ):
                task["ready"] = True
            else:
                task["ready"] = False
        global_criteria = self._global_criteria(
            plan, evidence=evidence, run_status=document.status,
        )
        detail.update({
            "summary": {
                "objective": plan.objective,
                "approval": plan.approval.model_dump(mode="json"),
                "scope": plan.scope,
                "constraints": plan.constraints,
            },
            "plan": {
                "id": plan.plan_id,
                "revision": run.plan_revision,
                "global_criteria": global_criteria,
            },
            "tasks": tasks,
        })
        detail["task_counts"], detail["criterion_counts"] = self._counts(
            tasks, global_criteria,
        )
        self._localize_plan_detail(detail, pinned)
        detail["run_language"] = detail["language"]
        detail["language"] = detail["plan_language"]
        self._localize_run_detail(detail, document)

    def _prepare_handoff(self, detail: dict[str, Any], document: MissionDocument) -> None:
        handoff = detail["handoff"]
        required = ("current_state", "recorded_by", "recorded_at")
        missing_fields = [field for field in required if not handoff.get(field)]
        stale = False
        recorded_at = handoff.get("recorded_at")
        if recorded_at:
            try:
                parsed = datetime.fromisoformat(str(recorded_at).replace("Z", "+00:00"))
                stale = utc_now() - parsed.astimezone(timezone.utc) > timedelta(days=7)
            except (TypeError, ValueError):
                missing_fields.append("recorded_at")
        if handoff.get("mode") == "empty":
            state = "missing"
        elif missing_fields:
            state = "incomplete"
        elif stale:
            state = "stale"
        else:
            state = "complete"
        handoff["completeness"] = {
            "state": state, "missing_fields": sorted(set(missing_fields)), "stale": stale,
        }
        known_evidence = {item["evidence_id"] for item in detail.get("evidence", [])}
        handoff["evidence_refs"] = [
            {
                "value": evidence_id,
                "state": "available" if evidence_id in known_evidence else "unavailable",
                "href": f"#evidence-{evidence_id}" if evidence_id in known_evidence else None,
                "reason": "" if evidence_id in known_evidence else "evidence_not_found",
            }
            for evidence_id in handoff.get("evidence_ids", [])
        ]
        handoff["artifact_refs"] = []
        for artifact in handoff.get("artifacts", []):
            is_url = isinstance(artifact, str) and artifact.startswith(("https://", "http://"))
            handoff["artifact_refs"].append({
                "value": artifact,
                "state": "available" if is_url else "unavailable",
                "href": artifact if is_url else None,
                "reason": "" if is_url else "artifact_viewer_unavailable",
            })


def evidence_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
