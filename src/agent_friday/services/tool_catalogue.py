"""Send an index of the tools, not 13,300 tokens of their schemas.

MEASURED ON THE REFERENCE MACHINE:

    75 tools, ~13,324 tokens of JSON schema
      names + framing   ~  383
      descriptions      ~ 5,708
      parameter schemas ~ 6,224
    41% of a 32,768-token window, spent before the user says anything
    a 15,930-token prompt to answer "how many conversations are stored"
    three tools DROPPED to make a 66-token question fit

Only 383 of those tokens are tool NAMES. Ninety-seven per cent is detail the
model does not need until it has already decided to call something.

So this sends a catalogue - name plus one line each, ~2,283 tokens - with a
single `load_tools` tool that returns full schemas on demand. The model reads
the index, asks for the two or three it wants, and those arrive for the next
round. Tool overhead goes from 41% of the window to about 7%, which hands back
roughly 11,000 tokens per turn on a machine where context is the scarcest
thing there is.

WHY THIS BEATS TRIMMING. `tool_budget.fit_tools_to_seat` already drops tools
when the request will not fit, and it drops the EXPENSIVE ones - measured, the
three it discards are the 2nd, 3rd and 4th largest schemas in the set. So the
capability Friday loses under pressure is selected by how verbose its schema
is, which is a bad reason to lose anything. Nothing is lost here: every tool
stays reachable, it simply arrives when asked for.

The prior art is the session this was written in, where most tools are
deferred and fetched on demand.

ON BY DEFAULT. `FRIDAY_TOOL_CATALOGUE=0` switches it off and sends every
schema in full; see `enabled()` for why defaulting on is safe.
"""
from __future__ import annotations

import json
import math
import os
import re

#: The tool the model calls to get real schemas.
LOADER_NAME = "load_tools"

#: Tools that skip the catalogue and are always sent in full.
#:
#: WHY ANY AT ALL. Progressive disclosure costs a round trip, and a round trip
#: on a local 27B is measured in tens of seconds. The handful of tools that
#: almost every turn reaches for should not pay it. Kept deliberately short -
#: every name here is ~200 tokens of permanent rent.
# Local knowledge is as fundamental as public retrieval. Keep its read tools
# callable without requiring a discovery round before a wiki or graph request.
ALWAYS_RESIDENT = ("search_web", "read_file", "search_files",
                   "search_wiki", "read_wiki", "knowledge_query")

#: Resident when present in a turn's catalogue, which is only in a chat in the
#: Chat Hub (agent.WORKSPACE_TOOLS["hub"]): the one tool a build turn reaches
#: for every time, so it does not pay a search round trip on the local seat.
#: Elsewhere it is not in the catalogue, so it costs nothing.
HUB_RESIDENT = ("codebase_edit",)


#: ON by default, because the risk is understood rather than assumed.
#:
#: WHY IT IS SAFE TO DEFAULT ON. `_execute_tool` dispatches by NAME out of
#: CLAUDE_TOOL_HANDLERS and never consults the list the model was sent. So the
#: catalogue governs what Friday is TOLD ABOUT, not what it can DO: a model
#: that calls a tool it was never shown still gets it executed, and
#: `_resolve_tool_name` even repairs near-miss names. Every one of the 75 tools
#: is named with a summary in the loader's description, so nothing is hidden -
#: the only cost of not loading a schema is guessing the argument shape, and a
#: malformed call now pulls the schema in for the next round (see the loop).
#:
#: The failure this could still cause is a model that never thinks to ask.
#: That is why the hot tools stay resident and why this remains one env var
#: away from off.
def enabled() -> bool:
    raw = str(os.environ.get("FRIDAY_TOOL_CATALOGUE", "")).strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def _name_of(tool: dict) -> str:
    fn = tool.get("function") or tool
    return fn.get("name") or ""


