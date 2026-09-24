"""A seat declared local-only cannot be seated on a cloud model.

From a screenshot of Settings > Models, 2026-09-24: the "Memory keeper" seat
reads "Reads the day and decides what is worth keeping. Local only." while
showing "Claude Opus 5.5 - cloud - proven - has served 35 min ago".

WHAT ACTUALLY HAPPENED -- established before changing anything, because "is the
day going to the cloud" deserves evidence, not inference:

  1. `memory_proposals._ask_seat()` raises `SeatUnavailable` BEFORE any network
     call when the provider is not local. The rule was already enforced.
  2. Its orb label "Reading the day" appears ZERO times in friday.log.
  3. ZERO of 486,810 entries in ~/.friday/vault/egress-log.jsonl mention memory.
  4. `memory_proposals.propose()` is not in the scheduler's task list at all.

So nothing leaked. The label was accurate as a RULE; three things were wrong
around it:

  * the UI let a cloud model be SEATED on a local-only seat, leaving the seat in
    a permanently-refusing state that nothing surfaced;
  * the "proven - has served 35 min ago" badge is derived per MODEL, not per
    seat ("Proven means this exact model has actually served this machine"), and
    Opus 5.5 also holds the reasoning and subagent seats -- so a badge about
    chat traffic read as evidence that the Memory keeper had been working;
  * the refusal was a log warning and a False return, which no one sees.

This file pins the rule in ONE place: the declaration, the save-time refusal and
the runtime refusal all read `seat_policy.LOCAL_ONLY_SEATS`, so the label and the
enforcement cannot drift apart again.

Deliberately NOT a revival of `_check_local_model_seat_gate`, which is a no-op by
maintainer decision: that gate refused a user's chosen model for failing a
homegrown quality eval. This is a coherence rule -- a seat whose whole point is
that it never leaves the machine cannot be pointed at a paid API -- which is a
different claim and a much narrower one.
"""

import pytest


def test_the_declaration_exists_in_one_place():
    from agent_friday.services import seat_policy
    assert "memory_manager" in seat_policy.LOCAL_ONLY_SEATS


def test_the_seat_label_is_generated_from_the_declaration():
    """The prose and the rule must come from the same fact, or they drift --
    which is exactly what produced a 'Local only' label on a cloud seat."""
    from agent_friday.routes import intelligence
    from agent_friday.services import seat_policy

    rows = {r[0]: r for r in intelligence.ROLE_SPEC}
    for key in seat_policy.LOCAL_ONLY_SEATS:
        assert key in rows, "%s is declared local-only but is not a seat" % key
        blurb = rows[key][4]
        assert "local only" in blurb.lower(), (
            "%s is declared local-only but its description does not say so"
            % key)


@pytest.mark.parametrize("provider,model", [
    ("anthropic", "claude-opus-5-5"),
    ("openrouter", "anthropic/claude-sonnet-5"),
    ("google-gemini", "gemini-3-pro"),
    ("openai", "gpt-5"),
])
def test_saving_a_cloud_model_on_a_local_only_seat_is_refused(
        client, provider, model):
    """The UI must not be able to put the seat into the state in the screenshot."""
    r = client.post("/api/settings", json={"settings": {
        "capability_routing": {
            "memory_manager": {"model": model, "provider": provider},
        },
    }})
    assert r.status_code == 400, (
        "seating %s/%s on a local-only seat was accepted (HTTP %s)"
        % (provider, model, r.status_code))
    body = (r.get_json() or {})
    text = (str(body.get("detail") or "") + str(body.get("error") or "")
            + str(body.get("message") or "")).lower()
    assert "local" in text, "the refusal does not explain why: %r" % body


@pytest.mark.parametrize("provider,model", [
    ("ollama-local", "bonsai2:27b"),
    ("llama-cpp-local", "bonsai2:27b"),
])
def test_saving_a_local_model_on_a_local_only_seat_is_allowed(
        client, provider, model):
    """The rule must not block the thing it is asking for."""
    r = client.post("/api/settings", json={"settings": {
        "capability_routing": {
            "memory_manager": {"model": model, "provider": provider},
        },
    }})
    assert r.status_code == 200, (
        "seating a LOCAL model on the local-only seat was refused: %s"
        % r.get_json())


def test_other_seats_are_not_restricted(client):
    """Only declared seats are restricted. `reasoning` is cloud by design."""
    r = client.post("/api/settings", json={"settings": {
        "capability_routing": {
            "reasoning": {"model": "claude-opus-5-5", "provider": "anthropic"},
        },
    }})
    assert r.status_code == 200, r.get_json()


# ─────────────────────────────────────────────────────────────────────────────
# The runtime half: refuse, and SAY so.
# ─────────────────────────────────────────────────────────────────────────────

def test_the_runtime_still_refuses_a_cloud_seat(monkeypatch):
    """Belt and braces: even if a cloud provider reaches the seat by some other
    path (a hand-edited settings.json), the call must not go out."""
    from agent_friday.services import memory_proposals as mp
    with pytest.raises(mp.SeatUnavailable) as exc:
        mp._ask_seat("prompt", "claude-opus-5-5", "anthropic")
    assert "local" in str(exc.value).lower()


def test_a_refusal_is_announced_not_just_logged(monkeypatch):
    """A capability that silently does nothing is the failure mode here: the
    Memory keeper was inert for weeks and the only trace was a log line nobody
    read."""
    from agent_friday.services import memory_proposals as mp

    pushed = []
    monkeypatch.setattr(mp, "_notify_seat_refusal",
                        lambda reason: pushed.append(reason), raising=False)
    monkeypatch.setattr(mp, "seat", lambda: {
        "assigned": True, "model": "claude-opus-5-5", "provider": "anthropic",
        "reason": ""})
    # `propose()` pulls the day's turns via memory_dreaming._pull_turns, and
    # returns ok=True with "No conversation turns found" when there are none --
    # so without real turns the seat is never consulted and no refusal happens.
    # The first version of this test patched a name that does not exist (with
    # raising=False, so it passed silently) and asserted against that early
    # return instead of the refusal.
    from agent_friday.services import memory_dreaming as md
    monkeypatch.setattr(md, "_pull_turns",
                        lambda memory, day: ([{"role": "user",
                                               "text": "hello"}], False))

    out = mp.propose(day="2026-09-24")

    assert out["ok"] is False
    assert "local" in (out.get("reason") or "").lower()
    assert pushed, "the refusal was not announced to the user"


def test_no_seat_declared_local_only_is_currently_on_a_cloud_provider():
    """The audit, as a standing check.

    Seven seats have drifted from a local DEFAULT to a cloud provider
    (embedding, function_manager, heavy_hitter, memory_manager, orchestrator,
    researcher, sidekick_fast). Drifting from a default is allowed -- picking a
    model is the user's call. Contradicting a local-ONLY declaration is not, and
    that is all this asserts.
    """
    from agent_friday.services import seat_policy
    from agent_friday.core import DEFAULT_SETTINGS

    cr = (DEFAULT_SETTINGS.get("capability_routing") or {})
    for key in seat_policy.LOCAL_ONLY_SEATS:
        prov = ((cr.get(key) or {}).get("provider") or "").strip()
        assert seat_policy.is_local_provider_name(prov), (
            "the SHIPPED default for local-only seat %r is %r, which is not "
            "local" % (key, prov))
