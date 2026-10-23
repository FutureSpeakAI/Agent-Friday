"""Home shows 'What I see so far' (static/friday_experience.js): the seven fields and seven marks.

Read as text: the component exists, Home renders it, and marks go to the mark route through the
page's own fetch (which carries the desk proof). On the old tree the component is missing.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "static" / "friday_experience.js").read_text(encoding="utf-8")


def _component():
    m = re.search(r"function SeeSoFar\(\{ api, onNavigate \}\) \{.*?\n  \}\n", JS, re.S)
    assert m, "there is no 'What I see so far' on Home (SeeSoFar in static/friday_experience.js)"
    return m.group(0)


def test_home_renders_it_above_the_cards():
    _component()
    day = JS[JS.index("function dayView()"):JS.index("function formView()")]
    assert day.index("h(SeeSoFar,") < day.index("h(HomeCards,")


def test_it_shows_the_seven_fields():
    c = _component()
    assert "item.observation" in c and "SEE_SO_FAR_FIELDS.map" in c
    fields = re.search(r"const SEE_SO_FAR_FIELDS = (\[.*?\]);\n", JS).group(1)
    for key in ("'why'", "'source'", "'freshness'", "'confidence'", "'related'", "'next_step'"):
        assert key in fields, key


def test_marks_go_through_the_pages_own_fetch_to_the_mark_route():
    c = _component()
    assert "api('/api/first-synthesis/mark'" in c and "method: 'POST'" in c
    assert "fetch(" not in c
    assert "api('/api/first-synthesis'" in c


def test_a_mark_sends_the_text_it_showed_and_offers_only_the_items_marks():
    c = _component()
    assert "observation: item.observation" in c
    assert "item.marks ||" in c
    assert "item.remember_text" in c                 # the line Remember would save is shown first


def test_the_onboarding_checks_are_answered_by_the_owners_press():
    m = re.search(r"function OnboardingChecks\(\{ api \}\) \{.*?\n  \}\n", JS, re.S)
    assert m, "Home does not show the section 9.6 checks (OnboardingChecks)"
    c = m.group(0)
    assert "api('/api/onboarding/status'" in c and "api('/api/onboarding/answer'" in c
    assert "answer: 'saw_where_it_ran', seq: data.receipt.seq" in c and "answer: 'skip_goal'" in c
    day = JS[JS.index("function dayView()"):JS.index("function formView()")]
    assert "h(OnboardingChecks, { api })" in day