def _summary_of(tool: dict, limit: int = 90) -> str:
    """One line. The first sentence of the description, truncated.

    The first sentence is doing the work a whole paragraph was doing: it is
    what tells the model whether this is the tool it wants. The rest of the
    paragraph explains HOW to use it, which only matters once it has decided.
    """
    fn = tool.get("function") or tool
    d = " ".join(str(fn.get("description") or "").split())
    if not d:
        return ""
    first = d.split(". ")[0].strip().rstrip(".")
    return (first[:limit] + "…") if len(first) > limit else first


def index(tools: list) -> list:
    """`[{name, summary}]` for every tool. No parameters, no prose."""
    out = []
    for t in (tools or []):
        n = _name_of(t)
        if n:
            out.append({"name": n, "summary": _summary_of(t)})
    return out


LOADER_DESCRIPTION = (
    "Find and load tools. Friday has many tools; only the few described in "
    "full here are loaded. Give `query` (a few words about what you need, such "
    "as \"send an email\", \"calendar\", \"github pull request\", \"play a song\") "
    "to load the best matches, or `names` when you already know them. Their "
    "schemas arrive before your next turn; then call them normally. Searching "
    "is cheap: do it whenever the task needs something not already loaded, and "
    "never say a tool does not exist without searching first."
)


def loader_spec(tools: list) -> dict:
    """The one tool that is always present: find and load schemas.

    Its description never lists the tools. A list grows with every connector
    and changes whenever one connects, which rewrites the opening of every
    prompt and empties the local seat's prompt cache. The description is a
    constant; what is loadable is found by `query` (a ranked search over
    every tool's name, description and parameters) or asked for by `names`.
    `tools` is accepted for the callers that pass it; the spec does not
    depend on it.
    """
    return {
        "name": LOADER_NAME,
        "description": LOADER_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What you need to do, in a few words. Loads the best-matching tools.",
                },
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Exact tool names to load, when you know them.",
                },
            },
        },
    }


def resident(tools: list) -> list:
    """The tools sent in full from the start."""
    keep = set(ALWAYS_RESIDENT) | set(HUB_RESIDENT)
    return [t for t in (tools or []) if _name_of(t) in keep]


_PILOT_READ_TOOLS = {
    "apps": ("query_calendar", "search_email", "list_tasks", "find_calendar_events"),
    "knowledge": ("search_wiki", "read_wiki", "knowledge_query"),
    "files": ("search_files", "read_file", "search_library"),
}
_PILOT_SCHEMA_CHARS = 6000


def opening_set(tools: list, pilot=None) -> list:
    """Resident schemas plus bounded advisory reads and the complete loader.

    Preparation grants no authority and executes nothing. A missing, late or
    invalid prediction leaves the original opening unchanged; every tool stays
    reachable through the same loader and ordinary execution gates.
    """
    opening = resident(tools)
    if pilot is not None and getattr(pilot, "arm", None) == "assisted":
        try:
            wanted = _PILOT_READ_TOOLS.get(pilot.plan(), ())
            have = {_name_of(t) for t in opening}
            by_name = {_name_of(t): t for t in (tools or [])}
            added, size = [], 0
            for name in wanted:
                if name in have or name not in by_name:
                    continue
                tool = by_name[name]
                cost = len(json.dumps(tool, ensure_ascii=False))
                if size + cost > _PILOT_SCHEMA_CHARS:
                    continue
                added.append(tool)
                have.add(name)
                size += cost
            if added:
                opening.extend(added)
                pilot.increment("added_tools", len(added))
        except Exception:
            pass
    return opening + [loader_spec(tools)]


# ── Finding tools by query ───────────────────────────────────────────────────
#
# A small BM25 ranker over each tool's name (and the name with underscores
# turned into spaces), its description and its parameter names and
# descriptions. Adapted from pi's tool_search extension (earendil-works/pi,
# MIT, Copyright (c) 2025 Mario Zechner; see THIRD_PARTY_LICENSES.md), which
# ranks hidden tools the same way and returns 8 by default.

