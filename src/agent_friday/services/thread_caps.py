"""Bound the native thread pools once, at boot.

OpenMP, MKL, OpenBLAS, and numexpr (which the ONNX and CTranslate2 runtimes also honour) each size
a pool to the machine's logical core count the first time they load, and a
process that loads several of them (torch, onnxruntime, ctranslate2, numpy)
ends up with hundreds of OS threads that Python never sees. Every extra thread
is address space, a loader-lock participant on Windows, and one more thing that
can be mid-attach when something else needs to create a thread.

The caps are environment variables and must be set BEFORE the libraries load,
so the server entry point calls apply() ahead of its first heavy import. A
value the person already set is never overridden.
"""
from __future__ import annotations

import os

CAPPED_VARS = (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)

# Above this many OS threads the process is reported as thread-heavy.
OS_THREAD_WARN = 256


def default_cap() -> int:
    """Half the logical cores, between 2 and 8: enough for inference, small
    enough that four pools together stay bounded."""
    n = os.cpu_count() or 4
    return max(2, min(8, n // 2))


def apply(environ=None) -> dict:
    """Set every cap that is not already set. Returns what this call set."""
    env = os.environ if environ is None else environ
    cap = str(default_cap())
    set_now = {}
    for name in CAPPED_VARS:
        if not env.get(name):
            env[name] = cap
            set_now[name] = env[name]
    return set_now


def thread_heavy(os_threads) -> bool:
    return bool(os_threads and os_threads > OS_THREAD_WARN)
