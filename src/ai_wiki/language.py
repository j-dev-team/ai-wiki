"""Shared wiki authoring-language resolution.

The wiki configuration owns the authoring language. A web session may select a
different display language, but that preference must never mutate the wiki
configuration or silently change how agents author Mission documents.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from ai_wiki.runtime import get_runtime


SUPPORTED_WIKI_LANGUAGES = ("ko", "en")
LEGACY_DEFAULT_LANGUAGE = "en"


@dataclass(frozen=True)
class WikiLanguage:
    language: str
    source: str
    warning: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "language": self.language,
            "source": self.source,
            "warning": self.warning,
        }


def _environment_language() -> str | None:
    value = os.environ.get("AI_WIKI_LANG", "").strip().lower()
    return value if value in SUPPORTED_WIKI_LANGUAGES else None


def resolve_wiki_language(root: str | Path | None = None) -> WikiLanguage:
    """Resolve one deterministic authoring language for the active wiki.

    New wikis persist ``lang`` in their configuration. Older installations may
    not have that field, so an explicit ``AI_WIKI_LANG`` is honored before the
    documented Korean legacy fallback. The warning is intentionally returned
    to callers instead of being printed on every read.
    """
    if root is None:
        from ai_wiki.storage import get_wiki_root

        wiki_root = get_wiki_root()
    else:
        wiki_root = Path(root).expanduser().resolve()
    runtime_filename = get_runtime().config_filename
    config_path = wiki_root / runtime_filename
    core_config = wiki_root / ".ai-wiki.yaml"
    if not config_path.exists() and runtime_filename != ".ai-wiki.yaml" and core_config.exists():
        config_path = core_config
    configured: object = None
    if config_path.exists():
        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                configured = raw.get("lang")
        except (OSError, UnicodeError, yaml.YAMLError):
            configured = None
    if isinstance(configured, str):
        language = configured.strip().lower()
        if language in SUPPORTED_WIKI_LANGUAGES:
            return WikiLanguage(language, "config")
        fallback = _environment_language() or LEGACY_DEFAULT_LANGUAGE
        return WikiLanguage(fallback, "fallback", "invalid_wiki_language")
    fallback = _environment_language()
    if fallback:
        return WikiLanguage(fallback, "environment", "missing_wiki_language")
    return WikiLanguage(
        LEGACY_DEFAULT_LANGUAGE,
        "legacy_default",
        "missing_wiki_language",
    )


def wiki_language(root: str | Path | None = None) -> str:
    return resolve_wiki_language(root).language
