"""Whether quitting Friday hands the memory back is a setup choice.

The local brain holds around 14 GB of RAM and most of a 12 GB card while
Friday is open. Whether it keeps them after a quit is the owner's call about
his own machine, so setup asks instead of deciding (NS-8.15-2: revisable
later, in Settings > Models).

Skipping hands the memory back, which is the shipped default: the machine is
his again the moment he has closed her.
"""
import pytest

sc = pytest.importorskip("agent_friday.services.setup_chat")
copy = pytest.importorskip("agent_friday.services.setup_chat_copy")
ra = pytest.importorskip("agent_friday.services.residency_arbiter")


@pytest.fixture
def saved(monkeypatch):
    seen = {}
    monkeypatch.setattr(sc, "_set_keep_brain_warm",
                        lambda v: seen.__setitem__("value", v))
    return seen


def _run(value, saved):
    st, transcript = {"stage": "brain_warm"}, []
    sc._on_brain_warm(st, transcript, value, "")
    return st, transcript, saved


# ── It is asked, and it is a real choice ───────────────────────────────────

def test_the_stage_is_in_the_flow_before_finish():
    i = copy.STAGES.index
    assert i("room_voice") < i("brain_warm") < i("finish")


def test_it_shows_on_the_progress_rail():
    stages = [s for _g, _l, ss in copy.STAGE_GROUPS for s in ss]
    assert "brain_warm" in stages, "a stage off the rail is invisible"


def test_the_question_gives_him_the_numbers_and_both_costs():
    ask = copy.BRAIN_WARM_ASK
    assert "14 GB" in ask, "he cannot weigh it without the number"
    assert "graphics card" in ask
    assert "a minute" in ask, "the cost of handing it back is his to see too"
    # No toggle is rendered for this key yet, so the question must not send
    # him to a Settings pane that has no control in it. Re-running setup is
    # the path that exists today (NS-8.15-2).
    assert "Settings" not in ask, (
        "do not promise a control that is not there")
    assert "setup again" in ask or "change it" in ask


def test_the_question_says_a_restart_keeps_it():
    """Otherwise 'hand it back' reads as 'reload on every deploy'."""
    assert "restart" in copy.BRAIN_WARM_ASK.lower()


def test_both_answers_and_a_skip_are_offered():
    keys = [k for k, _l in copy.BRAIN_WARM_CHIPS]
    assert keys == ["release", "keep", "skip"], keys


# ── The answers ────────────────────────────────────────────────────────────

def test_keeping_it_warm_is_saved(saved):
    _st, _t, s = _run("keep", saved)
    assert s.get("value") is True


def test_handing_it_back_is_saved(saved):
    _st, _t, s = _run("release", saved)
    assert s.get("value") is False


@pytest.mark.parametrize("value", ["skip", "", None, "mumble"])
def test_skipping_writes_nothing_and_keeps_the_default(value, saved):
    _st, _t, s = _run(value, saved)
    assert "value" not in s, (
        "the default already hands the memory back; a skip must not write")


@pytest.mark.parametrize("value", ["keep", "release", "skip"])
def test_every_answer_finishes_setup(value, saved):
    st, _t, _s = _run(value, saved)
    assert st["stage"] == "finish"


def test_the_answer_is_confirmed_out_loud(saved):
    _st, transcript, _s = _run("keep", saved)
    said = " ".join(str(m.get("text") or "") for m in transcript)
    assert said.strip()


# ── The default, and a write that fails ────────────────────────────────────

def test_the_shipped_default_hands_the_memory_back():
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS.get(ra.KEEP_WARM_SETTING) is False


def test_the_setting_name_is_the_one_the_arbiter_reads():
    """One name, or the choice writes somewhere nothing looks."""
    assert ra.KEEP_WARM_SETTING == "keep_brain_warm_between_sessions"


def test_a_failed_write_does_not_strand_setup(monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "_save_settings",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
    st, transcript = {"stage": "brain_warm"}, []
    sc._on_brain_warm(st, transcript, "keep", "")
    assert st["stage"] == "finish"


def test_the_writer_sets_only_its_own_key(monkeypatch):
    import agent_friday.core as core
    written = {}
    monkeypatch.setattr(core, "_load_settings", lambda: {"unrelated": "keep me"})
    monkeypatch.setattr(core, "_save_settings", lambda d, **k: written.update(d))
    sc._set_keep_brain_warm(True)
    assert written.get(ra.KEEP_WARM_SETTING) is True
    assert written.get("unrelated") == "keep me"
