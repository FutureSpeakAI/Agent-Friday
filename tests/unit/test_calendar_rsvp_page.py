"""An invitation shows the owner's reply and three buttons; a reply goes through a confirmation that says
the organiser will hear it. Read from the page sources; the click path is tests/app/specs/calendar_rsvp.spec.ts."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "static" / "calendar_views.js").read_text(encoding="utf-8")


@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_the_day_view_offers_a_reply_and_one_layer_confirms_it(page):
    src = (ROOT / page).read_text(encoding="utf-8")
    body = src[src.index("function CalendarWS("):]
    body = body[:body.index("// ═══ MEETINGS")] if "// ═══ MEETINGS" in body else body[:40000]
    assert "CalendarViews.RsvpButtons" in body and "CalendarViews.RsvpLayer" in body


def test_a_reply_button_only_proposes_and_the_confirmation_sends():
    buttons = JS[JS.index("function RsvpButtons("):JS.index("function RsvpLayer(")]
    assert "api(" not in buttons and "fetch(" not in buttons
    assert "ev.organizer_self" in buttons, "no reply buttons on an event she organises"
    layer = JS[JS.index("function RsvpLayer("):]
    layer = layer[:layer.index("// ── styles")]
    assert "confirmed: true" in layer and "organiserLine(pending.ev)" in layer


def test_the_confirmation_names_the_organiser_who_hears_the_reply():
    line = JS[JS.index("function organiserLine("):JS.index("function RsvpLayer(")]
    assert "ev.organizer_name" in line and "ev.organizer_email" in line
    assert "'Google will let the organiser, ' + named + ', know.'" in line
    assert "'Google will let the organiser know.'" in line, "an organiser Google does not name is still said"


def test_the_reply_is_by_design_a_click_in_the_laya_contract():
    import json
    m = json.loads((ROOT / "src/agent_friday/resources/laya_manifests/calendar.json").read_text(encoding="utf-8"))
    entry = [n for n in m["not_for_laya"] if n["action"] == "calendar.rsvp"][0]
    assert entry["kind"] == "by_design" and "organiser" in entry["reason"]
