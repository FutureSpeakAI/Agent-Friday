"""Read-only lookups of one model round run at the same time (KG latency fix 4, 2026-11-26).

A model round that asks for read_wiki AND search_wiki used to run them one after the other. Here
the reads start together, before the loop walks the round's calls in order; when the loop reaches
a call, its gates run exactly as before (`_execute_tool`, the vault check, the search card, the
crew boundary), and only the handler step takes the result that is already there.

What this may run early is fixed and small, and reads no state of the call it belongs to:
  * PARALLEL_READ_TOOLS: read_wiki and knowledge_query, whose handlers read only this PC's wiki and
    knowledge graph (no origin, provider, turn or taint), run whole;
  * a SPLIT tool (search_wiki) runs only its context-free half early (its page search); its other
    half (the memory card, gated on the call's origin and provider) runs inside the real call.
Nothing read early leaves the PC unless the loop, after every gate, puts it in the round's
results; a call a gate refuses simply never uses its early read. A call whose arguments reach the
handler changed (a hook restored a value) runs the handler afresh. Started only when a round has
two or more such reads; one read gains nothing from a thread.
"""
from __future__ import annotations

import contextvars
import copy
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Dict, Optional, Tuple

_log = logging.getLogger(__name__)

#: Only these: they read this PC's own wiki and knowledge graph and change nothing.
PARALLEL_READ_TOOLS = frozenset({"read_wiki", "knowledge_query"})
MAX_WORKERS = 4


class Prefetch:
    def __init__(self, handlers: Optional[Dict[str, Callable]] = None):
        self._handlers = handlers or {}
        self._started: Dict[str, tuple] = {}        # tool_use id -> (name, input, future, finish)

    def __len__(self) -> int:
        return len(self._started)

    def handler_for(self, tu) -> Optional[Callable]:
        """The handler `_execute_tool` should call for this tool_use, or None (run as usual)."""
        got = self._started.get(getattr(tu, "id", None))
        if not got:
            return None
        name, started_with, fut, finish = got
        real = self._handlers.get(name)

        def handler(inp):
            if inp == started_with:
                early = fut.result()
                return finish(inp, early) if finish else early
            return real(inp)
        return handler


def start(tool_uses, handlers: Dict[str, Callable], prepare: Callable[[str, dict], Optional[dict]],
          split: Optional[Dict[str, Tuple[Callable, Callable]]] = None) -> Prefetch:
    """Start the round's read-only lookups together. `prepare(name, input)` gives the input the
    handler will receive (or None to leave that call alone). `split` maps a tool to (early, finish):
    `early(input)` runs now, `finish(input, early_result)` runs inside the real call. Never raises."""
    pre = Prefetch(handlers)
    split = split or {}
    try:
        reads = [tu for tu in tool_uses or ()
                 if (getattr(tu, "name", None) in PARALLEL_READ_TOOLS and handlers.get(tu.name))
                 or getattr(tu, "name", None) in split]
        if len(reads) < 2:
            return pre
        pool = ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(reads)), thread_name_prefix="friday-read")
        try:
            for tu in reads:
                try:
                    inp = prepare(tu.name, copy.deepcopy(tu.input if isinstance(tu.input, dict) else {}))
                except Exception:
                    inp = None
                if inp is None:
                    continue
                early, finish = split.get(tu.name) or (handlers[tu.name], None)
                ctx = contextvars.copy_context()
                fut: Future = pool.submit(ctx.run, early, copy.deepcopy(inp))
                pre._started[tu.id] = (tu.name, inp, fut, finish)
        finally:
            pool.shutdown(wait=False)
    except Exception as e:  # noqa: BLE001  the loop runs its calls as before
        _log.debug("parallel reads not started: %s", e)
    return pre
