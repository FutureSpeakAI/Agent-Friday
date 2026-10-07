"""The hologram window, by voice (services/hologram_tools.py): one tool,
hologram_window, declared once in the text registry and shared into voice.
A change is persisted through the settings path first and then pushed to the
open page; calibration is pushed and answered by the page, since only the page
knows how wide the face is."""
import pytest

from agent_friday.services import hologram_tools as ht


@pytest.fixture
def store(monkeypatch):
    """A fake settings store and a fake desktop page."""
    state = {"tracking": dict(ht._defaults()), "pushed": [], "page": True,
             "ack": {"result": {"calibrated": 0.21}}}

    def current():
        return dict(state["tracking"])

    def persist(t):
        state["tracking"].update(t)

    def push(action):
        state["pushed"].append(action)
        if not state["page"]:
            return {"delivered": False, "reason": "no Friday desktop page is open to show it in"}
        return {"delivered": True, "acked": True, "ack": state["ack"], "page": "desktop"}

    monkeypatch.setattr(ht, "current", current)
    monkeypatch.setattr(ht, "persist", persist)
    monkeypatch.setattr(ht, "push", push)
    return state


def test_the_tool_is_declared_once_and_shared_into_voice():
    from agent_friday.services import agent, voice_engine
    # Declared once: always-on, or on demand (agent.ON_DEMAND_TOOLS).
    names = [t["name"] for t in agent.CLAUDE_TOOLS + agent.WORKSPACE_TOOLS.get("on_demand", [])]
    assert names.count("hologram_window") == 1
    assert "hologram_window" in agent.CLAUDE_TOOL_HANDLERS
    assert agent.TOOL_RINGS.get("hologram_window") == 1
    assert "hologram_window" in voice_engine._VOICE_SHARED_TOOLS
    assert "hologram_window" in [n for n, _d, _s in voice_engine._voice_shared_tool_specs()]


def test_it_changes_only_the_owners_own_settings_and_screen_so_it_needs_no_card():
    from agent_friday.governance import action_gate
    assert "hologram_window" in action_gate.INTERNAL_TOOLS


def test_the_defaults_name_every_dial_the_tool_can_set():
    from agent_friday.core import DEFAULT_SETTINGS
    t = DEFAULT_SETTINGS["tracking"]
    assert set(ht.DIALS) <= set(t)
    assert t["zoom_in_max"] == 1.8 and t["zoom_out_max"] == 1.5
    assert t["head_response"] == 0.5 and t["neutral_face_width"] == 0
    # The engine caps zoom_in_max at 1 / MIN_GLASS_FRACTION = 2.5, and the
    # tool's own range must not promise more than the engine will give.
    assert ht.DIALS["zoom_in_max"][1] <= 2.5


def test_set_persists_only_changed_dials_then_pushes_them_live(store):
    out = ht.handle({"action": "set", "zoom_in_max": 2.2, "depth_strength": 1.5})
    assert store["tracking"]["zoom_in_max"] == 2.2
    assert store["tracking"]["depth_strength"] == 1.5
    # Settings merges fields: untouched dials stay in the store rather than
    # travelling in a stale snapshot that could replace a concurrent UI edit.
    assert store["tracking"]["hand_gain"] == ht._defaults()["hand_gain"]
    assert store["pushed"] == [{"type": "tracking", "tracking": {"zoom_in_max": 2.2, "depth_strength": 1.5}}]
    assert "two point two" in out and "one point five" in out
    assert "zoom_in_max" not in out                     # words, not key names


def test_set_clamps_to_the_slider_range_rather_than_refusing(store):
    ht.handle({"action": "set", "zoom_in_max": 40, "parallax_strength": -3})
    assert store["tracking"]["zoom_in_max"] == 2.5
    assert store["tracking"]["parallax_strength"] == 0.0


def test_relative_adds_to_the_current_value(store):
    store["tracking"]["depth_strength"] = 1.0
    ht.handle({"action": "set", "depth_strength": 0.25, "relative": True})
    assert store["tracking"]["depth_strength"] == 1.25
    ht.handle({"action": "set", "depth_strength": -5, "relative": True})
    assert store["tracking"]["depth_strength"] == 0.0


def test_set_without_a_dial_or_with_a_non_number_is_an_error_not_a_write(store):
    before = dict(store["tracking"])
    assert ht.handle({"action": "set"}).startswith("hologram_window error")
    assert ht.handle({"action": "set", "zoom_in_max": "big"}).startswith("hologram_window error")
    assert store["tracking"] == before and store["pushed"] == []


def test_a_change_with_no_page_open_is_still_saved_and_says_so(store):
    store["page"] = False
    out = ht.handle({"action": "set", "zoom_out_max": 2.0})
    assert store["tracking"]["zoom_out_max"] == 2.0
    assert "when the Friday window is open" in out


def test_calibrate_is_answered_by_the_page(store):
    out = ht.handle({"action": "calibrate"})
    assert store["pushed"] == [{"type": "tracking", "op": "calibrate"}]
    assert "Calibrated" in out
    store["ack"] = {"result": {"calibrated": None}}
    out = ht.handle({"action": "calibrate"})
    assert "can't see a face" in out
    store["page"] = False
    out = ht.handle({"action": "calibrate"})
    assert "without the Friday window open" in out


def test_reset_restores_the_feel_but_keeps_the_calibration_and_the_other_tabs(store):
    store["tracking"].update({"zoom_in_max": 2.4, "neutral_face_width": 0.23,
                              "dock_depth": 0.3, "debug_overlay": True,
                              "viewing_distance_cm": 80, "screen_width_cm": 34})
    out = ht.handle({"action": "reset"})
    t = store["tracking"]
    assert t["zoom_in_max"] == 1.8
    assert t["neutral_face_width"] == 0.23 and t["dock_depth"] == 0.3 and t["debug_overlay"] is True
    # Where they sit and how big the screen is are not the feel.
    assert t["viewing_distance_cm"] == 80 and t["screen_width_cm"] == 34
    assert "defaults" in out


def test_status_is_a_sentence_about_the_window(store):
    out = ht.handle({"action": "status"})
    assert "one point eight times nearer the glass" in out
    assert "sixty centimetres from a screen it measures itself" in out
    assert "not calibrated" in out
    store["tracking"]["neutral_face_width"] = 0.2
    assert "calibrated to where you sit" in ht.handle({"action": "status"})
    store["tracking"]["depth_strength"] = 0
    assert "Leaning in and out is off" in ht.handle({"action": "status"})


def test_the_windows_scale_is_set_by_voice_and_clamped(store):
    out = ht.handle({"action": "set", "viewing_distance_cm": 80})
    assert store["tracking"]["viewing_distance_cm"] == 80
    assert "eighty" in out and "viewing_distance_cm" not in out
    ht.handle({"action": "set", "screen_width_cm": 500})
    assert store["tracking"]["screen_width_cm"] == 120
    assert "a screen one hundred and twenty centimetres wide" in ht.handle({"action": "status"})


def test_numbers_are_spoken():
    assert ht.say_number(1.8) == "one point eight"
    assert ht.say_number(2) == "two"
    assert ht.say_number(0.35) == "zero point three five"
    assert ht.say_number(1.25) == "one point two five"
    assert ht.say_number(120) == "one hundred and twenty" and ht.say_number(300) == "three hundred"
