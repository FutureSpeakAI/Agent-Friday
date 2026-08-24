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
import logging
import time as _time

_log = logging.getLogger("friday.tool_budget")

# Tools that ARE Friday rather than a connector she happens to have.
_CONNECTOR_PREFIX = "mcp_"

# The share of a seat's context the tool preamble may occupy. The rest has to
# hold the system prompt, the transcript and the answer; at 0.4 a 32k seat
# spends ~13k on tools and keeps ~19k for the conversation.
_TOOL_SHARE = 0.4

# Reserved out of the window for the model's answer plus the chat template's
# rendering overhead (the gemma tool declarations expand beyond the raw JSON
# the chars/4 estimate sees). Without a reserve, a request budgeted to exactly
# the window is a request the server rejects.
_GEN_HEADROOM = 4608

# Only say it once per (model, decision) — this runs on every turn.
_ANNOUNCED: set = set()

# The served context, briefly cached per seat: one loopback GET per minute,
# not one per turn.
_SERVED_CACHE: dict = {}
_SERVED_TTL_S = 60.0


def _tokens(obj) -> int:
    """Rough token cost of a JSON payload. Four characters to a token."""
    try:
        return len(json.dumps(obj)) // 4
    except Exception:
        return 0


def _served_ctx(model_id: str) -> int | None:
    """What the seat's server says it is serving, or None when unreachable.

    The server is the authority — the same principle `local_call._serves`
    already states for model identity, applied to context size. The plan is a
    record of intent and it drifts: measured 2026-08-19, the plan said
    gemma4:e4b at 65,536 while `_spawn` had capped the actual llama-server to
    32,768 ("to keep the display reserve") and nothing wrote the cap back.
    Budgeting against the plan built a >32k request for a 32k seat, every
    local turn 400'd with `exceed_context_size_error`, and the router fell
    back to the cloud — the exact failure this module exists to prevent,
    reintroduced one layer deeper.
    """
    now = _time.time()
    hit = _SERVED_CACHE.get(model_id)
    if hit and (now - hit[0]) < _SERVED_TTL_S:
        return hit[1]
    base = None
    try:
        from agent_friday.services.local_call import seat_endpoint
        base = seat_endpoint(model_id)
    except Exception:
        base = None
    if not base:
        # Dispatch has a second resolution branch — a registered local
        # OpenAI-compatible descriptor (the llama.cpp brain wrinkle). A seat
        # only reachable that way must still be measured that way.
        try:
            from agent_friday.services.model_seat_gate import (
                _local_openai_descriptor)
            prov = _local_openai_descriptor(model_id)
            base = ((prov or {}).get("base_url") or "").rstrip("/") or None
        except Exception:
            base = None
    n = None
    if base:
        try:
            import urllib.request
            root = base[:-3] if base.endswith("/v1") else base
            with urllib.request.urlopen(f"{root}/props", timeout=2) as r:
                d = json.loads(r.read().decode())
            n = int((d.get("default_generation_settings") or {})
                    .get("n_ctx") or 0) or None
        except Exception:
            n = None
    _SERVED_CACHE[model_id] = (now, n)
    return n


def _spawn_cap(model_id: str) -> int | None:
    """The ceiling every arbiter-run llama-server seat is spawned under.

    `_spawn` caps num_ctx at MAX_SEAT_NUM_CTX "to keep the display reserve"
    and the plan is never told. A model with an extracted GGUF will be served
    under that cap whenever it is served locally, so when the live server
    cannot be asked, the plan's larger number is not a window anyone can use.
    """
    try:
        from agent_friday.services.residency_catalog import (
            canonical_model_id, gguf_models_canonical)
        if canonical_model_id(model_id) in gguf_models_canonical():
            from agent_friday.services.residency_arbiter import (
                LlamaServerBackend)
            return int(LlamaServerBackend.MAX_SEAT_NUM_CTX)
    except Exception:
        pass
    return None


def _window(model_id: str) -> int:
    """The context this seat is actually SERVED at, not its architectural max.

    The distinction is the whole bug. `context_window_for()` reports what the
    model could do -- 131,072 for the Gemma-4 E4B -- while the daemon serves
    it at the `num_ctx` residency picked to keep the display alive on a 12 GB
    card: 32,768. Budgeting against the larger number is the same as not
    budgeting, which is how a 46k-token request got built for a 32k seat.

    Order of authority: the live server first (see `_served_ctx`), then the
    plan, then the catalog. Each step down is a step from fact toward intent.
    """
    served = _served_ctx(model_id)
    if served:
        return served
    try:
        from agent_friday.services.residency_policy import num_ctx_for_model
        planned = int(num_ctx_for_model(model_id) or 0)
        if planned > 0:
            cap = _spawn_cap(model_id)
            return min(planned, cap) if cap else planned
    except Exception:
        pass
    try:
        from agent_friday.services.model_catalog import context_window_for
        return int(context_window_for(model_id) or 0) or 8192
    except Exception:
        return 8192


#: Kept first when core tools have to be trimmed. Not "the most useful" — the
#: ones whose absence changes what a turn can HONESTLY do. Reading and
#: searching keep Friday able to ground an answer; writing and navigation keep
#: her able to finish a task she has been given. A turn that can neither look
#: something up nor say where it got it is worse than a turn with fewer tools.
_ESSENTIAL_TOOLS = frozenset({
    "search_web", "browse_web", "read_wiki", "search_wiki", "write_wiki",
    "query_calendar", "search_email", "open_path", "navigate",
})


