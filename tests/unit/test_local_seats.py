"""A model name in the source is a guess about the disk. Check it.

On 2026-08-18 three modules named models that had been uninstalled hours
earlier, and each degraded into something that looked like a legitimate
result rather than an outage: the privacy gate fell back to its deterministic
verdict, and the research grind reported "no usable JSON" — which its own
pipeline is entitled to read as a model declining to comply.

The tests that matter here are the ones about honesty under substitution.
"""
import pytest

from agent_friday.services import local_seats as seats


@pytest.fixture(autouse=True)
def clean():
    seats._CACHE.update(at=0.0, rows=[])
    seats._ANNOUNCED.clear()
    yield
    seats._CACHE.update(at=0.0, rows=[])


def _inventory(monkeypatch, rows, configured=None):
    monkeypatch.setattr(seats, "installed", lambda force=False: list(rows))
    monkeypatch.setattr(seats, "_configured", lambda role: configured)


BIG = ("big:12b", 7.5)
MID = ("mid:9b", 6.6)
SMALL = ("small:4b", 2.1)
TOY = ("toy:270m", 0.3)


def test_a_configured_model_that_exists_is_never_overridden(monkeypatch):
    """The resolver replaces missing names. It does not second-guess working ones."""
    _inventory(monkeypatch, [SMALL, MID, BIG])
    assert seats.resolve("brain", "mid:9b") == "mid:9b"


def test_a_missing_model_is_replaced(monkeypatch):
    _inventory(monkeypatch, [SMALL, MID, BIG])
    got = seats.resolve("brain", "gemma4:12b")
    assert got in {"small:4b", "mid:9b", "big:12b"}
    assert got != "gemma4:12b"


def test_the_substitution_is_announced(monkeypatch, capsys):
    """Silent substitution is the whole defect. It has to say so."""
    _inventory(monkeypatch, [SMALL, MID, BIG])
    seats.resolve("brain", "gemma4:12b")
    out = capsys.readouterr().out
    assert "gemma4:12b" in out and "not installed" in out


def test_the_announcement_does_not_repeat_in_a_grinding_loop(monkeypatch, capsys):
    _inventory(monkeypatch, [SMALL, MID, BIG])
    for _ in range(5):
        seats.resolve("brain", "gemma4:12b")
    assert capsys.readouterr().out.count("not installed") == 1


def test_an_unreachable_daemon_returns_the_caller_preference_unchanged(monkeypatch):
    """A transient blip must not permanently rewrite what Stephen asked for."""
    _inventory(monkeypatch, [], configured=None)
    assert seats.resolve("brain", "gemma4:12b") == "gemma4:12b"


def test_capability_routing_is_consulted_before_the_size_fallback(monkeypatch):
    """The setting he already edits wins over an arbitrary size pick."""
    _inventory(monkeypatch, [SMALL, MID, BIG], configured="mid:9b")
    assert seats.resolve("brain", "gone:1b") == "mid:9b"


def test_heavy_wants_the_largest_and_cheap_roles_want_the_smallest(monkeypatch):
    _inventory(monkeypatch, [SMALL, MID, BIG])
    assert seats.resolve("heavy", "gone:1b") == "big:12b"
    assert seats.resolve("sidekick", "gone:1b") == "small:4b"


def test_a_toy_model_is_not_offered_as_a_fallback(monkeypatch):
    """`functiongemma:270m` cannot extract passages or return a verdict.

    Falling back to it would turn a missing-model problem into a baffling one.
    """
    _inventory(monkeypatch, [TOY, MID, BIG])
    assert seats.resolve("extractor", "gone:1b") == "mid:9b"


def test_a_toy_is_still_better_than_refusing_when_it_is_all_there_is(monkeypatch):
    _inventory(monkeypatch, [TOY])
    assert seats.resolve("extractor", "gone:1b") == "toy:270m"


