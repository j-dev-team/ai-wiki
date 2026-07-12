from __future__ import annotations

import re
from copy import deepcopy

from ai_wiki.missions import MissionControlReader, MissionStore
from ai_wiki.policy import Principal, SecurityPolicy
from tests.mission_control_fixtures import large_run, plan_document, run_document


def test_task_sections_render_every_contract_field_and_dependency_link(wiki_root):
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
    for text in (
        "Task navigator", "Build detail", "Render task content and linked proof.",
        "Assignee", "Attempt", "Started", "Completed", "Readiness",
        "Dependencies", "Verification", "Authorization", "Resources", "Result",
        "Task completion criteria",
    ):
        assert text in html
    assert 'id="task-T1"' in html and 'href="#task-T0"' in html
    assert "Blocked by dependencies" in html
    assert "Awaiting review" in html
    assert "Flask client" in html and "read missions" in html


def test_criteria_have_unique_stable_anchors_states_counts_and_evidence_targets(wiki_root):
    from ai_wiki.web import app

    store = MissionStore(wiki_root)
    try:
        plan = plan_document()
        store.create(plan)
        run = run_document()
        run["evidence"][0]["criterion_ids"].append(
            plan["payload"]["acceptance_criteria"][0]
        )
        store.create(run)
    finally:
        store.close()
    app.config.update(TESTING=True)
    html = app.test_client().get(f"/missions/{run['id']}?revision=4&lang=en").get_data(as_text=True)
    ids = re.findall(r'\sid="([^"]+)"', html)
    assert len(ids) == len(set(ids))
    criterion_ids = [item for item in ids if item.startswith("criterion-T")]
    assert len(criterion_ids) == 3
    global_criterion_ids = [item for item in ids if item.startswith("criterion-global-")]
    assert len(global_criterion_ids) == 2
    assert 'data-coverage="covered"' in html
    assert 'data-coverage="pending_review"' in html
    assert 'data-coverage="missing"' in html
    evidence_links = re.findall(r'href="#(evidence-[^"]+)"', html)
    assert evidence_links
    assert all(target in ids for target in evidence_links)
    assert any(target == "evidence-E-file" for target in evidence_links)
    assert 'href="#criterion-global-' in html
    assert "1 evidence items" in html
    assert "mission_tasks.evidence_items" not in html
    assert "evidence items" in html
    assert 'href="#task-navigator"' in html


def test_policy_redaction_changes_linked_criterion_state_before_render(wiki_root, monkeypatch):
    from ai_wiki import web

    store = MissionStore(wiki_root)
    try:
        store.create(plan_document())
        run = run_document()
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
    html = web.app.test_client().get(f"/missions/{run['id']}?revision=4&lang=en").get_data(as_text=True)
    assert 'data-coverage="redacted"' in html
    assert "Redacted by policy" in html


def test_proof_rail_supports_disputed_unavailable_and_redacted_states(wiki_root, monkeypatch):
    from ai_wiki import web

    store = MissionStore(wiki_root)
    try:
        store.create(plan_document())
        run = run_document()
        store.create(run)
        reader = MissionControlReader(
            store, SecurityPolicy(wiki_root), Principal("reviewer", frozenset({"reviewer"})),
        )
        detail = reader.detail(run["id"], 4)
    finally:
        store.close()
    for task, state in zip(detail["tasks"], ("disputed", "unavailable", "redacted")):
        task["criteria"][0]["coverage_status"] = state

    class FakeReader:
        def detail(self, *_args, **_kwargs):
            return deepcopy(detail)

    def fake_helper():
        new_store = MissionStore(wiki_root)
        return new_store, FakeReader()

    monkeypatch.setattr(web, "_mission_reader", fake_helper)
    web.app.config.update(TESTING=True)
    html = web.app.test_client().get(f"/missions/{run['id']}?revision=4&lang=en").get_data(as_text=True)
    for state, label in (
        ("disputed", "Disputed"), ("unavailable", "Unavailable"),
        ("redacted", "Redacted by policy"),
    ):
        assert f'data-coverage="{state}"' in html
        assert label in html


def test_long_content_and_1_9_100_task_documents_remain_bounded(wiki_root):
    from ai_wiki.web import app

    app.config.update(TESTING=True)
    for task_count in (1, 9, 100):
        root = wiki_root / f"case-{task_count}"
        root.mkdir()
        (root / "data").mkdir()
        plan, run = large_run(task_count=task_count, evidence_count=max(task_count, 12))
        plan["id"] = plan["payload"]["plan_id"] = f"plan-{task_count}"
        run["id"] = run["payload"]["run_id"] = f"run-{task_count}"
        run["payload"]["plan_id"] = plan["id"]
        plan["payload"]["tasks"][0]["instructions"] = "긴 지시 사항 " * 120
        plan["payload"]["tasks"][0]["resources"] = ["resource/" + "very-long-segment/" * 30]
        store = MissionStore(root)
        try:
            store.create(plan)
            store.create(run)
        finally:
            store.close()
        # The app root follows the environment fixture; point it at this isolated case.
        import os
        os.environ["AI_WIKI_ROOT"] = str(root)
        html = app.test_client().get(f"/missions/{run['id']}?revision=4&lang=en").get_data(as_text=True)
        assert html.count('<article class="mission-task-card"') == task_count
        assert "very-long-segment" in html
    import os
    os.environ["AI_WIKI_ROOT"] = str(wiki_root)


def test_task_css_wraps_long_content_and_encodes_all_proof_states():
    from pathlib import Path

    css = Path("src/ai_wiki/static/style.css").read_text(encoding="utf-8")
    assert "overflow-wrap: anywhere" in css
    for state in ("covered", "missing", "pending_review", "redacted", "disputed", "unavailable"):
        assert f'[data-coverage="{state}"]' in css
