"""Regression-suite conftest — provides the Flask test `client` fixture by
reusing the same server import + LLM kill-switch machinery as the API suite.

Importing tests.api.conftest registers its fixtures (app, client, patch_app,
_no_real_llm autouse) for this package too, so regression tests that hit routes
work exactly like the API tests.
"""
from __future__ import annotations

# Re-export the API suite's fixtures (client, app, patch_app, _no_real_llm, ...).
from tests.api.conftest import *  # noqa: F401,F403
