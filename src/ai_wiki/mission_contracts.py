"""AI Wiki Missions contracts and transition validation."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MISSION_SCHEMA_VERSION = 1
PLAN_STATES = {"draft", "proposed", "approved", "active", "superseded", "cancelled", "completed"}
RUN_STATES = {"created", "running", "paused", "blocked", "failed", "in_review", "completed", "cancelled"}
TASK_STATES = {"planned", "ready", "in_progress", "blocked", "failed", "in_review", "completed", "skipped", "cancelled"}

PLAN_TRANSITIONS = {
    "draft": {"proposed", "cancelled"},
    "proposed": {"draft", "approved", "cancelled"},
    "approved": {"active", "superseded", "cancelled"},
    "active": {"completed", "superseded", "cancelled"},
    "superseded": set(), "cancelled": set(), "completed": set(),
}
RUN_TRANSITIONS = {
    "created": {"running", "cancelled"},
    "running": {"paused", "blocked", "failed", "in_review", "cancelled"},
    "paused": {"running", "blocked", "cancelled"},
    "blocked": {"running", "failed", "cancelled"},
    "failed": {"running", "cancelled"},
    "in_review": {"completed", "failed", "running"},
    "completed": set(), "cancelled": set(),
}
TASK_TRANSITIONS = {
    "planned": {"ready", "in_progress", "skipped", "cancelled"},
    "ready": {"in_progress", "blocked", "skipped", "cancelled"},
    "in_progress": {"in_review", "blocked", "failed"},
    "blocked": {"ready", "failed", "skipped", "cancelled"},
    "failed": {"ready", "in_progress", "cancelled"},
    "in_review": {"completed", "failed", "in_progress"},
    "completed": {"ready"}, "skipped": set(), "cancelled": set(),
}


def _dt(value: Any) -> Any:
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


class MissionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MissionEvidence(MissionModel):
    evidence_id: str = Field(min_length=1)
    type: Literal[
        "file_change", "command", "test_result", "commit", "document",
        "screenshot", "external_source", "human_decision",
    ]
    locator: str = Field(min_length=1)
    content_hash: str | None = None
    captured_at: datetime
    captured_by: str = Field(min_length=1)
    result: str = ""
    source_ids: list[str] = Field(default_factory=list)
    criterion_ids: list[str] = Field(default_factory=list)

    @field_validator("captured_at", mode="before")
    @classmethod
    def parse_date(cls, value):
        return _dt(value)


class MissionEvent(MissionModel):
    event_id: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    at: datetime
    previous_status: str | None = None
    new_status: str = Field(min_length=1)
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    plan_revision: int | None = Field(default=None, ge=1)
    event_hash: str = ""

    @field_validator("at", mode="before")
    @classmethod
    def parse_date(cls, value):
        return _dt(value)

    def canonical_hash(self) -> str:
        raw = self.model_dump(mode="json", exclude={"event_hash"})
        return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()


class MissionMetadata(MissionModel):
    created_at: datetime
    modified_at: datetime
    created_by: str = Field(min_length=1)
    namespace: Literal["plans", "runs", "artifacts"]
    source_language: Literal["ko", "en", "und"] = "und"

    @field_validator("created_at", "modified_at", mode="before")
    @classmethod
    def parse_dates(cls, value):
        return _dt(value)


class MissionLocalization(MissionModel):
    language: Literal["ko", "en"]
    source_revision: int | None = Field(default=None, ge=1)
    values: dict[str, str] = Field(min_length=1)

    @field_validator("values")
    @classmethod
    def values_are_readable(cls, values: dict[str, str]) -> dict[str, str]:
        for key, value in values.items():
            if not key.strip() or not value.strip():
                raise ValueError("localized narrative keys and values must be non-empty")
        return values


class PlanApproval(MissionModel):
    required: bool = True
    status: Literal["pending", "approved", "rejected"] = "pending"
    requested_at: datetime | None = None
    decided_at: datetime | None = None
    decided_by: str | None = None
    note: str = ""

    @field_validator("requested_at", "decided_at", mode="before")
    @classmethod
    def parse_dates(cls, value):
        return _dt(value)


class Criterion(MissionModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    scope: Literal["global", "task"]
    order: int = Field(default=0, ge=0)
    legacy_aliases: list[str] = Field(default_factory=list)


CriterionInput = str | Criterion


def legacy_criterion_id(text: str, *, scope: str, owner_id: str) -> str:
    seed = f"{scope}\0{owner_id}\0{text}".encode("utf-8")
    return f"legacy-{hashlib.sha256(seed).hexdigest()[:16]}"


def criterion_record(
    value: CriterionInput, *, scope: Literal["global", "task"],
    owner_id: str, order: int,
) -> Criterion:
    if isinstance(value, Criterion):
        if value.scope != scope:
            raise ValueError(
                f"criterion {value.id} has scope {value.scope}, expected {scope}"
            )
        return value
    return Criterion(
        id=legacy_criterion_id(value, scope=scope, owner_id=owner_id),
        text=value,
        scope=scope,
        order=order,
        legacy_aliases=[value],
    )


def criterion_records(
    values: list[CriterionInput], *, scope: Literal["global", "task"], owner_id: str,
) -> list[Criterion]:
    return [
        criterion_record(value, scope=scope, owner_id=owner_id, order=index)
        for index, value in enumerate(values)
    ]


def criterion_coverage_keys(value: Criterion) -> set[str]:
    return {value.id, value.text, *value.legacy_aliases}


class PlanTask(MissionModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    acceptance_criteria: list[CriterionInput] = Field(min_length=1)
    verification: list[str] = Field(min_length=1)
    authorization: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)


class WorkPlanPayload(MissionModel):
    plan_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    scope: list[str] = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[CriterionInput] = Field(min_length=1)
    tasks: list[PlanTask] = Field(min_length=1)
    approval: PlanApproval = Field(default_factory=PlanApproval)

    @model_validator(mode="after")
    def task_graph_is_valid(self):
        ids = [task.id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task IDs must be unique")
        known = set(ids)
        graph = {task.id: set(task.dependencies) for task in self.tasks}
        for task_id, dependencies in graph.items():
            unknown = dependencies - known
            if unknown or task_id in dependencies:
                raise ValueError(f"invalid task dependencies for {task_id}: {sorted(unknown)}")
        visiting: set[str] = set()
        visited: set[str] = set()
        def visit(task_id: str):
            if task_id in visiting:
                raise ValueError("task dependency cycle detected")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in graph[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)
        for task_id in graph:
            visit(task_id)
        criteria = criterion_records(
            self.acceptance_criteria, scope="global", owner_id=self.plan_id,
        )
        for task in self.tasks:
            criteria.extend(criterion_records(
                task.acceptance_criteria, scope="task",
                owner_id=f"{self.plan_id}:{task.id}",
            ))
        criterion_ids = [criterion.id for criterion in criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("criterion IDs must be unique within a plan")
        return self


class ResearchFinding(MissionModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class ResearchReportPayload(MissionModel):
    workspace_root: str = Field(min_length=1)
    scope: list[str] = Field(min_length=1)
    excluded_scope: list[str] = Field(default_factory=list)
    findings: list[ResearchFinding] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    sufficient: bool = False


class TaskState(MissionModel):
    task_id: str = Field(min_length=1)
    status: Literal[
        "planned", "ready", "in_progress", "blocked", "failed",
        "in_review", "completed", "skipped", "cancelled",
    ] = "planned"
    attempt: int = Field(default=0, ge=0)
    assigned_to: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: str = ""
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("started_at", "completed_at", mode="before")
    @classmethod
    def parse_dates(cls, value):
        return _dt(value)

    @model_validator(mode="after")
    def completed_has_evidence(self):
        if self.status == "completed" and not self.evidence_ids:
            raise ValueError("completed tasks require evidence")
        return self


class MissionHandoff(MissionModel):
    handoff_schema_version: Literal[1]
    current_state: str = Field(min_length=1)
    changed_files: list[str] = Field(default_factory=list)
    remaining_work: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    recorded_by: str = Field(min_length=1)
    recorded_at: datetime
    next_owner: str | None = None

    @field_validator("recorded_at", mode="before")
    @classmethod
    def parse_recorded_at(cls, value):
        return _dt(value)

    @field_validator("recorded_at")
    @classmethod
    def recorded_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("handoff recorded_at must include a timezone")
        return value


def handoff_view(value: MissionHandoff | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, MissionHandoff):
        return {"mode": "typed", **value.model_dump(mode="json")}
    if not value:
        return {
            "mode": "empty", "current_state": "", "changed_files": [],
            "remaining_work": [], "blockers": [], "evidence_ids": [],
            "artifacts": [], "recorded_by": "", "recorded_at": None,
            "next_owner": None, "legacy_fields": {},
        }
    known = {
        "current_state", "summary", "reason", "changed_files", "remaining_work",
        "blockers", "blocking_reasons", "evidence_ids", "artifacts", "recorded_by",
        "recorded_at", "next_owner",
    }
    return {
        "mode": "legacy",
        "current_state": str(
            value.get("current_state") or value.get("summary") or value.get("reason") or ""
        ),
        "changed_files": list(value.get("changed_files") or []),
        "remaining_work": list(value.get("remaining_work") or []),
        "blockers": list(value.get("blockers") or value.get("blocking_reasons") or []),
        "evidence_ids": list(value.get("evidence_ids") or []),
        "artifacts": list(value.get("artifacts") or []),
        "recorded_by": str(value.get("recorded_by") or ""),
        "recorded_at": value.get("recorded_at"),
        "next_owner": value.get("next_owner"),
        "legacy_fields": {key: item for key, item in value.items() if key not in known},
    }


class WorkRunPayload(MissionModel):
    run_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    plan_revision: int = Field(ge=1)
    started_by: str = Field(min_length=1)
    started_at: datetime | None = None
    task_states: list[TaskState] = Field(min_length=1)
    execution_events: list[MissionEvent] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    handoff: MissionHandoff | dict[str, Any] = Field(default_factory=dict)
    completed_at: datetime | None = None

    @field_validator("started_at", "completed_at", mode="before")
    @classmethod
    def parse_dates(cls, value):
        return _dt(value)

    @field_validator("handoff", mode="before")
    @classmethod
    def validate_typed_handoff(cls, value):
        if isinstance(value, dict) and "handoff_schema_version" in value:
            return MissionHandoff.model_validate(value, strict=True)
        return value


class KnowledgeCandidatePayload(MissionModel):
    source_run_id: str = Field(min_length=1)
    source_task_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(min_length=1)
    action: Literal["create", "patch"]
    target_document_id: str | None = None
    proposed_document: dict[str, Any] | None = None
    proposed_operations: list[dict[str, Any]] | None = None
    reusable_summary: str = Field(min_length=1)
    verification_status: Literal["pending", "approved", "rejected", "promoted"] = "pending"

    @model_validator(mode="after")
    def action_payload(self):
        if self.action == "create" and self.proposed_document is None:
            raise ValueError("create candidate requires proposed_document")
        if self.action == "patch" and (not self.target_document_id or not self.proposed_operations):
            raise ValueError("patch candidate requires target and operations")
        return self


Payload = ResearchReportPayload | WorkPlanPayload | WorkRunPayload | KnowledgeCandidatePayload


class MissionDocument(MissionModel):
    mission_schema_version: Literal[1]
    kind: Literal["research_report", "work_plan", "work_run", "knowledge_candidate"]
    id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    status: str = Field(min_length=1)
    metadata: MissionMetadata
    payload: dict[str, Any]
    localizations: list[MissionLocalization] = Field(default_factory=list)
    evidence: list[MissionEvidence] = Field(default_factory=list)
    history: list[MissionEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_kind(self):
        models = {
            "research_report": ResearchReportPayload,
            "work_plan": WorkPlanPayload,
            "work_run": WorkRunPayload,
            "knowledge_candidate": KnowledgeCandidatePayload,
        }
        states = {
            "research_report": {"draft", "proposed", "approved", "rejected"},
            "work_plan": PLAN_STATES,
            "work_run": RUN_STATES,
            "knowledge_candidate": {"pending", "approved", "rejected", "promoted"},
        }
        if self.status not in states[self.kind]:
            raise ValueError(f"invalid {self.kind} status: {self.status}")
        parsed = models[self.kind].model_validate(self.payload, strict=True)
        self.payload = parsed.model_dump(mode="python")
        languages = [item.language for item in self.localizations]
        if len(languages) != len(set(languages)):
            raise ValueError("Mission localization languages must be unique")
        for localization in self.localizations:
            if (
                localization.source_revision is not None
                and localization.source_revision > self.revision
            ):
                raise ValueError("localization source revision cannot be newer than Mission revision")
            invalid = [
                key for key in localization.values
                if not localization_key_allowed(self.kind, key)
            ]
            if invalid:
                raise ValueError(
                    f"invalid localized narrative keys for {self.kind}: {sorted(invalid)}"
                )
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence IDs must be unique")
        known = set(evidence_ids)
        referenced: set[str] = set()
        if self.kind == "research_report":
            for finding in parsed.findings:
                referenced.update(finding.evidence_ids)
        elif self.kind == "work_run":
            for task in parsed.task_states:
                referenced.update(task.evidence_ids)
            for event in parsed.execution_events:
                referenced.update(event.evidence_ids)
            if isinstance(parsed.handoff, MissionHandoff):
                referenced.update(parsed.handoff.evidence_ids)
        elif self.kind == "knowledge_candidate":
            referenced.update(parsed.evidence_ids)
        if referenced - known:
            raise ValueError(f"unknown mission evidence IDs: {sorted(referenced - known)}")
        return self


_LOCALIZATION_KEY_PATTERNS: dict[str, tuple[str, ...]] = {
    "research_report": (
        r"summary", r"scope\.\d+", r"excluded_scope\.\d+",
        r"findings\.[^.]+\.(?:title|detail)",
        r"recommendations\.\d+", r"uncertainties\.\d+",
    ),
    "work_plan": (
        r"objective", r"scope\.\d+", r"constraints\.\d+",
        r"global_criteria\.[^.]+",
        r"tasks\.[^.]+\.(?:title|instructions)",
        r"tasks\.[^.]+\.criteria\.[^.]+",
        r"tasks\.[^.]+\.(?:verification|authorization)\.\d+",
    ),
    "work_run": (
        r"tasks\.[^.]+\.result", r"handoff\.current_state",
        r"handoff\.(?:remaining_work|blockers)\.\d+",
    ),
    "knowledge_candidate": (r"reusable_summary",),
}


def localization_key_allowed(kind: str, key: str) -> bool:
    return any(re.fullmatch(pattern, key) for pattern in _LOCALIZATION_KEY_PATTERNS[kind])


def localization_values(document: MissionDocument, language: str) -> dict[str, str]:
    return next(
        (dict(item.values) for item in document.localizations if item.language == language),
        {},
    )


def _criterion_source_text(value: CriterionInput) -> str:
    return value.text if isinstance(value, Criterion) else value


def mission_narrative_texts(document: MissionDocument) -> list[str]:
    """Return source-language prose while excluding technical/audit fields."""
    if document.kind == "research_report":
        report = ResearchReportPayload.model_validate(document.payload)
        return [
            *report.scope,
            *report.excluded_scope,
            *[value for finding in report.findings for value in (finding.title, finding.detail)],
            *report.recommendations,
            *report.uncertainties,
        ]
    if document.kind == "work_plan":
        plan = WorkPlanPayload.model_validate(document.payload)
        return [
            plan.objective,
            *plan.scope,
            *plan.constraints,
            *[_criterion_source_text(value) for value in plan.acceptance_criteria],
            *[
                value
                for task in plan.tasks
                for value in (
                    task.title,
                    task.instructions,
                    *[_criterion_source_text(item) for item in task.acceptance_criteria],
                    *task.authorization,
                )
            ],
        ]
    if document.kind == "work_run":
        run = WorkRunPayload.model_validate(document.payload)
        texts = [state.result for state in run.task_states if state.result.strip()]
        handoff = handoff_view(run.handoff)
        texts.extend(filter(None, [
            handoff["current_state"], *handoff["remaining_work"], *handoff["blockers"],
        ]))
        return texts
    candidate = KnowledgeCandidatePayload.model_validate(document.payload)
    return [candidate.reusable_summary]


def validate_mission_authoring_quality(
    document: MissionDocument, *, expected_language: str | None = None,
) -> None:
    """Validate readable source prose for newly-authored localized Missions."""
    language = document.metadata.source_language
    if language == "und":
        return
    if any(item.source_revision is None for item in document.localizations):
        raise ValueError("localized Mission narratives require source_revision")
    if expected_language and language != expected_language:
        raise ValueError(
            f"mission source language {language} does not match wiki authoring language "
            f"{expected_language}"
        )
    if document.kind == "research_report":
        report = ResearchReportPayload.model_validate(document.payload)
        if not report.findings or not report.recommendations:
            raise ValueError("research report requires readable findings and recommendations")
        if any(len(finding.detail.strip()) < 20 for finding in report.findings):
            raise ValueError("research finding detail must explain the finding")
    if document.kind == "work_plan":
        plan = WorkPlanPayload.model_validate(document.payload)
        if len(plan.objective.strip()) < 20:
            raise ValueError("work plan objective must be a readable summary")
        if any(len(task.instructions.strip()) < 10 for task in plan.tasks):
            raise ValueError("work plan task instructions must be readable")
    texts = [text.strip() for text in mission_narrative_texts(document) if text.strip()]
    if not texts:
        return
    korean = re.compile(r"[가-힣]")
    latin = re.compile(r"[A-Za-z]")
    for localization in document.localizations:
        for value in localization.values.values():
            if localization.language == "ko":
                valid = bool(korean.search(value))
            else:
                valid = bool(latin.search(value)) and not korean.search(value)
            if not valid:
                raise ValueError(
                    f"localized narrative does not match language "
                    f"{localization.language}: {value[:80]}"
                )
    if language == "ko":
        mismatched = [text for text in texts if not korean.search(text)]
    else:
        mismatched = [text for text in texts if not latin.search(text) or korean.search(text)]
    if mismatched:
        raise ValueError(
            f"mission narrative does not match source language {language}: "
            f"{mismatched[0][:80]}"
        )


def validate_transition(before: MissionDocument, after: MissionDocument, *, roles: set[str]) -> None:
    if before.id != after.id or before.kind != after.kind:
        raise ValueError("mission identity is immutable")
    if after.revision != before.revision + 1:
        raise ValueError("mission revision must increment by one")
    before_history = [item.model_dump(mode="json") for item in before.history]
    after_history = [item.model_dump(mode="json") for item in after.history]
    if after_history[:len(before_history)] != before_history:
        raise ValueError("mission history is append-only")
    if before.kind == "work_plan":
        allowed = PLAN_TRANSITIONS[before.status]
        if after.status != before.status and after.status not in allowed:
            raise ValueError(f"invalid plan transition: {before.status}->{after.status}")
        if after.status == "approved" and not roles.intersection({"owner", "reviewer"}):
            raise PermissionError("plan approval requires owner or reviewer")
        if after.status == "approved" and before.metadata.created_by == after.payload.get("approval", {}).get("decided_by"):
            raise PermissionError("plan creators cannot approve their own plan")
    elif before.kind == "work_run":
        allowed = RUN_TRANSITIONS[before.status]
        if after.status != before.status and after.status not in allowed:
            raise ValueError(f"invalid run transition: {before.status}->{after.status}")
        old = WorkRunPayload.model_validate(before.payload)
        new = WorkRunPayload.model_validate(after.payload)
        if old.plan_id != new.plan_id or old.plan_revision != new.plan_revision:
            raise ValueError("run plan revision is immutable")
        old_events = [item.model_dump(mode="json") for item in old.execution_events]
        new_events = [item.model_dump(mode="json") for item in new.execution_events]
        if new_events[:len(old_events)] != old_events:
            raise ValueError("WorkRun execution events are append-only")
        old_states = {item.task_id: item for item in old.task_states}
        for task in new.task_states:
            previous = old_states.get(task.task_id)
            if previous and task.status != previous.status:
                if task.status not in TASK_TRANSITIONS[previous.status]:
                    raise ValueError(f"invalid task transition: {previous.status}->{task.status}")
                if task.status == "completed" and not roles.intersection({"owner", "reviewer"}):
                    raise PermissionError("task completion requires owner or reviewer")
                if previous.status == "completed" and task.status == "ready" and not roles.intersection({"owner", "reviewer"}):
                    raise PermissionError("task reopen requires owner or reviewer")


def mission_json_schema(kind: str | None = None) -> dict[str, Any]:
    if kind is None:
        return MissionDocument.model_json_schema()
    models = {
        "research-report": ResearchReportPayload,
        "work-plan": WorkPlanPayload,
        "work-run": WorkRunPayload,
        "knowledge-candidate": KnowledgeCandidatePayload,
    }
    if kind not in models:
        raise ValueError(f"unknown mission contract: {kind}")
    return models[kind].model_json_schema()
