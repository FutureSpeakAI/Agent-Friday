"""The cloud voice engines, spelled once. Import-free, so a failing gate can still read it.

Held by scripts/check_cloud_engine_literals.py and tests/unit/test_cloud_engine_literals_guard.py.
"""
from __future__ import annotations

from typing import Any

CLOUD_ENGINES = ("gemini", "gemini-live", "openai")
SWITCH_ENGINES = ("gemini", "openai")


def is_cloud(engine: Any) -> bool:
    return str(engine or "").strip().lower() in CLOUD_ENGINES
