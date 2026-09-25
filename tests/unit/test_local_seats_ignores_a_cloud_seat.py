"""A cloud seat pick is not a missing local model.

When `capability_routing.reasoning.model` is `claude-opus-5-5` -- a cloud
model, deliberately chosen in the picker -- `local_seats._configured("brain")`
returns that string. If `resolve()` then asks whether it is in the LOCALLY
INSTALLED list, it is not, so it "heals" the seat onto a local model and says so:

    [seats] brain: 'claude-opus-5-5' is not installed — using 'bonsai2:27b' instead

Every word after the model name is wrong. Opus 5.5 is not installable on this
machine and is never meant to be; nothing is missing and nothing needs
healing. `local_seats` exists to choose among LOCAL seats, so a cloud id is
simply not a question it has been asked.

The damage is not only cosmetic. `_ANNOUNCED` dedupes on
`(role, wanted, got)`, so that sentence is emitted once per process and every
later substitution is silent, and the consumers of `resolve("brain")`
(`capability_state`, `voice_engine`, `voice_manifest`) go on reporting a local
brain for a machine whose reasoning seat is a cloud model.
"""

import pytest

from agent_friday.services import local_seats


CLOUD_SEATS = [
    "claude-opus-5-5",
    "claude-sonnet-5",
    "gpt-5",
    "gemini-3-pro",
    "anthropic/claude-opus-5.5",      # gateway-prefixed, still cloud
    "openai/gpt-5",
]

LOCAL_SEATS = [
    "bonsai2:27b",
    "gemma4:e2b-fridayweaver-1.0",
]


@pytest.fixture
def seated(monkeypatch):
    """Point `capability_routing.reasoning.model` at whatever the test wants."""
    def _seat(model):
        import agent_friday.core as core
        monkeypatch.setattr(
            core, "_load_settings",
            lambda *a, **k: {"capability_routing": {"reasoning": {"model": model}}},
            raising=False)
    return _seat


@pytest.mark.parametrize("model", CLOUD_SEATS)
def test_a_cloud_seat_is_not_read_as_a_local_preference(seated, model):
    """`_configured` answers "which LOCAL model does the user want here", so a
    cloud pick must come back as "no local preference", not as a name to go
    looking for in the Ollama store."""
    seated(model)
    assert local_seats._configured("brain") is None, (
        "%s was offered to the local seat resolver as a local model" % model)


@pytest.mark.parametrize("model", LOCAL_SEATS)
def test_a_local_seat_is_still_honoured(seated, model):
    """The whole point of `_configured` must survive: a genuinely local pick is
    still the preference, so a user who seats a local model still gets it."""
    seated(model)
    assert local_seats._configured("brain") == model


def test_no_false_not_installed_announcement_for_a_cloud_seat(seated, monkeypatch):
    """End to end through `resolve`, which is what the callers use.

    With a cloud seat configured and a local inventory that does not contain it,
    `resolve` must pick a local seat WITHOUT claiming the cloud model is missing.
    The announcement is the artefact the user sees, so it is what is asserted.
    """
    seated("claude-opus-5-5")
    monkeypatch.setattr(local_seats, "installed",
                        lambda *a, **k: [("bonsai2:27b", 17.0)])
    # A fresh dedupe set, or an earlier test's announcement hides a new one.
    monkeypatch.setattr(local_seats, "_ANNOUNCED", set())

    said = []
    monkeypatch.setattr(local_seats, "_announce",
                        lambda role, wanted, got: said.append((role, wanted, got)))

    got = local_seats.resolve("brain")

    assert got == "bonsai2:27b", "a local seat should still be chosen"
    for role, wanted, _got in said:
        assert wanted is None, (
            "resolve announced %r as a model it expected to find installed; "
            "it is a cloud model" % (wanted,))
