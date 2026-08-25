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


# ── Friday's own runtime counts as installed ────────────────────────────────
#
# The first version of this module asked only the Ollama daemon, which
# inverted its purpose. gemma4:e2b / :12b / :e4b / :26b are real entries in
# ~/.friday/runtime/models/models.json with files on disk, served by the
# Arbiter as processes Friday owns. The daemon has never heard of them. A
# resolver that trusts the daemon alone declares her own models missing and
# substitutes whatever was last `ollama pull`ed -- moving a seat off the
# runtime Stephen chose, which is what happened to his reasoning seat.

def test_a_model_in_fridays_own_store_is_installed(monkeypatch):
    monkeypatch.setattr(seats, "_friday_store",
                        lambda: [("gemma4:e2b", 7.16), ("gemma4:26b", 16.95)])
    monkeypatch.setattr(seats.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no daemon")))
    names = {n for n, _ in seats.installed(force=True)}
    assert "gemma4:e2b" in names, (
        "her own runtime was treated as empty because the daemon was asked")


def test_a_seat_bound_to_fridays_own_model_is_not_healed_away(monkeypatch):
    monkeypatch.setattr(seats, "installed",
                        lambda force=False: [("gemma4:e2b", 7.16),
                                             ("qwen3.5:9b", 6.59)])
    settings = {"orchestrator_model": "gemma4:e2b",
                "capability_routing": {"reasoning": {"model": "gemma4:e2b"}}}
    assert seats.heal(settings) == []
    assert settings["orchestrator_model"] == "gemma4:e2b"


def _daemon_returning(monkeypatch, payload):
    """Stub Ollama's /api/tags with a fixed inventory."""
    import json as _json

    class _R:
        def read(self): return _json.dumps(payload).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(seats.urllib.request, "urlopen", lambda *a, **k: _R())


def _seats_live(monkeypatch, live_names):
    """Stub `seat_endpoint` so reachability is decided by the test, not the host.

    `installed()` imports this from `local_call` at call time, so the patch has
    to land on the DEFINING module. Patching `seats.seat_endpoint` would bind a
    name nothing reads and the test would pass or fail on whatever happens to be
    listening on the developer's machine.
    """
    from agent_friday.services import local_call
    monkeypatch.setattr(
        local_call, "seat_endpoint",
        lambda m: "http://127.0.0.1:8090/v1" if m in live_names else None)


def test_both_stores_are_merged_without_duplicates(monkeypatch):
    """functiongemma:270m lives in BOTH. It is one seat, not two."""
    _daemon_returning(monkeypatch,
                      {"models": [{"name": "functiongemma:270m", "size": 3e8},
                                  {"name": "qwen3.5:9b", "size": 6.59e9}]})
    monkeypatch.setattr(seats, "_friday_store",
                        lambda: [("functiongemma:270m", 0.30), ("gemma4:12b", 7.38)])
    # gemma4:12b exists only in Friday's store, so the reachability filter added
    # in 8a30831 would drop it and this test would be measuring that filter
    # instead of the merge it is named for. Give it a live seat and the subject
    # is the union again.
    _seats_live(monkeypatch, {"gemma4:12b"})
    names = [n for n, _ in seats.installed(force=True)]
    assert names.count("functiongemma:270m") == 1
    assert {"gemma4:12b", "qwen3.5:9b"} <= set(names)


# ── reachability: "on disk" and "callable right now" are different claims ────
#
# The filter these cover is what broke the merge test above, and it had no test
# of its own -- so the only signal it existed was an unrelated failure. That is
# the same shape of defect as the one it was written to fix.

def test_a_store_model_with_no_live_seat_is_not_offered(monkeypatch):
    """A GGUF on disk that nothing is serving resolves to a 404, not a model."""
    _daemon_returning(monkeypatch, {"models": [{"name": "qwen3.5:9b", "size": 6.59e9}]})
    monkeypatch.setattr(seats, "_friday_store", lambda: [("gemma4:e2b", 7.16)])
    _seats_live(monkeypatch, set())
    names = {n for n, _ in seats.installed(force=True)}
    assert "gemma4:e2b" not in names, (
        "a model with no seat and no daemon entry was offered as installed; "
        "every call to it 404s and escalates to the cloud (commit 8a30831)")
    assert "qwen3.5:9b" in names, "the daemon's own models must be unaffected"


def test_a_store_model_with_a_live_seat_is_offered(monkeypatch):
    """The filter drops unreachable seats, not Friday's runtime as a category."""
    _daemon_returning(monkeypatch, {"models": []})
    monkeypatch.setattr(seats, "_friday_store", lambda: [("gemma4:12b", 7.38)])
    _seats_live(monkeypatch, {"gemma4:12b"})
    names = {n for n, _ in seats.installed(force=True)}
    assert "gemma4:12b" in names


def test_a_daemon_model_is_offered_without_any_llama_seat(monkeypatch):
    """Being in /api/tags already means the daemon will serve it on request."""
    _daemon_returning(monkeypatch, {"models": [{"name": "qwen3:8b", "size": 5.2e9}]})
    monkeypatch.setattr(seats, "_friday_store", lambda: [])
    _seats_live(monkeypatch, set())
    names = {n for n, _ in seats.installed(force=True)}
    assert "qwen3:8b" in names


def test_a_manifest_entry_whose_file_is_gone_does_not_count(monkeypatch, tmp_path):
    """Listed is not the same as present."""
    import json as _json
    manifest = {"models": {"ghost:1b": {"path": str(tmp_path / "nope.gguf"),
                                        "size_bytes": 1e9}}}
    home = tmp_path / "home"
    (home / ".friday" / "runtime" / "models").mkdir(parents=True)
    (home / ".friday" / "runtime" / "models" / "models.json").write_text(
        _json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(home))
    assert seats._friday_store() == []
