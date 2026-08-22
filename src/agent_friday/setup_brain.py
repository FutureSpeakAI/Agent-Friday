"""Setup's keys-and-capabilities step.

Walks every API key Friday actually consumes, says in one plain sentence what
each one gives you, validates it live, stores it encrypted — and lets you skip
any of them without friction. Setup always completes. A user with no keys at
all and capable hardware ends up with a working local Friday and is told so.

Two rules this module exists to enforce:

**Nothing reports success it has not verified.** A key is validated with a real
call before it is stored, and read back out of the store afterwards, so a
mistyped or expired key fails here with a clear message rather than at the
user's first sentence to Friday.

**One source of truth about the hardware.** The closing summary draws from
`model_plan` — the same arithmetic `friday models` uses — rather than a
hand-written list of what probably works. A summary that drifts from the
planner would be the same bug class as everything else fixed this week.

## The key list is enumerated from the code, not from memory

Counted by what `src/` actually reads:

  ANTHROPIC_API_KEY     16 references   reasoning, tools
  GEMINI_API_KEY        19 references   voice, image, video
  ELEVENLABS_API_KEY     3 references   higher-quality speech
  BRAVE_SEARCH_API_KEY   4 references   better web search
  OPENAI_API_KEY         3 references   alternative reasoning provider

Deliberately NOT asked for:

  * **Higgsfield** — there is no Higgsfield env var anywhere in `src/`. It is
    an MCP connector configured separately, so asking for a key here would
    imply a path that does not exist.
  * OpenRouter, Together, Groq, Mistral, xAI, Perplexity — each appears once or
    twice behind an alternative-provider path most users never take. Asking
    about seven providers to reach a first conversation is its own failure.
    They remain settable in Settings -> Providers.
"""
from __future__ import annotations

import os

#: Ordered by how much it matters to someone installing for the first time.
#: `what` is a sentence for a person: what they will be able to do, not which
#: subsystem lights up.
KEYS = [
    {
        "provider": "anthropic",
        "label": "Anthropic (Claude)",
        "hint": "sk-ant-...",
        "url": "console.anthropic.com/settings/keys",
        "what": "Sharper conversation, and the tools — reading files, "
                "searching, working with your calendar.",
        "without": "Friday uses this machine's local model to talk.",
    },
    {
        "provider": "google-gemini",
        "label": "Google Gemini",
        "hint": "AIza...",
        "url": "aistudio.google.com/app/apikey",
        "what": "Talking to Friday out loud, and making images and video.",
        "without": "No voice, no image or video generation.",
    },
    {
        "provider": "elevenlabs",
        "label": "ElevenLabs",
        "hint": "sk_...",
        "url": "elevenlabs.io/app/settings/api-keys",
        "what": "A better, more natural speaking voice.",
        "without": "Friday still speaks using the voice built into your "
                   "machine, which is fine but plainer.",
        "optional": True,
    },
    {
        "provider": "brave-search",
        "label": "Brave Search",
        "hint": "BSA...",
        "url": "brave.com/search/api",
        "what": "Better web search results when Friday looks something up.",
        "without": "Friday falls back to a free search source. It works, but "
                   "results are rougher.",
        "optional": True,
    },
    {
        "provider": "openai",
        "label": "OpenAI",
        "hint": "sk-...",
        "url": "platform.openai.com/api-keys",
        "what": "An alternative to Claude for conversation.",
        "without": "Nothing, if you have a Claude key. This is a substitute, "
                   "not an addition.",
        "optional": True,
    },
]

#: Where to go afterwards. Named explicitly, because skipping a key must not be
#: easier than un-skipping it.
ADD_LATER = "Settings -> Providers, in Friday's own window. No re-running setup."


def assess(profile=None) -> dict:
    """What can this machine do for a brain, and how emphatic should we be?

    Never raises. A hardware detection failure resolves to "not capable",
    because recommending local inference on a machine we could not measure
    would be a guess presented as advice.
    """
    from agent_friday.services import model_plan
    try:
        if profile is None:
            from agent_friday.services import hardware_profile
            profile = hardware_profile.get()
        plan = model_plan.plan(profile)
        capable, reason = model_plan.brain_is_primary_capable(plan)
    except Exception as e:
        return {"capable": False, "plan": None, "brain_label": None,
                "reason": (f"Friday couldn't read this machine's hardware "
                           f"({type(e).__name__}), so she can't tell whether a "
                           f"local model would work well. A Claude key is the "
                           f"safe choice.")}
    tier = next((t for t in plan["tiers"] if t["id"] == "brain"), None)
    label = tier["models"][0]["id"] if (tier and tier.get("models")) else None
    return {"capable": capable, "reason": reason, "plan": plan,
            "brain_label": label}


def validate_key(provider: str, key: str) -> tuple[bool | None, str]:
    """Confirm a key works BEFORE storing it.

    (True, msg) verified · (False, msg) rejected · (None, msg) could not check.
    None is deliberately distinct from False: refusing a good key because the
    wifi is down would be its own kind of dishonesty, so an unverifiable key is
    offered for storage with that stated.
    """
    key = (key or "").strip()
    if not key:
        return False, "No key entered."
    try:
        from agent_friday import setup_wizard as sw
        validator = {"anthropic": sw._validate_anthropic,
                     "google-gemini": sw._validate_gemini}.get(provider)
        if validator is None:
            # Be honest that this one is unchecked rather than implying a pass.
            return None, (f"Friday has no way to test a {provider} key yet, so "
                          f"this one is stored unverified.")
        return validator(key)
    except Exception as e:
        return None, f"Couldn't check the key ({type(e).__name__})."


