"""The rule every Flint seat is spawned under: weights loaded, never memory-mapped.

Flint (Recursive's coding model, shortlist id ``flint:40b``) is a 39.8B
mixture-of-experts model. On a 12 GB card its routed experts are split between
the card and the processor (``--n-cpu-moe``). With llama.cpp's default loading
the file is memory-mapped, and while the seat generates the mapped pages are
counted in its working set: measured on the reference PC (RTX 4070 12 GB,
32 GB RAM, live Friday beside it), the seat grew to 11.3 GB and available RAM
fell to 0.7 GB. With ``--load-mode none`` the same seat held about 5.9 GB and
available RAM stayed at or above 6.3 GB.

So a Flint seat is always spawned with ``--load-mode none``, whatever its
record, an override or a later default says: any other loading flag on the
command is removed and the rule is appended last (llama.cpp takes the last
occurrence of a flag). A seat counts as Flint when the model id is a shortlist
entry served by the ``llama.cpp-flint`` runtime, when its record says the GGUF
architecture is ``flint``, or when the server binary lives in the
``llama.cpp-flint`` runtime folder.

Spec: core_models_flint_laya_bonsai2, CT-E29.
"""
from __future__ import annotations

import re

RUNTIME = "llama.cpp-flint"
ARCHITECTURE = "flint"

#: The memory-safe loading mode.
LOAD_MODE = "none"

#: Loading flags that take a value, and the boolean ones; every spelling is
#: removed before the rule is applied.
_VALUE_FLAGS = ("--load-mode", "-lm")
_BOOL_FLAGS = ("--mmap", "--no-mmap", "--mlock", "--no-mlock",
               "-dio", "--direct-io", "--no-direct-io")

#: Environment variables llama-server reads for the same settings; a Flint
#: seat's environment carries none of them.
ENV_VARS = ("LLAMA_ARG_LOAD_MODE", "LLAMA_ARG_MMAP", "LLAMA_ARG_MLOCK", "LLAMA_ARG_DIO")


def is_flint_seat(model_id, *, binary=None, record=None) -> bool:
    """True when this spawn serves Flint (see the module docstring)."""
    rec = record if isinstance(record, dict) else {}
    if str(rec.get("architecture") or "").strip().lower() == ARCHITECTURE:
        return True
    if binary is not None:
        try:
            # Either separator, so a Windows path reads the same on every OS;
            # a versioned folder ("llama.cpp-flint-b11803") counts too.
            if any(p.lower().startswith(RUNTIME) for p in re.split(r"[\\/]+", str(binary))):
                return True
        except Exception:
            pass
    try:
        from agent_friday.services import model_shortlist as sl
        entry = sl.get(str(model_id)) or {}
        if entry.get("runtime") == RUNTIME or str(entry.get("architecture") or "").lower() == ARCHITECTURE:
            return True
    except Exception:
        pass
    return False


def enforce(cmd) -> list:
    """`cmd` with every loading flag removed and ``--load-mode none`` last."""
    out = []
    skip = False
    for a in list(cmd):
        s = str(a)
        if skip:
            skip = False
            if not s.startswith("-"):
                continue                      # the dropped flag's value
        if s in _VALUE_FLAGS:
            skip = True                       # drop the flag (and its value, if it has one)
            continue
        if any(s.startswith(f + "=") for f in _VALUE_FLAGS) or s in _BOOL_FLAGS:
            continue
        out.append(a)
    return out + ["--load-mode", LOAD_MODE]


def spawn_env(env) -> dict:
    """The environment for a Flint seat: `env` without any loading variable."""
    return {k: v for k, v in dict(env).items() if k.upper() not in ENV_VARS}
