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
#:
#: The membership rule, so the next person does not have to guess at it: a tool
#: belongs here if FRIDAY_SYSTEM_PROMPT makes an explicit capability PROMISE
#: about it, or if its absence makes the model narrate instead of act.
#:
#:   * The prompt's "WHAT YOU CAN DO ON THIS COMPUTER" section says of the
#:     browser and file tools: "NEVER tell the user you can't open a browser
#:     tab, a website, a file, or an app — you can, and these tools are how."
#:     Trimming open_url, open_path, navigate, read_file or write_file while
#:     that sentence stands turns the prompt into an instruction to lie.
#:   * spawn_task is how any job longer than ~10s gets done, and the prompt
#:     tells the model to reach for it by name. Without it the model says
#:     "Started — track it in the task tray" over a task that was never
#:     started. That is the reported failure mode, verbatim.
#:
#: `write_wiki` was listed here and HAS NEVER EXISTED in the registry — the
#: wiki write path is propose_wiki_update / correct_wiki. The entry was inert
#: from the day it was written, so the docstring's "writing ... keeps her able
#: to finish a task" was never actually enforced. Resolved to the real name.
_ESSENTIAL_TOOLS = frozenset({
    # Ground an answer.
    "search_web", "browse_web", "read_file", "read_wiki", "search_wiki",
    "query_calendar", "search_email",
    # Finish the task.
    "write_file", "propose_wiki_update", "open_url", "open_path", "navigate",
    "run_command", "spawn_task",
})


