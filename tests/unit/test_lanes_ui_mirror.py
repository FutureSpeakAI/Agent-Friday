"""NA-3: the four places in both UI files (index.html and ui_parts/app.html), and the routes behind them.

Reads the two pages and the route module as text; starts no browser and no server. The rendered look is
the Playwright spec tests/lanes_activity.spec.ts (owed a browser run).
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PAGES = ("index.html", "ui_parts/app.html")
START = "// ── The four places behind the chip: Needs you, Activity, Updates, Receipts & traces ──"
END = "function LocalAiQueueChip() {"


def _text(page):
    return (ROOT / page).read_text(encoding="utf-8").replace("\r\n", "\n")


def _block(page):
    t = _text(page)
    return t[t.index(START):t.index(END)]


def test_both_pages_carry_the_same_lane_block_and_the_same_extended_chip():
    assert _block(PAGES[0]) == _block(PAGES[1])
    chip = []
    for p in PAGES:
        t = _text(p)
        chip.append(t[t.index(END):t.index("// Settings > Advanced: how long the computer must be idle")])
    assert chip[0] == chip[1]
    for needle in ("laneChipContent(lane,", "'/api/lanes/summary'", "if (!jobs.length && !laneAny) return null;",
                   '"data-testid": "local-ai-queue-chip"', "React.createElement(LaneBody, {", "'/api/local-queue/'"):
        assert needle in chip[0], needle


@pytest.mark.parametrize("page", PAGES)
def test_the_chip_is_extended_not_replaced_and_there_is_one_of_it(page):
    t = _text(page)
    assert len(re.findall(r"data-testid[\":= ]+local-ai-queue-chip", t)) == 1
    assert "lane-chip" not in t, "no second indicator"
    assert t.count("function LocalAiQueueChip()") == 1


@pytest.mark.parametrize("page", PAGES)
def test_the_four_places_are_there_with_their_rules(page):
    b = _block(page)
    for needle in ("['needs', 'Needs you']", "['activity', 'Activity']", "['updates', 'Updates']",
                   "['receipts', 'Receipts & traces']", "role: 'tablist'", "role: 'tab'", "role: 'tabpanel'",
                   "'ArrowRight'", "'ArrowLeft'",
                   "'/api/lanes/needs-you'", "'/api/lanes/activity'", "'/api/lanes/updates?today=1'",
                   "'/api/lanes/receipts?limit=30'", "'/api/lanes/why/'", "'/api/lanes/quiet'",
                   "'/api/approvals/batches/'", "approval_ids: b.approval_ids", "decided_by: 'owner:ui'",
                   "Approve these ", "Decline these ", "Review each", "'Quiet until morning'", "'End quiet'",
                   "Nothing needs you.", "No new updates today.", "Nothing is running and nothing is waiting.",
                   "window.fridayOpenTrace(", "There is nothing to fix from here.",
                   "!each && h('div', { className: 'place-readonly', 'data-testid': 'lane-batch-items' }, "
                   "b.items.map(it => h(ApprovalCardBody, { key: it.approval_id, a: it,"):
        assert needle in b, needle


@pytest.mark.parametrize("page", PAGES)
def test_stop_is_drawn_only_when_the_server_says_the_job_is_stoppable(page):
    b = _block(page)
    assert "t.stoppable && t.stop ?" in b and "t.stop.url" in b and "t.stop.method" in b
    assert "DELETE" not in b, "Stop never deletes a record"
    assert "t.trace_id ?" in b, "a Trace button only for a task that has a trace"
    assert "model chosen when it starts" in b, "a queued task says why it names no model yet"


@pytest.mark.parametrize("page", PAGES)
def test_a_card_judged_quiet_does_not_pop_the_approval_window_open(page):
    t = _text(page)
    assert "window.__fridayQuietApprovals = new Set()" in t
    assert "d.interrupt === false" in t and "(d.quiet || []).forEach" in t
    assert "!fridayApprovalQuiet(a.approval_id)" in t
    assert "const folded = later || !peek && items.every(a => fridayApprovalQuiet(a.approval_id));" in t
    assert "if (folded) return " in t and "setPeek(true)" in t
    assert "function fridayApprovalQuiet(id)" in _block(page)


@pytest.mark.parametrize("page", PAGES)
def test_settings_offer_the_choices_with_recommended_labels_and_nothing_decided_for_the_owner(page):
    b, t = _block(page), _text(page)
    assert "[6, '6 an hour (Recommended)']" in b and "[0, 'No limit']" in b and "[3, '3 an hour']" in b
    assert "22:00 to 07:00 (Recommended)" in b
    for key in ("interruption_budget_per_hour", "quiet_hours_enabled", "quiet_hours_start", "quiet_hours_end",
                "honour_focus_assist"):
        assert "save({" in b and key in b, key
    assert "value: s && s.honour_focus_assist ? 'on' : 'off'" in b, "Focus Assist reads off until chosen"
    assert "React.createElement(SettingsInterruptions, {" in t
    assert "React.createElement(QuietHoursOffer," in t or "<QuietHoursOffer" in t, "quiet hours are offered in setup"


def test_every_setting_the_page_writes_has_a_default():
    from agent_friday.core import DEFAULT_SETTINGS
    for key in ("interruption_budget_per_hour", "quiet_hours_enabled", "quiet_hours_start", "quiet_hours_end",
                "quiet_until_ts", "honour_focus_assist"):
        assert key in DEFAULT_SETTINGS, key
    assert DEFAULT_SETTINGS["interruption_budget_per_hour"] == 6
    assert DEFAULT_SETTINGS["quiet_hours_enabled"] is False and DEFAULT_SETTINGS["honour_focus_assist"] is False


def test_every_lane_call_in_the_page_is_a_route_that_exists():
    from agent_friday.routes import lanes
    rules = {r for r in (rule for rule in _rules(lanes))}
    for page in PAGES:
        for url in set(re.findall(r"'(/api/(?:lanes|approvals/batches)[^'?]*)", _block(page))):
            assert any(_matches(url, r) for r in rules), (page, url)


def _rules(mod):
    from flask import Flask
    app = Flask("lanes_test")
    app.register_blueprint(mod.lanes_bp)
    return [r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/api/")]


def _matches(url, rule):
    """The page names a route whole ('/api/lanes/needs-you') or by its prefix ('/api/lanes/why/')."""
    return rule == url or rule.startswith(url)


@pytest.mark.parametrize("page", PAGES)
def test_the_lane_styles_use_tokens_amber_only_for_needs_you_and_nothing_moves(page):
    css = "index.html" if page == "index.html" else "ui_parts/styles_and_scene.html"
    lines = [l for l in _text(css).splitlines() if re.match(r"\s*\.place-", l)]
    assert lines, "the lane styles are in " + css
    for l in lines:
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(|animation|transition", l), l
    amber = [l.split("{")[0].strip() for l in lines if "--fr-warn" in l]
    assert amber == [".place-badge", ".place-tab.needs .place-count"], amber
    assert any(":focus-visible" in l and "outline: 2px solid var(--fr-cyan)" in l for l in lines), \
        "every control shows the focus ring (a token that is not defined would draw none)"
    assert any(l.strip().startswith('.place-readonly [data-testid="approval-card"] > div:last-child')
               and "display: none" in l for l in lines), "a batch body shows the card, not its single buttons"


@pytest.mark.parametrize("page", PAGES)
def test_the_classes_do_not_collide_with_the_mail_lanes_view(page):
    """The Messages lanes view owns .lane-tab, .lane-tabs, .lane-count and .lane-dot; these classes are place-*."""
    b = _block(page)
    assert not re.findall(r"className: '[^']*lane-", b)
    for cls in ("place-tabs", "place-tab", "place-count", "place-badge", "place-dot", "place-card"):
        assert cls in b, cls


@pytest.mark.parametrize("page", PAGES)
def test_no_string_in_the_lane_block_names_her_by_hand(page):
    strings = re.findall(r"'([^'\n]*)'", _block(page))
    assert not [s for s in strings if re.search(r"(?<!Agent )\bFriday\b", s)]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("page", PAGES)
def test_the_lane_block_and_chip_parse(tmp_path, page):
    t = _text(page)
    chunk = "const {useState,useRef,useEffect}=React;\n" + t[t.index("// ── The Local AI queue chip (top bar) ──"):
                                                          t.index("function StandaloneApprovals(")]
    f = tmp_path / "chunk.js"
    f.write_text(chunk, encoding="utf-8")
    r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[:500]
