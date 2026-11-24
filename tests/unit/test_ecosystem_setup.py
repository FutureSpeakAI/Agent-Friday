"""The guided set-up of a companion app: where things stand and the one next thing to do.

Rules it has to keep (spec ECOSYSTEM_KNOWLEDGE_AND_SETUP_STORM_2026-11-22, part C and D):
every device is optional, a recommended one is labelled and never chosen for the owner, each walk says
what the device adds and what it can see, the advanced install is called advanced, nothing asks for
the six digits, and the walk-through only reads: it never opens pairing or switches anything on.
"""
from __future__ import annotations

import re

import pytest

from agent_friday.services import ecosystem as eco
from agent_friday.services import ecosystem_setup as es

NOTHING = es.Live(readable=True, gateway_on=False, window_open=False, paired=frozenset(), signed_in=frozenset())
GATEWAY_ON = es.Live(readable=True, gateway_on=True, window_open=False, paired=frozenset(), signed_in=frozenset())
WINDOW_OPEN = es.Live(readable=True, gateway_on=True, window_open=True, paired=frozenset(), signed_in=frozenset())
PHONE_PAIRED = es.Live(readable=True, gateway_on=True, window_open=False, paired=frozenset({"phone"}),
                       signed_in=frozenset())
PHONE_WORKING = es.Live(readable=True, gateway_on=True, window_open=False, paired=frozenset({"phone"}),
                        signed_in=frozenset({"phone"}))
UNREADABLE = es.Live(readable=False, gateway_on=False, window_open=False, paired=frozenset(), signed_in=frozenset())


def _states(app_id, live):
    return {s.id: s.state for s in es.steps(eco.app(app_id), live)}


# ── The menu: optional, labelled, never chosen for the owner ─────────────────

def test_the_menu_names_every_app_says_none_is_needed_and_labels_only_the_recommended_one():
    menu = es.menu(NOTHING)
    assert es.OPTIONAL in menu
    for a in eco.apps():
        assert f"- {a.noun[0].upper()}{a.noun[1:]}" in menu
    lines = [line for line in menu.splitlines() if "(recommended)" in line]
    assert len(lines) == len(es.RECOMMENDED) == 1 and lines[0].startswith("- Phone")
    assert set(es.RECOMMENDED) <= set(eco.APP_IDS)


def test_no_device_is_walked_through_until_one_is_named():
    """Recommended is a label. Asking with no device gets the menu, never the phone's steps."""
    for inp in ({}, {"device": ""}, {"device": None}, None, "phone", {"device": 7}):
        out = es.walk(inp.get("device") if isinstance(inp, dict) else None, NOTHING)
        assert out == es.menu(NOTHING)
        assert "Next:" not in out


def test_an_unknown_device_is_named_as_unknown():
    assert es.walk("fridge", NOTHING).startswith("I have no companion app called that.")


def test_the_developer_preview_is_not_offered_as_ready():
    menu = es.menu(NOTHING)
    watch_line = next(line for line in menu.splitlines() if line.startswith("- Watch"))
    assert "developer preview" in watch_line and "(recommended)" not in watch_line


# ── Each walk says what it adds and what it can see, first ───────────────────

@pytest.mark.parametrize("app_id", eco.APP_IDS)
def test_every_walk_opens_with_what_it_adds_and_what_it_can_see(app_id):
    a = eco.app(app_id)
    text = es.walk(app_id, NOTHING)
    head = text.split("Where things stand:")[0]
    for line in a.adds:
        assert line in head
    for line in eco.sees(a):
        assert line in head
    assert es.OPTIONAL_ONE.format(noun=a.noun) in head


def test_the_tv_walk_keeps_the_kids_mode_rule_and_never_tells_anyone_to_change_it():
    text = "\n".join(es.walk("tv", live) for live in (NOTHING, GATEWAY_ON, WINDOW_OPEN))
    rule = next(x for x in eco.app("tv").limits if "kids mode" in x.lower())
    assert rule in text
    assert not re.search(r"(?:turn|switch) (?:off|on) kids mode|(?:leave|exit|enter) kids mode", text, re.I)


# ── Where things stand follows the live state, and only one next step is given ─

def test_with_the_gateway_off_the_walk_stops_at_enable_and_says_why():
    states = _states("phone", NOTHING)
    assert states["enable"] == es.BLOCKED
    assert states["open"] == states["pair"] == states["verify"] == states["test"] == es.WAITING
    text = es.walk("phone", NOTHING)
    assert es.GATEWAY_OFF in text
    assert "six digits" not in text.split("Next:")[1], "steps past a blocked one are not given"


