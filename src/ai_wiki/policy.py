"""Local-first principal, role, namespace, and secret-reference policy."""
from __future__ import annotations

import os
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROLE_PERMISSIONS = {
    "owner": {"read", "search", "create", "patch", "review", "approve", "complete", "policy"},
    "reviewer": {"read", "search", "create", "patch", "review", "approve", "complete"},
    "agent": {"read", "search", "create", "patch", "submit"},
    "reader": {"read", "search"},
}


@dataclass(frozen=True)
class Principal:
    id: str
    roles: frozenset[str]

    @property
    def permissions(self) -> set[str]:
        output: set[str] = set()
        for role in self.roles:
            output.update(ROLE_PERMISSIONS.get(role, set()))
        return output


@dataclass(frozen=True)
class PolicyDecision:
    effect: str
    reason: str = ""
    redacted_fields: tuple[str, ...] = ()


class PolicyDenied(PermissionError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SecurityPolicy:
    def __init__(self, root: Path, config_filename: str = ".ai-wiki.yaml"):
        self.root = root.resolve()
        self.config_path = self.root / config_filename
        self.raw = self._load()
        security = self.raw.get("security", {}) if isinstance(self.raw, dict) else {}
        self.mode = security.get("mode", "trusted-local")
        self.default_principal = security.get("default_principal", "local-owner")
        self.secret_policy = security.get("secret_policy", "references-only")
        self.principals: dict[str, Principal] = {}
        for item in security.get("principals", []):
            if isinstance(item, dict) and item.get("id"):
                roles = frozenset(str(role) for role in item.get("roles", []))
                unknown = roles - set(ROLE_PERMISSIONS)
                if unknown:
                    raise ValueError(f"unknown security roles: {sorted(unknown)}")
                self.principals[str(item["id"])] = Principal(str(item["id"]), roles)
        if self.mode == "trusted-local" and self.default_principal not in self.principals:
            self.principals[self.default_principal] = Principal(
                self.default_principal, frozenset({"owner"}),
            )

    def _load(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}
        value = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(value, dict):
            raise ValueError("wiki configuration must be a mapping")
        return value

    def resolve(self, explicit: str | None = None) -> Principal:
        principal_id = explicit or os.environ.get("AI_WIKI_PRINCIPAL")
        if not principal_id and self.mode == "trusted-local":
            principal_id = self.default_principal
        if not principal_id:
            raise PolicyDenied("principal_required", "strict-local mode requires a principal")
        principal = self.principals.get(principal_id)
        if principal is None:
            raise PolicyDenied("unknown_principal", f"principal is not registered: {principal_id}")
        return principal

    def authorize(self, principal: Principal, operation: str, namespace: str) -> None:
        if operation not in principal.permissions:
            raise PolicyDenied(
                "permission_denied",
                f"principal {principal.id} cannot {operation} in namespace {namespace}",
            )
        if namespace not in {"knowledge", "plans", "runs", "artifacts", "external_evidence"}:
            raise PolicyDenied("unknown_namespace", f"unknown namespace: {namespace}")

    def decide(self, principal: Principal, operation: str, namespace: str,
               document: dict[str, Any] | None = None) -> PolicyDecision:
        try:
            self.authorize(principal, operation, namespace)
        except PolicyDenied as exc:
            return PolicyDecision("deny", str(exc))
        access = {}
        if isinstance(document, dict):
            extensions = document.get("extensions", {})
            if isinstance(extensions, dict):
                access = extensions.get("access", {}) or {}
        if not isinstance(access, dict):
            return PolicyDecision("deny", "invalid access policy")
        allowed_principals = set(access.get("principals", []))
        allowed_roles = set(access.get("roles", []))
        if access.get("visibility") == "private" and not (
            principal.id in allowed_principals or principal.roles.intersection(allowed_roles)
            or "owner" in principal.roles
        ):
            return PolicyDecision("deny", "document visibility denies this principal")
        field_roles = access.get("field_roles", {})
        redacted = tuple(sorted(
            path for path, roles in field_roles.items()
            if not principal.roles.intersection(set(roles)) and "owner" not in principal.roles
        )) if isinstance(field_roles, dict) else ()
        return PolicyDecision("redact" if redacted else "allow", redacted_fields=redacted)

    def validate_secrets(self, value: Any, path: str = "") -> None:
        if self.secret_policy != "references-only":
            return
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}/{key}"
                key_lower = str(key).lower().replace("-", "_")
                sensitive_key = (
                    key_lower in {"password", "secret", "api_key", "api_token", "token", "access_token",
                                  "refresh_token", "auth_token", "client_secret"}
                    or key_lower.endswith(("_password", "_secret", "_api_key", "_api_token", "_access_token",
                                           "_refresh_token", "_auth_token"))
                )
                if sensitive_key:
                    if child is None:
                        continue
                    if not isinstance(child, str) or not child.startswith(("env:", "secret:")):
                        raise PolicyDenied(
                            "plaintext_secret", f"secret value must be a reference: {child_path}",
                        )
                self.validate_secrets(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                self.validate_secrets(child, f"{path}/{index}")

    @staticmethod
    def mission_visibility(principal: Principal) -> str:
        if "owner" in principal.roles:
            return "owner"
        if "reviewer" in principal.roles:
            return "reviewer"
        if "agent" in principal.roles:
            return "agent"
        return "reader"

    @staticmethod
    def _private_path(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        return bool(re.match(r"^(?:[A-Za-z]:[\\/]|/|file:)", value))

    @staticmethod
    def _redact_secret_values(value: Any, path: str, redacted: list[str]) -> Any:
        secret_pattern = re.compile(
            r"(?i)(?:password|secret|token|api[_-]?key|access[_-]?token)\s*[:=]"
        )
        if isinstance(value, dict):
            output = {}
            for key, child in value.items():
                child_path = f"{path}/{key}"
                key_name = str(key).lower().replace("-", "_")
                if key_name in {
                    "password", "secret", "token", "api_key", "api_token",
                    "access_token", "refresh_token", "client_secret",
                }:
                    output[key] = "[redacted]"
                    redacted.append(child_path)
                else:
                    output[key] = SecurityPolicy._redact_secret_values(
                        child, child_path, redacted,
                    )
            return output
        if isinstance(value, list):
            return [
                SecurityPolicy._redact_secret_values(child, f"{path}/{index}", redacted)
                for index, child in enumerate(value)
            ]
        if isinstance(value, str) and secret_pattern.search(value):
            redacted.append(path)
            return "[redacted]"
        return value

    def redact_mission_detail(
        self, principal: Principal, detail: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        output = deepcopy(detail)
        redacted: list[str] = []
        visibility = self.mission_visibility(principal)
        redacted_evidence_ids: set[str] = set()

        for index, evidence in enumerate(output.get("evidence", [])):
            base = f"/evidence/{index}"
            if visibility == "reader":
                for field in ("result", "locator", "content_hash", "source_ids"):
                    if field in evidence and evidence[field] not in (None, "", []):
                        evidence[field] = "[redacted]" if field != "source_ids" else []
                        redacted.append(f"{base}/{field}")
                        redacted_evidence_ids.add(str(evidence.get("evidence_id") or ""))
            elif visibility == "agent" and self._private_path(evidence.get("locator")):
                evidence["locator"] = "[redacted]"
                redacted.append(f"{base}/locator")
                redacted_evidence_ids.add(str(evidence.get("evidence_id") or ""))

        if redacted_evidence_ids:
            plan = output.get("plan") or {}
            for criterion in plan.get("global_criteria", []):
                if redacted_evidence_ids.intersection(criterion.get("evidence_ids", [])):
                    criterion["coverage_status"] = "redacted"
            for task in output.get("tasks", []):
                for criterion in task.get("criteria", []):
                    if redacted_evidence_ids.intersection(criterion.get("evidence_ids", [])):
                        criterion["coverage_status"] = "redacted"

        handoff = output.get("handoff")
        if isinstance(handoff, dict):
            if visibility == "reader":
                for field in ("changed_files", "blockers", "artifacts", "legacy_fields"):
                    if handoff.get(field):
                        handoff[field] = [] if field != "legacy_fields" else {}
                        redacted.append(f"/handoff/{field}")
                if handoff.get("artifact_refs"):
                    handoff["artifact_refs"] = []
                    redacted.append("/handoff/artifact_refs")
            elif visibility == "agent":
                for field in ("changed_files", "artifacts"):
                    values = handoff.get(field)
                    if isinstance(values, list):
                        for index, value in enumerate(values):
                            if self._private_path(value):
                                values[index] = "[redacted]"
                                redacted.append(f"/handoff/{field}/{index}")
                refs = handoff.get("artifact_refs")
                if isinstance(refs, list):
                    for index, ref in enumerate(refs):
                        if isinstance(ref, dict) and self._private_path(ref.get("value")):
                            ref.update({
                                "value": "[redacted]", "href": None,
                                "state": "redacted", "reason": "policy_redacted",
                            })
                            redacted.append(f"/handoff/artifact_refs/{index}")

        output = self._redact_secret_values(output, "", redacted)
        output["policy"] = {
            "effect": "redact" if redacted else "allow",
            "visibility": visibility,
            "redacted_fields": sorted(set(redacted)),
        }
        return output, sorted(set(redacted))


def namespace_for_kind(kind: str) -> str:
    return {
        "work_plan": "plans",
        "work_run": "runs",
        "research_report": "artifacts",
        "knowledge_candidate": "artifacts",
    }.get(kind, "knowledge")
