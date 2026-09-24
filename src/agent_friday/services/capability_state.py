"""capability_state — one live answer to "what can Friday actually do right now",
with ABSENT and UNCONFIGURED kept apart.

THE DEFECT THIS EXISTS TO END (several surfaces, one disease):

  * The seat control announced a model change it never made.
  * The settings screen asserted Google accounts were connected when they
    were not.
  * Firecrawl and Brave web search both reported as nonexistent -- the model
    told the user "I don't have Firecrawl wired up as a tool right now,
    nothing in my toolkit is named that" -- when they were merely unkeyed.

Each surface answered a live question from something other than the live
source, and each collapsed two different states into one word. A capability
that is built but lacks its key is not absent; it is unconfigured, and the
only useful thing to say about it is WHICH variable would fix it.

THE VOCABULARY, and it is closed:

  working              proven by a real call (a search that returned results,
                       a seat that answered a completion)
  present_unverified   installed and keyed, no proof yet
  present_failing      installed and keyed, and the last real attempt failed
  unconfigured         built and wired in, missing a key or a setting;
                       `needs` names it
  absent               not installed on this machine

`describe_for_model()` renders that as a prompt block that rides in the
VOLATILE tail (after the clock), so it is read live every turn and never
churns the cached prefix. Every probe here calls something live -- a key
lookup, a store read, an endpoint cache -- and none of them touches the
network: a status block that phoned out on every turn would be its own
problem (web_search's health model already records proofs as they happen).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger("friday.capability_state")

WORKING = "working"
PRESENT_UNVERIFIED = "present_unverified"
PRESENT_FAILING = "present_failing"
UNCONFIGURED = "unconfigured"
ABSENT = "absent"
STATES = (WORKING, PRESENT_UNVERIFIED, PRESENT_FAILING, UNCONFIGURED, ABSENT)

_WORDS = {
    WORKING: "working",
    PRESENT_UNVERIFIED: "present, unverified",
    PRESENT_FAILING: "present, failing",
    UNCONFIGURED: "unconfigured",
    ABSENT: "absent",
}


@dataclass(frozen=True)
class CapabilityState:
    key: str
    label: str
    state: str
    needs: tuple = field(default_factory=tuple)   # env vars / settings keys
    fix: str = ""                                  # one sentence, user-facing
    detail: str = ""                               # what was observed

    def line(self) -> str:
        word = _WORDS.get(self.state, self.state)
        out = f"- {self.label}: {word}"
        if self.detail:
            out += f" — {self.detail}"
        if self.state == UNCONFIGURED and self.fix:
            out += f". {self.fix}"
        return out


# ── probes ───────────────────────────────────────────────────────────────────

def _keyed_backend(key, label, configured, health, env_var, settings_key,
                   how_to_prove) -> CapabilityState:
    needs = (env_var, settings_key)
    fix = (f"To enable it, set {env_var} in your environment (start.bat, beside "
           f"the other keys) or `{settings_key}` in settings.json, then restart "
           f"Friday")
    if not configured:
        return CapabilityState(key, label, UNCONFIGURED, needs, fix,
                               "wired in, no API key on this machine")
    st = (health or {}).get("state") or ""
    detail = str((health or {}).get("detail") or "").strip()
    if st == "working":
        return CapabilityState(key, label, WORKING, needs, "",
                               detail or f"proven by {how_to_prove}")
    if st == "present_but_failing":
        return CapabilityState(key, label, PRESENT_FAILING, needs, "",
                               detail or "the last real call failed")
    return CapabilityState(key, label, PRESENT_UNVERIFIED, needs, "",
                           "keyed; not yet proven by a real call this session")


def brave_state() -> CapabilityState:
    try:
        from agent_friday.services import web_search as ws
        return _keyed_backend("brave", "Web search: Brave", bool(ws.brave_key()),
                              ws.health_state(), "BRAVE_SEARCH_API_KEY",
                              "brave_search_api_key", "a live search")
    except Exception as e:  # noqa: BLE001
        return CapabilityState("brave", "Web search: Brave", ABSENT, (), "",
                               f"module unavailable: {type(e).__name__}")


def firecrawl_state() -> CapabilityState:
    try:
        from agent_friday.services import web_search as ws
        from agent_friday.services import firecrawl as fc
        return _keyed_backend("firecrawl", "Web search / page fetch: Firecrawl",
                              bool(fc.configured()), ws.firecrawl_health(),
                              "FIRECRAWL_API_KEY", "firecrawl_api_key",
                              "a live search")
    except Exception as e:  # noqa: BLE001
        return CapabilityState("firecrawl", "Web search / page fetch: Firecrawl",
                               ABSENT, (), "", f"module unavailable: {type(e).__name__}")


def duckduckgo_state() -> CapabilityState:
    return CapabilityState(
        "duckduckgo", "Web search: DuckDuckGo scrape (last resort)",
        PRESENT_UNVERIFIED, (), "",
        "no key needed; often answers an anti-bot challenge (HTTP 202) instead "
        "of results")


def local_brain_state() -> CapabilityState:
    try:
        from agent_friday.services import local_seats
        from agent_friday.services.local_call import seat_endpoint
        seat = local_seats.resolve("brain")
        if not seat:
            return CapabilityState("local_brain", "Local model (brain seat)", ABSENT,
                                   (), "", "no local model is installed")
        base = seat_endpoint(seat)
        if base:
            return CapabilityState("local_brain", "Local model (brain seat)", WORKING,
                                   (), "", f"{seat} serving at {base}")
        return CapabilityState("local_brain", "Local model (brain seat)",
                               PRESENT_FAILING, (), "",
                               f"{seat} is installed but nothing is serving it "
                               "right now")
    except Exception as e:  # noqa: BLE001
        return CapabilityState("local_brain", "Local model (brain seat)", ABSENT,
                               (), "", f"could not be read: {type(e).__name__}")


def google_accounts_state() -> CapabilityState:
    """Same live source as live_state's probe: the account store, read now."""
    label = "Google accounts (Gmail, Calendar, Drive)"
    try:
        from agent_friday.services import google_accounts as ga
        s = ga.accounts_summary()
    except Exception as e:  # noqa: BLE001
        return CapabilityState("google", label, ABSENT, (), "",
                               f"account store unreadable: {type(e).__name__}; "
                               "say you cannot tell")
    fix = "Settings -> Accounts & Keys -> Google -> Connect / Reconnect"
    if s.get("total", 0) == 0:
        return CapabilityState("google", label, UNCONFIGURED, ("google account",),
                               f"To enable it, connect an account under {fix}",
                               "no Google account has been connected")
    names = "; ".join(f"{a.get('email') or a.get('label')} ({a.get('summary')})"
                      for a in (s.get("needs_attention") or []))
    if not s.get("connected"):
        return CapabilityState("google", label, PRESENT_FAILING, (), "",
                               f"0 of {s['total']} account(s) working; not connected: "
                               f"{names}; reconnect under {fix}")
    if s.get("degraded"):
        return CapabilityState("google", label, PRESENT_FAILING, (), "",
                               f"{s['healthy']} of {s['total']} working; broken: {names}")
    return CapabilityState("google", label, WORKING, (), "",
                           f"all {s['total']} account(s) connected and working")