SEARCH_RESULTS = 8
_K1, _B = 1.2, 0.75
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list:
    return _TOKEN.findall(str(text or "").lower())


def _search_text(tool: dict) -> str:
    fn = tool.get("function") or tool
    name = str(fn.get("name") or "")
    parts = [name, name.replace("_", " "), str(fn.get("description") or "")]
    schema = fn.get("input_schema") or fn.get("parameters") or {}
    for pname, spec in (schema.get("properties") or {}).items():
        parts.append(str(pname).replace("_", " "))
        if isinstance(spec, dict):
            parts.append(str(spec.get("description") or ""))
    return " ".join(parts)


def search(tools: list, query: str, k: int = SEARCH_RESULTS) -> list:
    """The `k` tools that best match `query`, best first; [] when none match."""
    terms = _tokens(query)
    if not terms or not tools:
        return []
    docs = [(t, _tokens(_search_text(t))) for t in tools if _name_of(t)]
    if not docs:
        return []
    n = len(docs)
    avg = sum(len(d) for _, d in docs) / n
    df = {}
    for _, d in docs:
        for term in set(d):
            df[term] = df.get(term, 0) + 1
    scored = []
    for tool, d in docs:
        if not d:
            continue
        counts = {}
        for term in d:
            counts[term] = counts.get(term, 0) + 1
        score = 0.0
        for term in terms:
            tf = counts.get(term)
            if not tf:
                continue
            idf = math.log(1.0 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * (tf * (_K1 + 1)) / (tf + _K1 * (1 - _B + _B * len(d) / avg))
        if score > 0:
            scored.append((score, tool))
    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored[:max(1, int(k))]]


def expand(all_tools: list, names, already: list, query: str = "",
           k: int = SEARCH_RESULTS) -> tuple:
    """Schemas for `names`, or the best matches for `query`, minus anything sent.

    Returns `(new_tools, message)`. The message is what the model reads, and it
    names what it did NOT get as well as what it did - a loader that silently
    drops an unknown name teaches the model to trust a tool that will never
    arrive.
    """
    have = {_name_of(t) for t in (already or [])}
    by_name = {_name_of(t): t for t in (all_tools or [])}
    # A workspace's own tools (agent.WORKSPACE_TOOLS) are not in the always-on
    # catalogue, but the loader hands them over by name from anywhere.
    try:
        from agent_friday.services.agent import WORKSPACE_TOOLS as _ws_tools
        for _lst in _ws_tools.values():
            for _t in _lst:
                by_name.setdefault(_name_of(_t), _t)
    except Exception:
        pass
    wanted = [str(n).strip() for n in (names or []) if str(n).strip()]
    query = str(query or "").strip()

    new, dup, missing = [], [], []
    for n in wanted:
        if n in have:
            dup.append(n)
        elif n in by_name:
            new.append(by_name[n])
            have.add(n)
        else:
            missing.append(n)

    found_by_query = []
    if query:
        pool = [t for t in by_name.values() if _name_of(t) != LOADER_NAME]
        for t in search(pool, query, k=k):
            n = _name_of(t)
            if n in have:
                continue
            new.append(t)
            found_by_query.append(t)
            have.add(n)

    bits = []
    if found_by_query:
        bits.append("Loaded for \"%s\": %s. Their schemas are available now — call "
                    "them directly." % (query, "; ".join(
                        "%s — %s" % (_name_of(t), _summary_of(t)) if _summary_of(t)
                        else _name_of(t) for t in found_by_query)))
    elif query and not wanted:
        bits.append("Nothing matched \"%s\". Try different words for what you need, "
                    "or give exact names." % query)
    named = [t for t in new if t not in found_by_query]
    if named:
        bits.append("Loaded: %s. Their schemas are available now — call them "
                    "directly." % ", ".join(_name_of(t) for t in named))
    if dup:
        bits.append("Already loaded: %s." % ", ".join(dup))
    if missing:
        close = []
        for m in missing:
            hit = [k2 for k2 in by_name if m.lower() in k2.lower()
                   or k2.lower() in m.lower()]
            if not hit:
                hit = [_name_of(t) for t in search(list(by_name.values()), m, k=3)]
            if hit:
                close.append("%s (did you mean %s?)" % (m, ", ".join(hit[:3])))
            else:
                close.append(m)
        bits.append("No such tool: %s." % "; ".join(close))
    if not bits:
        bits.append("No tool names or query were given.")
    return new, " ".join(bits)


