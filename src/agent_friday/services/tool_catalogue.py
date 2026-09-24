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
import os

#: The tool the model calls to get real schemas.
LOADER_NAME = "load_tools"

#: Tools that skip the catalogue and are always sent in full.
#:
#: WHY ANY AT ALL. Progressive disclosure costs a round trip, and a round trip
#: on a local 27B is measured in tens of seconds. The handful of tools that
#: almost every turn reaches for should not pay it. Kept deliberately short -
#: every name here is ~200 tokens of permanent rent.
ALWAYS_RESIDENT = ("search_web", "read_file", "search_files")


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


def loader_spec(tools: list) -> dict:
    """The one tool that is always present: fetch schemas by name.

    The catalogue lives in this tool's own description, so it costs nothing
    extra on the wire - the model has to be told the tool exists anyway.
    """
    lines = ["%s — %s" % (r["name"], r["summary"]) if r["summary"] else r["name"]
             for r in index(tools)]
    return {
        "name": LOADER_NAME,
        "description": (
            "Load the full schemas for tools you want to use. Friday has many "
            "tools; only a few are described in full above. Call this with the "
            "names you need and their schemas arrive before your next turn, "
            "then call them normally.\n\nAvailable tools:\n" + "\n".join(lines)
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tool names to load, exactly as listed.",
                },
            },
            "required": ["names"],
        },
    }


def resident(tools: list) -> list:
    """The tools sent in full from the start."""
    keep = set(ALWAYS_RESIDENT)
    return [t for t in (tools or []) if _name_of(t) in keep]


def opening_set(tools: list) -> list:
    """What to send on round one: the loader plus the resident few."""
    return resident(tools) + [loader_spec(tools)]


def expand(all_tools: list, names, already: list) -> tuple:
    """Schemas for `names`, minus anything already sent.

    Returns `(new_tools, message)`. The message is what the model reads, and it
    names what it did NOT get as well as what it did - a loader that silently
    drops an unknown name teaches the model to trust a tool that will never
    arrive.
    """
    have = {_name_of(t) for t in (already or [])}
    by_name = {_name_of(t): t for t in (all_tools or [])}
    wanted = [str(n).strip() for n in (names or []) if str(n).strip()]

    new, dup, missing = [], [], []
    for n in wanted:
        if n in have:
            dup.append(n)
        elif n in by_name:
            new.append(by_name[n])
            have.add(n)
        else:
            missing.append(n)

    bits = []
    if new:
        bits.append("Loaded: %s. Their schemas are available now — call them "
                    "directly." % ", ".join(_name_of(t) for t in new))
    if dup:
        bits.append("Already loaded: %s." % ", ".join(dup))
    if missing:
        close = []
        for m in missing:
            hit = [k for k in by_name if m.lower() in k.lower()
                   or k.lower() in m.lower()]
            if hit:
                close.append("%s (did you mean %s?)" % (m, ", ".join(hit[:3])))
            else:
                close.append(m)
        bits.append("No such tool: %s." % "; ".join(close))
    if not bits:
        bits.append("No tool names were given.")
    return new, " ".join(bits)


def savings(tools: list) -> dict:
    """What this costs and saves, in tokens. For the harness and the logs."""
    def toks(o):
        s = o if isinstance(o, str) else json.dumps(o, default=str)
        return int(len(s) / 3.9)

    full = toks(tools)
    opening = toks(opening_set(tools))
    return {
        "tools": len(tools or []),
        "full_tokens": full,
        "opening_tokens": opening,
        "saved_tokens": full - opening,
        "saved_pct": round(100.0 * (full - opening) / max(1, full), 1),
        "resident": list(ALWAYS_RESIDENT),
    }
