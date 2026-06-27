"""Backwards-compatible shim — real module at src/agent_friday/core.py"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

from agent_friday.core import *  # noqa: F401, F403
from agent_friday.core import app, sock  # noqa: F401 — ensure Flask globals are importable
