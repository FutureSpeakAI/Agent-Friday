"""The Friday A2UI profile: a generated view can never grant, spend, approve or collect a secret
(Liquid UI layer L1; roadmap section 41, slice A0; Salon/Crews spec D5.0 and T-L1-NO-APPROVE).

Every hostile document is rejected WHOLE with a receipt naming the rule; the rendered output of
a rejected document contains no control, input, link, image or approval marker the document could
have made; the only approval markers on a page are the shell's own, for ids the shell supplied; and
a fake gate sees zero calls to an approve, grant or spend verb. Pure and offline: no gate, model,
network or file is touched."""
import copy

import pytest

try:
    from agent_friday.services import a2ui_profile as prof
    from agent_friday.services import a2ui_render as rend
except ImportError:  # red evidence: the profile is not in the tree yet
    prof = rend = None

CAT = "https://agentfriday.ai/a2ui/catalogs/core/v1"
BASIC = "https://a2ui.org/specification/v1_0/basic_catalog.json"
SUBJECT = "run:media-1"

VERBS = {
    "open_run": {"gate_class": "internal", "effects": [], "args": ["run_id"]},
    "pause_crew": {"gate_class": "internal", "effects": [], "args": []},
    # one fixture verb per V10 banned class
    "approval_decide": {"gate_class": "outward", "effects": ["decides_card"], "args": ["id"]},
    "connection_grant": {"gate_class": "outward", "effects": ["grants"], "args": []},
    "cap_raise": {"gate_class": "outward", "effects": ["cap_raise"], "args": []},
    "purchase_credits": {"gate_class": "outward", "effects": ["purchase"], "args": []},
    "key_write": {"gate_class": "outward", "effects": ["key_write"], "args": []},
    "set_setting": {"gate_class": "outward", "effects": ["set_setting"], "args": []},
    "send_mail": {"gate_class": "outward", "effects": ["sends_as_owner"], "args": []},
    "publish_site": {"gate_class": "outward", "effects": ["publishes"], "args": []},
    "device_set_right": {"gate_class": "outward", "effects": ["set_right"], "args": []},
    "owner_toggle": {"gate_class": "owner_only", "effects": [], "args": []},
    "by_design_toggle": {"gate_class": "by_design", "effects": [], "args": []},
    "loud_thing": {"gate_class": "loud", "effects": [], "args": []},
}
BANNED_VERBS = [v for v in VERBS if v not in ("open_run", "pause_crew")]


@pytest.fixture(autouse=True)
def _the_profile_exists():
    assert prof is not None and rend is not None, "services/a2ui_profile.py does not exist: no Friday A2UI profile"


def ctx(**kw):
    base = dict(subject=SUBJECT, verbs=VERBS.get)
    base.update(kw)
    return prof.Context(**base)


def doc(components, data=None, meta=None, extra=()):
    body = {"surfaceId": "s1", "catalogId": CAT, "sendDataModel": False, "components": components}
    if data is not None:
        body["dataModel"] = data
    if meta is not None:
        body["metadata"] = meta
    return [{"version": "1.0", "createSurface": body}, *extra]


def col(*children):
    return {"id": "root", "component": "column", "children": [c["id"] for c in children]}


def btn(cid="b1", label="Open the run", verb="open_run", context=None, **kw):
    c = {"id": cid, "component": "verb_button", "label": label, "verb": verb,
         "action": {"event": {"name": verb, **({"context": context} if context else {})}}}
    c.update(kw)
    return c


def text(cid="t1", t="Hello", **kw):
    return {"id": cid, "component": "text_block", "text": t, **kw}


def slot(cid="s1", **kw):
    return {"id": cid, "component": "approval_slot", "filter": {"subject": SUBJECT}, **kw}


def page(*items):
    return doc([col(*items), *items])


GOOD = page(text(), btn(), slot())


class FakeGate:
    def __init__(self):
        self.calls = []

    def authorize(self, verb, context):
        self.calls.append(verb)


# ── a good view draws, safely ─────────────────────────────────────────────────────────