def test_with_the_gateway_on_the_next_step_is_opening_pairing_at_the_pc():
    states = _states("phone", GATEWAY_ON)
    assert states["enable"] == es.DONE and states["open"] == es.TODO
    nxt = es.walk("phone", GATEWAY_ON).split("Next:")[1]
    assert 'type "Friday, add a device"' in nxt
    assert "six digits" not in nxt


def test_with_pairing_open_the_next_step_is_the_device_and_the_approve_press():
    states = _states("phone", WINDOW_OPEN)
    assert states["open"] == es.DONE and states["pair"] == es.TODO
    nxt = es.walk("phone", WINDOW_OPEN).split("Next:")[1]
    assert "six digits" in nxt and "press Approve on this PC's screen" in nxt
    assert es.NEVER_THE_DIGITS in nxt


def test_once_paired_install_and_pairing_are_done_and_the_next_step_is_the_test():
    states = _states("phone", PHONE_PAIRED)
    assert [states[i] for i in ("install", "enable", "open", "pair", "verify")] == [es.DONE] * 5
    assert states["test"] == es.TODO
    assert "ask me something" in es.walk("phone", PHONE_PAIRED).split("Next:")[1]


def test_once_it_has_signed_in_everything_is_done_and_nothing_is_left():
    assert set(_states("phone", PHONE_WORKING).values()) == {es.DONE}
    text = es.walk("phone", PHONE_WORKING)
    assert "Next:" not in text and es.ALL_DONE.format(noun="phone") in text


def test_a_paired_phone_says_nothing_about_a_tablet():
    assert _states("tablet", PHONE_PAIRED)["pair"] != es.DONE


def test_install_cannot_be_seen_from_here_and_is_said_so():
    assert _states("phone", GATEWAY_ON)["install"] == es.CANNOT_TELL
    assert f'{es.INSTALL_UNSEEN.format(noun="phone")} {es.INSTALL_FIRST}' in es.walk("phone", GATEWAY_ON)
    assert es.INSTALL_UNSEEN.format(noun="phone") not in es.walk("phone", PHONE_PAIRED)


def test_an_unreadable_device_list_is_not_read_as_nothing_paired():
    text = es.walk("phone", UNREADABLE)
    assert es.LIVE_UNREADABLE in text
    assert "Next:" not in text and es.DONE not in _states("phone", UNREADABLE).values()


# ── The watch goes through the phone ─────────────────────────────────────────

def test_the_watch_walk_asks_for_the_phone_first_and_cannot_verify_itself():
    states = _states("watch", GATEWAY_ON)
    assert states["phone_first"] == es.TODO
    assert "Set up the phone first" in es.walk("watch", GATEWAY_ON).split("Next:")[1]
    states = _states("watch", PHONE_WORKING)
    assert states["phone_first"] == es.DONE and states["verify"] == es.CANNOT_TELL
    rest = es.walk("watch", PHONE_WORKING).split(es.REST_UNSEEN)[1]
    assert "open Agent Friday on the watch" in rest and es.WATCH_UNVERIFIED in rest and "Next:" not in rest
    assert "enable" not in states and "open" not in states, "a watch never pairs with this PC"
    assert es.walk("watch", PHONE_WORKING).startswith("The watch app is a developer preview")


# ── The advanced route is called what it is ──────────────────────────────────

@pytest.mark.parametrize("app_id", eco.APP_IDS)
def test_the_install_step_gives_the_advanced_notice_before_any_developer_step(app_id):
    text = es.step_detail(eco.app(app_id), "install", GATEWAY_ON)
    assert eco.ADVANCED_NOTICE in text and eco.ADVANCED_CLOSE in text
    assert text.index(eco.ADVANCED_NOTICE) < text.index("adb ")


def test_one_step_can_be_asked_for_by_name_and_an_unknown_one_lists_the_names():
    assert es.walk("phone", GATEWAY_ON, step="install") == es.step_detail(eco.app("phone"), "install", GATEWAY_ON)
    assert "install, enable, open, pair, verify, test" in es.walk("phone", GATEWAY_ON, step="dance")


# ── Safety: it only reads, and no code is asked for or shown ─────────────────

FORBIDDEN = (
    re.compile(r"(?:tell|give|say|read) (?:me|friday) (?:the|your) (?:pin|six digits|code)", re.I),
    re.compile(r"\b\d{6}\b"),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(r"scan (?:the|a) (?:qr|code)", re.I),
)


