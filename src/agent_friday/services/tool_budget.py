"""A tool registry that does not fit in the window is not a registry.

Measured on the reference machine:

    request (46288 tokens) exceeds the available context size (32768 tokens)

Every local turn returns HTTP 400 and the router falls back to the cloud, so
the visible symptom is "it took forever to reply then kicked back to the cloud
model, which I do not want". Nothing is wrong with the model, the seat, the
picker or the routing mode — the request simply cannot be built.

The cause is arithmetic. Friday's own tools cost about 9.7k tokens. The
Higgsfield connector registers 86 more and GitHub 26, and their schemas cost
roughly 36k on top. Any seat with a 32k window is unreachable from the moment
those connectors come up, and the seat's context is deliberately set to 32768
to keep the display alive on a 12 GB card — so raising it is not free.

So the tool payload is fitted to the seat instead. Friday's own tools always
travel: they are what she is. Connector tools are all-or-nothing per request,
because "some of the Higgsfield tools exist today" is a worse thing to explain
than "none of them do on this seat", and because a caller that can see half a
connector will try the half that is missing.
"""
from __future__ import annotations

import hashlib as _hashlib
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

# MEASURED, 2026-09-09, against the live fridayweaver seat (llama-server,
# gemma4:e2b, n_ctx 32,768) using /apply-template + /tokenize:
#
#   75 tool declarations   chars/4 estimate 12,740   TRUE 12,438   (0.98x)
#   102-turn transcript    chars/4 estimate  5,253   TRUE  5,113   (0.97x)
#
# So the docstring above was WRONG about the direction of the error: this
# template does not expand tool declarations beyond what chars/4 sees, and
# chars/4 is very slightly CONSERVATIVE (it over-counts) on both tools and
# prose. The under-count that actually killed turns is elsewhere -- see
# _GEN_HEADROOM.
#
# The estimate is still only an estimate, and a seat that can be asked exactly
# should be asked exactly. `measure_request()` below does that; the chars/4
# path is the fallback for when the seat cannot be reached.
_EST_TOKENS_PER_CHAR = 4

# Reserved out of the window for everything that arrives AFTER the budget is
# computed.
#
# THIS IS THE NUMBER THAT WAS WRONG, and not for the reason the docstring
# above guessed. 4,608 is a sane reserve for ONE answer. The local path does
# not send one answer: `_oai_agentic_loop` runs up to 50 rounds, and every
# round appends the assistant's tool call AND the tool's result back into the
# same conversation and re-sends it. The window has to hold the whole loop,
# not the first reply.
#
# Observed on the reference machine (friday.log, 2026-09-09 20:55:58): a turn
# budgeted at a ~27,000-token prompt passed llama.cpp's pre-flight check --
# 27,000 + 4,096 max_tokens = 31,096, under 32,768 -- and then died mid-loop
# with `500 Context size has been exceeded`, which is the RUNTIME overflow,
# not the pre-flight `400 request (N tokens) exceeds the available context
# size`. Two different errors; only the 400 is an arithmetic mistake about the
# prompt. The 500 is a budget that never accounted for the loop's growth.
#
# So the reserve covers: the answer, the [SEAT] note appended after fitting,
# and room for the tool-result rounds. Erring high costs a few tool schemas;
# erring low costs the entire turn, and in local_only mode there is no cloud
# to catch it.
_GEN_HEADROOM = 4608
_LOOP_RESERVE = 6144          # tool calls + tool results, several rounds
_NOTE_ALLOWANCE = 512         # the [SEAT] surface note this module itself adds

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


class FittedTools(list):
    """A tool list that has ALREADY been budgeted. Do not budget it again.

    ONE AUTHORITY. Three layers used to fit the same request independently:
    `routes/chat.py` fitted the registry, then `_via_ollama` fitted it again,
    then `model_router._call_openai` fitted THAT against a prompt which had
    meanwhile grown by the `[SEAT]` note the first fit appended. Each layer was
    individually defensible and together they compounded.

    Observed in friday.log, 2026-09-09 20:26:04, one turn, two lines:

        core tools trimmed 75 -> 10 ... with a ~27,039-token prompt
        core tools trimmed 10 -> 8  ... with a ~27,285-token prompt

    The second trim is not a correction of the first. It is the first trim's
    own note (+246 tokens) being charged back to the tools that survived it.
    A budget that bills tools for the notice that they were trimmed will
    always converge downward.

    Marking the result makes the decision idempotent: whoever fits first owns
    it, and the layers below pass it through untouched.
    """


