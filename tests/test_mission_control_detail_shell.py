from __future__ import annotations

from copy import deepcopy

from ai_wiki.mission_contracts import MissionDocument
from ai_wiki.missions import MissionControlReader, MissionStore
from ai_wiki.policy import Principal, SecurityPolicy, PolicyDenied
from tests.mission_control_fixtures import (
    candidate_document,
    plan_document,
    research_document,
    run_document,
)


def _seed_all(store: MissionStore):
    plan = plan_document()
    run = run_document()
    research = research_document()
    candidate = candidate_document()
    for document in (plan, run, research, candidate):
        store.create(document)
    return plan, run, research, candidate


def test_plan_detail_is_exact_revision_semantic_and_read_only(wiki_root):
    from ai_wiki.web import app

    store = MissionStore(wiki_root)
    try:
        plan, run, _, _ = _seed_all(store)
        newer = deepcopy(plan)
        newer["revision"] = 2
        newer["payload"]["objective"] = "Objective from revision two"
        store._write(MissionDocument.model_validate(newer, strict=True))
    finally:
        store.close()
    app.config.update(TESTING=True)
    response = app.test_client().get(f"/missions/{plan['id']}?revision=1&lang=en")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Make Mission work independently reviewable." in html
    assert "Objective from revision two" not in html
    assert "Revision 1" in html and 'rel="next"' in html
    assert "Mission overview" in html
    assert "Mission overview" in html and "Mission detail" in html
    assert "Read-only UI" in html
    assert "The exact plan revision is visible" in html
    assert f'/missions/{run["id"]}?revision=4' in html
    assert "This view is read-only" in html
    article = html.split('<article class="mission-detail"', 1)[1]
    assert "<button" not in article and "method=\"post\"" not in article.lower()
    assert html.index("<h1") < html.index("<h2") < html.index("<h3")


def test_run_detail_shows_pinned_plan_progress_blockers_and_coverage(wiki_root):
    from ai_wiki.web import app

    store = MissionStore(wiki_root)
    try:
        plan = plan_document()
        run = run_document()
        store.create(plan)
        store.create(run)
    finally:
        store.close()
    app.config.update(TESTING=True)
    response = app.test_client().get(f"/missions/{run['id']}?revision=4&lang=en")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Is this work safe to approve or continue?" in html
    assert "Progress and completion proof" in html
    assert "1 / 3" in html
    assert "tasks are blocked" in html and "<strong>1</strong>" in html
    assert f'/missions/{plan["id"]}?revision=1' in html
    assert f"{plan['id']} · r1" in html
    assert "Plan completion criteria" in html
    assert "The exact plan revision is visible" in html
    assert "Every task links criteria to evidence" in html
    assert 'id="criterion-global-' in html
    assert 'data-coverage="missing"' in html
    assert "Viewing this page does not change work or approval state" in html


def test_detail_supports_every_mission_kind(wiki_root):
    from ai_wiki.web import app

    store = MissionStore(wiki_root)
    try:
        documents = _seed_all(store)
    finally:
        store.close()
    app.config.update(TESTING=True)
    client = app.test_client()
    for document in documents:
        response = client.get(f"/missions/{document['id']}?revision={document['revision']}&lang=en")
        assert response.status_code == 200
        assert document["id"] in response.get_data(as_text=True)


def test_detail_invalid_missing_denied_and_degraded_states_are_explicit(wiki_root, monkeypatch):
    from ai_wiki import web

    store = MissionStore(wiki_root)
    try:
        missing_run = run_document(plan_id="missing-pinned-plan")
        store.create(missing_run)
    finally:
        store.close()
    web.app.config.update(TESTING=True)
    client = web.app.test_client()

    invalid = client.get(f"/missions/{missing_run['id']}?revision=0&lang=en")
    assert invalid.status_code == 400
    assert "Revision must be a positive integer" in invalid.get_data(as_text=True)
    missing = client.get("/missions/no-such-mission?revision=1&lang=en")
    assert missing.status_code == 404
    assert "Check the Mission ID and revision" in missing.get_data(as_text=True)
    degraded = client.get(f"/missions/{missing_run['id']}?revision=4&lang=en")
    assert degraded.status_code == 200
    degraded_html = degraded.get_data(as_text=True)
    assert "Missing information needs attention" in degraded_html
    assert "missing_pinned_plan" in degraded_html
    assert "never infer task instructions from a newer plan" in degraded_html

    def denied():
        raise PolicyDenied("permission_denied", "denied")

    monkeypatch.setattr(web, "_mission_reader", denied)
    denied_response = client.get(f"/missions/{missing_run['id']}?revision=4&lang=en")
    assert denied_response.status_code == 403
    assert "ask an owner for access" in denied_response.get_data(as_text=True)


def test_reader_detail_route_exposes_policy_state_but_not_sensitive_evidence(wiki_root, monkeypatch):
    from ai_wiki import web

    store = MissionStore(wiki_root)
    try:
        plan = plan_document()
        run = run_document()
        store.create(plan)
        store.create(run)
    finally:
        store.close()

    def reader_helper():
        new_store = MissionStore(wiki_root)
        reader = MissionControlReader(
            new_store, SecurityPolicy(wiki_root), Principal("reader", frozenset({"reader"})),
        )
        return new_store, reader

    monkeypatch.setattr(web, "_mission_reader", reader_helper)
    web.app.config.update(TESTING=True)
    response = web.app.test_client().get(f"/missions/{run['id']}?revision=4&lang=en")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "reader" in html and "fields redacted" in html
    assert "C:/private/workspace/command.log" not in html
    assert "token=secret" not in html
    assert "Fixture added" not in html
