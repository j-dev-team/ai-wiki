from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from ai_wiki.api import AIWikiClient
from ai_wiki.mission_contracts import (
    MissionDocument,
    localization_values,
    mission_json_schema,
)
from ai_wiki.missions import MissionStore
from tests.mission_control_fixtures import plan_document, run_document


def test_legacy_mission_gets_und_language_without_rewriting_source_shape():
    raw = plan_document()
    document = MissionDocument.model_validate(raw, strict=True)
    assert document.metadata.source_language == "und"
    assert document.localizations == []
    assert "source_language" not in raw["metadata"]


def test_localized_narratives_are_language_keyed_and_source_is_preserved():
    raw = plan_document()
    raw["metadata"]["source_language"] = "ko"
    raw["localizations"] = [{
        "language": "en",
        "source_revision": 1,
        "values": {
            "objective": "Make Mission reviewable.",
            "scope.0": "Mission overview",
            "tasks.T0.title": "Capture baseline",
            "tasks.T0.instructions": "Record the immutable baseline.",
        },
    }]
    document = MissionDocument.model_validate(raw, strict=True)
    assert document.payload["objective"] == raw["payload"]["objective"]
    assert localization_values(document, "en")["tasks.T0.title"] == "Capture baseline"
    assert localization_values(document, "ko") == {}


@pytest.mark.parametrize("mutation,match", [
    (lambda raw: raw.update({"localizations": [
        {"language": "ja", "values": {"objective": "x"}},
    ]}), "language"),
    (lambda raw: raw.update({"localizations": [
        {"language": "en", "values": {"evidence.E-file.locator": "translated"}},
    ]}), "invalid localized narrative"),
    (lambda raw: raw.update({"localizations": [
        {"language": "en", "values": {"objective": "One"}},
        {"language": "en", "values": {"objective": "Two"}},
    ]}), "languages must be unique"),
])
def test_invalid_language_technical_field_and_duplicate_translation_are_rejected(mutation, match):
    raw = plan_document()
    mutation(raw)
    with pytest.raises(ValidationError, match=match):
        MissionDocument.model_validate(raw, strict=True)


def test_run_localization_cannot_target_paths_commands_or_evidence():
    raw = run_document()
    raw["localizations"] = [{
        "language": "ko",
        "source_revision": 1,
        "values": {"handoff.current_state": "검토 준비 완료", "tasks.T0.result": "기준선 기록 완료"},
    }]
    assert MissionDocument.model_validate(raw, strict=True).localizations[0].language == "ko"
    for key in ("handoff.changed_files.0", "artifacts.0", "evidence.E-file.result"):
        invalid = deepcopy(raw)
        invalid["localizations"][0]["values"] = {key: "번역 금지"}
        with pytest.raises(ValidationError, match="invalid localized narrative"):
            MissionDocument.model_validate(invalid, strict=True)


def test_mission_schema_and_capabilities_expose_language_contract(wiki_root):
    (wiki_root / ".ai-wiki.yaml").write_text("lang: ko\n", encoding="utf-8")
    schema = mission_json_schema()
    assert "localizations" in schema["properties"]
    metadata_ref = schema["properties"]["metadata"]["$ref"].split("/")[-1]
    assert "source_language" in schema["$defs"][metadata_ref]["properties"]

    client = AIWikiClient(wiki_root)
    try:
        data = client.capabilities()["data"]
    finally:
        client.close()
    assert data["language"]["authoring_language"] == "ko"
    assert data["language"]["technical_fields_are_source_only"] is True
    assert data["commands"]["mission"]["source_language_field"] == "metadata.source_language"


def test_work_run_keeps_the_approved_plan_localization_revision(wiki_root):
    raw = plan_document()
    raw["metadata"]["source_language"] = "en"
    raw["localizations"] = [{
        "language": "ko",
        "source_revision": 1,
        "values": {"objective": "승인된 한국어 목표"},
    }]
    raw["status"] = "approved"
    raw["payload"]["approval"].update({
        "status": "approved",
        "decided_by": "owner",
        "decided_at": raw["metadata"]["modified_at"],
    })
    store = MissionStore(wiki_root)
    try:
        store.create(raw)
        run = store.start_run(raw["id"], actor="agent", run_id="localized-run")
        pinned = store.get(run.payload["plan_id"], run.payload["plan_revision"])
        assert localization_values(pinned, "ko")["objective"] == "승인된 한국어 목표"
        assert run.payload["plan_revision"] == 1
    finally:
        store.close()


def test_localization_source_revision_is_required_for_authored_documents_and_bounded():
    raw = plan_document()
    raw["metadata"]["source_language"] = "en"
    raw["localizations"] = [{
        "language": "ko",
        "values": {"objective": "검토 가능한 계획"},
    }]
    document = MissionDocument.model_validate(raw, strict=True)
    from ai_wiki.mission_contracts import validate_mission_authoring_quality
    with pytest.raises(ValueError, match="source_revision"):
        validate_mission_authoring_quality(document)

    raw["localizations"][0]["source_revision"] = 2
    with pytest.raises(ValidationError, match="cannot be newer"):
        MissionDocument.model_validate(raw, strict=True)


def test_localized_narrative_language_is_validated_for_authored_documents():
    raw = plan_document()
    raw["metadata"]["source_language"] = "en"
    raw["localizations"] = [{
        "language": "ko",
        "source_revision": 1,
        "values": {"objective": "English-only localization"},
    }]
    document = MissionDocument.model_validate(raw, strict=True)
    from ai_wiki.mission_contracts import validate_mission_authoring_quality
    with pytest.raises(ValueError, match="does not match language ko"):
        validate_mission_authoring_quality(document)