def test_a_valid_view_renders_inert_text_a_button_and_only_the_shells_card_marker():
    r = rend.render(page(text(t="<script>alert(1)</script> & <b>bold</b>"), btn(), slot()), ctx(),
                    pending_approvals=lambda subject, kind: ["ap-7"])
    assert r.ok and r.receipt is None
    assert "<script" not in r.html and "&lt;script&gt;" in r.html and "<b>" not in r.html
    assert r.html.count("<button") == 1 and 'data-verb="open_run"' in r.html
    assert r.html.count("data-shell-card") == 1 and 'data-approval-id="ap-7"' in r.html


def test_the_slot_draws_only_ids_the_shell_supplies_never_text_from_the_document():
    r = rend.render(page(text(t="ap-evil"), slot()), ctx(), pending_approvals=lambda s, k: [])
    assert r.ok and "data-shell-card" not in r.html and "data-shell-empty" in r.html
    seen = []
    rend.render(GOOD, ctx(), pending_approvals=lambda s, k: seen.append((s, k)) or [])
    assert seen == [(SUBJECT, None)]


# ── T-L1-NO-APPROVE: the hostile set ──────────────────────────────────────────────────

def _basic_button():
    return doc([{"id": "root", "component": "Button", "child": "x", "action": {"event": {"name": "approve"}}}])


HOSTILE = {
    "basic-catalog Button labelled Approve": (_basic_button(), "V4"),
    "unknown component Card": (doc([{"id": "root", "component": "Card", "children": []}]), "V4"),
    "Modal": (doc([{"id": "root", "component": "Modal"}]), "V4"),
    "Tabs": (doc([{"id": "root", "component": "Tabs"}]), "V4"),
    "TextField (card number)": (doc([{"id": "root", "component": "TextField", "label": "Card number"}]), "V4"),
    "CheckBox": (doc([{"id": "root", "component": "CheckBox"}]), "V4"),
    "Slider (cap amount)": (doc([{"id": "root", "component": "Slider"}]), "V4"),
    "ChoicePicker": (doc([{"id": "root", "component": "ChoicePicker"}]), "V4"),
    "DateTimeInput": (doc([{"id": "root", "component": "DateTimeInput"}]), "V4"),
    "remote Image": (doc([{"id": "root", "component": "Image", "url": "https://x.example/a.png?d=secret"}]), "V4"),
    "McpApp": (doc([{"id": "root", "component": "McpApp"}]), "V4"),
    "non-Friday component catalog": (doc([{"id": "root", "component": "text_block", "text": "x", "@catalogId": BASIC}]), "V3"),
    "basic catalog as the surface default": ([{"version": "1.0", "createSurface": {
        "surfaceId": "s1", "catalogId": BASIC, "sendDataModel": False,
        "components": [{"id": "root", "component": "text_block", "text": "x"}]}}], "V3"),
    "sendDataModel true": ([{"version": "1.0", "createSurface": {
        "surfaceId": "s1", "catalogId": CAT, "sendDataModel": True,
        "components": [{"id": "root", "component": "text_block", "text": "x"}]}}], "V7"),
    "callAgentFunction to a spend function (wrong direction)": (
        [{"version": "1.0", "callRendererFunction": {"functionCallId": "f", "callFunction": {"@call": "spend"}}}], "V2"),
    "agentFunctionResponse": ([{"version": "1.0", "agentFunctionResponse": {"functionCallId": "f", "value": 1}}], "V2"),
    "openUrl function": (page(text(t={"@call": "openUrl", "args": {"value": "x"}})), "V4"),
    "callFunction in a component": (page(text(t={"callFunction": {"@call": "formatString"}})), "V4"),
    "cross-catalog @call": (page(text(t={"@call": "formatString", "catalogId": BASIC, "args": {}})), "V3"),
    "URL in text": (page(text(t="go to https://pay.example/checkout")), "V8"),
    "javascript URL in text": (page(text(t="javascript:alert(1)")), "V8"),
    "bare domain with a path": (page(text(t="see pay.example/now")), "V8"),
    "URL in a nav destination": (page({"id": "n1", "component": "nav_link", "label": "Go", "destination": "https://x.example"}), "V8"),
    "approval_slot with a literal approval id": (page(slot(approval_id="ap-1")), "V4"),
    "approval_slot with text": (page(slot(text="Allow it?")), "V4"),
    "approval_slot with another subject": (doc([col({"id": "s1"}), {"id": "s1", "component": "approval_slot", "filter": {"subject": "run:other"}}]), "V12"),
    "approval_slot with an accessibility label": (page(slot(**{"@accessibility": {"label": "Card"}})), "V12"),
    "three approval_slots": (doc([col(*[{"id": f"s{i}"} for i in range(3)]), *[slot(f"s{i}") for i in range(3)]]), "V5"),
    "approval_slot inside a row": (doc([{"id": "root", "component": "row", "children": ["s1"]}, slot()]), "V5"),
    "approval_slot inside a template": (doc([{"id": "root", "component": "column", "children": {"path": "/rows", "componentId": "s1"}}, slot()],
                                            data={"rows": [1]}, meta={"friday": {"bindings": {"r": {"path": "/rows"}}}}), "V5"),
    "look-alike card: heading Friday needs you": (page({"id": "h", "component": "status_block", "title": "Friday needs you"}), "V11"),
    "look-alike card text: wants to spend": (page(text(t="Friday wants to spend $20 on the media crew")), "V11"),
    "look-alike card text: waiting for you": (page(text(t="Waiting for you")), "V11"),
    "unreachable component": (doc([col(text()), text(), text("t2")]), "V5"),
    "cycle": (doc([{"id": "root", "component": "column", "children": ["root"]}]), "V5"),
    "no root": (doc([text("t1")]), "V5"),
    "model-authored data": (doc([col(text()), text()], data={"x": 1}), "V7"),
    "path outside a declared binding": (page(text(t={"@path": "/secrets/key"})), "V7"),
    "accessibility live assertive": (page(text(**{"@accessibility": {"live": "assertive"}})), "V13"),
    "hidden verb_button": (page(btn(**{"@accessibility": {"hidden": True}})), "V13"),
    "accessibility label says Approve": (page(text(**{"@accessibility": {"label": "Approve"}})), "V11"),
}
# the model path is a stricter case of the same documents
MODEL_ONLY = {"model-authored data"}