def _surface_override(kept: list, dropped: list) -> str:
    """The authoritative statement of what this seat can actually call.

    THE CRUX OF THIS MODULE, and the part it was missing.

    `FRIDAY_SYSTEM_PROMPT` names ~35 tools in its "== AVAILABLE TOOLS =="
    section, tells the model to "use these tools proactively", and — for the
    browser and file tools — that it must NEVER say it cannot do those things
    ("you can, and these tools are how"). That block is a COMPILE-TIME
    CONSTANT. It knows nothing about trimming and never has.

    So a trimmed turn shipped a prompt naming tools the request did not carry.
    A model in that position does the only thing left to it: it announces the
    action and nothing happens. Measured on this machine 2026-08-24 against the
    live registry — every local seat is served at 32,768 and the assembled
    system prompt alone is ~13,550 tokens, so ~3.5k of transcript is enough to
    start dropping core tools, and ~8k drops `spawn_task` itself.

    This is the same defect the LIVE VOICE path fixed the same day with
    `_voice_tool_surface_note` — there the prompt advertised the ~30-tool text
    toolbox while the Live API was handed nine. Same disease, different cause:
    voice lost tools to an API shape, text loses them to arithmetic. The note
    is appended LAST by every caller, so it wins over the constant above it.

    Whichever list is shorter gets printed. Naming what is GONE and naming what
    REMAINS are equally true, and the cheaper sentence is the one that still
    fits when the budget is the reason we are here in the first place.
    """
    # Coerce before formatting. A registry entry with no "name" yields None,
    # and `"  • " + None` raises — which two of the three callers swallow into
    # "return the tools untrimmed", quietly restoring the exact overflow this
    # module exists to prevent. A malformed tool must not cost the seat.
    kept = [str(n) for n in (kept or []) if n]
    dropped = [str(n) for n in (dropped or []) if n]
    head = "\n=== TOOL SURFACE ON THIS SEAT (OVERRIDES ANY TOOL LIST ABOVE) ===\n"
    if not kept:
        return (
            head +
            "You have NO callable tools this turn. Every tool named in the "
            "'== AVAILABLE TOOLS ==' section above is unavailable here, "
            "including the browser, file and calendar tools that section says "
            "you must never deny. Do not announce, promise or describe any "
            "action that needs a tool. Answer from what is already in this "
            "conversation, and say plainly that you cannot do the rest on "
            "this seat.\n")
    body = [head,
            "This seat could not hold the whole toolbox, so the "
            "'== AVAILABLE TOOLS ==' list above is NOT accurate for this "
            "turn. What follows is.\n"]
    if len(dropped) <= len(kept):
        body.append("These %d tools are NOT loaded and CANNOT be called, "
                    "whatever the section above says about them:\n" % len(dropped))
        body.extend("  • " + n + "\n" for n in dropped)
    else:
        body.append("You can call EXACTLY these %d tools and nothing else:\n"
                    % len(kept))
        body.extend("  • " + n + "\n" for n in kept)
    body.append(
        "\nTHE RULE: never announce an action you cannot actually take. The "
        "instruction above to never tell the user you can't open a page, a "
        "file or an app does NOT apply to any tool missing here — for those, "
        "saying so plainly is the honest answer. Say which tool you are "
        "missing and offer to do it on a larger-context seat.\n")
    return "".join(body)


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
    # The generation reserve is a fixed 4,608 tokens, which is a sane reserve
    # for a 32k seat and LARGER THAN THE WHOLE WINDOW of a small one. Unclamped,
    # a 4,096-token seat carrying a zero-token prompt computed a NEGATIVE budget
    # and dropped every tool it had, then blamed the prompt for it. Scale the
    # reserve to the seat: on every window Friday actually serves (32,768 and
    # up) `window // 4` exceeds 4,608, so this is a no-op there and only bites
    # where the flat number was nonsense.
    headroom = min(_GEN_HEADROOM, max(0, window // 4))
    # Tools may take their share of the window OR whatever the prompt and the
    # answer's headroom leave free — whichever is smaller. The share keeps
    # tools from crowding out conversation; the remainder keeps the sum an
    # actual request.
    budget = min(int(window * share),
                 window - int(prompt_cost or 0) - headroom)

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
    # against a 32,768-token seat.
    #
    # CORRECTION, remeasured 2026-08-24 against the registry itself: that
    # 47,579 is the WHOLE registry — Friday's own 67 tools are ~11,131 tokens
    # of it and the ~64 connectors are the other ~36,448. Friday's own tools DO
    # fit a 32,768 window by themselves, comfortably. The original sentence here
    # read a combined number as a core-only one.
    #
    # The change it justified is still right, for the real reason: the budget is
    # the whole request. Core (11,131) + the assembled system prompt (13,550)
    # + the generation reserve (4,608) is 29,289 of 32,768, so roughly 3.5k
    # tokens of transcript — a few turns — is enough to put core over. Core has
    # to be droppable. It just is not the tool definitions that push it there.
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
        #
        # Name the RIGHT cause. With prompt_cost=0 this used to announce a
        # request "about 0 tokens" that had somehow overflowed the seat — a
        # sentence that is both false and unactionable. A zero-token prompt
        # that leaves no budget means the WINDOW is too small, full stop.
        if int(prompt_cost or 0) <= 0:
            why = ("No tools were loaded: %s's %s-token window has no room for "
                   "tool definitions once the generation reserve is set aside. "
                   "Use a larger-context seat." % (model_id, f"{window:,}"))
        else:
            why = ("No tools were loaded: the request itself is about %s tokens "
                   "against %s's %s-token window, before any tool definitions. "
                   "Shorten the input or use a larger-context seat."
                   % (f"{int(prompt_cost or 0):,}", model_id, f"{window:,}"))
        # Say it to the MODEL too, not only to Stephen. An empty tool array
        # under a prompt that still names thirty-five tools is the exact
        # condition that produces a confident announcement and no action.
        return [], why + _surface_override([], [t.get("name") for t in tools])

    # Connectors are named by COUNT, not individually: the prompt above refers
    # to them only in the abstract ("plus any MCP connectors"), so it makes no
    # per-tool promise there is anything to correct, and naming sixty-odd of
    # them would spend the very budget this branch is trying to reclaim. The
    # rule against announcing what you cannot call still has to be stated.
    note = (f"{len(connectors)} connector tools are not loaded on this seat: "
            f"their definitions cost about {conn_cost:,} tokens and "
            f"{model_id} has a {window:,}-token window. Ask me on a "
            f"larger-context seat if you need them. None of them can be "
            f"called this turn, so do not offer or announce anything that "
            f"depends on one — say plainly that it is not available here.")

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
    # happening. Trim core too, essential-first, and keep what a turn cannot
    # function without.
    #
    # WHAT WAS ACTUALLY DECIDING THIS. The sort below reads
    # "(not essential, token cost)", so outside the essential set the ONLY
    # criterion was schema size — cheapest survives. Nobody chose that; it is
    # the same shape as the residency planner's "largest model on disk wins".
    # Measured on the live registry: at a 20k prompt the eight tools dropped
    # were exactly the eight most expensive schemas — content_create_post,
    # generate_music, generate_image, compose_timeline, creative_project,
    # annotate_calendar_events, speak_text, generate_video — while
    # get_career_pipeline (40 tokens) and type_text (60) survived to the end.
    # A job-search lookup outranking every creative tool and `spawn_task` is a
    # verdict on description length, not on usefulness.
    #
    # Size stays as the TIEBREAK — within one tier, cheaper first genuinely
    # fits more tools — but it no longer outranks the essential set, which is
    # now large enough to cover every capability the prompt promises by name.
    kept, kept_cost = [], 0
    for t in sorted(core, key=lambda x: (str(x.get("name")) not in _ESSENTIAL_TOOLS,
                                         _tokens([x]))):
        c = _tokens([t])
        if kept_cost + c > budget:
            continue
        kept.append(t)
        kept_cost += c

    kept_names = [t.get("name") for t in kept]
    dropped_names = [t.get("name") for t in core if t.get("name") not in set(kept_names)]
    dropped = len(dropped_names)
    # A COUNT IS NOT A DISCLOSURE.
    #
    # This note used to say "I am working with 53 of my 67 tools" and stop
    # there. The model was told HOW MANY it had lost and never WHICH, under a
    # prompt that still named them all and told it to use them proactively.
    # "53 of 67" is not something a model can act on; the names are.
    note = (f"This seat is small, so I am working with {len(kept)} of my "
            f"{len(core)} tools plus none of the {len(connectors)} connectors. "
            f"{model_id} has a {window:,}-token window and the full set costs "
            f"about {core_cost + conn_cost:,}. If I need something I do not "
            f"have here, ask me on a larger-context seat."
            + _surface_override(kept_names, dropped_names))
    _log.warning("%s: core tools trimmed %d -> %d (~%d of ~%d tokens) to fit a "
                 "%d-token window with a ~%d-token prompt",
                 model_id, len(core), len(kept), kept_cost, core_cost,
                 window, int(prompt_cost or 0))
    print(f"  [tools] {model_id}: kept {len(kept)}/{len(core)} of Friday's own "
          f"tools (~{kept_cost:,} tokens), dropped {dropped} + "
          f"{len(connectors)} connectors to fit {window:,}")
    return kept, note