PROBES = (firecrawl_state, brave_state, duckduckgo_state, local_brain_state,
          google_accounts_state)


def snapshot() -> list:
    out = []
    for probe in PROBES:
        try:
            out.append(probe())
        except Exception as e:  # noqa: BLE001
            log.warning("capability probe %s failed: %s", getattr(probe, "__name__", probe), e)
    return out


def as_dicts() -> list:
    return [{"key": c.key, "label": c.label, "state": c.state,
             "needs": list(c.needs), "fix": c.fix, "detail": c.detail}
            for c in snapshot()]


RULE = (
    "Every capability listed here EXISTS in your toolkit. 'unconfigured' means "
    "it is wired in and lacks a key or setting: say exactly that and name the "
    "variable; NEVER say it is not part of your toolkit or does not exist. "
    "'absent' means it is genuinely not installed on this machine. 'present, "
    "failing' means it is keyed or installed and not working right now, with "
    "the reason. This block is read live on every turn; it outranks anything "
    "you remember or were told earlier about these capabilities.")


def describe_for_model(states=None) -> str:
    states = snapshot() if states is None else list(states)
    if not states:
        return ""
    return ("== CAPABILITY STATE (live, read now) ==\n" + RULE + "\n"
            + "\n".join(c.line() for c in states) + "\n")


def unconfigured_backends_note() -> str:
    """The sentence a failed web search carries when keyed backends were
    skipped: which ones, and what would enable each."""
    parts = []
    for st in (firecrawl_state(), brave_state()):
        if st.state == UNCONFIGURED:
            parts.append(f"{st.label.split(': ')[-1]} is wired in as a search backend but "
                         f"has no API key on this machine, so it was not tried. {st.fix}.")
    if not parts:
        return ""
    return (" ".join(parts) + " Do not tell the user that a backend named here is "
            "unavailable or not a tool: it exists and is unconfigured.")
