"""Which seats may never be pointed at a paid API, in one place.

Settings > Models can show the "Memory keeper" seat reading "Reads the day and
decides what is worth keeping. Local only." while showing "Claude Opus 5.5 -
cloud". Nothing leaks -- `memory_proposals._ask_seat()` refuses before any
network call -- but the UI puts the seat into a state where its whole purpose is
disabled, and nothing says so.

The fault is the CLAIM living in a description string and the RULE living in an
`if` a thousand lines away. Two copies of one fact drift. So the
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

#: Internal local aliases used when no registered descriptor names the seat.
#: An actual descriptor takes precedence: a familiar name cannot make a public
#: endpoint local, and a custom name cannot make an on-device endpoint cloud.
LOCAL_PROVIDER_NAMES = frozenset({
    "ollama-local", "arbiter-local", "llama-cpp-local", "local",
    "local-comfyui", "local-voice", "local-voice-lite", "nemo-local",
})


def is_local_descriptor(descriptor) -> bool:
    """Use transport locality without overriding an explicit cloud declaration."""
    if not isinstance(descriptor, dict):
        return False
    claimed = str(descriptor.get("classification") or
                  descriptor.get("egress_classification") or "").strip().lower()
    if claimed == "cloud":
        return False
    try:
        from agent_friday.routing.provider_descriptors import classification_of
        return classification_of(descriptor) == "local"
    except Exception:
        return False


def is_local_provider_name(provider) -> bool:
    """True when `provider` names something that runs on this machine.

    Registered descriptors use the same adapter-and-address classification as
    the catalogue and transport. Legacy internal aliases remain valid when
    they have no descriptor; an unreadable registry does not verify locality.
    The outbound transport still checks the actual destination at call time.
    """
    name = str(provider or "").strip()
    p = name.lower()
    if not p:
        return False
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        registry = get_provider_registry()
        descriptor = registry.get_provider(name) or registry.get_provider(p)
        if descriptor is not None:
            return is_local_descriptor(descriptor)
    except Exception:
        return False
    if p in LOCAL_PROVIDER_NAMES:
        return True
    try:
        from agent_friday.routing.model_router import ModelRouter
        return p in {str(x).lower() for x in ModelRouter._LOCAL_PROVIDERS}
    except Exception:
        return False


def is_local_seat(provider, model="") -> bool:
    """A local transport with a model that is not relayed to a cloud service."""
    from agent_friday.services.local_only_guard import is_cloud_model_tag
    return not is_cloud_model_tag(model) and is_local_provider_name(provider)


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
        if is_local_seat(provider, model):
            continue
        out.append({
            "seat": key,
            "model": model,
            "provider": provider,
            "why": (
                "The %s seat is local-only: it reads a body of your private "
                "text and that text never leaves this machine. %s cannot be "
                "verified as a local model and provider, so this seat would "
                "refuse to run. Pick a local model instead."
                % (key, "/".join(s for s in (provider, model) if s) or "That selection")),
        })
    return out
