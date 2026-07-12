"""Plugin discovery for replaceable AI Wiki backends."""
from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any


PLUGIN_GROUPS = {
    "extractors": "ai_wiki.extractors",
    "rerankers": "ai_wiki.rerankers",
    "embeddings": "ai_wiki.embeddings",
    "graph_backends": "ai_wiki.graph_backends",
    "connectors": "ai_wiki.connectors",
}


def discover_plugins(*, load: bool = False) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    all_points = entry_points()
    for kind, group in PLUGIN_GROUPS.items():
        points = list(all_points.select(group=group))
        rows = []
        for point in points:
            row = {"name": point.name, "value": point.value, "group": group,
                   "version": getattr(getattr(point, "dist", None), "version", None),
                   "status": "discovered", "error": None}
            if load:
                try:
                    plugin = point.load()
                    row["status"] = "ready"
                    row["capabilities"] = getattr(plugin, "capabilities", lambda: {})()
                except Exception as exc:
                    row["status"] = "degraded"
                    row["error"] = str(exc)
            rows.append(row)
        result[kind] = rows
    return result
