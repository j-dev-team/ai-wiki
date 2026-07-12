from __future__ import annotations

import os

import yaml

from ai_wiki.language import resolve_wiki_language


def _config(root, **values):
    (root / ".ai-wiki.yaml").write_text(
        yaml.safe_dump(values, allow_unicode=True),
        encoding="utf-8",
    )


def test_config_is_the_canonical_wiki_authoring_language(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_WIKI_LANG", "en")
    _config(tmp_path, lang="ko")
    resolved = resolve_wiki_language(tmp_path)
    assert resolved.language == "ko"
    assert resolved.source == "config"
    assert resolved.warning is None

    _config(tmp_path, lang="en")
    resolved = resolve_wiki_language(tmp_path)
    assert resolved.language == "en"
    assert resolved.source == "config"
    assert resolved.warning is None


def test_missing_language_uses_explicit_environment_then_documented_fallback(tmp_path, monkeypatch):
    _config(tmp_path, security={"mode": "trusted-local"})
    monkeypatch.setenv("AI_WIKI_LANG", "en")
    resolved = resolve_wiki_language(tmp_path)
    assert resolved.as_dict() == {
        "language": "en",
        "source": "environment",
        "warning": "missing_wiki_language",
    }

    monkeypatch.delenv("AI_WIKI_LANG")
    resolved = resolve_wiki_language(tmp_path)
    assert resolved.language == "en"
    assert resolved.source == "legacy_default"
    assert resolved.warning == "missing_wiki_language"


def test_invalid_language_is_explicit_and_deterministic(tmp_path, monkeypatch):
    _config(tmp_path, lang="xx")
    monkeypatch.delenv("AI_WIKI_LANG", raising=False)
    resolved = resolve_wiki_language(tmp_path)
    assert resolved.language == "en"
    assert resolved.source == "fallback"
    assert resolved.warning == "invalid_wiki_language"


def test_cli_and_web_share_the_same_default_without_mutating_config(wiki_root, monkeypatch):
    from ai_wiki.cli import _get_lang
    from ai_wiki.web import app

    _config(wiki_root, lang="en")
    monkeypatch.delenv("AI_WIKI_LANG", raising=False)
    before = (wiki_root / ".ai-wiki.yaml").read_bytes()
    assert _get_lang() == "en"
    app.config.update(TESTING=True)
    with app.test_client() as client:
        response = client.get("/create")
        assert response.status_code == 200
        assert "New Article" in response.get_data(as_text=True)
        client.get("/create?lang=ko")
    assert (wiki_root / ".ai-wiki.yaml").read_bytes() == before


def test_variant_config_filename_uses_the_same_resolver(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_WIKI_CONFIG_FILENAME", ".law-wiki.yaml")
    (tmp_path / ".law-wiki.yaml").write_text("lang: ko\n", encoding="utf-8")
    assert resolve_wiki_language(tmp_path).as_dict() == {
        "language": "ko",
        "source": "config",
        "warning": None,
    }


def test_leaked_variant_runtime_does_not_hide_core_wiki_config(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_WIKI_CONFIG_FILENAME", ".law-wiki.yaml")
    (tmp_path / ".ai-wiki.yaml").write_text("lang: ko\n", encoding="utf-8")
    resolved = resolve_wiki_language(tmp_path)
    assert resolved.language == "ko"
    assert resolved.source == "config"