def test_embedding_models_are_never_offered_as_answering_seats(monkeypatch):
    """They cannot answer, and the failure would be incomprehensible."""
    payload = {"models": [{"name": "qwen3-embedding:0.6b", "size": 6e8},
                          {"name": "real:9b", "size": 6.6e9}]}

    import json
    import io as _io

    class _R:
        def read(self): return json.dumps(payload).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(seats.urllib.request, "urlopen", lambda *a, **k: _R())
    assert [n for n, _ in seats.installed(force=True)] == ["real:9b"]
    assert _io  # keep the import honest


def test_resolve_never_raises_on_a_broken_daemon(monkeypatch):
    def _boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(seats.urllib.request, "urlopen", _boom)
    monkeypatch.setattr(seats, "_configured", lambda role: None)
    assert seats.resolve("brain", "whatever:1b") == "whatever:1b"


# ── the settings heal ───────────────────────────────────────────────────────
#
# This is the one that actually explains "I change the model and it does not
# stick". `_sync_capability_routing` lets the legacy flat key outrank the
# canonical entry on any save that did not explicitly set routing, so a stale
# `orchestrator_model` re-stamped a DELETED model over the picker's choice on
# every write. Three fixes aimed at the picker could not have found it.

VISION = ("qwen3-vl:8b", 6.14)
TEXT = ("text:9b", 6.33)


def test_a_deleted_local_model_is_repaired_in_both_representations(monkeypatch):
    _inventory(monkeypatch, [TEXT, BIG])
    settings = {
        "orchestrator_model": "gemma4:e2b",
        "capability_routing": {"reasoning": {"model": "gemma4:e2b",
                                             "provider": "ollama-local"}},
    }
    notes = seats.heal(settings)
    assert notes, "the repair said nothing"
    assert settings["orchestrator_model"] != "gemma4:e2b"
    assert settings["capability_routing"]["reasoning"]["model"] != "gemma4:e2b"
    assert (settings["orchestrator_model"]
            == settings["capability_routing"]["reasoning"]["model"]), (
        "the two representations disagree, which is the bug itself")


def test_a_healthy_configuration_is_left_completely_alone(monkeypatch):
    """heal() repairs missing models. It does not re-pick working ones."""
    _inventory(monkeypatch, [TEXT, BIG])
    settings = {
        "orchestrator_model": "text:9b",
        "capability_routing": {"reasoning": {"model": "text:9b"}},
    }
    assert seats.heal(settings) == []
    assert settings["orchestrator_model"] == "text:9b"


def test_a_cloud_model_is_never_touched(monkeypatch):
    """`claude-sonnet-5` is not in `ollama list`, and that means nothing."""
    _inventory(monkeypatch, [TEXT, BIG])
    settings = {
        "orchestrator_model": "claude-sonnet-5",
        "capability_routing": {"reasoning": {"model": "claude-sonnet-5"}},
    }
    assert seats.heal(settings) == []
    assert settings["orchestrator_model"] == "claude-sonnet-5"


def test_nothing_is_healed_while_the_daemon_is_unreachable(monkeypatch):
    """A blip must not rewrite his choices permanently."""
    monkeypatch.setattr(seats, "installed", lambda force=False: [])
    settings = {"orchestrator_model": "gemma4:e2b",
                "capability_routing": {"reasoning": {"model": "gemma4:e2b"}}}
    assert seats.heal(settings) == []
    assert settings["orchestrator_model"] == "gemma4:e2b"


def test_a_vision_model_does_not_win_a_text_role_by_being_smaller(monkeypatch):
    """Measured: healing chose qwen3-vl:8b over his own text model at +190 MB."""
    _inventory(monkeypatch, [VISION, TEXT, BIG])
    assert seats.resolve("brain", "gone:1b") == "text:9b"


def test_a_vision_model_is_used_when_it_is_the_only_thing_there(monkeypatch):
    _inventory(monkeypatch, [VISION])
    assert seats.resolve("brain", "gone:1b") == "qwen3-vl:8b"
