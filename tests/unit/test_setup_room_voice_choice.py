"""The room-approval default is a setup choice, not a decision Friday makes.

Whose spoken "yes" counts when several people are in earshot is the owner's
to set, so setup asks him instead of telling him (NS-8.15-2: any setup choice
can be revised later, in Settings > Voice).

Skipping keeps the careful answer, which is also the shipped default: in a
room, a spoken approval has to say Friday's name. Turning it off grants voice
nothing — it changes who may approve, not what may be approved.
"""
import pytest

sc = pytest.importorskip("agent_friday.services.setup_chat")
copy = pytest.importorskip("agent_friday.services.setup_chat_copy")

KEY = "voice_room_approvals_require_name"


@pytest.fixture
def saved(monkeypatch):
    """Capture the settings write without touching the real settings file."""
    seen = {}
    monkeypatch.setattr(sc, "_set_room_approvals_require_name",
                        lambda v: seen.__setitem__("value", v))
    return seen


def _run(value, saved):
    st, transcript = {"stage": "room_voice"}, []
    sc._on_room_voice(st, transcript, value, "")
    return st, transcript, saved


# ── It is asked, and it is a real choice ───────────────────────────────────

def test_the_stage_exists_in_the_flow():
    assert "room_voice" in copy.STAGES
    i = copy.STAGES.index
    assert i("scheduled_cloud") < i("room_voice") < i("finish"), (
        "it belongs at the end, with the other finishing choices")


def test_it_shows_on_the_progress_rail():
    stages = [s for _g, _l, ss in copy.STAGE_GROUPS for s in ss]
    assert "room_voice" in stages, "a stage missing from the rail is invisible"


def test_the_question_offers_both_answers_and_says_where_to_change_it():
    ask = copy.ROOM_VOICE_ASK
    keys = [k for k, _label in copy.ROOM_VOICE_CHIPS]
    assert "name" in keys and "anyone" in keys and "skip" in keys
    assert "Settings" in ask, "a setup choice has to say where it lives after"
    assert "earshot" in ask or "room" in ask


def test_the_question_does_not_pretend_it_limits_voice():
    """It decides whose yes counts, which is not the same as a restriction."""
    low = copy.ROOM_VOICE_ASK.lower()
    assert "approve" in low, copy.ROOM_VOICE_ASK


# ── The answers ────────────────────────────────────────────────────────────

def test_requiring_the_name_is_saved(saved):
    _st, _t, s = _run("name", saved)
    assert s.get("value") is True


def test_letting_anyone_approve_is_saved(saved):
    _st, _t, s = _run("anyone", saved)
    assert s.get("value") is False


@pytest.mark.parametrize("value", ["skip", "", None, "something else"])
def test_skipping_writes_nothing_and_keeps_the_careful_default(value, saved):
    _st, _t, s = _run(value, saved)
    assert "value" not in s, (
        "a skipped choice must not write; the default already requires the name")


@pytest.mark.parametrize("value", ["name", "anyone", "skip"])
def test_every_answer_moves_on_to_finish(value, saved):
    st, _t, _s = _run(value, saved)
    assert st["stage"] == "finish", "setup must not stall on this question"


def test_the_answer_is_read_back(saved):
    _st, transcript, _s = _run("anyone", saved)
    said = " ".join(str(m.get("text") or "") for m in transcript)
    assert said.strip(), "Friday should confirm what was set"


# ── The default, and a write that fails ────────────────────────────────────

def test_the_shipped_default_still_requires_the_name():
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS.get(KEY) is True


def test_a_failed_write_does_not_strand_setup(monkeypatch):
    """The setting is worth less than a finishable setup."""
    import agent_friday.core as core
    monkeypatch.setattr(core, "_save_settings",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    st, transcript = {"stage": "room_voice"}, []
    sc._on_room_voice(st, transcript, "anyone", "")
    assert st["stage"] == "finish"


def test_the_writer_sets_only_its_own_key(monkeypatch):
    import agent_friday.core as core
    written = {}
    monkeypatch.setattr(core, "_load_settings", lambda: {"unrelated": "keep me"})
    monkeypatch.setattr(core, "_save_settings",
                        lambda d, **k: written.update(d))
    sc._set_room_approvals_require_name(False)
    assert written.get(KEY) is False
    assert written.get("unrelated") == "keep me", (
        "the rest of settings must survive the write")
