"""Which seats may never be pointed at a paid API, in one place.

From a screenshot of Settings > Models, 2026-09-24: the "Memory keeper" seat read
"Reads the day and decides what is worth keeping. Local only." while showing
"Claude Opus 5.5 - cloud". Nothing leaked -- `memory_proposals._ask_seat()` was
already refusing before any network call, its orb label appears zero times in
friday.log, and zero of 486,810 egress-ledger entries mention memory -- but the
UI had happily put the seat into a state where its whole purpose was disabled,
and nothing said so.

The fault was that the CLAIM lived in a description string and the RULE lived in
an `if` a thousand lines away. Two copies of one fact drift; these did. So the
declaration lives here, and the label, the save-time refusal and the runtime
refusal all read it.

WHAT THIS IS NOT. `routes/core_routes._check_local_model_seat_gate` is a no-op by
maintainer decision, because it refused a user's chosen model for failing a
homegrown quality eval -- gating someone's own pick behind our benchmark. This is
a much narrower claim: a seat that exists SPECIFICALLY so that a body of private
text never leaves the machine cannot be aimed off the machine. That is coherence,
not quality, and the user can still change it -- by changing the declaration,
which is a code change with a reason, rather than by a dropdown that silently
turns the feature off.
"""

from __future__ import annotations

#: Seats whose entire reason for existing is that their input never leaves this
#: machine. Keyed by `capability_routing` key.
#:
#: `memory_manager` (the Memory keeper) reads the user's WHOLE conversation
#: history for a day to decide what is worth remembering. That is the most
#: private corpus Friday holds, and shipping it to a paid API is not something
#: anyone should get by accident from a dropdown.
LOCAL_ONLY_SEATS = frozenset({"memory_manager"})

#: Provider names that mean "on this machine". Mirrors
#: `ModelRouter._LOCAL_PROVIDERS` and `memory_proposals._is_local`, and is read
#: in preference to either so there is one answer to the question.
LOCAL_PROVIDER_NAMES = frozenset({
    "ollama-local", "arbiter-local", "llama-cpp-local", "local",
    "local-comfyui", "local-voice", "local-voice-lite", "nemo-local",
})


def is_local_provider_name(provider) -> bool:
    """True when `provider` names something that runs on this machine.

    Name-based on purpose: this is asked about a SEAT ASSIGNMENT in settings,
    before anything is dialled, so there is no host to re-resolve. The
    network-level question -- "is the thing I am about to talk to actually
    loopback" -- is `egress_gate.is_local_provider()`, which re-verifies the host
    at call time. Both exist; they answer different questions.
    """
    p = str(provider or "").strip().lower()
    if not p:
        return False
    if p in LOCAL_PROVIDER_NAMES:
        return True
    try:
        from agent_friday.routing.model_router import ModelRouter
        return p in {str(x).lower() for x in ModelRouter._LOCAL_PROVIDERS}
    except Exception:
        return False


def local_only_violations(capability_routing) -> list[dict]:
    """Every local-only seat currently aimed at a non-local provider.

    Returns [] when there is nothing wrong, so a caller can treat a truthy
    result as "refuse and explain".
    """
    out: list[dict] = []
    cr = capability_routing or {}
    for key in sorted(LOCAL_ONLY_SEATS):
        entry = cr.get(key)
        if not isinstance(entry, dict):
            continue
        model = (entry.get("model") or "").strip()
        provider = (entry.get("provider") or "").strip()
        if not model and not provider:
            continue          # unset is not a violation; it is just unassigned
        if is_local_provider_name(provider):
            continue
        out.append({
            "seat": key,
            "model": model,
            "provider": provider,
            "why": (
                "The %s seat is local-only: it reads a body of your private "
                "text and that text never leaves this machine. %s is a cloud "
                "provider, so this seat would refuse to run and the feature "
                "would be silently off. Pick a local model instead."
                % (key, provider or "that provider")),
        })
    return out
