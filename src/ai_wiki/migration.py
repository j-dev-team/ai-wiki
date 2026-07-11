"""Idempotent migrations from legacy AI Wiki documents to schema v2."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import shutil
import yaml

from ai_wiki.schema_v2 import SCHEMA_VERSION, validate_v2_document


def _fmt(value: Any, fallback: datetime) -> str:
    if value in (None, ""):
        return fallback.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def _pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _extract_verification(value: Any, path: str, output: list[dict]) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if key == "_v" or (isinstance(key, str) and key.endswith("_v")):
                target = path if key == "_v" else f"{path}/{_pointer_token(key[:-2])}"
                if isinstance(item, dict):
                    output.append({
                        "path": target or "/content/data",
                        "level": item.get("level", "unverified"),
                        "source_ids": [],
                        "note": item.get("note", ""),
                    })
                continue
            cleaned[key] = _extract_verification(
                item, f"{path}/{_pointer_token(str(key))}", output
            )
        return cleaned
    if isinstance(value, list):
        return [
            _extract_verification(item, f"{path}/{index}", output)
            for index, item in enumerate(value)
        ]
    return value


def normalize_legacy_content(
    content: dict[str, Any], source_records: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Remove legacy control keys and return normalized system records."""
    value = deepcopy(content)
    legacy_meta = value.pop("_meta", {}) if isinstance(value.get("_meta", {}), dict) else {}
    legacy_history = value.pop("_changelog", []) if isinstance(value.get("_changelog", []), list) else []
    verification: list[dict[str, Any]] = []
    cleaned = _extract_verification(value, "/content/data", verification)

    url_to_id = {item.get("url"): item.get("id") for item in source_records}
    raw_records: list[dict[str, Any]] = []
    def collect(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if (key == "_v" or str(key).endswith("_v")) and isinstance(child, dict):
                    raw_records.append(child)
                else:
                    collect(child)
        elif isinstance(item, list):
            for child in item:
                collect(child)
    collect(content)
    for record, raw in zip(verification, raw_records):
        urls = raw.get("sources", [])
        if isinstance(urls, str):
            urls = [urls]
        record["source_ids"] = [url_to_id[url] for url in urls if url in url_to_id]

    history = []
    for item in legacy_history:
        if isinstance(item, dict):
            history.append({
                "at": _fmt(item.get("date"), datetime.now(timezone.utc)),
                "action": item.get("action") or "legacy_change",
                "fields": item.get("fields", []),
                "note": item.get("note", ""),
            })
    return cleaned, verification, legacy_meta, history


def migrate_v1_to_v2(data: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Convert a legacy mapping to canonical v2. Calling it on v2 is a no-op."""
    if data.get("schema_version") == SCHEMA_VERSION:
        validate_v2_document(data)
        return deepcopy(data)
    if "schema_version" in data:
        raise ValueError(f"unsupported schema_version: {data['schema_version']}")

    now = now or datetime.now(timezone.utc)
    content = deepcopy(data.get("content", {}))
    if isinstance(content, str):
        content = {"type": "ingested", "original_filename": "legacy", "file_type": "text", "text": content}
    if not isinstance(content, dict):
        raise ValueError("legacy content must be a mapping or string")

    sources_raw = data.get("sources", [])
    if isinstance(sources_raw, str):
        sources_raw = [sources_raw]
    sources = [
        {"id": f"src-{index}", "url": url}
        for index, url in enumerate(sources_raw, start=1)
    ]
    content, verification, legacy_meta, history = normalize_legacy_content(content, sources)
    content_type = content.pop("type", "")

    related = data.get("related", [])
    if isinstance(related, str):
        related = [item.strip() for item in related.split(",") if item.strip()]

    known = {"id", "title", "category", "tags", "confidence", "version", "created_at",
             "last_modified", "last_verified", "sources", "related", "author", "content"}
    legacy_extra = {key: deepcopy(value) for key, value in data.items() if key not in known}
    extensions = {"legacy": legacy_extra} if legacy_extra else {}
    extra_meta = {
        key: value for key, value in legacy_meta.items()
        if key not in {"maturity", "completeness"}
    }
    if extra_meta:
        extensions["system_metadata"] = extra_meta
    defaulted_dates = [
        field for field in ("created_at", "last_modified", "last_verified")
        if data.get(field) in (None, "")
    ]
    if defaulted_dates:
        extensions["migration"] = {
            "defaulted_fields": defaulted_dates,
            "reason": "field absent from legacy document",
        }

    result = {
        "schema_version": SCHEMA_VERSION,
        "id": data.get("id", ""),
        "title": data.get("title", ""),
        "category": data.get("category", ""),
        "tags": data.get("tags", []),
        "metadata": {
            "confidence": float(data.get("confidence", 0.8)),
            "document_version": int(data.get("version", 1)),
            "created_at": _fmt(data.get("created_at"), now),
            "modified_at": _fmt(data.get("last_modified"), now),
            "verified_at": _fmt(data.get("last_verified"), now),
            "author": data.get("author", "unknown"),
            "maturity": legacy_meta.get("maturity", "stub"),
            "completeness": float(legacy_meta.get("completeness", 0.0)),
        },
        "sources": sources,
        "relations": [{"target_id": target} for target in related],
        "content": {"type": content_type, "data": content},
        "verification": verification,
        "history": history,
        "extensions": extensions,
    }
    validate_v2_document(result)
    return result


def v2_to_article_fields(data: dict[str, Any]) -> dict[str, Any]:
    document = validate_v2_document(data)
    raw = document.model_dump(mode="python")
    content = {"type": raw["content"]["type"], **raw["content"]["data"]}
    return {
        "schema_version": SCHEMA_VERSION,
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
            **raw["extensions"].get("system_metadata", {}),
        },
        "source_records": raw["sources"], "relations": raw["relations"],
        "verification": raw["verification"], "history": raw["history"],
        "extensions": raw["extensions"],
    }


def migrate_article_files(
    wiki_root: Path, *, dry_run: bool = True, backup: bool = True
) -> dict[str, Any]:
    """Migrate all article YAML files and return a per-file report."""
    from ai_wiki.storage import _atomic_write_bytes
    from ai_wiki.yaml_loader import load_yaml_file

    articles_dir = wiki_root / "articles"
    report: dict[str, Any] = {
        "dry_run": dry_run, "scanned": 0, "migrated": 0,
        "already_current": 0, "failed": [], "files": [], "backup_dir": None,
    }
    if not articles_dir.exists():
        return report

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = wiki_root / "backups" / f"schema-v1-{timestamp}"
    for path in sorted(articles_dir.rglob("*.yaml")):
        report["scanned"] += 1
        relative = path.relative_to(wiki_root)
        try:
            data = load_yaml_file(path)
            if not isinstance(data, dict):
                raise ValueError("document root must be a mapping")
            if data.get("schema_version") == SCHEMA_VERSION:
                validate_v2_document(data)
                report["already_current"] += 1
                continue
            migrated = migrate_v1_to_v2(data)
            report["migrated"] += 1
            report["files"].append(str(relative))
            if dry_run:
                continue
            if backup:
                destination = backup_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
                report["backup_dir"] = str(backup_dir)
            serialized = yaml.safe_dump(
                migrated, allow_unicode=True, default_flow_style=False, sort_keys=False,
            ).encode("utf-8")
            _atomic_write_bytes(path, serialized)
        except Exception as exc:
            report["failed"].append({"file": str(relative), "error": str(exc)})
    return report
