from __future__ import annotations

import re
from html.parser import HTMLParser

from ai_wiki.missions import MissionStore
from tests.mission_control_fixtures import plan_document, run_document


class OutlineParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.headings: list[int] = []
        self.landmarks: list[str] = []
        self.details = 0
        self.summaries = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "a" and values.get("href", "").startswith("#"):
            self.links.append(values["href"][1:])
        if tag in {"main", "nav", "article", "section", "aside"}:
            self.landmarks.append(tag)
        if re.fullmatch(r"h[1-6]", tag):
            self.headings.append(int(tag[1]))
        if tag == "details":
            self.details += 1
        if tag == "summary":
            self.summaries += 1


def _pages(wiki_root):
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
    client = app.test_client()
    return (
        client.get("/missions?lang=en").get_data(as_text=True),
        client.get(f"/missions/{run['id']}?revision=4&lang=en").get_data(as_text=True),
    )


def test_skip_links_landmarks_headings_and_anchor_targets(wiki_root):
    overview, detail = _pages(wiki_root)
    for html, skip_target in (
        (overview, "mission-overview-main"), (detail, "mission-detail-main"),
    ):
        parser = OutlineParser()
        parser.feed(html)
        assert f'href="#{skip_target}"' in html
        assert skip_target in parser.ids
        assert {"main", "nav", "section"}.issubset(parser.landmarks)
        assert parser.headings[0] == 1
        assert all(current <= previous + 1 for previous, current in zip(parser.headings, parser.headings[1:]))
        assert all(target in parser.ids for target in parser.links)


def test_mobile_task_accordions_and_evidence_drawers_are_named(wiki_root):
    _, detail = _pages(wiki_root)
    parser = OutlineParser()
    parser.feed(detail)
    # Three task disclosures, three evidence drawers, and a collapsed audit ledger.
    assert parser.details == parser.summaries == 7
    assert detail.count("Expand or collapse task content") == 3
    assert 'aria-labelledby="evidence-title-E-file"' in detail
    assert "Full audit ledger" in detail


def test_statuses_have_visible_text_and_proof_rail_is_semantic(wiki_root):
    overview, detail = _pages(wiki_root)
    combined = (overview + detail).lower()
    for label in ("running", "blocked", "in review", "completed"):
        assert label in combined
    assert detail.count('class="mission-proof-rail"') == 3
    assert 'data-coverage="covered"' in detail
    assert 'data-coverage="missing"' in detail
    assert "evidence items" in detail


def _relative_luminance(hex_color: str) -> float:
    channels = [int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first: str, second: str) -> float:
    high, low = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_mission_palette_meets_aa_on_paper_and_focus_is_visible():
    paper = "#fbfcfb"
    for color in ("#18201e", "#18785d", "#a35d13", "#b83232", "#64706c"):
        assert _contrast(color, paper) >= 4.5
    css = open("src/ai_wiki/static/style.css", encoding="utf-8").read()
    assert ".mission-control :focus-visible" in css
    assert "outline: 3px solid" in css
    assert ".mission-skip-link:focus" in css


def test_responsive_touch_zoom_reduced_motion_and_forced_color_contracts():
    css = open("src/ai_wiki/static/style.css", encoding="utf-8").read()
    for breakpoint in ("980px", "800px", "760px", "680px", "620px", "520px", "440px", "400px"):
        assert breakpoint in css
    assert "min-height: 44px" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "transition: none !important" in css
    assert "@media (forced-colors: active)" in css
    assert "grid-template-columns: 1fr" in css
