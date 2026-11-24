"""One live line per workspace for the owner's phone list (UI-CRITIC Q6; contract draft 3, section 4 addition).

Stub: the rules are in tests/unit/test_tv_ws_summary.py.
"""
from __future__ import annotations

import time

TEXT_MAX = 80
TTL_S = 60
_clock_real = time.time
_clock = time.time


def forget() -> None:
    pass


def line(ws: str):
    return None
