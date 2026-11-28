"""One log line per model round: how long the model took and how long each tool took (KG fix 5).

Why: on 2026-11-26 a phone answer took 54 s and the friday.log said nothing about where. The
numbers existed only as separate activity-ledger rows (search_wiki 41,131 ms). This writes them
together, to the ``friday.turn_timing`` logger, so the next slow turn is pinned from one line:

    round=1 model=claude-opus-5-5 model_ms=2564 tools=read_wiki=201ms,search_wiki=41131ms
    round=2 model=claude-opus-5-5 model_ms=7990 final

A tool slower than SLOW_TOOL_MS is logged again as a WARNING naming it. Names and numbers only:
never a tool's input or result.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional, Tuple

_log = logging.getLogger("friday.turn_timing")

#: A tool this slow in one call is a warning of its own.
SLOW_TOOL_MS = 5000


def round_line(iteration: int, model: Optional[str], model_ms: int,
               tools: Iterable[Tuple[str, int]] = (), final: bool = False) -> str:
    tools = list(tools or ())
    head = "round=%d model=%s model_ms=%d" % (int(iteration), model or "?", int(model_ms))
    if tools:
        head += " tools=" + ",".join("%s=%dms" % (str(n), int(ms)) for n, ms in tools)
    if final:
        head += " final"
    return head


def log_round(iteration: int, model: Optional[str], model_ms: int,
              tools: Iterable[Tuple[str, int]] = (), final: bool = False) -> None:
    """Never raises."""
    try:
        tools = list(tools or ())
        _log.info(round_line(iteration, model, model_ms, tools, final))
        for name, ms in tools:
            if int(ms) >= SLOW_TOOL_MS:
                _log.warning("slow tool: %s took %d ms in round %d", name, int(ms), int(iteration))
    except Exception:
        pass
