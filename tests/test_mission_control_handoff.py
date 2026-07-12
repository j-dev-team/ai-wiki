from __future__ import annotations

import os
from copy import deepcopy

from ai_wiki.missions import MissionControlReader, MissionStore
from ai_wiki.policy import Principal, SecurityPolicy
from tests.mission_control_fixtures import NOW, plan_document, run_document


def _render(wiki_root, run, *, lang="en"):
    from ai_wiki.web import app

    store = MissionStore(wiki_root)
    try:
        store.create(plan_document())
        store.create(run)
    finally:
        store.close()
    app.config.update(TESTING=True)
    previous = os.environ.get("AI_WIKI_ROOT")
    os.environ["AI_WIKI_ROOT"] = str(wiki_root)
    try:
        return app.test_client().get(
            f"/missions/{run['id']}?revision={run['revision']}&lang={lang}",
        ).get_data(as_text=True)
    finally:
        if previous is None:
            os.environ.pop("AI_WIKI_ROOT", None)
        else:
            os.environ["AI_WIKI_ROOT"] = previous


def test_typed_handoff_has_stable_order_complete_state_and_resolved_evidence(wiki_root):
    run = run_document()
    html = _render(wiki_root, run)
    headings = (
        "Current state", "Changed files", "Remaining work", "Blockers",
        "Linked evidence", "Artifacts", "Recorded by", "Recorded at", "Next owner",
    )
    positions = [html.index(item) for item in headings]
    assert positions == sorted(positions)
    assert "Resume information complete" in html
    assert "Validated format" in html
    assert 'href="#evidence-E-file"' in html
    assert "No safe viewer is available" in html
    assert "reviewer-1" in html
    assert "Viewing does not transfer ownership" in html


def test_legacy_empty_incomplete_stale_and_large_handoffs_are_bounded(wiki_root):
    legacy = run_document(handoff="legacy")
    legacy["payload"]["handoff"].update({
        "custom": {"nested": "x" * 4000},
        "changed_files": [f"path/{index}/" + "segment/" * 20 for index in range(80)],
        "remaining_work": [f"remaining {index}" for index in range(80)],
        "evidence_ids": ["missing-evidence"],
        "artifacts": ["missing/local/artifact.png"],
    })
    html = _render(wiki_root, legacy)
    assert "Legacy format" in html and "Resume information incomplete" in html
    assert "Additional legacy information" in html
    assert "Evidence not found" in html and "No safe viewer is available" in html
    assert "x" * 600 not in html

    empty_root = wiki_root / "empty"
    empty_root.mkdir()
    empty = run_document()
    empty["id"] = empty["payload"]["run_id"] = "run-empty"
    empty["payload"]["handoff"] = {}
    empty_html = _render(empty_root, empty)
    assert "No handoff recorded" in empty_html and "No handoff" in empty_html

    stale_root = wiki_root / "stale"
    stale_root.mkdir()
    stale = run_document()
    stale["id"] = stale["payload"]["run_id"] = "run-stale"
    stale["payload"]["handoff"]["recorded_at"] = "2020-01-01T00:00:00Z"
    stale_html = _render(stale_root, stale)
    assert "Handoff may be stale" in stale_html


def test_handoff_safe_external_artifact_and_unavailable_links(wiki_root):
    run = run_document()
    run["payload"]["handoff"]["artifacts"] = [
        "https://example.com/safe-report", "local/missing-report.png",
    ]
    html = _render(wiki_root, run)
    assert 'href="https://example.com/safe-report"' in html
    assert 'rel="noopener noreferrer"' in html
    assert "local/missing-report.png" in html
    assert "No safe viewer is available" in html


def test_agent_handoff_redacts_private_paths_and_artifact_refs(wiki_root, monkeypatch):
    from ai_wiki import web

    run = run_document()
    run["payload"]["handoff"]["changed_files"] = ["C:/private/source.py"]
    run["payload"]["handoff"]["artifacts"] = ["C:/private/artifact.png"]
    store = MissionStore(wiki_root)
    try:
        store.create(plan_document())
        store.create(run)
    finally:
        store.close()

    def helper():
        new_store = MissionStore(wiki_root)
        return new_store, MissionControlReader(
            new_store, SecurityPolicy(wiki_root), Principal("agent", frozenset({"agent"})),
        )

    monkeypatch.setattr(web, "_mission_reader", helper)
    web.app.config.update(TESTING=True)
    response = web.app.test_client().get(f"/missions/{run['id']}?revision=4&lang=en")
    html = response.get_data(as_text=True)
    assert "C:/private" not in html
    assert "Redacted by policy" in html
    assert "Viewing does not transfer ownership" in html


def test_handoff_korean_and_english_labels_and_css_limits(wiki_root):
    run = run_document()
    english = _render(wiki_root, run, lang="en")
    assert "Work handoff" in english and "Remaining work" in english
    # Reuse the same Mission root for the localized request.
    from ai_wiki.web import app
    korean = app.test_client().get(f"/missions/{run['id']}?revision=4&lang=ko").get_data(as_text=True)
    assert "작업 인계" in korean and "남은 작업" in korean
    css = open("src/ai_wiki/static/style.css", encoding="utf-8").read()
    assert "max-height: 300px" in css and "max-height: 220px" in css
