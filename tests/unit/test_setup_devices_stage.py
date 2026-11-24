"""First-run setup offers the companion devices, inside the one setup conversation.

It is the same walk-through the tool `ecosystem_setup` gives later (services/ecosystem_setup): every
device optional, the recommended one labelled and never chosen for him, and nothing paired, opened
or switched on by answering.
"""
import pytest

sc = pytest.importorskip("agent_friday.services.setup_chat")
copy = pytest.importorskip("agent_friday.services.setup_chat_copy")

from agent_friday.services import ecosystem as eco  # noqa: E402
from agent_friday.services import ecosystem_setup as es  # noqa: E402

LIVE = es.Live(readable=True, gateway_on=True, window_open=False, paired=frozenset(), signed_in=frozenset())


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """A fixed live state, and no record written outside the test."""
    monkeypatch.setattr(es, "live", lambda: LIVE)
    kept = []
    monkeypatch.setattr(sc.record, "append", lambda *a, **k: kept.append((a, k)))
    return kept


def _answer(st, value=None, text=""):
    transcript = []
    sc._on_devices(st, transcript, value, text)
    return transcript


def _friday(transcript):
    return [m["text"] for m in transcript if m["role"] == "friday"]


# ── It is part of the one conversation ───────────────────────────────────────

def test_the_stage_sits_after_the_household_question_and_on_the_rail():
    i = copy.STAGES.index
    assert i("household") + 1 == i("devices") < i("room_voice")
    assert "devices" in [s for _g, _l, ss in copy.STAGE_GROUPS for s in ss]
    assert sc._HANDLERS["devices"] is sc._on_devices


def test_the_household_answer_leads_to_the_devices_question():
    st, transcript = {"stage": "household"}, []
    sc._on_household(st, transcript, "no", "")
    assert st["stage"] == "devices"
    assert _friday(transcript)[-1] == es.menu(LIVE) + "\n" + copy.DEVICES_ASK


# ── Optional, labelled, never chosen for him ─────────────────────────────────

def test_the_chips_label_the_recommended_device_and_offer_a_way_out():
    chips = sc._prompt({"stage": "devices"})["chips"]
    assert [c["value"] for c in chips] == ["phone", "tablet", "tv", "none"]
    assert [c["label"] for c in chips] == ["Phone (recommended)", "Tablet", "TV", copy.DEVICES_NONE]
    assert set(copy.DEVICES_OFFERED) <= set(eco.APP_IDS)


def test_the_developer_preview_is_named_but_not_offered_as_a_chip():
    assert "watch" not in copy.DEVICES_OFFERED and eco.app("watch").status == "developer_preview"
    assert "- Watch: a developer preview" in es.menu(LIVE)


def test_the_step_has_no_recommended_answer_button():
    """"Use the recommended answer" would pick the phone for him; the label is all there is."""
    prompt = sc._prompt({"stage": "devices"})
    assert prompt["recommended"] is False
    assert copy.USE_RECOMMENDED not in [c["label"] for c in prompt["chips"]]


@pytest.mark.parametrize("value,text", [("none", ""), ("skip", ""), (None, "skip"), (None, "not now"),
                                        (None, "no thanks"), (None, "later")])
def test_saying_no_moves_on_and_says_nothing_was_changed(value, text, quiet):
    st = {"stage": "devices"}
    transcript = _answer(st, value, text)
    assert st["stage"] == "room_voice"
    assert copy.DEVICES_LATER in _friday(transcript)
    assert "Nothing was switched on or changed" in copy.DEVICES_LATER
    assert quiet[-1][0] == ("devices", "none", "you")


def test_words_that_name_no_choice_are_asked_again_not_guessed():
    st = {"stage": "devices"}
    with pytest.raises(sc.Refused):
        _answer(st, None, "the big one in the lounge")
    assert st["stage"] == "devices" and "devices_heard" not in st


# ── Choosing a device reads out its walk-through and stays on the step ───────

@pytest.mark.parametrize("value,text", [("phone", ""), (None, "phone"), (None, "my phone")])
def test_choosing_a_device_reads_the_same_walk_through_as_the_tool(value, text):
    st = {"stage": "devices"}
    said = _friday(_answer(st, value, text))
    assert said == [es.walk("phone", LIVE), copy.DEVICES_MORE]
    assert said[0] == es.tool_ecosystem_setup({"device": "phone"})
    assert st["stage"] == "devices" and st["devices_heard"] == ["phone"]


def test_after_one_device_the_way_out_reads_carry_on_and_records_what_was_heard(quiet):
    st = {"stage": "devices"}
    _answer(st, "tv")
    _answer(st, "phone")
    _answer(st, "tv")
    assert st["devices_heard"] == ["tv", "phone"]
    assert sc._prompt(st)["chips"][-1] == {"value": "none", "label": copy.DEVICES_DONE}
    transcript = _answer(st, "none")
    assert st["stage"] == "room_voice"
    assert copy.DEVICES_LATER not in _friday(transcript)
    assert quiet[-1][0] == ("devices", "tv, phone", "you")


def test_the_tv_choice_carries_the_kids_mode_rule():
    said = _friday(_answer({"stage": "devices"}, "tv"))[0]
    assert next(x for x in eco.app("tv").limits if "kids mode" in x.lower()) in said


# ── Answering changes nothing on the PC ──────────────────────────────────────

def test_no_answer_opens_pairing_or_switches_anything(monkeypatch):
    from agent_friday.services import device_pairing
    called = []
    for name in ("set_gateway", "open_pairing_window", "close_pairing_window", "add_device", "set_right",
                 "remove_device", "_save"):
        monkeypatch.setattr(device_pairing, name, lambda *a, _n=name, **k: called.append(_n))
    st = {"stage": "devices"}
    for value in ("phone", "tablet", "tv", "none"):
        _answer(st, value)
    assert called == []
    assert "pairing is opened and approved by you" in copy.DEVICES_MORE


def test_a_pasted_key_is_refused_like_on_every_other_step():
    with pytest.raises(sc.Refused):
        _answer({"stage": "devices"}, None, "sk-" + "ant-" + "a1B2c3D4e5F6g7H8i9J0kLmNoPqRsTuV")  # pragma: allowlist secret
