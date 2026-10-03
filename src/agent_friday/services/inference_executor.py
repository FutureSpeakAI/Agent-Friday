"""One long-lived thread for in-process model inference.

Intel OpenMP gives every OS thread that enters a parallel kernel its own
worker team, and the team is never torn down when that thread exits
(services/thread_caps.py). Request and worker threads here are short-lived,
so a model called directly from them leaves a team behind per call site
thread and the process grows without bound. Every in-process embedding call
(the privacy classifier's semantic layer, the tool selector, the context
pruner, conversation memory) runs through run() instead, which executes it on
a single thread that lives as long as the process: the teams are made once.

The worker is started on first use, so importing this module costs nothing.
It is an executor thread, joined at interpreter exit, not a daemon. A call made
from the worker itself runs inline, so a job that reaches another routed call
does not wait on itself.

Calls share one queue, so a caller that must answer in bounded time passes
``_timeout``: past it the caller gets TimeoutError (and the job is dropped if
it has not started), instead of waiting behind someone else's bulk encode.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

THREAD_NAME = "friday-inference"

_executor: ThreadPoolExecutor | None = None
_worker_ident: int | None = None
_lock = threading.Lock()


def _mark_worker() -> None:
    global _worker_ident
    _worker_ident = threading.get_ident()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    ex = _executor
    if ex is not None:
        return ex
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=1,
                                           thread_name_prefix=THREAD_NAME,
                                           initializer=_mark_worker)
        return _executor


def _job(fn, args, kwargs):
    from agent_friday.services import thread_caps
    thread_caps.limit_loaded_pools()
    return fn(*args, **kwargs)


def on_worker() -> bool:
    """True when the caller is the inference thread."""
    return _worker_ident is not None and threading.get_ident() == _worker_ident


def run(fn, *args, _timeout: float | None = None, **kwargs):
    """Run ``fn(*args, **kwargs)`` on the inference thread and return its result.

    Blocks the caller until it finishes, or raises TimeoutError after
    ``_timeout`` seconds when one is given; its exception is re-raised in the
    caller. Calls are served one at a time, in arrival order.
    """
    if on_worker():
        return _job(fn, args, kwargs)
    fut = _get_executor().submit(_job, fn, args, kwargs)
    try:
        return fut.result(timeout=_timeout)
    except TimeoutError:
        fut.cancel()
        raise


def routed(cls):
    """A subclass of the callable ``cls`` whose ``__call__`` runs on the
    inference thread. Everything else, including how a vector store names and
    persists it, is inherited unchanged. One subclass per base class."""
    sub = _ROUTED.get(cls)
    if sub is not None:
        return sub
    base_call = cls.__call__

    def __call__(self, input):  # noqa: A002 - the name the base signature uses
        return run(base_call, self, input)

    sub = type(cls.__name__, (cls,), {"__call__": __call__,
                                      "__module__": __name__,
                                      "__qualname__": cls.__qualname__})
    _ROUTED[cls] = sub
    return sub


_ROUTED: dict = {}