# ── Loading tools mid-task without editing the request (Claude) ─────────────
#
# Claude models that check replayed thinking compare the `tools` array of
# every request with the one each thinking block was produced under; the
# array also renders first, so growing it re-bills the whole cached prefix.
# On the models that accept the beta below, the cloud loop declares every
# tool from the first request, the non-resident ones with defer_loading, and
# a load surfaces a tool with an appended system message carrying a
# `tool_addition` block. The `tools` array never changes during a task.

TOOL_CHANGES_BETA = "mid-conversation-tool-changes-2026-07-01"

#: Models that accept TOOL_CHANGES_BETA (Claude Sonnet 5 and Haiku do not).
TOOL_CHANGE_MODELS = frozenset({
    "claude-opus-5", "claude-opus-5-5", "claude-opus-4-8",
    "claude-fable-5", "claude-fable-5-1", "claude-mythos-5", "claude-mythos-5-1",
    "claude-sonnet-5-5",
})

#: Models whose provider refused the beta in this process: the loop falls
#: back to sending the grown tool list, and does not pay the 400 again.
_REFUSED_TOOL_CHANGES: set = set()

_TOOL_CHANGE_TYPES = ("tool_addition", "tool_removal")


def _model_id(model) -> str:
    return str(model or "").strip().lower().split("/")[-1]


def tool_changes_supported(model) -> bool:
    """True when the cloud loop should declare deferred tools and surface them
    with `tool_addition`. `FRIDAY_TOOL_CHANGES=0` switches it off."""
    raw = str(os.environ.get("FRIDAY_TOOL_CHANGES", "")).strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    m = _model_id(model)
    return m in TOOL_CHANGE_MODELS and m not in _REFUSED_TOOL_CHANGES


def refuse_tool_changes(model) -> None:
    _REFUSED_TOOL_CHANGES.add(_model_id(model))


def checks_replayed_thinking(model) -> bool:
    """Models whose provider validates replayed thinking against the history."""
    return _model_id(model) in TOOL_CHANGE_MODELS


def declared_tools(all_tools: list, opening: list) -> list:
    """`opening` in full, then every other loadable tool with defer_loading.

    Built once per task, in registry order, so it is byte-identical every
    round. A deferred tool costs nothing in context until it is surfaced."""
    out = list(opening or [])
    seen = {_name_of(t) for t in out}
    pool = list(all_tools or [])
    try:
        from agent_friday.services.agent import WORKSPACE_TOOLS as _ws_tools
        for _lst in _ws_tools.values():
            pool.extend(_lst)
    except Exception:
        pass
    for t in pool:
        n = _name_of(t)
        if not n or n in seen or n == LOADER_NAME or not isinstance(t, dict):
            continue
        seen.add(n)
        out.append({**t, "defer_loading": True})
    return out


def is_tool_change(msg) -> bool:
    """A system message that only adds or removes tools."""
    if not isinstance(msg, dict) or msg.get("role") != "system":
        return False
    c = msg.get("content")
    return isinstance(c, list) and bool(c) and all(
        isinstance(b, dict) and b.get("type") in _TOOL_CHANGE_TYPES for b in c)


def surfaced_in(convo: list) -> set:
    names = set()
    for m in convo or []:
        if is_tool_change(m):
            for b in m["content"]:
                if b.get("type") == "tool_addition":
                    names.add(((b.get("tool") or {}).get("name")) or "")
    return names


