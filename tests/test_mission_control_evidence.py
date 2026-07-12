from __future__ import annotations

from copy import deepcopy

from ai_wiki.missions import MissionControlReader, MissionStore
from ai_wiki.policy import Principal, SecurityPolicy
from tests.mission_control_fixtures import CRITERIA, NOW, plan_document, run_document


def _rich_run():
    run = run_document()
    extra_types = ("commit", "document", "screenshot", "external_source", "human_decision")
    for index, evidence_type in enumerate(extra_types):
        evidence_id = f"E-{evidence_type}"
        run["evidence"].append({
            "evidence_id": evidence_id,
            "type": evidence_type,
            "locator": "path/" + "long-segment/" * 40 + str(index),
            "captured_at": NOW,
            "captured_by": "agent-1",
            "result": ("output line\n" * 1000) if evidence_type == "commit" else "",
            "source_ids": [f"source-{index}"],
            "criterion_ids": [CRITERIA["detail"]],
        })
        run["payload"]["task_states"][1]["evidence_ids"].append(evidence_id)
    events = [
        {"event_id": "later", "actor": "agent-1", "at": "2026-07-12T02:00:00Z",
         "previous_status": "created", "new_status": "running", "reason": "execution later"},
        {"event_id": "earlier", "actor": "local-owner", "at": "2026-07-12T01:00:00Z",
         "previous_status": "created", "new_status": "running", "reason": "owner review"},
    ]
    run["history"] = deepcopy(events)
    run["payload"]["execution_events"] = list(reversed(deepcopy(events)))
    return run


def test_evidence_is_deduplicated_and_links_both_directions(wiki_root):
    from ai_wiki.web import app

    store = MissionStore(wiki_root)
    try:
        store.create(plan_document())
        run = _rich_run()
        store.create(run)
    finally:
        store.close()
    app.config.update(TESTING=True)
    html = app.test_client().get(f"/missions/{run['id']}?revision=4&lang=en").get_data(as_text=True)
    assert html.count('id="evidence-E-test"') == 1
    assert 'href="#evidence-E-test"' in html
    assert html.count('href="#criterion-T') >= 2
    assert "2 criteria linked" in html
    for evidence_type in (
        "file change", "test result", "command", "commit", "document",
        "screenshot", "external source", "human decision",
    ):
        assert evidence_type in html.lower()


def test_evidence_fields_have_absent_bounded_and_multi_criterion_states(wiki_root):
    from ai_wiki.web import app

    store = MissionStore(wiki_root)
    try:
        store.create(plan_document())
        run = _rich_run()
        store.create(run)
    finally:
        store.close()
    app.config.update(TESTING=True)
    html = app.test_client().get(f"/missions/{run['id']}?revision=4&lang=en").get_data(as_text=True)
    for label in ("Result", "Locator", "Hash", "Captured by", "Captured at", "Source IDs", "Source criterion IDs"):
        assert label in html
    assert "Not recorded" in html
    assert "output line" in html
    assert "long-segment" in html
    css = open("src/ai_wiki/static/style.css", encoding="utf-8").read()
    assert "max-height: 220px" in css and "overflow: auto" in css
    assert "white-space: pre-wrap" in css and "overflow-wrap: anywhere" in css


def test_history_execution_and_owner_review_are_separate_and_sorted(wiki_root):
    from ai_wiki.web import app

    store = MissionStore(wiki_root)
    try:
        store.create(plan_document())
        run = _rich_run()
        store.create(run)
    finally:
        store.close()
    app.config.update(TESTING=True)
    html = app.test_client().get(f"/missions/{run['id']}?revision=4&lang=en").get_data(as_text=True)
    for heading in ("Mission revision history", "Execution events", "Reviewer and owner decisions"):
        assert heading in html
    history = html.split('id="mission-history-title"', 1)[1].split("</section>", 1)[0]
    execution = html.split('id="execution-events-title"', 1)[1].split("</section>", 1)[0]
    review = html.split('id="review-decisions-title"', 1)[1].split("</section>", 1)[0]
    assert history.index("01:00:00") < history.index("02:00:00")
    assert execution.index("01:00:00") < execution.index("02:00:00")
    assert "local-owner" in review and "agent-1" not in review


def test_html_and_json_share_reader_redaction(wiki_root, monkeypatch):
    from ai_wiki import web

    store = MissionStore(wiki_root)
    try:
        store.create(plan_document())
        run = _rich_run()
        store.create(run)
    finally:
        store.close()

    def reader_helper():
        new_store = MissionStore(wiki_root)
        return new_store, MissionControlReader(
            new_store, SecurityPolicy(wiki_root), Principal("reader", frozenset({"reader"})),
        )

    monkeypatch.setattr(web, "_mission_reader", reader_helper)
    web.app.config.update(TESTING=True)
    client = web.app.test_client()
    api = client.get(f"/api/missions/{run['id']}?revision=4").get_json()["mission"]
    html = client.get(f"/missions/{run['id']}?revision=4&lang=en").get_data(as_text=True)
    assert all(item["locator"] == "[redacted]" for item in api["evidence"])
    assert "C:/private/workspace/command.log" not in html
    assert "Redacted by policy" in html
    assert api["policy"]["redacted_fields"]


def test_unavailable_evidence_reason_is_explicit(wiki_root, monkeypatch):
    from ai_wiki import web

    store = MissionStore(wiki_root)
    try:
        store.create(plan_document())
        run = run_document()
        store.create(run)
        detail = MissionControlReader(
            store, SecurityPolicy(wiki_root), Principal("reviewer", frozenset({"reviewer"})),
        ).detail(run["id"], 4)
    finally:
        store.close()
    detail["evidence"][0]["locator"] = "[unavailable]"
    detail["evidence"][0]["result"] = "[unavailable]"

    class FakeReader:
        def detail(self, *_args, **_kwargs):
            return deepcopy(detail)

    def helper():
        new_store = MissionStore(wiki_root)
        return new_store, FakeReader()

    monkeypatch.setattr(web, "_mission_reader", helper)
    web.app.config.update(TESTING=True)
    html = web.app.test_client().get(f"/missions/{run['id']}?revision=4&lang=en").get_data(as_text=True)
    assert "The source evidence is unavailable" in html
    assert html.count("Unavailable") >= 2