def _served_ctx(model_id: str) -> int | None:
    """What the seat's server says it is serving, or None when unreachable.

    The server is the authority — the same principle `local_call._serves`
    already states for model identity, applied to context size. The plan is a
    record of intent and it drifts: the plan can say gemma4:e4b at 65,536
    while `_spawn` has capped the actual llama-server to 32,768 ("to keep the
    display reserve") without writing the cap back. Budgeting against the
    plan builds a >32k request for a 32k seat, every local turn 400s with
    `exceed_context_size_error`, and the router falls back to the cloud —
    the exact failure this module exists to prevent, one layer deeper.
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


def _seat_base(model_id: str) -> str | None:
    """The seat's HTTP root, by the same two-branch resolution as _served_ctx."""
    try:
        from agent_friday.services.local_call import seat_endpoint
        base = seat_endpoint(model_id)
    except Exception:
        base = None
    if not base:
        try:
            from agent_friday.services.model_seat_gate import (
                _local_openai_descriptor)
            prov = _local_openai_descriptor(model_id)
            base = ((prov or {}).get("base_url") or "").rstrip("/") or None
        except Exception:
            base = None
    if not base:
        return None
    return base[:-3] if base.endswith("/v1") else base


def _oai_tools(tools) -> list:
    """Registry entries (name/description/input_schema) rendered the way the
    seat receives them, so a measurement counts what will be served. Entries
    already in OpenAI shape pass through."""
    out = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "function" and isinstance(t.get("function"), dict):
            out.append(t)
            continue
        out.append({"type": "function", "function": {
            "name": t.get("name"), "description": t.get("description") or "",
            "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}})
    return out


def measure_request(model_id: str, system, messages, tools) -> int | None:
    """EXACTLY how many tokens this request renders to, or None.

    Prefer measuring over estimating. llama-server renders the same jinja chat
    template it will use to serve the request (`/apply-template`) and will
    tokenize the result with the same tokenizer (`/tokenize`), so this is not
    an approximation of the cost -- it is the cost.

    Measured to be practical on the reference machine: both endpoints answer a
    102-turn conversation with 75 tool declarations in well under a second, so
    this is affordable once per turn. It is still best-effort: any seat that
    does not expose these endpoints returns None and the caller falls back to
    chars/4, which measurement showed runs about 0.98x of true -- close, and
    on the safe side.
    """
    root = _seat_base(model_id)
    if not root:
        return None
    try:
        import urllib.request
        convo = []
        if system:
            convo.append({"role": "system", "content": str(system)})
        for m in (messages or []):
            c = m.get("content")
            if isinstance(c, str):
                convo.append({"role": m.get("role") or "user", "content": c})
        body = {"messages": convo}
        if tools:
            body["tools"] = tools

        def _post(path, payload, timeout=10):
            req = urllib.request.Request(
                root + path, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())

        prompt = _post("/apply-template", body).get("prompt")
        if not isinstance(prompt, str):
            return None
        return len(_post("/tokenize", {"content": prompt}).get("tokens") or [])
    except Exception:
        return None


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
            # Per-model declared window when there is one, so the budget is
            # drawn against the seat this model actually gets rather than the
            # ceiling the class would allow some other model.
            return int(LlamaServerBackend.seat_cap(model_id))
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
    # Reason over what Friday already knows. `knowledge_query` answers from
    # the knowledge graph's structure alone -- offline, no LLM -- and its own
    # description says to call it BEFORE reading wiki pages speculatively.
    # Dropping it therefore does not just remove a capability, it converts
    # Friday into the speculative reader that tool exists to replace.
    "knowledge_query",
    # Finish the task.
    "write_file", "propose_wiki_update", "open_url", "open_path", "navigate",
    "run_command", "spawn_task",
})

# ── The floor: tools that survive any budget ─────────────────────────────────
#
# Ranking is not protection. The loop below sorts well and then still skips
# anything that does not fit the remaining budget, so on a long prompt the
# tail of the ranking is dropped no matter how it was ranked. On 2026-09-10 a
# 17,650-token prompt trimmed 75 tools to 40 and took `knowledge_query`,
# `read_doc`, `search_drive`, `search_news` and `search_files` with it --
# every of one Friday's ways of looking something up, gone in the same turn,
# on the machine whose owner had just said local voice "always needs to
# involve a model in the loop that can call tools and do research into the
# knowledge graph."
#
# These four are reserved BEFORE the budget is spent. If they cannot fit, the
# seat is too small to be Friday at all and that is worth saying loudly rather
# than quietly shipping a mute assistant.
_FLOOR_TOOLS = ("knowledge_query", "search_wiki", "read_wiki", "search_web")


def _surface_override(kept: list, dropped: list) -> str:
    """The authoritative statement of what this seat can actually call.

    THE CRUX OF THIS MODULE, and the part it was missing.

    `FRIDAY_SYSTEM_PROMPT` names ~35 tools in its "== AVAILABLE TOOLS =="
    section, tells the model to "use these tools proactively", and — for the
    browser and file tools — that it must NEVER say it cannot do those things
    ("you can, and these tools are how"). That block is a COMPILE-TIME
    CONSTANT. It knows nothing about trimming and never has.

    So a trimmed turn would ship a prompt naming tools the request does not
    carry. A model in that position does the only thing left to it: it
    announces the action and nothing happens. Measured on the reference
    machine against the live registry — every local seat is served at 32,768
    and the assembled system prompt alone is ~13,550 tokens, so ~3.5k of
    transcript is enough to start dropping core tools, and ~8k drops
    `spawn_task` itself.

    This is the same defect the LIVE VOICE path handles with
    `_voice_tool_surface_note` — there the prompt advertises the ~30-tool text
    toolbox while the Live API is handed nine. Same disease, different cause:
    voice loses tools to an API shape, text loses them to arithmetic. The note
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


_STOP = frozenset("""a an and are as at be by can could do does for from get
give go has have how i if in is it its me my need of on or please should so
that the their them then there these this to us was we what when where which
who will with would you your""".split())


def _intent_terms(intent) -> set:
    """Content words from what the user actually asked for."""
    import re
    if not intent:
        return set()
    words = re.findall(r"[a-z_]{3,}", str(intent).lower())
    return {w for w in words if w not in _STOP}


def _matches_intent(tool, terms: set) -> bool:
    """Does this tool plausibly serve the request in front of us?

    THE SPECIFIC FAILURE THIS EXISTS TO PREVENT: asked about the calendar on a
    long conversation, the trimmer dropped `query_calendar` -- it is an
    expensive schema and the sort's only real criterion was cheapness -- and
    the model, still reading a system prompt that promised a calendar tool,
    announced "I checked, boss. The calendar shows no events for tomorrow."
    over a check that never happened. (conv-main, 2026-09-09 01:56.)

    Matching on the tool's NAME and the first line of its description is
    deliberately crude. It does not need to rank tools well; it needs to stop
    the one tool the turn is obviously about from being sorted out by size.
    """
    if not terms:
        return False
    name = str(tool.get("name") or "").lower()
    hay = set(name.replace("-", "_").split("_"))
    desc = str(tool.get("description") or "").lower()[:200]
    import re
    hay |= set(re.findall(r"[a-z_]{3,}", desc))
    return bool(hay & terms)


def _narrow_by_relevance(tools: list, intent, window: int, prompt_cost: int):
    """Choose which tools to carry BEFORE deciding how many will fit.

    The trimmer below sorts by cost and cuts from the bottom. That is the
    right way to answer "how many fit" and the wrong way to answer "which
    ones", and the log has the receipts: 75 -> 48, 75 -> 40, and once
    75 -> 8, each time on grounds of size rather than need. `_matches_intent`
    was added to stop the single obviously-relevant tool being sorted out by
    cost; this goes further and picks the handful the turn is about, so the
    arithmetic below is applied to a set that already makes sense.

    DELIBERATELY CONSERVATIVE. It runs only when the full catalogue could not
    have been carried anyway. If everything fits, everything ships: a
    selector that narrows a request which had room to spare is pure downside,
    because every wrong pick is a capability the model cannot see. Narrowing
    only replaces a cut that was going to happen regardless.

    Measured 2026-09-18 against the live 75-tool catalogue: ~12,821 tokens of
    schema down to ~2,600, in 160 ms, with the expected tool present in all
    six probe cases. Returns `tools` unchanged on any failure.
    """
    if not intent or len(tools) < 24:
        return tools
    try:
        would_fit = _tokens(_oai_tools(tools)) + max(0, prompt_cost) < window
        if would_fit:
            return tools
    except Exception:
        return tools
    try:
        from agent_friday.services import tool_selector
        picked = tool_selector.subset(tools, str(intent))
    except Exception as e:
        _log.debug("relevance narrowing unavailable (%s)", e)
        return tools
    if not picked or len(picked) >= len(tools):
        return tools
    _log.info("tools narrowed by relevance %d -> %d for %r",
              len(tools), len(picked), str(intent)[:60])
    return picked


#: The tool budget is rounded DOWN to a multiple of this before anything is
#: selected, so the chosen list is a step function of the prompt rather than a
#: continuous one.
#:
#: THE TOOL LIST IS PART OF THE CACHED PREFIX. A chat template renders tool
#: declarations before anything else, so one tool appearing or disappearing
#: moves every token after it and the seat's prefix cache matches nothing.
#: Measured on 2026-09-18: consecutive turns sent 62 tools and then 63,
#: because the budget subtracts the prompt from the window, the prompt
#: breathes as the conversation moves, and the trim count breathes with it.
#: The difference in capability between 62 tools and 63 is nil. The cost was
#: the whole ~21,000-token prompt reprocessed at about 500 tokens a second —
#: some forty-three seconds — on every single turn.
#:
#: Quantising rather than remembering the last decision, and that distinction
#: was learned the hard way: a remembered decision has to be revised in SOME
#: direction when the budget moves, and whichever direction it prefers becomes
#: a ratchet. Preferring the smaller list means the toolbox only ever shrinks
#: (39 tools, then 37, then 36, measured); preferring the larger means it
#: grows past what fits. A step function has no memory and so cannot drift:
#: the same prompt size always yields the same list, and the list changes only
#: when the budget crosses a step, which is a real change worth one cache miss.
#:
#: The cost is up to 2,047 tokens of budget left unclaimed — about 6% of a
#: 32,768 window, or roughly eighteen tool declarations. Against forty-three
#: seconds a turn, that is not a close call.
_BUDGET_QUANTUM = 2048


def fit_tools_to_seat(model_id: str, tools: list, *, share: float = _TOOL_SHARE,
                      prompt_cost: int = 0, intent=None,
                      system=None, messages=None):
    """Return the tools that fit this seat, plus a note when any were left out.

    Returns (tools, note). `note` is None when everything fit; otherwise it is
    a plain sentence suitable for the model's system prompt AND for telling
    the user, because a capability that quietly is not there is the failure
    this module exists to prevent.

    `prompt_cost` is the estimated token cost of everything else in the
    request — system prompt plus transcript. The share cap alone cannot
    protect a seat: at share 0.4 of a doubled window, tools "within budget"
    plus an ordinary prompt already exceeds what the seat can hold
    (measured on the reference machine: 26,214 allowed + 8,116 prompt against
    32,768 served). The request is budgeted as a WHOLE or it is not budgeted.
    """
    # ONE AUTHORITY. Somebody upstream already decided; do not re-decide
    # against a prompt their decision has since grown. See `FittedTools`.
    if isinstance(tools, FittedTools):
        return tools, None

    tools = list(tools or [])
    if not model_id or not tools:
        return FittedTools(tools), None

    window = _window(model_id)

    # Relevance first, arithmetic second. No-op whenever the whole catalogue
    # would have fitted; see `_narrow_by_relevance`.
    tools = _narrow_by_relevance(tools, intent, window, prompt_cost)

    # Measure if the seat will let us, estimate only if it will not. The
    # measurement covers the system prompt and the transcript exactly as the
    # template renders them, which is strictly better than summing chars.
    if messages is not None or system is not None:
        true_prompt = measure_request(model_id, system, messages, None)
        if true_prompt:
            prompt_cost = true_prompt
    # The generation reserve is a fixed 4,608 tokens, which is a sane reserve
    # for a 32k seat and LARGER THAN THE WHOLE WINDOW of a small one. Unclamped,
    # a 4,096-token seat carrying a zero-token prompt computed a NEGATIVE budget
    # and dropped every tool it had, then blamed the prompt for it. Scale the
    # reserve to the seat: on every window Friday actually serves (32,768 and
    # up) `window // 4` exceeds 4,608, so this is a no-op there and only bites
    # where the flat number was nonsense.
    # The reserve now covers the whole turn, not the first answer: generation,
    # the tool-result rounds the agentic loop appends, and the [SEAT] note this
    # function may append immediately after returning. Budgeting the tools
    # against a window that the note then eats is what produced the second
    # trim; charging for it up front is what makes one decision final.
    #
    # THE RESERVE IS ELASTIC, WITH A FLOOR. Reserving the full loop allowance
    # unconditionally is its own failure: on the 27,039-token prompt from the
    # log it drives the budget below zero and the turn ships with NO tools,
    # which is the narrate-instead-of-act symptom arriving by a different road.
    # So: ask for the full reserve, and give up the loop portion — never the
    # generation portion or the note — when the only alternative is an empty
    # toolbox. A turn that keeps `query_calendar` and might overflow on round
    # four is worth more than a turn that is guaranteed to invent an answer on
    # round one.
    #
    # This is a compromise, not a fix, and the honest note about it: at a
    # ~27k prompt against a 32,768 window the real problem is the TRANSCRIPT,
    # and no tool budget can solve it. Trimming the conversation is the actual
    # remaining repair.
    # The floor is never negotiable: without room to generate, the request is
    # refused before it starts. The loop reserve is negotiable, and it SCALES
    # rather than switching on and off — an earlier draft flipped between a
    # full reserve and no reserve, which made the budget non-monotonic (a
    # 22,000-token prompt was granted 48 tools where a 20,000-token prompt got
    # 12, purely because the longer one crossed the threshold and stopped
    # reserving). A budget that rewards a longer conversation is not a budget.
    #
    # So the loop takes at most half of whatever survives the floor. It always
    # gets something, it never starves the toolbox to zero, and more prompt is
    # always fewer tools.
    headroom = min(_GEN_HEADROOM + _NOTE_ALLOWANCE, max(0, window // 4))
    avail = window - int(prompt_cost or 0) - headroom
    loop_reserve = max(0, min(_LOOP_RESERVE, avail // 2))
    # Tools may take their share of the window OR whatever the prompt, the
    # answer's headroom and the loop's growth leave free — whichever is
    # smaller. The share keeps tools from crowding out conversation; the
    # remainder keeps the sum an actual request.
    budget = min(int(window * share), avail - loop_reserve)
    # Round DOWN to a step so the selection below is a step function of the
    # prompt. See `_BUDGET_QUANTUM` — this is what keeps the tool list
    # byte-identical between adjacent turns, and byte-identical is the only
    # kind of identical a prefix cache can use.
    #
    # ONLY WHERE THE STEP IS SMALL RELATIVE TO THE BUDGET. Rounding down is
    # safe when it costs a fraction and ruinous when it costs everything: on
    # an 8,192-token seat carrying a 4,200-token prompt the budget is 972
    # tokens, and `972 // 2048 * 2048` is ZERO — every tool dropped, on the
    # small seats that can least afford to lose them. Caught by
    # test_the_model_is_told_which_tools_it_lost, which is exactly the job of
    # a test that could have failed.
    #
    # Requiring at least two whole steps caps the loss at half and, more to
    # the point, confines quantising to the large seats where the prompt
    # cache is worth tens of seconds. A small seat keeps its exact budget and
    # simply does not get this optimisation, which is the right trade: it was
    # never the one paying forty-three seconds a turn.
    if budget >= 2 * _BUDGET_QUANTUM:
        budget = (budget // _BUDGET_QUANTUM) * _BUDGET_QUANTUM

    core = [t for t in tools if not str(t.get("name") or "").startswith(_CONNECTOR_PREFIX)]
    connectors = [t for t in tools if str(t.get("name") or "").startswith(_CONNECTOR_PREFIX)]

    core_cost = _tokens(core)
    conn_cost = _tokens(connectors)

    # THE SEAT COUNTS THE TOOLS TOO. The prompt was measured exactly above;
    # the tool declarations were still chars/4, which cannot see how a chat
    # template renders them. Re-measured 2026-09-18 against the FridayWeaver
    # seat: 75 declarations estimate 12,740 tokens, render to 12,433 (0.98x),
    # and the prompt estimates 14,315 against 14,359 -- so on THIS seat the
    # estimate is honest, and the "Context size has been exceeded" errors
    # date from 2026-09-09 at a 32k window and did not recur at 65k. The
    # exact count is used anyway: another template may expand tools
    # differently, and a decision the seat can make for us should not rest
    # on an estimate in either direction.
    true_all = None
    if tools and (messages is not None or system is not None):
        true_all = measure_request(model_id, system, messages, _oai_tools(tools))
    if true_all and (core_cost + conn_cost) > 0:
        true_tools = max(0, int(true_all) - int(prompt_cost or 0))
        if true_tools <= budget:
            return FittedTools(tools), None
        ratio = true_tools / float(core_cost + conn_cost)
        if ratio > 0 and abs(ratio - 1.0) > 0.01:
            # Rescale the budget rather than every per-tool cost below: the
            # comparisons that follow stay in estimate units, corrected by
            # how far the seat's rendering departs from chars/4.
            budget = int(budget / ratio)

    # CORE TOOLS MUST BE DROPPABLE TOO. A trimmer whose only lever is
    # connectors cannot trim the thing that is too big.
    #
    # Measured on the reference machine against the registry itself: the
    # whole registry is ~47,579 tokens, of which Friday's own 67 tools are
    # ~11,131 and the ~64 connectors are the other ~36,448. Friday's own tools
    # DO fit a 32,768 window by themselves, comfortably.
    #
    # The budget is the whole request, though. Core (11,131) + the assembled
    # system prompt (13,550) + the generation reserve (4,608) is 29,289 of
    # 32,768, so roughly 3.5k tokens of transcript — a few turns — is enough
    # to put core over. It is not the tool definitions that push it there,
    # but core still has to give: a briefing chain or a distill-to-wiki pass
    # at ~38k tokens into 32,768 otherwise dies as a provider 400 with no
    # explanation the user can act on.
    #
    # So core tools are droppable, lowest value first, and the caller is
    # told plainly when even an empty tool list will not fit — because at that
    # point the prompt is the problem and no amount of tool trimming is the
    # answer.
    if core_cost + conn_cost <= budget:
        return FittedTools(tools), None

    if budget <= 0:
        # The prompt alone has eaten the window. Report it as such: this is not
        # a tools problem and pretending otherwise sends the caller round a
        # loop that cannot terminate.
        #
        # Name the RIGHT cause. With prompt_cost=0, announcing a request
        # "about 0 tokens" that somehow overflowed the seat is both false and
        # unactionable. A zero-token prompt that leaves no budget means the
        # WINDOW is too small, full stop.
        if int(prompt_cost or 0) <= 0:
            why = ("No tools were loaded: %s's %s-token window has no room for "
                   "tool definitions once the generation reserve is set aside. "
                   "Use a larger-context seat." % (model_id, f"{window:,}"))
        else:
            why = ("No tools were loaded: the request itself is about %s tokens "
                   "against %s's %s-token window, before any tool definitions. "
                   "Shorten the input or use a larger-context seat."
                   % (f"{int(prompt_cost or 0):,}", model_id, f"{window:,}"))
        # Say it to the MODEL too, not only to the user. An empty tool array
        # under a prompt that still names thirty-five tools is the exact
        # condition that produces a confident announcement and no action.
        _log.warning("%s: NO tools loaded — %s", model_id, why)
        return FittedTools(), why + _surface_override(
            [], [t.get("name") for t in tools])

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
        return FittedTools(core), note

    # Core alone still overflows. Sending them anyway and letting the seat
    # 400 ("an honest 400 beats a silent trip to the cloud") is right about
    # the cloud and wrong about the 400: on a vault turn there IS no cloud to
    # fall back to, so the honest 400 is simply the work not happening. Trim
    # core too, essential-first, and keep what a turn cannot function without.
    #
    # WHY THE ESSENTIAL SET OUTRANKS SIZE. Outside the essential set the sort
    # below reads "(not essential, token cost)", so the only remaining
    # criterion is schema size — cheapest survives. On its own that is the
    # same shape as "largest model on disk wins": at a 20k prompt the tools
    # dropped are exactly the most expensive schemas — content_create_post,
    # generate_music, generate_image, compose_timeline, creative_project,
    # annotate_calendar_events, speak_text, generate_video — while
    # get_career_pipeline (40 tokens) and type_text (60) survive to the end.
    # A job-search lookup outranking every creative tool and `spawn_task` is a
    # verdict on description length, not on usefulness.
    #
    # Size stays as the TIEBREAK — within one tier, cheaper first genuinely
    # fits more tools — but it does not outrank the essential set, which is
    # large enough to cover every capability the prompt promises by name.
    #
    # INTENT OUTRANKS BOTH. The essential set is a standing judgement about
    # what Friday needs in general; the user's message is evidence about what
    # this turn needs in particular, and particular beats general. Without
    # this tier, `query_calendar` -- essential, but an expensive schema --
    # loses to `get_career_pipeline` on a calendar question, purely on size.
    terms = _intent_terms(intent)
    kept, kept_cost = [], 0

    # THE FLOOR GOES IN FIRST, before the budget can be spent on anything
    # else. See _FLOOR_TOOLS: without this, the research core is merely
    # ranked highly and then dropped anyway once a long prompt eats the
    # budget, which is what happened on 2026-09-10.
    _floor_set = set(_FLOOR_TOOLS)
    _by_name = {str(t.get("name")): t for t in core}
    for _fname in _FLOOR_TOOLS:
        t = _by_name.get(_fname)
        if t is None:                        # not offered on this seat at all
            continue
        c = _tokens([t])
        kept.append(t)
        kept_cost += c
    if kept_cost > budget:
        # Not a trim any more: the seat cannot hold the tools that make Friday
        # able to look anything up. Say it in the loudest channel available.
        _log.error("%s: the reserved research tools (%s) cost ~%d tokens but "
                   "the whole tool budget is ~%d. This seat cannot do lookup "
                   "or knowledge-graph work at this prompt length; it is not "
                   "a trim, it is a capability loss.",
                   model_id, ", ".join(_FLOOR_TOOLS), kept_cost, budget)

    for t in sorted(core, key=lambda x: (not _matches_intent(x, terms),
                                         str(x.get("name")) not in _ESSENTIAL_TOOLS,
                                         _tokens([x]))):
        if str(t.get("name")) in _floor_set:
            continue                         # already reserved above
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
    # NAME THE CASUALTIES IN THE LOG. "trimmed 75 -> 8" is the line that was
    # here, and it is unfalsifiable from the outside: it cannot tell anyone
    # whether the one tool the turn needed survived. The names can, and they
    # are the only record once the turn is over.
    _log.warning("%s: core tools trimmed %d -> %d (~%d of ~%d tokens) to fit a "
                 "%d-token window with a %s-token prompt; DROPPED: %s",
                 model_id, len(core), len(kept), kept_cost, core_cost,
                 window, f"{int(prompt_cost or 0):,}",
                 ", ".join(sorted(str(n) for n in dropped_names)) or "none")
    if terms:
        _log.warning("%s: intent-preserved tools: %s", model_id,
                     ", ".join(sorted(str(t.get('name')) for t in kept
                                      if _matches_intent(t, terms))) or "none")
    print(f"  [tools] {model_id}: kept {len(kept)}/{len(core)} of Friday's own "
          f"tools (~{kept_cost:,} tokens), dropped {dropped} + "
          f"{len(connectors)} connectors to fit {window:,}")
    return FittedTools(kept), note
