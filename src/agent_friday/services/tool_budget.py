"""A tool registry that does not fit in the window is not a registry.

Measured on Stephen's machine, 2026-08-18:

    request (46288 tokens) exceeds the available context size (32768 tokens)

Every local turn returned HTTP 400 and the router fell back to Anthropic, so
the symptom he reported was "it took forever to reply then kicked back to
Sonnet 4.6 again, which I do not want". Nothing was wrong with the model, the
seat, the picker or the routing mode — the request simply could not be built.

The cause was arithmetic. Friday's own tools cost about 9.7k tokens. The
Higgsfield connector registered 86 more and GitHub 26, and their schemas cost
roughly 36k on top. Any seat with a 32k window was unreachable from the moment
those connectors came up, and the seat's context had deliberately been set to
32768 to keep the display alive on a 12 GB card — so raising it is not free.

So the tool payload is fitted to the seat instead. Friday's own tools always
travel: they are what she is. Connector tools are all-or-nothing per request,
because "some of the Higgsfield tools exist today" is a worse thing to explain
than "none of them do on this seat", and because a caller that can see half a
connector will try the half that is missing.
"""
from __future__ import annotations

import json

# Tools that ARE Friday rather than a connector she happens to have.
_CONNECTOR_PREFIX = "mcp_"

# The share of a seat's context the tool preamble may occupy. The rest has to
# hold the system prompt, the transcript and the answer; at 0.4 a 32k seat
# spends ~13k on tools and keeps ~19k for the conversation.
_TOOL_SHARE = 0.4

# Only say it once per (model, decision) — this runs on every turn.
_ANNOUNCED: set = set()


def _tokens(obj) -> int:
    """Rough token cost of a JSON payload. Four characters to a token."""
    try:
        return len(json.dumps(obj)) // 4
    except Exception:
        return 0


def _window(model_id: str) -> int:
    """The context this seat is actually SERVED at, not its architectural max.

    The distinction is the whole bug. `context_window_for()` reports what the
    model could do -- 131,072 for the Gemma-4 E4B -- while the daemon serves
    it at the `num_ctx` residency picked to keep the display alive on a 12 GB
    card: 32,768. Budgeting against the larger number is the same as not
    budgeting, which is how a 46k-token request got built for a 32k seat.
    """
    try:
        from agent_friday.services.residency_policy import num_ctx_for_model
        served = int(num_ctx_for_model(model_id) or 0)
        if served > 0:
            return served
    except Exception:
        pass
    try:
        from agent_friday.services.model_catalog import context_window_for
        return int(context_window_for(model_id) or 0) or 8192
    except Exception:
        return 8192


def fit_tools_to_seat(model_id: str, tools: list, *, share: float = _TOOL_SHARE):
    """Return the tools that fit this seat, plus a note when any were left out.

    Returns (tools, note). `note` is None when everything fit; otherwise it is
    a plain sentence suitable for the model's system prompt AND for telling
    Stephen, because a capability that quietly is not there is the failure
    this module exists to prevent.
    """
    tools = list(tools or [])
    if not model_id or not tools:
        return tools, None

    window = _window(model_id)
    budget = int(window * share)

    core = [t for t in tools if not str(t.get("name") or "").startswith(_CONNECTOR_PREFIX)]
    connectors = [t for t in tools if str(t.get("name") or "").startswith(_CONNECTOR_PREFIX)]
    if not connectors:
        return tools, None

    core_cost = _tokens(core)
    conn_cost = _tokens(connectors)

    if core_cost + conn_cost <= budget:
        return tools, None

    note = (f"{len(connectors)} connector tools are not loaded on this seat: "
            f"their definitions cost about {conn_cost:,} tokens and "
            f"{model_id} has a {window:,}-token window. Ask me on a "
            f"larger-context seat if you need them.")

    sig = (model_id, len(connectors))
    if sig not in _ANNOUNCED:
        _ANNOUNCED.add(sig)
        print(f"  [tools] {model_id}: dropped {len(connectors)} connector tool(s) "
              f"(~{conn_cost:,} tokens) to fit a {window:,}-token window; "
              f"kept {len(core)} of Friday's own (~{core_cost:,})")

    if core_cost > budget:
        # Nothing left to trim without taking away what Friday IS. Send them
        # and let the seat complain — an honest 400 naming the real number
        # beats a silent trip to a cloud model.
        print(f"  [tools] WARNING {model_id}: Friday's own tools alone cost "
              f"~{core_cost:,} tokens against a {window:,}-token window")

    return core, note