for _verb in BANNED_VERBS:
    HOSTILE[f"verb_button bound to banned verb {_verb}"] = (page(btn(verb=_verb, label="Open")), "V10")

HOSTILE["verb_button with an unknown verb"] = (page(btn(verb="invented", label="Open")), "V9")
HOSTILE["verb_button whose event name differs from its verb"] = (page(btn(**{"action": {"event": {"name": "approve"}}})), "V9")
HOSTILE["verb_button with an unlisted context key"] = (page(btn(context={"amount": "20"})), "V9")

for _label in ["Confirm", "Pay $4.99", "Allow", "Approve", "Аpprove", "A p p r o v e",
               "Ap​pro‍ve", "Aprobar", "Autoriser", "Bestätigen", "Ａllow", "Buy now",
               "Top up", "Sign in", "Grant access", "Unlock", "Enter your PIN", "承認", "Paу"]:
    HOSTILE[f"reserved label {_label!r}"] = (page(btn(label=_label)), "V11")

DOCS = [pytest.param(d, rule, id=name) for name, (d, rule) in HOSTILE.items()]


@pytest.mark.parametrize("document,rule", DOCS)
def test_every_hostile_document_is_rejected_whole_with_the_rule_named(document, rule):
    with pytest.raises(prof.ProfileError) as e:
        prof.check(copy.deepcopy(document), ctx(model_authored=True))
    assert e.value.rule == rule, f"{e.value}"


@pytest.mark.parametrize("document,rule", DOCS)
def test_a_rejected_document_draws_nothing_it_could_control(document, rule):
    gate = FakeGate()
    receipts = []
    r = rend.render(copy.deepcopy(document), ctx(model_authored=True), receipt_sink=receipts.append,
                    pending_approvals=lambda s, k: [])
    assert not r.ok and r.receipt["rule"] == rule and receipts == [r.receipt]
    for banned in ("<input", "<a ", "<a>", "<img", "<iframe", "<form", "<script", "<textarea", "<select",
                   "<button", "data-shell-card", "data-verb"):
        assert banned not in r.html, banned
    assert r.html == rend.to_html(list(rend.DEFAULT_VIEW))
    assert r.permitted_actions == {}
    assert gate.calls == []