def fit_tools_to_seat(model_id: str, tools: list, *, share: float = _TOOL_SHARE,
                      prompt_cost: int = 0):
    """Return the tools that fit this seat, plus a note when any were left out.

    Returns (tools, note). `note` is None when everything fit; otherwise it is
    a plain sentence suitable for the model's system prompt AND for telling
    Stephen, because a capability that quietly is not there is the failure
    this module exists to prevent.

    `prompt_cost` is the estimated token cost of everything else in the
    request — system prompt plus transcript. The share cap alone cannot
    protect a seat: at share 0.4 of a doubled window, tools "within budget"
    plus an ordinary prompt already exceeded what the seat could hold
    (measured 2026-08-19: 26,214 allowed + 8,116 prompt against 32,768
    served). The request is budgeted as a WHOLE or it is not budgeted.
    """
    tools = list(tools or [])
    if not model_id or not tools:
        return tools, None

    window = _window(model_id)
    # Tools may take their share of the window OR whatever the prompt and the
    # answer's headroom leave free — whichever is smaller. The share keeps
    # tools from crowding out conversation; the remainder keeps the sum an
    # actual request.
    budget = min(int(window * share),
                 window - int(prompt_cost or 0) - _GEN_HEADROOM)

    core = [t for t in tools if not str(t.get("name") or "").startswith(_CONNECTOR_PREFIX)]
    connectors = [t for t in tools if str(t.get("name") or "").startswith(_CONNECTOR_PREFIX)]

    core_cost = _tokens(core)
    conn_cost = _tokens(connectors)

    # A TRIMMER THAT COULD NOT TRIM THE THING THAT WAS TOO BIG.
    #
    # This function used to return `tools, None` unchanged whenever there were
    # no connector tools — no matter how far over budget the request was — and
    # otherwise dropped connectors and kept every core tool regardless. So its
    # only lever was connectors. Measured on this machine 2026-08-24 via
    # /api/residency/status: tool_tokens 47,579 and system_prompt_tokens 13,626
    # against a 32,768-token seat. Friday's OWN tools do not fit the window by
    # themselves; dropping connectors cannot save a request that is already
    # over on core.
    #
    # Three real failures came from this in one morning — 38,232 and 38,713
    # tokens into 32,768, twice on a briefing chain and once on a
    # distill-to-wiki pass. Each died as a provider 400 with no explanation the
    # user could act on.
    #
    # Core tools are now droppable too, lowest value first, and the caller is
    # told plainly when even an empty tool list will not fit — because at that
    # point the prompt is the problem and no amount of tool trimming is the
    # answer.
    if core_cost + conn_cost <= budget:
        return tools, None

    if budget <= 0:
        # The prompt alone has eaten the window. Report it as such: this is not
        # a tools problem and pretending otherwise sends the caller round a
        # loop that cannot terminate.
        return [], (
            "No tools were loaded: the request itself is about %s tokens "
            "against %s's %s-token window, before any tool definitions. "
            "Shorten the input or use a larger-context seat."
            % (f"{int(prompt_cost or 0):,}", model_id, f"{window:,}"))

    note = (f"{len(connectors)} connector tools are not loaded on this seat: "
            f"their definitions cost about {conn_cost:,} tokens and "
            f"{model_id} has a {window:,}-token window. Ask me on a "
            f"larger-context seat if you need them.")

    sig = (model_id, len(connectors))
    if sig not in _ANNOUNCED:
        _ANNOUNCED.add(sig)
        _log.info("%s: dropped %d connector tool(s) (~%d tokens) to fit a "
                  "%d-token window; kept %d of Friday's own (~%d)",
                  model_id, len(connectors), conn_cost, window,
                  len(core), core_cost)
        print(f"  [tools] {model_id}: dropped {len(connectors)} connector tool(s) "
              f"(~{conn_cost:,} tokens) to fit a {window:,}-token window; "
              f"kept {len(core)} of Friday's own (~{core_cost:,})")

    if core_cost <= budget:
        return core, note

    # Core alone still overflows. This used to send them anyway and let the
    # seat 400 — "an honest 400 beats a silent trip to the cloud", which was
    # right about the cloud and wrong about the 400: on a vault turn there IS
    # no cloud to fall back to, so the honest 400 is simply the work not
    # happening. Trim core too, cheapest-value-first, and keep what a turn
    # cannot function without.
    kept, kept_cost = [], 0
    for t in sorted(core, key=lambda x: (str(x.get("name")) not in _ESSENTIAL_TOOLS,
                                         _tokens([x]))):
        c = _tokens([t])
        if kept_cost + c > budget:
            continue
        kept.append(t)
        kept_cost += c

    dropped = len(core) - len(kept)
    note = (f"This seat is small, so I am working with {len(kept)} of my "
            f"{len(core)} tools plus none of the {len(connectors)} connectors. "
            f"{model_id} has a {window:,}-token window and the full set costs "
            f"about {core_cost + conn_cost:,}. If I need something I do not "
            f"have here, ask me on a larger-context seat.")
    _log.warning("%s: core tools trimmed %d -> %d (~%d of ~%d tokens) to fit a "
                 "%d-token window with a ~%d-token prompt",
                 model_id, len(core), len(kept), kept_cost, core_cost,
                 window, int(prompt_cost or 0))
    print(f"  [tools] {model_id}: kept {len(kept)}/{len(core)} of Friday's own "
          f"tools (~{kept_cost:,} tokens), dropped {dropped} + "
          f"{len(connectors)} connectors to fit {window:,}")
    return kept, note
