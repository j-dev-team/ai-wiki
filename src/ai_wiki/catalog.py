from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import yaml

from ai_wiki.storage import get_wiki_root, get_data_dir


def get_catalog_path() -> Path:
    return get_wiki_root() / "index.yaml"


def _extract_summary(content: dict) -> str:
    """content에서 한 줄 요약 추출. #20: what > definition > error_message > purpose 순."""
    if not isinstance(content, dict):
        return ""
    for key in ("what", "definition", "error_message", "purpose"):
        val = content.get(key)
        if val and isinstance(val, str):
            return val[:120]
    return ""


def rebuild_catalog() -> int:
    """#5: SQLite 기반 카탈로그 생성 (전체 파일 스캔 대신 DB 쿼리)."""
    db_path = get_data_dir() / "wiki.db"

    if not db_path.exists():
        # DB 없으면 폴백
        from ai_wiki.storage import list_all_articles
        return _rebuild_from_articles(list_all_articles())

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT id, title, category, tags, confidence, last_modified, content_type,
                   maturity, quality_score
            FROM articles_meta
            ORDER BY category, lower(title)
        """)

        by_category: dict[str, list] = {}
        total = 0
        for row in cur.fetchall():
            r = dict(row)
            top_cat = r["category"].split("/")[0]
            if top_cat not in by_category:
                by_category[top_cat] = []

            # summary: content에서 추출 필요 → 파일 읽기 (캐시된 경로로 O(1))
            summary = ""
            from ai_wiki.storage import load_article
            article = load_article(r["id"])
            if article:
                summary = _extract_summary(article.content)

            by_category[top_cat].append({
                "id": r["id"],
                "title": r["title"],
                "category": r["category"],
                "type": r["content_type"] or "",
                "summary": summary,
                "maturity": r.get("maturity", "unknown"),
                "quality_score": r.get("quality_score", 0.0),
                "confidence": r["confidence"],
                "tags": json.loads(r["tags"] or "[]"),
                "last_modified": r["last_modified"],
            })
            total += 1

        conn.close()
    except sqlite3.Error:
        from ai_wiki.storage import list_all_articles
        return _rebuild_from_articles(list_all_articles())

    _write_catalog(total, by_category)
    return total


def _rebuild_from_articles(articles) -> int:
    """폴백: Article 리스트에서 카탈로그 생성."""
    articles.sort(key=lambda a: a.category + "/" + a.title.lower())
    by_category: dict[str, list] = {}
    for a in articles:
        top_cat = a.category.split("/")[0]
        if top_cat not in by_category:
            by_category[top_cat] = []
        by_category[top_cat].append({
            "id": a.id,
            "title": a.title,
            "category": a.category,
            "type": a.content.get("type", "") if isinstance(a.content, dict) else "",
            "summary": _extract_summary(a.content),
            "confidence": a.confidence,
            "tags": a.tags,
            "last_modified": a._fmt(a.last_modified),
        })
    _write_catalog(len(articles), by_category)
    return len(articles)


def _write_catalog(total: int, by_category: dict) -> None:
    catalog = {"total": total, "categories": by_category}
    with open(get_catalog_path(), "w", encoding="utf-8") as f:
        yaml.dump(catalog, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