@pytest.mark.parametrize("live", [NOTHING, GATEWAY_ON, WINDOW_OPEN, PHONE_PAIRED, PHONE_WORKING, UNREADABLE])
def test_nothing_it_says_asks_for_a_code_shows_one_or_names_an_address(live):
    texts = [es.menu(live)] + [es.walk(a, live) for a in eco.APP_IDS]
    texts += [es.walk(a, live, step=s.id) for a in eco.APP_IDS for s in es.steps(eco.app(a), live)]
    for text in texts:
        for pattern in FORBIDDEN:
            assert not pattern.search(text), pattern.pattern


def test_reading_the_live_state_changes_nothing(monkeypatch):
    """The walk-through never opens pairing, switches the gateway or touches a device."""
    from agent_friday.services import device_awareness, device_pairing
    called = []
    for name in ("set_gateway", "open_pairing_window", "close_pairing_window", "add_device", "set_right",
                 "remove_device", "_save"):
        monkeypatch.setattr(device_pairing, name, lambda *a, _n=name, **k: called.append(_n))
    monkeypatch.setattr(device_awareness, "gather", lambda: ((), True))
    monkeypatch.setattr(device_pairing, "pairing_window", lambda: {"open": True, "seconds_left": 120})
    live = es.live()
    assert live == WINDOW_OPEN
    assert es.tool_ecosystem_setup({"device": "phone"}) == es.walk("phone", WINDOW_OPEN)
    assert called == []


def test_live_state_reads_paired_and_signed_in_kinds_from_the_device_list(monkeypatch):
    from agent_friday.services import device_awareness as da
    from agent_friday.services import device_pairing
    views = (da.DeviceView("phone", True, 100.0, 200.0, da.CONNECTED),
             da.DeviceView("tablet", True, 100.0, None, da.NEVER),
             da.DeviceView("tv", False, 100.0, 150.0, da.SEEN))
    monkeypatch.setattr(da, "gather", lambda: (views, True))
    monkeypatch.setattr(device_pairing, "pairing_window", lambda: {"open": False})
    live = es.live()
    assert live.paired == frozenset({"phone", "tablet", "tv"})
    assert live.signed_in == frozenset({"phone", "tv"})
    assert live.gateway_on and not live.window_open and live.readable


def test_a_failure_reading_the_live_state_is_unreadable_not_empty(monkeypatch):
    from agent_friday.services import device_awareness as da

    def boom():
        raise ValueError("the device list failed its signature")
    monkeypatch.setattr(da, "gather", boom)
    assert es.live() == UNREADABLE
    assert es.LIVE_UNREADABLE in es.tool_ecosystem_setup({"device": "phone"})


@pytest.mark.parametrize("inp", [None, "x", 3, [], {"device": {}}, {"device": "PHONE "}, {"device": "tv", "step": 9}])
def test_the_tool_answers_any_input_in_words(monkeypatch, inp):
    monkeypatch.setattr(es, "live", lambda: GATEWAY_ON)
    out = es.tool_ecosystem_setup(inp)
    assert isinstance(out, str) and out
    if isinstance(inp, dict) and inp.get("device") == "PHONE ":
        assert out == es.walk("phone", GATEWAY_ON)


def test_the_tool_is_registered_read_only_grouped_and_classed():
    import json
    from pathlib import Path

    import agent_friday.services.agent as agent
    from agent_friday.governance import action_gate
    assert agent.TOOL_RINGS["ecosystem_setup"] == 0
    assert agent.CLAUDE_TOOL_HANDLERS["ecosystem_setup"]({"device": "fridge"}).startswith("I have no companion app")
    spec = next(t for t in agent.CLAUDE_TOOLS if t["name"] == "ecosystem_setup")
    assert spec["input_schema"]["properties"]["device"]["enum"] == list(eco.APP_IDS)
    assert spec["input_schema"]["properties"]["step"]["enum"] == list(es.ALL_STEP_IDS)
    assert "ecosystem_setup" in action_gate.INTERNAL_TOOLS and action_gate.known("ecosystem_setup")
    groups = json.loads((Path(agent.__file__).resolve().parents[1] / "resources" / "tool_groups.json")
                        .read_text(encoding="utf-8"))
    assert "settings" in groups["ecosystem_setup"]


def test_the_prompt_points_at_the_setup_tool():
    assert "ecosystem_setup" in eco.prompt_block()
    assert len(eco.prompt_block()) <= eco.PROMPT_BLOCK_MAX_CHARS