def ensure_surfaced(convo: list, names) -> list:
    """Append a `tool_addition` for each of `names` not yet surfaced in
    `convo`. Mutates and returns `convo`. Called only while the newest
    message is unsent (after the tool results, or at the start of a round), so
    extending a trailing tool-change message edits nothing the model saw."""
    have = surfaced_in(convo)
    missing = [n for n in dict.fromkeys(names or []) if n and n not in have]
    if not missing:
        return convo
    blocks = [{"type": "tool_addition", "tool": {"type": "tool_reference", "name": n}}
              for n in missing]
    if convo and is_tool_change(convo[-1]):
        convo[-1] = {**convo[-1], "content": list(convo[-1]["content"]) + blocks}
    else:
        convo.append({"role": "system", "content": blocks})
    return convo


def drop_tool_changes(convo: list) -> list:
    """`convo` without tool-change messages, for a request sent without the beta."""
    return [m for m in (convo or []) if not is_tool_change(m)]


def is_tool_change_rejection(exc) -> bool:
    """A 400 that refuses the beta or its message shapes, not some other fault."""
    if getattr(exc, "status_code", None) != 400:
        return False
    text = str(exc).lower()
    return any(k in text for k in ("tool_addition", "tool_reference", "defer_loading",
                                   "mid-conversation-tool-changes", "anthropic-beta",
                                   "role 'system'", "role \"system\"", "system role"))


# ── The prompt's own tool text, generated from the registry ──────────────────

def prompt_block(tools: list, resident_names=None) -> str:
    """The "tools you have" text of the system prompt, written from the tools.

    One line per resident tool, taken from the tool's own description, plus
    the loader and a fixed sentence about everything else. Nothing here is
    typed by hand, so it cannot drift from the schemas the model is sent; and
    it is byte-identical turn to turn, so the prompt prefix stays cacheable.
    """
    keep = tuple(resident_names) if resident_names is not None else ALWAYS_RESIDENT
    by_name = {_name_of(t): t for t in (tools or [])}
    lines = []
    for n in keep:
        t = by_name.get(n)
        if t is None:
            continue
        s = _summary_of(t, limit=140)
        lines.append("  • %s — %s" % (n, s) if s else "  • %s" % n)
    lines.append("  • %s — Find and load any other tool, by `query` (what you need, in a few "
                 "words) or by `names`; the schemas arrive before your next turn." % LOADER_NAME)
    try:
        from agent_friday.services.tool_output import describe_limits
        limits = describe_limits()
    except Exception:
        limits = ""
    rest = ("Everything else Friday can do — email and calendar, files and the desktop, "
            "the wiki and knowledge graph, media, connectors, computer control — is one "
            "%s call away. When a task needs something not loaded, search for it first; "
            "never say a tool does not exist without searching." % LOADER_NAME)
    if not enabled():
        rest = ("Every tool is loaded in full this turn; the lines above are the ones "
                "almost every turn reaches for.")
    return ("== TOOLS ==\n"
            "Act by calling a tool; only its returned result is real. Loaded from the start:\n"
            + "\n".join(lines) + "\n" + rest + (" " + limits if limits else "") + "\n")


def savings(tools: list, opening=None) -> dict:
    """What this costs and saves, in tokens. For the harness and the logs."""
    def toks(o):
        s = o if isinstance(o, str) else json.dumps(o, default=str)
        return int(len(s) / 3.9)

    full = toks(tools)
    opening = toks(opening_set(tools) if opening is None else opening)
    return {
        "tools": len(tools or []),
        "full_tokens": full,
        "opening_tokens": opening,
        "saved_tokens": full - opening,
        "saved_pct": round(100.0 * (full - opening) / max(1, full), 1),
        "resident": list(ALWAYS_RESIDENT),
    }
