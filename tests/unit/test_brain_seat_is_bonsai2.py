"""The brain seat lands on the owner's local model, never on a guess.

`capability_routing.reasoning` routes cloud reasoning as well as the brain
seat, so it may legitimately name a cloud model. `_configured("brain")` then
returns None, and the resolver used to read that as licence to walk the
size-ordered list of whatever the daemon reported — landing the brain on a
Gemma-4 e-series behind an Ollama daemon that was not running, while Bonsai2
answered on :8090. In the log that reads as

    [seats] brain: using 'gemma4:e2b-fridayweaver-1.0'

with no substitution warning at all, because nothing had been asked for. Voice
then told the owner his local model was not running.

Nothing local recorded for a role is not the same as no preference. These
tests pin the brain on the owner's local model, and the drift guard at the end
pins the default itself to Bonsai2.
"""
import pytest

ls = pytest.importorskip("agent_friday.services.local_seats")

#: What the daemon reports: the e-series is small and would win on size.
ROWS = [("gemma4:e2b-fridayweaver-1.0", 1.9), ("gemma4:e4b", 6.3),
        ("bonsai2:27b", 16.4)]


@pytest.fixture
def daemon(monkeypatch):
    """Synthetic inventory with NOTHING live.

    Both liveness paths are stubbed. `seat_endpoint` alone is not enough:
    when it reports nothing the resolver probes ports 8090-8130 directly, and
    on this machine that finds the real Bonsai2 seat and quietly decides the
    test for us. A test that reaches the live seat is not a test.
    """
    monkeypatch.setattr(ls, "installed", lambda force=False: list(ROWS))
    monkeypatch.setattr(ls, "_ANNOUNCED", set())
    monkeypatch.setattr(ls, "_probe_seat", lambda name: False)
    from agent_friday.services import local_call
    monkeypatch.setattr(local_call, "seat_endpoint", lambda m: None)
    return ROWS


def test_the_fixture_really_isolates_the_machine(daemon, routing):
    """Guards the guard: with nothing live and nothing recorded, the plain
    size ordering must pick the smallest row. If this ever returns bonsai2,
    the stubs have stopped working and every red below is worthless."""
    routing["capability_routing"] = {}
    assert ls._probe_seat("bonsai2:27b") is False
    monkey = ls.resolve("sidekick")
    assert monkey == "gemma4:e2b-fridayweaver-1.0", monkey


@pytest.fixture
def routing(monkeypatch):
    """Drive settings, the way the live machine has them."""
    box = {"capability_routing": {}, "model_routing": {}}
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda: box)
    return box


# ── The bug ────────────────────────────────────────────────────────────────

def test_a_cloud_reasoning_entry_does_not_send_the_brain_to_a_guess(daemon, routing):
    """The live shape: reasoning is a cloud model, local says bonsai2."""
    routing["capability_routing"] = {
        "reasoning": {"provider": "anthropic", "model": "claude-sonnet-5-5"},
        "local": {"provider": "bonsai2-local", "model": "bonsai2:27b"},
    }
    assert ls.resolve("brain") == "bonsai2:27b"


def test_model_routing_local_model_is_honoured_too(daemon, routing):
    """The other place the local model is recorded."""
    routing["capability_routing"] = {
        "reasoning": {"provider": "anthropic", "model": "claude-sonnet-5-5"}}
    routing["model_routing"] = {"local_model": "bonsai2:27b"}
    assert ls.resolve("brain") == "bonsai2:27b"


def test_with_nothing_recorded_the_brain_still_defaults_to_bonsai2(daemon, routing):
    routing["capability_routing"] = {
        "reasoning": {"provider": "anthropic", "model": "claude-sonnet-5-5"}}
    assert ls.resolve("brain") == ls.LOCAL_BRAIN_DEFAULT == "bonsai2:27b"


def test_the_judge_seat_gets_the_same_treatment(daemon, routing):
    routing["capability_routing"] = {
        "reasoning": {"provider": "anthropic", "model": "claude-sonnet-5-5"},
        "local": {"provider": "bonsai2-local", "model": "bonsai2:27b"},
    }
    assert ls.resolve("judge") == "bonsai2:27b"


