"""Stable local service API shared by CLI and future adapters."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ai_wiki.agent_protocol import (
    PROTOCOL_VERSION, apply_json_patch, build_context, canonical_document,
    compact_document, estimate_tokens, project_fields, success,
)
from ai_wiki.index import WikiIndex
from ai_wiki.mission_contracts import mission_json_schema
from ai_wiki.language import SUPPORTED_WIKI_LANGUAGES, resolve_wiki_language
from ai_wiki.missions import MissionControlReader, MissionStore
from ai_wiki.models import Article
from ai_wiki.policy import PolicyDenied, SecurityPolicy, namespace_for_kind
from ai_wiki.quality import validate as quality_validate
from ai_wiki.schema_v2 import document_json_schema
from ai_wiki.schema_v3 import document_v3_json_schema
from ai_wiki.storage import atomic_save, atomic_update, load_article_with_path
from ai_wiki.temporal_contracts import temporal_json_schema
from ai_wiki.utils import generate_id


class AIWikiClient:
    def __init__(self, root: str | Path, principal: str | None = None):
        self.root = Path(root).resolve()
        self.principal_name = principal
        self.index = WikiIndex(self.root / "data" / "wiki.db")
        self.policy = SecurityPolicy(self.root)
        self.missions = MissionStore(self.root)

    @contextmanager
    def _activated(self):
        previous = os.environ.get("AI_WIKI_ROOT")
        os.environ["AI_WIKI_ROOT"] = str(self.root)
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("AI_WIKI_ROOT", None)
            else:
                os.environ["AI_WIKI_ROOT"] = previous

    def _principal(self):
        return self.policy.resolve(self.principal_name)

    def _authorize(self, operation: str, namespace: str):
        principal = self._principal()
        try:
            self.policy.authorize(principal, operation, namespace)
            self.index.record_authorization(principal.id, operation, namespace, True)
            return principal
        except PolicyDenied as exc:
            self.index.record_authorization(principal.id, operation, namespace, False, str(exc))
            raise

    def capabilities(self) -> dict[str, Any]:
        from ai_wiki.plugins import discover_plugins
        principal = self._principal()
        language = resolve_wiki_language(self.root)
        return success({
            "protocol_version": PROTOCOL_VERSION,
            "compatible_protocol_versions": ["1.1", "1.2", "1.3", "1.4", "1.5"],
            "commands": {
                "get": {"views": ["compact", "full", "raw"], "default": "compact"},
                "context": {"default_max_tokens": 4000, "default_limit": 8,
                            "scopes": ["default", "knowledge", "missions", "all"]},
                "record-use": {"outcomes": ["answered", "insufficient"]},
                "record-feedback": {"judgments": ["accepted", "rejected", "corrected"]},
                "patch": {"operations": ["test", "add", "replace", "remove"],
                          "if_version_required": True},
                "create": {"document_file": "JSON path or - for stdin"},
                "temporal": {"views": ["current", "as-of", "known-as-of", "timeline",
                                        "why-changed", "disputed"]},
                "mission": {"kinds": ["research_report", "work_plan", "work_run",
                                         "knowledge_candidate"],
                            "authoring_language": language.language,
                            "source_language_field": "metadata.source_language",
                            "localizations_field": "localizations"},
            },
            "language": {
                **language.as_dict(),
                "supported": list(SUPPORTED_WIKI_LANGUAGES),
                "authoring_language": language.language,
                "display_language_is_user_selectable": True,
                "technical_fields_are_source_only": True,
            },
            "contracts": ["document", "document-v3", "temporal", "research-report",
                          "work-plan", "work-run", "knowledge-candidate", "security",
                          "calibration-feedback"],
            "namespaces": ["knowledge", "plans", "runs", "artifacts", "external_evidence"],
            "security": {"mode": self.policy.mode, "principal": principal.id,
                         "roles": sorted(principal.roles)},
            "plugins": discover_plugins(load=False),
        })

    def contract_schema(self, name: str) -> dict[str, Any]:
        if name == "document":
            return document_json_schema()
        if name == "document-v3":
            return document_v3_json_schema()
        if name == "temporal":
            return temporal_json_schema()
        if name == "mission":
            return mission_json_schema()
        if name in {"research-report", "work-plan", "work-run", "knowledge-candidate"}:
            return mission_json_schema(name)
        if name == "security":
            return {"roles": ["owner", "reviewer", "agent", "reader"],
                    "modes": ["trusted-local", "strict-local"],
                    "secret_policy": "references-only"}
        if name == "calibration-feedback":
            return {"type": "object", "required": ["citation", "judgment", "evidence_type"],
                    "properties": {
                        "citation": {"type": "string"},
                        "judgment": {"enum": ["accepted", "rejected", "corrected"]},
                        "evidence_type": {"enum": ["agent", "human", "verification", "external_eval"]},
                        "evidence_reference": {"type": ["string", "null"]},
                    }}
        raise ValueError(f"unknown contract: {name}")

    def get(self, document_id: str, *, view: str = "compact", fields: str | None = None) -> dict:
        principal = self._authorize("read", "knowledge")
        with self._activated():
            article, path = load_article_with_path(document_id)
            if article is None:
                raise ValueError("not_found")
            decision = self.policy.decide(
                principal, "read", "knowledge", canonical_document(article),
            )
            if decision.effect == "deny":
                raise PolicyDenied("document_access_denied", decision.reason)
            if decision.effect == "redact" and view == "raw":
                raise PolicyDenied("raw_view_denied", "raw view would expose redacted fields")
            if view == "full":
                document = canonical_document(article)
            elif view == "raw":
                document = {"id": article.id, "version": article.version,
                            "raw_yaml": path.read_text(encoding="utf-8") if path else ""}
            else:
                document, _ = compact_document(article)
                document = project_fields(document, fields)
            if decision.effect == "redact" and view != "raw":
                for pointer in decision.redacted_fields:
                    self._remove_pointer(document, pointer)
            return success({"document": document}, meta={
                "view": view, "policy": decision.effect,
                "redacted_fields": list(decision.redacted_fields),
            })

    def context(self, query: str, *, max_tokens: int = 4000, limit: int = 8,
                scope: str = "default", **kwargs) -> dict:
        namespace = "runs" if scope == "missions" else "knowledge"
        principal = self._authorize("search", namespace)
        if scope not in {"default", "knowledge", "missions", "all"}:
            raise ValueError("invalid_scope")
        with self._activated():
            envelope = build_context(self.index, query, max_tokens=max_tokens, limit=limit, **kwargs)
            allowed_ids = []
            for document in envelope["data"]["documents"]:
                article, _ = load_article_with_path(document["id"])
                if article and self.policy.decide(
                    principal, "search", "knowledge", canonical_document(article),
                ).effect != "deny":
                    allowed_ids.append(document["id"])
            excluded = len(envelope["data"]["documents"]) - len(allowed_ids)
            allowed = set(allowed_ids)
            envelope["data"]["documents"] = [
                item for item in envelope["data"]["documents"] if item["id"] in allowed
            ]
            envelope["data"]["citations"] = [
                item for item in envelope["data"]["citations"]
                if item.get("document_id") in allowed
            ]
            envelope["meta"]["excluded_by_policy"] = excluded
            envelope["meta"]["budget"]["estimated_tokens"] = estimate_tokens(envelope)
            self.index.conn.execute(
                "UPDATE context_sessions SET document_ids=?, citations=?, estimated_tokens=? WHERE context_id=?",
                (json.dumps(allowed_ids), json.dumps([
                    item["key"] for item in envelope["data"]["citations"]
                ]), envelope["meta"]["budget"]["estimated_tokens"], envelope["data"]["context_id"]),
            )
            self.index.conn.commit()
        envelope["meta"]["scope"] = scope
        if scope in {"default", "knowledge"}:
            # Mission runs are stored outside articles, so no high-churn run log can leak here.
            return envelope
        envelope["data"]["missions"] = self.missions.list()
        return envelope

    def mission_list(
        self, *, kind: str | None = None, status: str | None = None,
        plan_id: str | None = None, run_id: str | None = None,
        limit: int = 50, offset: int = 0, language: str | None = None,
    ) -> dict[str, Any]:
        reader = MissionControlReader(
            self.missions, self.policy, self._principal(), display_language=language,
        )
        page = reader.list(
            kind=kind, status=status, plan_id=plan_id, run_id=run_id,
            limit=limit, offset=offset,
        )
        return success({
            "missions": page["items"], "total": page["total"],
            "limit": page["limit"], "offset": page["offset"],
            "has_more": page["has_more"],
        })

    def mission_detail(
        self, mission_id: str, *, revision: int | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        reader = MissionControlReader(
            self.missions, self.policy, self._principal(), display_language=language,
        )
        detail = reader.detail(mission_id, revision)
        if detail is None:
            raise ValueError("mission_not_found")
        return success(
            {"mission": detail},
            meta={"policy": detail["policy"]},
        )

    def record_use(self, context_id: str, citations: list[str], outcome: str) -> dict:
        self._authorize("read", "knowledge")
        return success(self.index.record_context_usage(context_id, citations, outcome))

    def record_feedback(self, context_id: str, feedback: dict[str, Any]) -> dict:
        principal = self._authorize("review" if feedback.get("evidence_type") != "agent" else "read", "knowledge")
        return success(self.index.record_feedback(
            context_id, feedback, principal_id=principal.id, roles=set(principal.roles),
            model_scope=self._model_scope(),
        ))

    def _model_scope(self) -> str:
        from ai_wiki.vector import VectorIndex
        index = VectorIndex(db_path=self.root / "data" / "vectors.db")
        try:
            state = index.state()
        finally:
            index.close()
        return ":".join(str(state.get(key, "")) for key in (
            "embedding_model", "embedding_version", "dimensions", "index_revision",
        ))

    def create(self, document: dict[str, Any], *, dry_run: bool = False) -> dict:
        principal = self._authorize("create", "knowledge")
        self.policy.validate_secrets(document)
        article = Article.from_yaml(document) if document.get("schema_version") in {2, 3} else Article(
            id=document.get("id") or generate_id(document["title"], document["category"]),
            title=document["title"], category=document["category"],
            content={"type": document["content"]["type"], **document["content"].get("data", {})}
            if set(document["content"]) == {"type", "data"} else document["content"],
            tags=document.get("tags", []), confidence=float(document.get("confidence", 0.8)),
            sources=[item.get("url") if isinstance(item, dict) else item for item in document.get("sources", [])],
            related=document.get("related", []), author=document.get("author", principal.id),
        )
        duplicates = self.index.find_similar_titles(article.title)
        if duplicates:
            raise ValueError("duplicate_conflict:" + json.dumps(duplicates, ensure_ascii=False))
        if not article.sources:
            article.confidence = min(article.confidence, 0.5)
            article.metadata["verification_status"] = "pending"
            article.verification = [{
                "path": "/content/data", "level": "unverified", "source_ids": [],
                "note": "Awaiting source verification",
            }]
        elif not article.verification:
            article.verification = [{
                "path": "/content/data", "level": "sourced",
                "source_ids": [f"src-{position}" for position in range(1, len(article.sources) + 1)],
            }]
        canonical = article.to_yaml_dict()
        report = quality_validate(article)
        response = {"article_id": article.id, "dry_run": dry_run,
                    "document": compact_document(article)[0], "quality": report.to_dict()}
        if not dry_run:
            with self._activated():
                path = atomic_save(
                    article, self.index, vector_upsert=self._vector_upsert,
                    vector_remove=self._vector_remove,
                )
            response["file_path"] = str(path.relative_to(self.root))
        return success(response)

    def patch(self, document_id: str, operations: list[dict[str, Any]], *, if_version: int,
              dry_run: bool = False) -> dict:
        self._authorize("patch", "knowledge")
        with self._activated():
            article, old_path = load_article_with_path(document_id)
            if article is None:
                raise ValueError("not_found")
            if article.version != if_version:
                raise ValueError(f"version_conflict:{article.version}")
            raw, changed = apply_json_patch(canonical_document(article), operations)
            raw["metadata"]["document_version"] += 1
            from ai_wiki.missions import utc_text
            raw["metadata"]["modified_at"] = utc_text()
            updated = Article.from_yaml(raw)
            self.policy.validate_secrets(raw)
            if quality_validate(updated).quality_score < quality_validate(article).quality_score:
                raise ValueError("quality_regression")
            if not dry_run:
                atomic_update(
                    updated, old_path, self.index, vector_upsert=self._vector_upsert,
                    vector_remove=self._vector_remove,
                )
        return success({"article_id": document_id, "version": updated.version,
                        "changed_paths": changed, "dry_run": dry_run,
                        "document": compact_document(updated)[0]})

    def _vector_upsert(self, article: Article) -> None:
        from ai_wiki.vector import VectorIndex
        index = VectorIndex(db_path=self.root / "data" / "vectors.db")
        try:
            index.upsert(article)
        finally:
            index.close()

    def _vector_remove(self, article_id: str) -> None:
        from ai_wiki.vector import VectorIndex
        index = VectorIndex(db_path=self.root / "data" / "vectors.db")
        try:
            index.remove(article_id)
        finally:
            index.close()

    @staticmethod
    def _remove_pointer(document: dict[str, Any], pointer: str) -> None:
        if not pointer.startswith("/"):
            return
        parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
        current: Any = document
        for part in parts[:-1]:
            if not isinstance(current, dict) or part not in current:
                return
            current = current[part]
        if isinstance(current, dict):
            current.pop(parts[-1], None)

    def close(self) -> None:
        self.missions.close()
        self.index.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
