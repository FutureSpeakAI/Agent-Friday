"""The setup chat's connect stage in cloud mode: one key is enough.

In cloud mode the chat says that one key is enough, offers Anthropic first
and OpenRouter as the alternative with a link to each, checks a key once it
is saved and says whether Friday can think, and says what will not work when
the stage is left with no key. A key typed into the chat is still refused.
"""
from __future__ import annotations

import pytest

from agent_friday.services import one_key
from agent_friday.services import setup_chat as sc
from agent_friday.services import setup_chat_copy as copy
from agent_friday.services import setup_profile as profile

FAKE_KEY = "sk-" + "ant-" + "Zz9Yy8Xx7Ww6Vv5Uu4Tt3Ss2Rr1Qq0Pp"   # pragma: allowlist secret


@pytest.fixture
def home(tmp_path, monkeypatch):
    import agent_friday.core as core
    from agent_friday.services import setup_connections, setup_reader
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "_SETUP_MARKER", tmp_path / ".setup_complete")
    monkeypatch.setattr(core, "_save_settings", lambda delta: None)
    monkeypatch.setattr(setup_reader, "local_model", lambda: None)
    monkeypatch.setattr(setup_reader, "cloud_model", lambda: None)
    monkeypatch.setattr(setup_connections, "connected_labels", lambda: [])
    keys = {"in_use": None}
    monkeypatch.setattr(one_key, "key_in_use", lambda: keys["in_use"])
    monkeypatch.setattr(sc, "KEY_VERIFY", None)
    return keys


def _to_connect(mode):
    sc.begin(mode, "")
    sc.answer("welcome", None, "Sam")
    sc.answer("agent_name", "Friday", "")
    sc.answer("basics", {"minor_mode": False, "distribution": "default"}, "")
    assert sc.load_state()["stage"] == "connect"


def _texts():
    return [e["text"] for e in profile.load_transcript() if e["role"] == "friday"]


def test_cloud_mode_says_one_key_is_enough_with_both_links(home):
    _to_connect("cloud_only")
    said = _texts()
    assert copy.CONNECT_ONE_KEY in said
    text = copy.CONNECT_ONE_KEY
    assert "One key is enough" in text
    assert text.index("Anthropic") < text.index("OpenRouter")
    assert "https://console.anthropic.com/settings/keys" in text
    assert "https://openrouter.ai/keys" in text
    assert "not into this chat" in text


def test_cloud_mode_with_a_key_already_says_it_is_enough(home):
    home["in_use"] = "openrouter"
    _to_connect("cloud_only")
    assert copy.CONNECT_ONE_KEY_HAVE.format(label="OpenRouter") in _texts()
    assert copy.CONNECT_ONE_KEY not in _texts()


def test_local_only_mode_does_not_ask_for_a_key(home):
    _to_connect("local_only")
    assert copy.CONNECT_ONE_KEY not in _texts()
    sc.answer("connect", "done", "")
    assert copy.NO_KEY_YET not in _texts()


def test_leaving_connect_with_no_key_says_what_will_not_work(home):
    _to_connect("cloud_only")
    sc.answer("connect", "done", "")
    said = _texts()
    assert copy.NO_KEY_YET in said
    for thing in ("chat", "briefings", "scheduled jobs", "research"):
        assert thing in copy.NO_KEY_YET


def test_leaving_connect_with_a_key_does_not_warn(home):
    _to_connect("cloud_only")
    home["in_use"] = "anthropic"
    sc.answer("connect", "done", "")
    assert copy.NO_KEY_YET not in _texts()


@pytest.mark.parametrize("verdict, tail, can_think", [
    ("ok", copy.KEY_CAN_THINK, True),
    ("rejected", copy.KEY_CANNOT_THINK, False),
    ("no_credit", copy.KEY_CANNOT_THINK, False),
    ("unknown", copy.KEY_UNSURE, False),
])
def test_a_saved_key_is_checked_and_the_chat_says_so(home, monkeypatch,
                                                     verdict, tail, can_think):
    monkeypatch.setattr(sc, "KEY_VERIFY",
                        lambda p: {"verdict": verdict, "text": "Verdict for %s." % p})
    _to_connect("cloud_only")
    out = sc.key_saved("openrouter")
    assert out["verdict"] == verdict and out["can_think"] is can_think
    assert out["text"].endswith(tail)
    assert _texts()[-1] == out["text"]


def test_the_key_check_says_nothing_outside_the_connect_stage(home, monkeypatch):
    monkeypatch.setattr(sc, "KEY_VERIFY", lambda p: {"verdict": "ok", "text": "ok."})
    _to_connect("cloud_only")
    sc.answer("connect", "done", "")
    before = list(_texts())
    sc.key_saved("anthropic")
    assert _texts() == before


def test_a_key_typed_into_the_chat_is_still_refused(home):
    sc.begin("cloud_only", "")
    with pytest.raises(sc.Refused):
        sc.answer("welcome", None, FAKE_KEY)
    assert FAKE_KEY not in " ".join(e["text"] for e in profile.load_transcript())