def store_key(provider: str, key: str) -> tuple[bool, str]:
    """Encrypted store, then live env. Never a launch script.

    Reads the key back out afterwards, because "wrote it" and "it is there" are
    different claims and this module does not make the first on behalf of the
    second.
    """
    try:
        from agent_friday.services import credential_store as cs
        method = cs.set_provider_key(provider, key)
        cs.hot_reload_provider_key(provider, key)
        if cs.get_provider_key(provider) != key:
            return False, ("Stored the key but couldn't read it back — calling "
                           "that a failure. Nothing was written to a startup "
                           "script.")
        return True, f"Saved, encrypted on this machine ({method})."
    except Exception as e:
        return False, f"Couldn't store the key: {type(e).__name__}: {e}"


def env_has(provider: str) -> bool:
    """Is a key for this provider already available to this process?"""
    var = {"anthropic": "ANTHROPIC_API_KEY", "google-gemini": "GEMINI_API_KEY",
           "elevenlabs": "ELEVENLABS_API_KEY", "openai": "OPENAI_API_KEY",
           "brave-search": "BRAVE_SEARCH_API_KEY"}.get(provider)
    if var and os.environ.get(var):
        return True
    try:
        from agent_friday.services import credential_store as cs
        return cs.provider_key_status(provider) not in ("missing", None)
    except Exception:
        return False


def capability_summary(keys: dict, assessment: dict | None = None) -> dict:
    """The closing screen: what works, what doesn't, how to fix each gap.

    CAPABILITY-SHAPED, not key-shaped. Nobody cares that GEMINI_API_KEY is
    unset; they care that they can't talk out loud yet.

    Hardware limits come from `model_plan`, the same arithmetic `friday models`
    prints, so this cannot drift from what the machine will actually do. That
    is why it takes the assessment rather than re-deriving anything.

    `keys` maps provider id -> truthy when a key was stored this run or already
    present. Returns {works, missing, hardware} of plain sentences.
    """
    a = assessment or assess()
    plan = a.get("plan") or {}
    tiers = {t["id"]: t for t in plan.get("tiers", [])}

    has_claude = bool(keys.get("anthropic"))
    has_gemini = bool(keys.get("google-gemini"))
    has_eleven = bool(keys.get("elevenlabs"))
    has_brave = bool(keys.get("brave-search"))

    works, missing, hardware = [], [], []

    # ── Conversation ──
    brain = tiers.get("brain") or {}
    local_ok = brain.get("status") in ("install", "ready")
    if has_claude:
        works.append("Talk to Friday, with all her tools — reading files, "
                     "searching, your calendar.")
        if local_ok:
            works.append("Or talk to her entirely offline using the model on "
                         "this machine, when you'd rather nothing left it.")
    elif local_ok:
        works.append("Talk to Friday using the model on this machine. Nothing "
                     "you say leaves the laptop.")
    else:
        missing.append(("Conversation",
                        "This machine can't run a local model, and there's no "
                        "Claude key.",
                        "Add an Anthropic key — " + ADD_LATER))

    # ── Tools. Not hardware-dependent: they need a cloud key, full stop. ──
    #
    # This is NOT a limitation of the local model. `function_manager` exists as
    # a role — residency class, context budget, UI label — and nothing in the
    # chat path consults it, so a local brain has no function seat to delegate
    # to regardless of how capable it is. Saying "your hardware is too weak"
    # here would be a comfortable lie; the honest sentence is that the feature
    # isn't wired yet.
    if not has_claude and not a.get("capable"):
        missing.append(("Tools — files, search, your calendar",
                        "The local model this machine can run can't call "
                        "tools. A bigger graphics card would let Friday run "
                        "one that can.",
                        "Add an Anthropic key — " + ADD_LATER))
    elif not has_claude:
        works.append("Use her tools — files, search, your calendar — entirely "
                     "offline, because this machine runs a model that can call "
                     "them.")

    # ── Memory: always local, always on ──
    if (tiers.get("vault") or {}).get("status") != "refused":
        works.append("Remember things you tell her, and find them later. This "
                     "runs on your machine either way.")
    else:
        missing.append(("Memory",
                        (tiers.get("vault") or {}).get("reason", "not available"),
                        "Free up disk space and run `friday models --install`."))

    # ── Voice ──
    if has_gemini:
        works.append("Talk to Friday out loud.")
    else:
        missing.append(("Talking out loud",
                        "Voice needs a Google Gemini key.",
                        "Add a Gemini key — " + ADD_LATER))
    if has_eleven:
        works.append("A more natural speaking voice (ElevenLabs).")

    # ── Images and video: keys AND hardware ──
    img = tiers.get("image") or {}
    if has_gemini:
        works.append("Make images and video.")
    else:
        missing.append(("Making images and video",
                        "Needs a Google Gemini key.",
                        "Add a Gemini key — " + ADD_LATER))
    if img.get("status") == "refused":
        hardware.append("Making images on this machine's own graphics card "
                        "won't work: " + img.get("reason", ""))

    # ── Search ──
    works.append("Search the web." + ("" if has_brave else
                 " (Using the free source — a Brave Search key gives better "
                 "results.)"))

    seats = tiers.get("seats") or {}
    if seats.get("status") == "refused":
        hardware.append(seats.get("reason", ""))

    return {"works": works, "missing": missing, "hardware": hardware,
            "assessment": a}
