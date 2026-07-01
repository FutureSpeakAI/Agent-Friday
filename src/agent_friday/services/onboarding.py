"""
Agent Friday — Voice-First Onboarding
FutureSpeak.AI · Asimov's Mind

On first run (no ``~/.friday/.setup_complete``), Friday greets by voice and walks
the user through setup: name → voice test → optional API keys → federation
identity → SOUL.md. This module owns the state machine and the *lines Friday
speaks*; the UI drives TTS/mic using the existing local voice engine, so
onboarding talks with ZERO cloud keys.

The step lines are pure functions (unit-testable). ``complete()`` writes the
setup marker, ensures the Ed25519 federation identity exists, and seeds SOUL.md
from the user's stated preferences.

Leaf module — no Flask. Returns envelopes; never raises to the caller.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_HOME = Path(os.environ.get("FRIDAY_HOME") or Path.home())
FRIDAY_DIR = _HOME / ".friday"
STATE_PATH = FRIDAY_DIR / "onboarding.json"
SETUP_MARKER = FRIDAY_DIR / ".setup_complete"

# Ordered steps. Each maps to a spoken line + the field it collects.
STEPS: List[str] = ["greet", "name", "voice_test", "keys", "identity", "soul", "done"]


def _greeting() -> str:
    return ("Hi, I'm Friday. I'm your personal AI, and I run right here on your "
            "computer — no cloud required. Let me help you get set up. "
            "What should I call you?")


def line_for(step: str, state: Optional[Dict[str, Any]] = None) -> str:
    """The line Friday speaks at `step`. Pure — depends only on step + state."""
    state = state or {}
    name = state.get("name") or "boss"
    lines = {
        "greet": _greeting(),
        "name": _greeting(),  # greet + name are the same prompt turn
        "voice_test": (f"Great to meet you, {name}. Let's make sure you can hear me "
                       "and I can hear you. Say something back to me."),
        "keys": (f"You're all set to chat with me locally, {name}. If you have "
                 "cloud API keys — for Anthropic or Google — you can add them now "
                 "for sharper answers, images, and richer voice. Or skip; "
                 "everything works locally without them."),
        "identity": ("I'm generating your sovereign identity — a cryptographic key "
                     "that's yours alone, so you can connect to peers securely later."),
        "soul": (f"Last thing, {name}. I've written my personality to a file called "
                 "SOUL.md that you can edit any time. Want to tell me in a sentence "
                 "how you'd like me to sound?"),
        "done": (f"All set, {name}. I'm ready. Ask me anything — or just tell me "
                 "what you're working on."),
    }
    return lines.get(step, "")


def next_step(step: str) -> str:
    try:
        i = STEPS.index(step)
        return STEPS[min(i + 1, len(STEPS) - 1)]
    except ValueError:
        return "greet"


# ── state persistence ─────────────────────────────────────────────────────────
def _default_state() -> Dict[str, Any]:
    return {
        "step": "greet", "name": "", "voice_pref": "", "keys_added": [],
        "identity_pubkey": "", "complete": False, "started_ts": time.time(),
    }


def load_state() -> Dict[str, Any]:
    try:
        if STATE_PATH.exists():
            s = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            base = _default_state()
            base.update(s or {})
            return base
    except Exception:
        pass
    return _default_state()


def _save_state(state: Dict[str, Any]) -> None:
    try:
        FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, STATE_PATH)
    except Exception:
        pass


def is_complete() -> bool:
    return SETUP_MARKER.exists() or load_state().get("complete", False)


# ── API ───────────────────────────────────────────────────────────────────────
def get_state() -> Dict[str, Any]:
    """Current step + the line Friday should speak."""
    state = load_state()
    step = state.get("step", "greet")
    return {
        "step": step,
        "line": line_for(step, state),
        "steps": STEPS,
        "complete": is_complete(),
        "name": state.get("name", ""),
    }


def advance(answer: str = "", *, key_provider: str = "", key_value: str = "") -> Dict[str, Any]:
    """Advance the state machine with the user's answer to the current step."""
    # Normalize once: a client POSTing {"answer": null} passes None through
    # dict.get(...,"") (present key → not the default), and None.strip() would
    # raise, violating the "never raises" contract. Guard all steps here.
    answer = (answer or "")
    key_provider = (key_provider or "")
    key_value = (key_value or "")
    state = load_state()
    step = state.get("step", "greet")
    warning = None

    if step in ("greet", "name"):
        if answer.strip():
            state["name"] = answer.strip()[:60]
        state["step"] = "voice_test"
    elif step == "voice_test":
        state["voice_pref"] = answer.strip()[:120]
        state["step"] = "keys"
    elif step == "keys":
        if key_provider and key_value:
            saved = _save_key(key_provider, key_value)
            if saved:
                added = set(state.get("keys_added") or [])
                added.add(key_provider)
                state["keys_added"] = sorted(added)
            else:
                # Don't advance as if the key was stored — surface the failure so
                # the user knows to re-enter it (their cloud calls would otherwise
                # fail with a key they believe they provided).
                warning = ("Your API key could not be stored securely — please "
                           "re-enter it, or skip and add it later in Settings.")
        state["step"] = "identity"
    elif step == "identity":
        state["identity_pubkey"] = _ensure_identity()
        state["step"] = "soul"
    elif step == "soul":
        if answer.strip():
            _seed_soul_from_pref(state.get("name", ""), answer.strip())
        state["step"] = "done"
    elif step == "done":
        return complete()

    _save_state(state)
    new_step = state["step"]
    out = {"ok": True, "step": new_step, "line": line_for(new_step, state),
           "complete": is_complete()}
    if warning:
        out["warning"] = warning
        out["key_saved"] = False
    return out


def complete() -> Dict[str, Any]:
    """Finalize onboarding: marker + identity + SOUL.md ensured."""
    state = load_state()
    state["complete"] = True
    state["step"] = "done"
    if not state.get("identity_pubkey"):
        state["identity_pubkey"] = _ensure_identity()
    _save_state(state)
    try:
        FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
        SETUP_MARKER.write_text("onboarded", encoding="utf-8")
    except Exception:
        pass
    try:
        from agent_friday.services import soul
        soul.ensure_soul()
    except Exception:
        pass
    return {"ok": True, "complete": True, "line": line_for("done", state),
            "identity": state.get("identity_pubkey", "")}


# ── internals ─────────────────────────────────────────────────────────────────
def _save_key(provider: str, value: str) -> bool:
    try:
        from agent_friday.services import credential_store
        prov = {"anthropic": "anthropic", "google": "google-gemini",
                "gemini": "google-gemini", "openai": "openai"}.get(
            provider.strip().lower(), provider.strip().lower())
        credential_store.set_provider_key(prov, value)
        return True
    except Exception:
        return False


def _ensure_identity() -> str:
    try:
        from agent_friday.services import federation
        ident = federation.get_identity() or {}
        return ident.get("agent_id", "") or ""
    except Exception:
        return ""


def _seed_soul_from_pref(name: str, pref: str) -> None:
    """Write a SOUL.md that folds in the user's stated tone preference."""
    try:
        from agent_friday.services import soul
        base = soul.default_soul()
        addition = (f"\n\n## {name or 'The user'}'s note\n"
                    f"{pref}\n")
        soul.save_soul(base + addition)
    except Exception:
        pass
