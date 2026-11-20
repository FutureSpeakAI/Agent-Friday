"""The deterministic A2UI composer (Liquid UI L1; roadmap section 41, slice A1).

Same blocks, same document, byte for byte; every composed document passes the profile; the
composer cannot be made to emit an approve/pay control, an unknown component or an unlisted verb.
Pure and offline."""
import json

import pytest

from agent_friday.services import a2ui_compose as comp
from agent_friday.services import a2ui_profile as prof
from agent_friday.services import a2ui_render as rend

SUBJECT = "run:lane-7"
VERBS = {
    "open_run": {"gate_class": "internal", "effects": [], "args": ["run_id"]},
    "pause_crew": {"gate_class": "internal", "effects": [], "args": []},
    "approval_decide": {"gate_class": "outward", "effects": ["decides_card"], "args": ["id"]},
    "publish_site": {"gate_class": "outward", "effects": ["publishes"], "args": []},
}


def ctx(**kw):
    base = dict(subject=SUBJECT, verbs=VERBS.get, model_authored=False)
    base.update(kw)
    return prof.Context(**base)


BLOCKS = [
    {"kind": "heading", "text": "Lane status"},
    {"kind": "status", "title": "Running", "children": [
        {"kind": "text", "text": "Two crews are working on the media run."},
        {"kind": "button", "label": "Open the run", "verb": "open_run", "context": {"run_id": "r1"}},
    ]},
    {"kind": "divider"},
    {"kind": "link", "label": "Back to lanes", "destination": "lanes"},
    {"kind": "approval_slot"},
]

GOLDEN = [{
    "version": "1.0",
    "createSurface": {
        "surfaceId": "view",
        "catalogId": "https://agentfriday.ai/a2ui/catalogs/core/v1",
        "sendDataModel": False,
        "components": [
            {"id": "root", "component": "column", "children": ["c1", "c2", "c5", "c6", "c7"]},
            {"id": "c1", "component": "text_block", "text": "Lane status", "style": "heading"},
            {"id": "c2", "component": "status_block", "title": "Running", "children": ["c3", "c4"]},
            {"id": "c3", "component": "text_block", "text": "Two crews are working on the media run.", "style": "body"},
            {"id": "c4", "component": "verb_button", "label": "Open the run", "verb": "open_run",
             "action": {"event": {"name": "open_run", "context": {"run_id": "r1"}}}},
            {"id": "c5", "component": "divider"},
            {"id": "c6", "component": "nav_link", "label": "Back to lanes", "destination": "lanes"},
            {"id": "c7", "component": "approval_slot", "filter": {"subject": SUBJECT}},
        ],
    },
}]


def test_the_same_blocks_always_give_the_same_document():
    a = comp.compose(SUBJECT, BLOCKS, ctx())
    b = comp.compose(SUBJECT, json.loads(json.dumps(BLOCKS)), ctx())
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_the_golden_stream_for_a_lane_status_view():
    # ids follow document order, nested blocks included: the status block is c2, its children c3 and c4
    assert comp.compose(SUBJECT, BLOCKS, ctx()) == GOLDEN


def test_a_composed_view_passes_the_profile_and_draws_only_the_shells_card_marker():
    msgs = comp.compose(SUBJECT, BLOCKS, ctx())
    r = rend.render(msgs, ctx(), pending_approvals=lambda subject, kind: ["a-1"] if subject == SUBJECT else [])
    assert r.ok, r.receipt
    assert r.html.count('data-shell-card="approval"') == 1
    assert "<a " not in r.html and "<input" not in r.html and "<form" not in r.html


@pytest.mark.parametrize("verb", ["approval_decide", "publish_site", "invented_verb"])
def test_the_composer_cannot_emit_a_button_for_a_banned_or_unknown_verb(verb):
    with pytest.raises(prof.ProfileError) as e:
        comp.compose(SUBJECT, [{"kind": "button", "label": "Open", "verb": verb}], ctx())
    assert e.value.rule in ("V9", "V10")


@pytest.mark.parametrize("label", ["Approve", "Pay now", "Allow", "Top up"])
def test_the_composer_cannot_emit_a_reserved_label(label):
    with pytest.raises(prof.ProfileError) as e:
        comp.compose(SUBJECT, [{"kind": "button", "label": label, "verb": "open_run"}], ctx())
    assert e.value.rule == "V11"


def test_unknown_blocks_and_malformed_blocks_are_refused():
    with pytest.raises(prof.ProfileError):
        comp.compose(SUBJECT, [{"kind": "modal"}], ctx())
    with pytest.raises(prof.ProfileError):
        comp.compose(SUBJECT, ["text"], ctx())


def test_a_url_in_composed_text_is_refused_and_a_link_destination_is_internal_only():
    with pytest.raises(prof.ProfileError) as e:
        comp.compose(SUBJECT, [{"kind": "text", "text": "see https://example.com/x"}], ctx())
    assert e.value.rule == "V8"
    with pytest.raises(prof.ProfileError) as e:
        comp.compose(SUBJECT, [{"kind": "link", "label": "Go", "destination": "https://example.com"}], ctx())
    assert e.value.rule == "V8"


def test_the_slot_is_always_for_this_views_subject_never_another():
    msgs = comp.compose("run:other", [{"kind": "approval_slot", "approval_kind": "pair_device"}], ctx(subject="run:other"))
    slot = msgs[0]["createSurface"]["components"][-1]
    assert slot["filter"] == {"subject": "run:other", "kind": "pair_device"}
    with pytest.raises(prof.ProfileError) as e:       # the validator's context disagrees: V12
        comp.compose("run:other", [{"kind": "approval_slot"}], ctx())
    assert e.value.rule == "V12"
