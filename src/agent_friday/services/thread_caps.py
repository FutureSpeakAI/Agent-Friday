"""Bound the native thread pools once, at boot, and keep them bounded.

OpenMP, MKL, OpenBLAS, and numexpr (which the ONNX and CTranslate2 runtimes
also honour) each size a pool to the machine's logical core count the first
time they load. Intel OpenMP (libiomp5, which torch and MKL bring) goes
further: every OS thread that enters a parallel kernel becomes the master of
its own team of workers, and that team outlives the thread. A server that
runs inference on short-lived request threads therefore gains a team per
request thread, and the process grows by hundreds of OS threads that Python
never sees. Two rules keep that bounded:

* The pools are small: apply() sets each cap to CAP (or keeps the value the
  operator set) before the libraries load, so the server entry point calls
  it ahead of its first heavy import. TOKENIZERS_PARALLELISM is off and
  KMP_BLOCKTIME is 0, so idle OpenMP workers sleep instead of spinning.
* Inference runs on one long-lived thread (services/inference_executor.py),
  so the teams are made once. That thread calls limit_loaded_pools() before
  each job, which applies the same cap to pools that are already loaded
  (torch's intra-op and inter-op pools, and whatever threadpoolctl can see).

apply() imports nothing heavy. A value the operator already set is never
overridden.
"""
from __future__ import annotations

import os
import sys
import threading

CAPPED_VARS = (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)

#: Threads per native pool. Inference here is one small model at a time; a
#: larger team buys little and costs a team's worth of threads per master.
CAP = 2

#: Set alongside the caps, only where unset.
FIXED_VARS = {
    "TOKENIZERS_PARALLELISM": "false",
    "KMP_BLOCKTIME": "0",
}

# Above this many OS threads the process is reported as thread-heavy.
OS_THREAD_WARN = 256

#: Modules whose pools limit_loaded_pools() has already limited.
_limited: set = set()
_limit_lock = threading.Lock()


def default_cap() -> int:
    return CAP


def apply(environ=None) -> dict:
    """Set every cap that is not already set. Returns what this call set."""
    env = os.environ if environ is None else environ
    cap = str(default_cap())
    set_now = {}
    for name in CAPPED_VARS:
        if not env.get(name):
            env[name] = cap
            set_now[name] = env[name]
    for name, value in FIXED_VARS.items():
        if not env.get(name):
            env[name] = value
            set_now[name] = value
    return set_now


def _intra_op_cap() -> int:
    try:
        n = int(os.environ.get("OMP_NUM_THREADS") or "")
    except ValueError:
        n = 0
    return n if n > 0 else default_cap()


def limit_loaded_pools() -> None:
    """Cap the pools of libraries that are already loaded, once per library.

    Never imports torch or numpy itself and never raises: a pool it cannot
    limit keeps the size the environment caps gave it.
    """
    pending = [m for m in ("numpy", "torch") if m in sys.modules and m not in _limited]
    if not pending:
        return
    with _limit_lock:
        pending = [m for m in pending if m not in _limited]
        if not pending:
            return
        _limited.update(pending)
        torch = sys.modules.get("torch")
        if "torch" in pending and torch is not None:
            try:
                torch.set_num_threads(_intra_op_cap())
            except Exception:
                pass
            try:
                torch.set_num_interop_threads(1)
            except Exception:
                pass  # RuntimeError once parallel work has started: its pool stays
        try:
            from threadpoolctl import threadpool_limits
        except Exception:
            return
        try:
            threadpool_limits(limits=_intra_op_cap())
        except Exception:
            pass


def thread_heavy(os_threads) -> bool:
    return bool(os_threads and os_threads > OS_THREAD_WARN)
