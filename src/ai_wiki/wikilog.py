from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from ai_wiki.storage import get_wiki_root


def get_logs_dir() -> Path:
    """#16: 월별 로그 디렉토리."""
    d = get_wiki_root() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_current_log_path() -> Path:
    now = datetime.now(timezone.utc)
    return get_logs_dir() / f"log-{now.strftime('%Y-%m')}.yaml"


def append_log(action: str, article_id: str = "", title: str = "",
               details: str = "", author: str = "unknown") -> None:
    """#16: 월별 로그 파일에 항목 추가."""
    log_path = _get_current_log_path()

    entry = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "action": action,
    }
    if article_id:
        entry["article_id"] = article_id
    if title:
        entry["title"] = title
    if details:
        entry["details"] = details
    entry["author"] = author

    entries = []
    if log_path.exists():
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if isinstance(data, list):
                entries = data
        except Exception:
            pass

    entries.append(entry)

    with open(log_path, "w", encoding="utf-8") as f:
        yaml.dump(entries, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def migrate_legacy_log() -> None:
    """기존 단일 log.yaml을 월별 파일로 마이그레이션."""
    legacy = get_wiki_root() / "log.yaml"
    if not legacy.exists():
        return

    try:
        with open(legacy, "r", encoding="utf-8") as f:
            entries = yaml.safe_load(f)
        if not isinstance(entries, list) or not entries:
            return
    except Exception:
        return

    by_month: dict[str, list] = {}
    for entry in entries:
        ts = entry.get("timestamp", "")
        month_key = ts[:7] if len(ts) >= 7 else "unknown"
        by_month.setdefault(month_key, []).append(entry)

    logs_dir = get_logs_dir()
    for month, month_entries in by_month.items():
        if month == "unknown":
            continue
        path = logs_dir / f"log-{month}.yaml"
        existing = []
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    existing = data
            except Exception:
                pass
        existing.extend(month_entries)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(existing, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    legacy.rename(legacy.with_suffix(".yaml.bak"))