# ── What must not change ───────────────────────────────────────────────────

def test_an_explicit_local_choice_still_wins(daemon, routing):
    """A recorded LOCAL reasoning seat is a real preference; honour it."""
    routing["capability_routing"] = {
        "reasoning": {"provider": "llama-cpp-local", "model": "gemma4:e4b"},
        "local": {"provider": "bonsai2-local", "model": "bonsai2:27b"},
    }
    assert ls.resolve("brain") == "gemma4:e4b"


def test_the_callers_own_preference_still_wins(daemon, routing):
    routing["capability_routing"] = {
        "local": {"provider": "bonsai2-local", "model": "bonsai2:27b"}}
    assert ls.resolve("brain", configured="gemma4:e4b") == "gemma4:e4b"


def test_a_smaller_role_is_not_pushed_onto_the_27b(daemon, routing):
    """A sidekick has its own smaller-is-fine ordering; the 27B would be a
    regression there, not a repair. Only the brain-shaped roles get the
    Bonsai2 fallback."""
    routing["capability_routing"] = {
        "subagent": {"provider": "anthropic", "model": "claude-sonnet-5-5"},
        "local": {"provider": "bonsai2-local", "model": "bonsai2:27b"},
    }
    assert ls.resolve("sidekick") == "gemma4:e2b-fridayweaver-1.0"


def test_an_unreachable_daemon_still_refuses_to_guess(monkeypatch, routing):
    """[] from installed() means unknown, never "nothing installed"."""
    monkeypatch.setattr(ls, "installed", lambda force=False: [])
    routing["capability_routing"] = {
        "local": {"provider": "bonsai2-local", "model": "bonsai2:27b"}}
    assert ls.resolve("brain") == "bonsai2:27b"


def test_a_default_that_is_not_installed_does_not_win(monkeypatch, routing):
    """The fallback is a candidate, not an override: with Bonsai2 absent the
    existing ordering still picks something that is actually there."""
    monkeypatch.setattr(ls, "installed", lambda force=False: [("gemma4:e4b", 6.3)])
    monkeypatch.setattr(ls, "_ANNOUNCED", set())
    monkeypatch.setattr(ls, "_probe_seat", lambda name: False)
    from agent_friday.services import local_call
    monkeypatch.setattr(local_call, "seat_endpoint", lambda m: None)
    routing["capability_routing"] = {
        "reasoning": {"provider": "anthropic", "model": "claude-sonnet-5-5"}}
    assert ls.resolve("brain") == "gemma4:e4b"


# ── Drift guard ────────────────────────────────────────────────────────────

def test_the_local_brain_default_is_bonsai2():
    """The Bonsai2-only standard, as a value a test can hold."""
    assert ls.LOCAL_BRAIN_DEFAULT == "bonsai2:27b"


def test_the_brain_default_is_not_the_installer_floor():
    """model_plan.FLOOR_MODEL is the smallest thing that can hold a tool
    contract, shared with the installer's tiers. It is not the brain."""
    from agent_friday.services.model_plan import FLOOR_MODEL
    assert ls.LOCAL_BRAIN_DEFAULT != FLOOR_MODEL, (
        "the brain seat must not follow the installer floor")


def test_the_brain_default_is_a_local_name_not_a_cloud_one():
    assert not ls._names_a_cloud_model(ls.LOCAL_BRAIN_DEFAULT)


def test_the_resolved_brain_is_always_one_of_the_installed_rows(daemon, routing):
    """The guard the owner asked for: the brain seat may never name a model
    that is not installed, whatever the settings say."""
    for cr in ({}, {"reasoning": {"provider": "anthropic", "model": "claude-opus-5-5"}},
               {"reasoning": {"provider": "anthropic", "model": "claude-sonnet-5-5"},
                "local": {"provider": "bonsai2-local", "model": "bonsai2:27b"}},
               {"local": {"provider": "x", "model": "a-model-nobody-installed"}}):
        routing["capability_routing"] = cr
        got = ls.resolve("brain")
        assert got in {n for n, _ in ROWS}, (cr, got)
