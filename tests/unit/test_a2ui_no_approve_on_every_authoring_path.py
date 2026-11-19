"""T-L1-NO-APPROVE on every path that may author a view (NS-41.3-3; Salon/Crews spec D5.0).

test_a2ui_profile.py runs the 75 hostile documents as a MODEL-authored proposal (the strictest case). A view may also
come from Friday's composer or from the friday-liquid fence in Friday's own turn. This file runs the same shared
fixture (tests/fixtures/a2ui/profile_cases_v1.json) on both of those authorships, and runs hostile BLOCKS through the
composer itself, so no path that can put a view on screen can render an approve, pay, allow or spend control or
reach a gate verb. Pure and offline: no gate, model, network or file beyond the fixture is touched.

The limit, stated plainly: there is no code yet that cuts a friday-liquid fence out of a model turn, so the
`friday_liquid` authorship is exercised by handing the validator and renderer the document directly. When the fence
reader exists it needs its own test that its output goes through the same `check`; the shared fixture is ready for it.
"""
import copy
import json
from pathlib import Path

import pytest

from agent_friday.services import a2ui_compose as comp
from agent_friday.services import a2ui_profile as prof
from agent_friday.services import a2ui_render as rend

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "a2ui" / "profile_cases_v1.json")
                     .read_text(encoding="utf-8"))
VERBS = FIXTURE["verbs"]
SUBJECT = FIXTURE["subject"]
# a composer or fence document is Friday's own, so it may carry a data model; the model-only rule does not apply
MODEL_ONLY = {"model-authored data"}
CASES = [pytest.param(case["document"], case["rule"], id=case["name"])
         for case in FIXTURE["hostile"] if case["name"] not in MODEL_ONLY]
AUTHORSHIPS = ("composer", "friday_liquid")
MARKUP_THE_DOCUMENT_MUST_NOT_CONTROL = ("<input", "<a ", "<a>", "<img", "<iframe", "<form", "<script", "<textarea",
                                       "<select", "<button", "data-shell-card", "data-verb")


def ctx(authorship, **kw):
    return prof.Context(subject=SUBJECT, verbs=VERBS.get, authorship=authorship, **kw)


class Gate:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


@pytest.mark.parametrize("authorship", AUTHORSHIPS)
@pytest.mark.parametrize("document,rule", CASES)
def test_every_hostile_document_is_rejected_whole_on_the_composer_and_the_fence_path_too(document, rule, authorship):
    with pytest.raises(prof.ProfileError) as e:
        prof.check(copy.deepcopy(document), ctx(authorship))
    assert e.value.rule == rule, f"{authorship}: {e.value}"


@pytest.mark.parametrize("authorship", AUTHORSHIPS)
@pytest.mark.parametrize("document,rule", CASES)
def test_a_rejected_document_draws_nothing_it_could_control_whoever_authored_it(document, rule, authorship):
    gate, receipts = Gate(), []
    r = rend.render(copy.deepcopy(document), ctx(authorship), receipt_sink=receipts.append,
                    pending_approvals=lambda s, k: [])
    assert not r.ok and r.receipt["rule"] == rule and receipts == [r.receipt]
    for banned in MARKUP_THE_DOCUMENT_MUST_NOT_CONTROL:
        assert banned not in r.html, banned
    assert r.permitted_actions == {}
    assert gate.calls == []


def test_the_fixture_is_the_full_hostile_set_so_this_file_cannot_quietly_run_fewer_documents():
    assert len(FIXTURE["hostile"]) >= 75
    assert len(CASES) >= len(FIXTURE["hostile"]) - len(MODEL_ONLY)
    rules = {case["rule"] for case in FIXTURE["hostile"]}
    assert {"V4", "V5", "V7", "V9", "V10", "V11", "V13"} <= rules


BANNED_VERBS = [v for v, m in VERBS.items() if m["gate_class"] not in ("internal",)]
RESERVED_LABELS = ["Approve", "Allow", "Confirm", "Pay $4.99", "Buy now", "Top up", "Grant access", "Sign in"]


@pytest.mark.parametrize("verb", BANNED_VERBS)
def test_the_composer_refuses_a_button_for_every_verb_that_is_not_internal(verb):
    with pytest.raises(prof.ProfileError) as e:
        comp.compose(SUBJECT, [{"kind": "button", "label": "Open", "verb": verb}], ctx("composer"))
    assert e.value.rule == "V10"


@pytest.mark.parametrize("label", RESERVED_LABELS)
def test_the_composer_refuses_every_reserved_label_on_a_button_a_heading_and_a_card_title(label):
    for block in ({"kind": "button", "label": label, "verb": "open_run"},
                  {"kind": "heading", "text": label},
                  {"kind": "status", "title": label, "children": []}):
        with pytest.raises(prof.ProfileError) as e:
            comp.compose(SUBJECT, [block], ctx("composer"))
        assert e.value.rule == "V11", (label, block["kind"])


def test_a_composed_view_draws_only_the_shells_own_card_marker_for_ids_the_shell_supplied():
    messages = comp.compose(SUBJECT, [
        {"kind": "heading", "text": "Media crew"},
        {"kind": "button", "label": "Open the run", "verb": "open_run"},
        {"kind": "approval_slot"},
    ], ctx("composer"))
    r = rend.render(messages, ctx("composer"), receipt_sink=lambda receipt: None,
                    pending_approvals=lambda subject, kind: ["card-1"] if subject == SUBJECT else [])
    assert r.ok
    assert r.html.count("data-shell-card") == 1 and "card-1" in r.html
    assert {verb for verb, _keys in r.permitted_actions.values()} == {"open_run"}


def test_a_slot_for_another_subject_is_refused_on_both_authorships():
    for authorship in AUTHORSHIPS:
        slot = [{"version": "1.0", "createSurface": {
            "surfaceId": "s1", "catalogId": prof.CATALOG_ID, "sendDataModel": False,
            "components": [{"id": "root", "component": "column", "children": ["a"]},
                           {"id": "a", "component": "approval_slot", "filter": {"subject": "run:someone-else"}}]}}]
        with pytest.raises(prof.ProfileError) as e:
            prof.check(slot, ctx(authorship))
        assert e.value.rule == "V12", authorship


def test_outside_content_may_not_author_a_view_at_all():
    document = copy.deepcopy(FIXTURE["good"][0])
    for authorship in ("peer", "model", "web", "", None):
        with pytest.raises(prof.ProfileError) as e:
            prof.check(copy.deepcopy(document), ctx(authorship))
        assert e.value.rule == "V14", authorship
    prof.check(copy.deepcopy(document), ctx("composer"))
    prof.check(copy.deepcopy(document), ctx("friday_liquid"))
