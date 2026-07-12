from __future__ import annotations

from copy import deepcopy

from ai_wiki.missions import MissionStore
from tests.mission_control_fixtures import research_document


def _report():
    raw = research_document()
    raw["payload"].update({
        "scope": ["Mission 문서 언어와 표시 방식을 조사한다."],
        "excluded_scope": ["코드 수정"],
        "findings": [{
            "id": "F1",
            "title": "보고서 본문이 보이지 않는다",
            "detail": "조사 결과보다 스크린샷 증거가 먼저 보여 사람이 결론을 이해하기 어렵다.",
            "evidence_ids": ["E-shot"],
        }],
        "recommendations": ["발견과 설명을 증거보다 먼저 표시한다."],
        "uncertainties": ["기존 보고서의 언어는 알 수 없을 수 있다."],
        "sufficient": True,
    })
    raw["evidence"] = [{
        "evidence_id": "E-shot",
        "type": "screenshot",
        "locator": "screenshots/report.png",
        "captured_at": raw["metadata"]["modified_at"],
        "captured_by": "researcher",
        "result": "화면에는 요약만 보인다.",
    }]
    return raw


def test_research_report_renders_findings_before_linked_evidence(wiki_root):
    from ai_wiki.web import app

    raw = _report()
    store = MissionStore(wiki_root)
    try:
        store.create(raw)
    finally:
        store.close()
    app.config.update(TESTING=True)
    html = app.test_client().get(
        f"/missions/{raw['id']}?revision=1&lang=ko",
    ).get_data(as_text=True)
    assert "조사 내용" in html
    assert "보고서 본문이 보이지 않는다" in html
    assert "조사 결과보다 스크린샷 증거가 먼저" in html
    assert "발견과 설명을 증거보다 먼저 표시한다" in html
    assert "기존 보고서의 언어는 알 수 없을 수 있다" in html
    assert html.index('id="finding-F1"') < html.index('id="evidence-E-shot"')
    assert 'href="#evidence-E-shot"' in html
    assert 'href="#finding-F1"' in html


def test_research_report_has_english_labels_and_semantic_sections(wiki_root):
    from ai_wiki.web import app

    raw = _report()
    store = MissionStore(wiki_root)
    try:
        store.create(raw)
    finally:
        store.close()
    html = app.test_client().get(
        f"/missions/{raw['id']}?revision=1&lang=en",
    ).get_data(as_text=True)
    for text in ("Research content", "Research scope", "Findings", "Recommended actions", "Open decisions"):
        assert text in html
    assert '<ol class="mission-finding-rail">' in html
    assert 'aria-labelledby="research-report-title"' in html


def test_research_report_policy_redaction_keeps_explanation(wiki_root, monkeypatch):
    from ai_wiki import web
    from ai_wiki.missions import MissionControlReader
    from ai_wiki.policy import Principal, SecurityPolicy

    raw = _report()
    private = deepcopy(raw)
    private["id"] = "research-private"
    private["evidence"][0]["locator"] = "C:/private/report.png"
    private["evidence"][0]["result"] = "token=secret:MISSION_TOKEN"
    store = MissionStore(wiki_root)
    try:
        store.create(private)
    finally:
        store.close()

    def reader():
        new_store = MissionStore(wiki_root)
        return new_store, MissionControlReader(
            new_store, SecurityPolicy(wiki_root), Principal("reader", frozenset({"reader"})),
        )

    monkeypatch.setattr(web, "_mission_reader", reader)
    web.app.config.update(TESTING=True)
    html = web.app.test_client().get(
        "/missions/research-private?revision=1&lang=ko",
    ).get_data(as_text=True)
    assert "조사 결과보다 스크린샷 증거가 먼저" in html
    assert "C:/private/report.png" not in html
    assert "MISSION_TOKEN" not in html


def test_research_report_css_is_responsive_and_wraps_long_content():
    css = (__import__("pathlib").Path(__file__).parents[1] / "src" / "ai_wiki" / "static" / "style.css").read_text(encoding="utf-8")
    assert ".mission-finding-rail" in css
    assert "overflow-wrap: anywhere" in css
    assert "@media (max-width: 768px)" in css
    assert ".mission-report-conclusions { grid-template-columns: 1fr; }" in css
