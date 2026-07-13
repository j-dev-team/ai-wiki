from __future__ import annotations

import json
import time

import pytest

from ai_wiki.missions import MissionStore
from ai_wiki.policy import PolicyDenied
from tests.mission_control_fixtures import plan_document, run_document


def _seed(store: MissionStore):
    plan = plan_document()
    run = run_document()
    store.create(plan)
    store.create(run)
    return plan, run


def test_overview_is_a_semantic_queue_with_exact_revision_links(wiki_root):
    from ai_wiki.web import app

    store = MissionStore(wiki_root)
    try:
        plan, run = _seed(store)
    finally:
        store.close()
    app.config.update(TESTING=True)
    response = app.test_client().get("/missions?lang=en")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert '<main class="container">' in html
    assert "<h1>Mission Control</h1>" in html
    assert '<h2 id="mission-queue-title">' in html
    assert '<ol class="mission-queue">' in html
    assert html.count('<article class="mission-queue-item') == 1
    assert f'/missions/{plan["id"]}?revision=1&amp;lang=en' in html
    assert f'/missions/{run["id"]}?revision=4&amp;lang=en' in html
    assert "Linked runs" in html
    assert "Blocked" in html and "In review" in html
    assert "Criteria with proof" in html and "Handoff" in html
    assert "1 / 3" in html
    assert "2 / 5" in html
    assert ">3</dd>" in html
    assert "Recorded" in html
    assert "Execution needing attention" in html
    assert "Next task" in html
    assert "Open execution record" in html
    assert "C:/private/workspace/command.log" not in html
    assert "token=secret" not in html
    assert "Detail is ready for review" not in html


def test_plan_detail_projects_representative_run_progress_and_proof(wiki_root):
    from ai_wiki.web import app

    store = MissionStore(wiki_root)
    try:
        plan, run = _seed(store)
    finally:
        store.close()
    app.config.update(TESTING=True)
    response = app.test_client().get(f"/missions/{plan['id']}?revision=1&lang=en")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "1 / 3" in html
    assert "1 / 5" in html
    assert "Baseline captured" in html
    assert "Awaiting review" in html
    assert "Detail is ready for review" in html
    assert run["id"] in html


def test_completed_run_is_representative_even_when_a_running_run_is_newer(wiki_root):
    from ai_wiki.missions import MissionControlReader
    from ai_wiki.policy import Principal, SecurityPolicy

    store = MissionStore(wiki_root)
    try:
        plan = plan_document()
        completed = run_document()
        completed["id"] = completed["payload"]["run_id"] = "run-completed"
        completed["status"] = "completed"
        completed["metadata"]["modified_at"] = "2026-07-12T00:00:01Z"
        completed["payload"]["completed_at"] = "2026-07-12T00:00:01Z"
        running = run_document(handoff="legacy")
        running["id"] = running["payload"]["run_id"] = "run-newer-running"
        running["metadata"]["modified_at"] = "2026-07-12T00:00:02Z"
        store.create(plan)
        store.create(completed)
        store.create(running)
        reader = MissionControlReader(
            store, SecurityPolicy(wiki_root),
            Principal("local-owner", frozenset({"owner"})),
            display_language="en",
        )
        listing = reader.list()
    finally:
        store.close()

    assert listing["total"] == 1
    assert listing["items"][0]["summary"]["representative_run"] == {
        "id": "run-completed", "revision": 4, "status": "completed",
    }


def test_overview_filters_and_pagination_preserve_query_and_language(wiki_root):
    from ai_wiki.web import app

    store = MissionStore(wiki_root)
    try:
        plan, run = _seed(store)
        store.create(run_document(handoff="legacy"))
    finally:
        store.close()
    app.config.update(TESTING=True)
    response = app.test_client().get(
        f"/missions?lang=en&kind=work_run&plan={plan['id']}&limit=1&offset=0",
    )
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert f'value="work_run" selected' in html
    assert f'value="{plan["id"]}"' in html
    assert "run-mission-control-" in html and plan["id"] in html
    assert html.count('<article class="mission-queue-item') == 1
    assert "lang=en" in html and "kind=work_run" in html
    assert 'rel="next"' in html


def test_overview_empty_no_results_invalid_and_denied_states_are_actionable(wiki_root, monkeypatch):
    from ai_wiki import web

    web.app.config.update(TESTING=True)
    client = web.app.test_client()
    empty = client.get("/missions?lang=en")
    assert empty.status_code == 200
    assert "Plans and runs will appear here" in empty.get_data(as_text=True)

    store = MissionStore(wiki_root)
    try:
        store.create(plan_document())
    finally:
        store.close()
    no_results = client.get("/missions?lang=en&kind=work_run")
    assert "No work matches these filters" in no_results.get_data(as_text=True)
    assert "Clear filters" in no_results.get_data(as_text=True)

    invalid = client.get("/missions?lang=en&kind=unknown")
    assert invalid.status_code == 400
    assert "Check the filter values" in invalid.get_data(as_text=True)

    def denied():
        raise PolicyDenied("permission_denied", "denied")

    monkeypatch.setattr(web, "_mission_reader", denied)
    denied_response = client.get("/missions?lang=en")
    assert denied_response.status_code == 403
    assert "ask an owner for access" in denied_response.get_data(as_text=True)


def test_overview_marks_degraded_index_summaries_without_opening_evidence(wiki_root):
    from ai_wiki.web import app

    store = MissionStore(wiki_root)
    try:
        run = run_document(plan_id="missing-plan")
        store.create(run)
    finally:
        store.close()
    app.config.update(TESTING=True)
    response = app.test_client().get("/missions?lang=en")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Some information is unavailable" in html
    assert "Restore the exact pinned plan" in html


def test_overview_500_row_index_page_is_bounded_and_lightweight(wiki_root):
    from ai_wiki.web import app

    store = MissionStore(wiki_root)
    summary = json.dumps({
        "objective": "Synthetic performance row", "approval_status": None,
        "task_counts": {"total": 0, "in_progress": 0, "blocked": 0,
                        "in_review": 0, "completed": 0, "skipped": 0},
        "criterion_counts": {"total": 0, "covered": 0, "missing": 0},
        "evidence_count": 0, "handoff_present": False, "degraded": False,
    })
    try:
        store.conn.executemany(
            "INSERT INTO mission_documents "
            "(id, kind, revision, status, file_path, modified_at, plan_id, run_id, summary_json) "
            "VALUES (?, 'research_report', 1, 'proposed', ?, '2026-07-12T00:00:00Z', NULL, NULL, ?)",
            [(f"performance-{index:04d}", f"missing-{index}.yaml", summary) for index in range(500)],
        )
        store.conn.commit()
    finally:
        store.close()
    app.config.update(TESTING=True)
    started = time.perf_counter()
    response = app.test_client().get("/missions?lang=en&limit=100")
    elapsed = time.perf_counter() - started
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "1–100 / 500" in html
    assert html.count('<article class="mission-queue-item') == 100
    assert elapsed < 2.0