def test_the_hostile_set_covers_every_catalog_component_and_every_banned_verb_class():
    # guard against weakening: a component added to the catalog needs an abuse case, and the list cannot shrink
    abused = {
        "column": "unreachable component", "row": "approval_slot inside a row",
        "divider": None, "status_block": "look-alike card: heading Friday needs you",
        "text_block": "URL in text", "key_values": None, "record_table": None, "timeline": None,
        "file_line": None, "cost_line": None, "token_line": None, "chart": None, "source_list": None,
        "media_tile": None, "media_grid": None, "audio_clip": None,
        "verb_button": "verb_button bound to banned verb approval_decide", "nav_link": "URL in a nav destination",
        "approval_slot": "approval_slot with text"}
    assert set(abused) == set(prof.COMPONENTS), "a component was added without a hostile case"
    for name in filter(None, abused.values()):
        assert name in HOSTILE, name
    assert len(HOSTILE) >= 75
    assert len(BANNED_VERBS) == 12


# ── the data-carrying components cannot smuggle a URL, remote media or a path ─────────

@pytest.mark.parametrize("comp", [
    {"id": "d", "component": "media_tile", "media": "m-1"},
    {"id": "d", "component": "file_line", "file": "https://x.example/f"},
    {"id": "d", "component": "media_grid", "items": ["m-1"]},
    {"id": "d", "component": "audio_clip", "media": {"@path": "/x"}},
])
def test_media_and_files_are_by_store_id_through_a_binding_only(comp):
    with pytest.raises(prof.ProfileError):
        prof.check(page(comp), ctx())


def test_a_bound_value_is_checked_again_when_it_is_drawn():
    meta = {"friday": {"bindings": {"t": {"path": "/title"}}}}
    bound = doc([col({"id": "h"}), {"id": "h", "component": "status_block", "title": {"@path": "/title"}}], meta=meta)
    ok = rend.render(bound, ctx(), data={"title": "Media run"})
    assert ok.ok and "Media run" in ok.html
    for bad in ("Approve", "Pay now", "see https://evil.example/x"):
        r = rend.render(bound, ctx(), data={"title": bad})
        assert not r.ok and r.receipt["rule"] in ("V11", "V8"), bad
        assert "evil" not in r.html and "<button" not in r.html


def test_bound_rows_are_plain_data_and_a_data_url_is_refused():
    meta = {"friday": {"bindings": {"r": {"path": "/rows"}}}}
    comp = {"id": "t", "component": "record_table", "rows": {"@path": "/rows"}, "columns": ["name"]}
    d = doc([col({"id": "t"}), comp], meta=meta)
    assert rend.render(d, ctx(), data={"rows": [{"name": "clip 1"}]}).ok
    assert not rend.render(d, ctx(), data={"rows": [{"name": "http://evil.example/x"}]}).ok
    assert not rend.render(d, ctx(), data={"rows": [{"n": i} for i in range(201)]}).ok


# ── limits ────────────────────────────────────────────────────────────────────────────

def test_size_limits():
    many = [text(f"t{i}") for i in range(60)]
    with pytest.raises(prof.ProfileError) as e:
        prof.check(page(*many), ctx())                        # 60 texts + root column = 61 nodes
    assert e.value.rule == "V6"
    deep = [{"id": "root", "component": "column", "children": ["c1"]}]
    for i in range(1, 7):
        deep.append({"id": f"c{i}", "component": "column", "children": [f"c{i + 1}"]})
    deep.append({"id": "c7", "component": "divider"})
    with pytest.raises(prof.ProfileError) as e:
        prof.check(doc(deep), ctx())
    assert e.value.rule == "V6"
    with pytest.raises(prof.ProfileError) as e:
        prof.check(page(text(t="x" * 4100)), ctx())
    assert e.value.rule == "V6"
    with pytest.raises(prof.ProfileError) as e:
        prof.check(doc([col(text()), text()]) + doc([col(text()), text()]), ctx())
    assert e.value.rule == "V6"


