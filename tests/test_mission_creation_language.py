from __future__ import annotations

import json
import os
import subprocess

import yaml

from ai_wiki.missions import MissionStore


NOW = "2026-07-13T00:00:00Z"


def _config(root, lang):
    (root / ".ai-wiki.yaml").write_text(
        yaml.safe_dump({
            "lang": lang,
            "security": {
                "mode": "trusted-local",
                "default_principal": "local-owner",
                "principals": [
                    {"id": "local-owner", "roles": ["owner"]},
                    {"id": "codex", "roles": ["agent"]},
                ],
            },
        }, allow_unicode=True),
        encoding="utf-8",
    )


def _report(mission_id="research-language-stdin"):
    return {
        "mission_schema_version": 1,
        "kind": "research_report",
        "id": mission_id,
        "revision": 1,
        "status": "proposed",
        "metadata": {
            "created_at": NOW,
            "modified_at": NOW,
            "created_by": "codex",
            "namespace": "artifacts",
        },
        "payload": {
            "workspace_root": "D:/workspace",
            "scope": ["한국어 위키의 작업 문서 작성 언어를 조사한다."],
            "findings": [{
                "id": "F1",
                "title": "작성 언어 기준이 필요하다",
                "detail": "위키 설정 언어를 따르지 않으면 사용자가 작업 기록을 이해하기 어렵다.",
            }],
            "recommendations": ["위키 설정 언어를 Mission 작성 언어로 사용한다."],
            "sufficient": True,
        },
        "evidence": [],
        "history": [],
    }


def _plan():
    return {
        "mission_schema_version": 1,
        "kind": "work_plan",
        "id": "plan-language-stdin",
        "revision": 1,
        "status": "proposed",
        "metadata": {
            "created_at": NOW,
            "modified_at": NOW,
            "created_by": "codex",
            "namespace": "plans",
        },
        "payload": {
            "plan_id": "plan-language-stdin",
            "objective": "Make every Mission narrative follow the configured wiki language.",
            "scope": ["Mission authoring language"],
            "constraints": ["Preserve every existing audit revision"],
            "acceptance_criteria": ["Every new narrative is readable in English"],
            "tasks": [{
                "id": "T0",
                "title": "Implement language resolution",
                "instructions": "Use the configured wiki language for every new Mission narrative.",
                "dependencies": [],
                "acceptance_criteria": ["English Mission prose remains readable"],
                "verification": ["pytest -q"],
                "authorization": ["Modify approved source and tests"],
            }],
            "approval": {"required": True, "status": "pending"},
        },
        "evidence": [],
        "history": [],
    }


def _create_via_utf8_stdin(root, document):
    env = os.environ.copy()
    env["AI_WIKI_ROOT"] = str(root)
    return subprocess.run(
        ["ai-wiki", "mission", "create", "--document-file", "-", "--principal", "codex"],
        input=json.dumps(document, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        env=env,
    )


def test_windows_safe_utf8_stdin_creates_korean_report_and_sets_source_language(tmp_path):
    _config(tmp_path, "ko")
    result = _create_via_utf8_stdin(tmp_path, _report())
    assert result.returncode == 0, result.stdout.decode("utf-8", errors="replace")
    store = MissionStore(tmp_path)
    try:
        document = store.get("research-language-stdin")
    finally:
        store.close()
    assert document.metadata.source_language == "ko"
    assert document.payload["findings"][0]["title"] == "작성 언어 기준이 필요하다"


def test_english_wiki_creates_english_plan(tmp_path):
    _config(tmp_path, "en")
    result = _create_via_utf8_stdin(tmp_path, _plan())
    assert result.returncode == 0, result.stdout.decode("utf-8", errors="replace")
    store = MissionStore(tmp_path)
    try:
        document = store.get("plan-language-stdin")
    finally:
        store.close()
    assert document.metadata.source_language == "en"
    assert document.payload["tasks"][0]["title"] == "Implement language resolution"


def test_empty_or_screenshot_only_report_is_rejected(tmp_path):
    _config(tmp_path, "ko")
    document = _report("research-empty")
    document["payload"]["findings"] = []
    document["payload"]["recommendations"] = []
    document["evidence"] = [{
        "evidence_id": "E-shot",
        "type": "screenshot",
        "locator": "screenshot.png",
        "captured_at": NOW,
        "captured_by": "codex",
    }]
    result = _create_via_utf8_stdin(tmp_path, document)
    assert result.returncode != 0
    assert b"readable findings and recommendations" in result.stdout


def test_source_prose_must_match_wiki_authoring_language(tmp_path):
    _config(tmp_path, "ko")
    result = _create_via_utf8_stdin(tmp_path, _plan())
    assert result.returncode != 0
    assert b"does not match source language ko" in result.stdout


def test_installed_skill_template_requires_language_and_readability():
    text = (
        __import__("pathlib").Path(__file__).parents[1]
        / "src" / "ai_wiki" / "mission_skill_templates" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "data.language.authoring_language" in text
    assert "metadata.source_language" in text
    assert "Screenshots and identifiers support" in text
