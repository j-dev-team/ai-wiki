"""Read-only source connectors with immutable, permission-aware snapshots."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ai_wiki.storage import _atomic_write_bytes


def now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Connector(ABC):
    def __init__(self, config: dict[str, Any]):
        self.config = config

    @abstractmethod
    def discover(self, cursor: str | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def fetch(self, resource_id: str) -> dict[str, Any]: ...

    def permissions(self, resource_id: str) -> dict[str, Any]:
        return {"visibility": self.config.get("visibility", "private"), "principals": []}

    def checkpoint(self) -> str:
        return now_text()

    def health(self) -> dict[str, Any]:
        return {"ready": True}


class GitConnector(Connector):
    def _root(self) -> Path:
        return Path(self.config["path"]).resolve()

    def discover(self, cursor: str | None = None) -> list[dict[str, Any]]:
        completed = subprocess.run(
            ["git", "ls-files"], cwd=self._root(), text=True,
            capture_output=True, check=True,
        )
        return [{"id": line, "revision": self.checkpoint()} for line in completed.stdout.splitlines()]

    def fetch(self, resource_id: str) -> dict[str, Any]:
        target = (self._root() / resource_id).resolve()
        target.relative_to(self._root())
        data = target.read_bytes()
        return {"id": resource_id, "content": data.decode("utf-8", errors="replace"),
                "content_hash": hashlib.sha256(data).hexdigest(), "url": target.as_uri()}

    def checkpoint(self) -> str:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self._root(), text=True,
            capture_output=True, check=True,
        )
        return completed.stdout.strip()


class WebConnector(Connector):
    def discover(self, cursor: str | None = None) -> list[dict[str, Any]]:
        return [{"id": url, "revision": cursor} for url in self.config.get("urls", [])]

    def fetch(self, resource_id: str) -> dict[str, Any]:
        request = urllib.request.Request(resource_id, headers={"User-Agent": "AI-Wiki/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
            content_type = response.headers.get("Content-Type", "")
            revision = response.headers.get("ETag") or response.headers.get("Last-Modified")
        return {"id": resource_id, "content": data.decode("utf-8", errors="replace"),
                "content_hash": hashlib.sha256(data).hexdigest(), "url": resource_id,
                "content_type": content_type, "revision": revision}


class JsonAPIConnector(Connector):
    def _token(self) -> str:
        reference = self.config.get("token")
        if not isinstance(reference, str) or not reference.startswith("env:"):
            raise ValueError("connector token must use env: reference")
        value = os.environ.get(reference[4:])
        if not value:
            raise ValueError(f"connector credential is unavailable: {reference}")
        return value

    def _json(self, url: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        request_headers = {"Authorization": f"Bearer {self._token()}"}
        request_headers.update(headers or {})
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=request_headers), timeout=30,
        ) as response:
            return json.loads(response.read().decode("utf-8"))


class GoogleDriveConnector(JsonAPIConnector):
    def discover(self, cursor: str | None = None) -> list[dict[str, Any]]:
        fields = "files(id,name,mimeType,modifiedTime,webViewLink),nextPageToken"
        payload = self._json(f"https://www.googleapis.com/drive/v3/files?pageSize=100&fields={fields}")
        return [{"id": item["id"], "revision": item.get("modifiedTime"), "name": item.get("name")}
                for item in payload.get("files", [])]

    def fetch(self, resource_id: str) -> dict[str, Any]:
        metadata = self._json(
            f"https://www.googleapis.com/drive/v3/files/{resource_id}?fields=id,name,mimeType,modifiedTime,webViewLink"
        )
        url = f"https://www.googleapis.com/drive/v3/files/{resource_id}?alt=media"
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._token()}"})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
        return {"id": resource_id, "content": data.decode("utf-8", errors="replace"),
                "content_hash": hashlib.sha256(data).hexdigest(), "url": metadata.get("webViewLink"),
                "revision": metadata.get("modifiedTime"), "metadata": metadata}


class NotionConnector(JsonAPIConnector):
    def discover(self, cursor: str | None = None) -> list[dict[str, Any]]:
        # Search is read-only; explicit page IDs can also be supplied in config.
        return [{"id": page_id} for page_id in self.config.get("page_ids", [])]

    def fetch(self, resource_id: str) -> dict[str, Any]:
        headers = {"Notion-Version": "2022-06-28"}
        page = self._json(f"https://api.notion.com/v1/pages/{resource_id}", headers=headers)
        blocks = self._json(
            f"https://api.notion.com/v1/blocks/{resource_id}/children?page_size=100", headers=headers,
        )
        data = json.dumps({"page": page, "blocks": blocks}, ensure_ascii=False).encode()
        return {"id": resource_id, "content": data.decode(),
                "content_hash": hashlib.sha256(data).hexdigest(), "url": page.get("url"),
                "revision": page.get("last_edited_time")}


class SlackConnector(JsonAPIConnector):
    def discover(self, cursor: str | None = None) -> list[dict[str, Any]]:
        return [{"id": channel_id} for channel_id in self.config.get("channel_ids", [])]

    def fetch(self, resource_id: str) -> dict[str, Any]:
        payload = self._json(
            f"https://slack.com/api/conversations.history?channel={resource_id}&limit=100"
        )
        if not payload.get("ok"):
            raise ValueError(payload.get("error", "slack API error"))
        data = json.dumps(payload.get("messages", []), ensure_ascii=False).encode()
        return {"id": resource_id, "content": data.decode(),
                "content_hash": hashlib.sha256(data).hexdigest(),
                "url": f"slack://channel/{resource_id}",
                "revision": payload.get("response_metadata", {}).get("next_cursor")}


CONNECTORS = {
    "git": GitConnector, "web": WebConnector, "google-drive": GoogleDriveConnector,
    "notion": NotionConnector, "slack": SlackConnector,
}


class ConnectorManager:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.config_path = self.root / ".ai-wiki-connectors.yaml"
        self.sources = self.root / "sources"
        self.sources.mkdir(parents=True, exist_ok=True)

    def _config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {"connectors": {}}
        return yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {"connectors": {}}

    def _write_config(self, value: dict[str, Any]) -> None:
        _atomic_write_bytes(self.config_path, yaml.safe_dump(
            value, allow_unicode=True, sort_keys=False,
        ).encode("utf-8"))

    def add(self, name: str, connector_type: str, config: dict[str, Any]) -> dict[str, Any]:
        if connector_type not in CONNECTORS:
            raise ValueError("unknown_connector_type")
        from ai_wiki.policy import SecurityPolicy
        SecurityPolicy(self.root).validate_secrets(config)
        value = self._config()
        value.setdefault("connectors", {})[name] = {"type": connector_type, **config}
        self._write_config(value)
        return {"name": name, "type": connector_type}

    def list(self) -> list[dict[str, Any]]:
        return [{"name": name, **config} for name, config in self._config().get("connectors", {}).items()]

    def _connector(self, name: str) -> Connector:
        config = self._config().get("connectors", {}).get(name)
        if not config:
            raise ValueError("connector_not_found")
        return CONNECTORS[config["type"]](config)

    def sync(self, name: str) -> dict[str, Any]:
        connector = self._connector(name)
        resources = connector.discover()
        updated = 0
        skipped = 0
        connector_dir = self.sources / name
        connector_dir.mkdir(parents=True, exist_ok=True)
        for resource in resources:
            fetched = connector.fetch(resource["id"])
            digest = fetched["content_hash"]
            snapshot = connector_dir / f"{digest}.json"
            if snapshot.exists():
                skipped += 1
                continue
            payload = {
                **fetched, "connector": name, "permissions": connector.permissions(resource["id"]),
                "retrieved_at": now_text(), "status": "pending_unverified",
            }
            _atomic_write_bytes(snapshot, json.dumps(payload, ensure_ascii=False, indent=2).encode())
            updated += 1
        return {"name": name, "discovered": len(resources), "updated": updated,
                "skipped": skipped, "checkpoint": connector.checkpoint()}

    def status(self, name: str) -> dict[str, Any]:
        connector = self._connector(name)
        return {"name": name, **connector.health()}

    def remove(self, name: str) -> dict[str, Any]:
        value = self._config()
        removed = value.get("connectors", {}).pop(name, None)
        if removed is None:
            raise ValueError("connector_not_found")
        self._write_config(value)
        return {"name": name, "removed": True, "snapshots_preserved": True}
