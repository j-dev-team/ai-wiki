from __future__ import annotations

from copy import deepcopy

from ai_wiki.mission_contracts import legacy_criterion_id
from ai_wiki.missions import MissionControlReader, MissionStore
from ai_wiki.policy import Principal, SecurityPolicy
from tests.mission_control_fixtures import CRITERIA, plan_document


def _localized_plan(*, approved=False):
    raw = plan_document()
    raw["metadata"]["source_language"] = "en"
    criterion_id = legacy_criterion_id(
        CRITERIA["baseline"], scope="task", owner_id=f"{raw['id']}:T0",
    )
    raw["localizations"] = [{
        "language": "ko",
        "source_revision": 1,
        "values": {
            "objective": "Mission 작업을 사람이 검토할 수 있게 만든다.",
            "scope.0": "Mission 작업 목록",
            "constraints.0": "읽기 전용 화면을 유지한다.",
            "tasks.T0.title": "기준 상태 기록",
            "tasks.T0.instructions": "변경 전 기준 상태를 안전하게 기록한다.",
            f"tasks.T0.criteria.{criterion_id}": "기준 상태가 증거와 함께 기록된다.",
        },
    }]
    if approved:
        raw["status"] = "approved"
        raw["payload"]["approval"].update({
            "status": "approved",
            "decided_by": "owner",
            "decided_at": raw["metadata"]["modified_at"],
        })
    return raw


def test_overview_detail_and_api_choose_the_same_korean_localization(wiki_root):
    from ai_wiki.web import app

    raw = _localized_plan()
    store = MissionStore(wiki_root)
    try:
        store.create(raw)
    finally:
        store.close()
    app.config.update(TESTING=True)
    client = app.test_client()
    overview = client.get("/missions?lang=ko").get_data(as_text=True)
    detail = client.get(f"/missions/{raw['id']}?revision=1&lang=ko").get_data(as_text=True)
    api = client.get(f"/api/missions/{raw['id']}?revision=1&lang=ko").get_json()["mission"]
    assert "Mission 작업을 사람이 검토할 수 있게 만든다" in overview
    assert "Mission 작업을 사람이 검토할 수 있게 만든다" in detail
    assert "기준 상태 기록" in detail
    assert "ko 번역 표시" in detail
    assert api["summary"]["objective"] == "Mission 작업을 사람이 검토할 수 있게 만든다."
    assert api["tasks"][0]["title"] == "기준 상태 기록"
    assert api["language"]["mode"] == "localized"
    assert api["language"]["localization_source_revision"] == 1


def test_source_language_view_keeps_original_and_technical_fields(wiki_root):
    raw = _localized_plan()
    store = MissionStore(wiki_root)
    try:
        store.create(raw)
        reader = MissionControlReader(
            store, SecurityPolicy(wiki_root),
            Principal("owner", frozenset({"owner"})), display_language="en",
        )
        detail = reader.detail(raw["id"], 1)
    finally:
        store.close()
    assert detail["summary"]["objective"] == "Make Mission work independently reviewable."
    assert detail["tasks"][0]["title"] == "Capture baseline"
    assert detail["tasks"][0]["resources"] == ["workspace:baseline"]
    assert detail["language"]["mode"] == "source"
    assert detail["payload"]["objective"] == "Make Mission work independently reviewable."


def test_missing_translation_is_explicit_fallback(wiki_root):
    from ai_wiki.web import app

    raw = plan_document()
    raw["metadata"]["source_language"] = "en"
    store = MissionStore(wiki_root)
    try:
        store.create(raw)
    finally:
        store.close()
    app.config.update(TESTING=True)
    html = app.test_client().get(
        f"/missions/{raw['id']}?revision=1&lang=ko",
    ).get_data(as_text=True)
    assert "en 원문으로 대체 표시" in html
    assert "원문 보기" in html
    assert "Make Mission work independently reviewable." in html


def test_run_uses_only_the_exact_pinned_plan_localization(wiki_root):
    raw = _localized_plan(approved=True)
    store = MissionStore(wiki_root)
    try:
        store.create(raw)
        run = store.start_run(raw["id"], actor="agent", run_id="localized-run")
        latest = store.get(raw["id"])
        newer_localizations = deepcopy(latest.model_dump(mode="json")["localizations"])
        newer_localizations[0]["values"]["tasks.T0.title"] = "새 리비전 제목은 노출되면 안 된다"
        store.patch(
            raw["id"], [{"op": "replace", "path": "/localizations", "value": newer_localizations}],
            if_revision=latest.revision, actor="agent", roles={"agent"},
        )
        detail = MissionControlReader(
            store, SecurityPolicy(wiki_root),
            Principal("owner", frozenset({"owner"})), display_language="ko",
        ).detail(run.id, run.revision)
    finally:
        store.close()
    assert run.payload["plan_revision"] == 1
    assert detail["tasks"][0]["title"] == "기준 상태 기록"
    assert "새 리비전" not in detail["tasks"][0]["title"]
    assert detail["language"]["mode"] == "localized"


def test_legacy_language_is_visible_without_rewriting_document(wiki_root):
    raw = plan_document()
    store = MissionStore(wiki_root)
    try:
        store.create(raw)
        before = store._path(store.get(raw["id"])).read_bytes()
        detail = MissionControlReader(
            store, SecurityPolicy(wiki_root),
            Principal("owner", frozenset({"owner"})), display_language="ko",
        ).detail(raw["id"], 1)
        after = store._path(store.get(raw["id"])).read_bytes()
    finally:
        store.close()
    assert detail["language"]["mode"] == "legacy_source"
    assert detail["language"]["fallback"] is True
    assert before == after


def test_localized_secret_is_redacted_equally_in_html_and_json(wiki_root):
    from ai_wiki.web import app

    raw = plan_document()
    raw["localizations"] = [{
        "language": "ko",
        "source_revision": 1,
        "values": {"objective": "token=secret:LOCALIZED_MISSION_TOKEN"},
    }]
    store = MissionStore(wiki_root)
    try:
        store.create(raw)
    finally:
        store.close()
    app.config.update(TESTING=True)
    client = app.test_client()
    html = client.get(
        f"/missions/{raw['id']}?revision=1&lang=ko",
    ).get_data(as_text=True)
    api = client.get(
        f"/api/missions/{raw['id']}?revision=1&lang=ko",
    ).get_json()["mission"]
    assert "LOCALIZED_MISSION_TOKEN" not in html
    assert api["summary"]["objective"] == "[redacted]"
    assert any("summary/objective" in path for path in api["policy"]["redacted_fields"])
