"""A blank routing mode has ONE reading everywhere, and it is reported as unset.

Before: `cloud_intent.routing_mode()` read a blank mode as `cloud_only` while
`cloud_mode_policy.current_mode()` (through `local_only_guard.routing_mode`)
read it as `local_preferred`, so one call site would send to the cloud and
another would hold it back, for the same settings file.

Now `cloud_intent.routing_mode()` is the single reader and the other two
delegate to it. A blank mode keeps today's factory behaviour (`cloud_only`),
and `cloud_intent.mode_is_unset()` says nobody chose it, so setup can ask.
"""
from __future__ import annotations

import pytest

from agent_friday import core
from agent_friday.services import cloud_intent as ci
from agent_friday.services import cloud_mode_policy as cmp
from agent_friday.services import local_only_guard as guard


def _settings(monkeypatch, mode, *, key=True):
    block = {"mode": mode} if key else {}
    monkeypatch.setattr(core, "_load_settings", lambda: {"model_routing": block})


@pytest.mark.parametrize("blank", ["", None, "   "])
def test_every_reader_gives_the_same_answer_for_a_blank_mode(monkeypatch, blank):
    _settings(monkeypatch, blank)
    # The assertion that was red: the policy reader used to say local_preferred.
    assert cmp.current_mode() == "cloud_only"
    assert guard.routing_mode() == "cloud_only"
    assert ci.routing_mode() == "cloud_only"


def test_a_missing_mode_key_reads_the_same(monkeypatch):
    _settings(monkeypatch, None, key=False)
    assert cmp.current_mode() == guard.routing_mode() == ci.routing_mode() == "cloud_only"


@pytest.mark.parametrize("blank", ["", None, "  "])
def test_a_blank_mode_is_reported_as_unset(monkeypatch, blank):
    _settings(monkeypatch, blank)
    assert ci.mode_is_unset() is True


@pytest.mark.parametrize("mode", ["cloud_only", "cloud_preferred", "local_preferred",
                                  "local_only", "smart"])
def test_a_chosen_mode_is_not_unset(monkeypatch, mode):
    _settings(monkeypatch, mode)
    assert ci.mode_is_unset() is False


@pytest.mark.parametrize("mode,expect", [
    ("cloud_only", "cloud_only"), ("cloud_preferred", "cloud_preferred"),
    ("local_preferred", "local_preferred"), ("local_only", "local_only"),
    ("smart", "local_preferred"), ("LOCAL_ONLY", "local_only")])
def test_the_three_readers_agree_for_every_mode(monkeypatch, mode, expect):
    _settings(monkeypatch, mode)
    assert cmp.current_mode() == guard.routing_mode() == expect
    # cloud_intent keeps the raw spelling of smart (its LOCAL_MODES knows it).
    assert ci.normalize_mode(ci.routing_mode()) == expect


def test_a_blank_mode_allows_the_cloud_like_cloud_only(monkeypatch):
    _settings(monkeypatch, "")
    ci.clear()
    assert cmp.cloud_allowed("x") is True
    assert ci.require("anthropic", "model call").intent == "cloud_mode"


def test_an_unreadable_setting_still_fails_closed(monkeypatch):
    def boom():
        raise RuntimeError("unreadable")
    monkeypatch.setattr(core, "_load_settings", boom)
    assert ci.routing_mode() == "local_only"
    assert cmp.current_mode() == "local_only"
    assert guard.routing_mode() == "local_only"
    assert ci.mode_is_unset() is False       # unreadable is not unset


def test_a_local_only_run_still_wins(monkeypatch):
    _settings(monkeypatch, "")
    with guard.local_only("test"):
        assert cmp.current_mode() == "local_only"
        assert ci.routing_mode() == "local_only"