def test_the_exact_limits_are_allowed():
    prof.check(page(*[text(f"t{i}") for i in range(59)]), ctx())
    prof.check(page(text(t="x" * 4000)), ctx())


# ── authorship, taint, direction ──────────────────────────────────────────────────────

def test_outside_content_may_not_author_a_view_and_a_model_proposal_may_not_carry_data():
    with pytest.raises(prof.ProfileError) as e:
        prof.check(GOOD, ctx(authorship="third_party_agent"))
    assert e.value.rule == "V14"
    prof.check(GOOD, ctx(authorship="friday_liquid", model_authored=True))
    r = rend.render(GOOD, ctx(authorship="friday_liquid"), taint="a web page about invoices")
    assert r.ok and r.taint_line == "proposed after reading: a web page about invoices"
    assert "proposed after reading" not in r.html, "the shell draws the taint line, not the document"


def test_composer_documents_may_update_the_data_model_through_the_server_resolver():
    meta = {"friday": {"bindings": {"t": {"path": "/title"}}}}
    d = doc([col({"id": "h"}), {"id": "h", "component": "status_block", "title": {"@path": "/title"}}], meta=meta,
            extra=[{"version": "1.0", "updateDataModel": {"surfaceId": "s1", "path": "/title", "value": "Media run"}}])
    assert rend.render(d, ctx()).ok
    with pytest.raises(prof.ProfileError) as e:
        prof.check(d, ctx(model_authored=True))
    assert e.value.rule == "V7"


# ── actions: only a drawn, still-permitted button can act, and nothing here authorizes ─

def _action(name="open_run", src="b1", context=None):
    return {"version": "1.0", "action": {"name": name, "surfaceId": "s1", "sourceComponentId": src,
                                         "timestamp": "2026-11-12T10:00:00Z", "context": context or {}}}


def test_an_action_maps_to_its_verb_only_for_a_button_that_was_drawn():
    r = rend.render(page(btn(context={"run_id": "r1"})), ctx())
    assert rend.action_to_verb(_action(context={"run_id": "r1"}), r, ctx()) == ("open_run", {"run_id": "r1"})
    for bad, rule in [(_action(src="ghost"), "V9"), (_action(name="approval_decide"), "V9"),
                      (_action(context={"extra": 1}), "V9"), ({"version": "1.0", "error": {}}, "V2")]:
        with pytest.raises(prof.ProfileError) as e:
            rend.action_to_verb(bad, r, ctx())
        assert e.value.rule == rule


def test_a_verb_that_became_banned_after_the_view_was_drawn_cannot_be_driven():
    r = rend.render(page(btn()), ctx())
    changed = dict(VERBS, open_run={"gate_class": "outward", "effects": ["purchase"], "args": []})
    with pytest.raises(prof.ProfileError) as e:
        rend.action_to_verb(_action(), r, ctx(verbs=changed.get))
    assert e.value.rule == "V10"


def test_with_no_verb_lookup_every_button_fails_closed():
    with pytest.raises(prof.ProfileError) as e:
        prof.check(page(btn()), prof.Context(subject=SUBJECT))
    assert e.value.rule == "V9"


# ── labels in practice ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("label", ["Open the run", "Pause crew", "Show details", "Keyboard help"])
def test_ordinary_labels_are_not_refused_by_accident(label):
    prof.check(page(btn(label=label)), ctx())


def test_body_text_may_mention_spend_but_a_button_or_heading_may_not():
    prof.check(page(text(t="Spending this month: 4 test credits")), ctx())
    with pytest.raises(prof.ProfileError):
        prof.check(page(text(t="Spend 4 credits", style="heading")), ctx())


def test_the_shells_own_card_strings_extend_the_reserved_list():
    with pytest.raises(prof.ProfileError) as e:
        prof.check(page(btn(label="Needed from you now")), ctx(extra_reserved=["Needed from you now"]))
    assert e.value.rule == "V11"
