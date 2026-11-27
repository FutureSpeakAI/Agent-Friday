"""Read-only lookups of one model round run at the same time (KG latency fix 4, 2026-11-26).

A model round that asks for read_wiki AND search_wiki used to run them one after the other. Here
the reads start together, before the loop walks the round's calls in order; when the loop reaches
a call, its gates run exactly as before (`_execute_tool`, the vault check, the search card, the
crew boundary), and only the handler step takes the result that is already there.

What this may run early is fixed and small: tools that only read this PC's own wiki and knowledge
graph (PARALLEL_READ_TOOLS). Nothing they return leaves the PC unless the loop, after every gate,
puts it in the round's results; a call a gate refuses simply never uses its early read. A call
whose arguments reach the handler changed (a hook restored a value) runs the handler afresh.
Started only when a round has two or more such reads; one read gains nothing from a thread.
"""
from __future__ import annotations

import contextvars
import copy
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Dict, Optional

_log = logging.getLogger(__name__)

#: Only these: they read this PC's own wiki and knowledge graph and change nothing.
PARALLEL_READ_TOOLS = frozenset({"search_wiki", "read_wiki", "knowledge_query"})
MAX_WORKERS = 4


class Prefetch:
    def __init__(self, handlers: Optional[Dict[str, Callable]] = None):
        self._handlers = handlers or {}
        self._started: Dict[str, tuple] = {}        # tool_use id -> (name, input, future)

    def __len__(self) -> int:
        return len(self._started)

    def handler_for(self, tu) -> Optional[Callable]:
        """The handler `_execute_tool` should call for this tool_use, or None (run as usual)."""
        got = self._started.get(getattr(tu, "id", None))
        if not got:
            return None
        name, started_with, fut = got
        real = self._handlers.get(name)

        def handler(inp):
            if inp == started_with:
                return fut.result()
            return real(inp)
        return handler


def start(tool_uses, handlers: Dict[str, Callable], prepare: Callable[[str, dict], Optional[dict]]) -> Prefetch:
    """Start the round's read-only lookups together. `prepare(name, input)` gives the input the
    handler will receive (or None to leave that call alone). Never raises."""
    pre = Prefetch(handlers)
    try:
        reads = [tu for tu in tool_uses or ()
                 if getattr(tu, "name", None) in PARALLEL_READ_TOOLS and handlers.get(tu.name)]
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
                ctx = contextvars.copy_context()
                fut: Future = pool.submit(ctx.run, handlers[tu.name], copy.deepcopy(inp))
                pre._started[tu.id] = (tu.name, inp, fut)
        finally:
            pool.shutdown(wait=False)
    except Exception as e:  # noqa: BLE001  the loop runs its calls as before
        _log.debug("parallel reads not started: %s", e)
    return pre
