"""Root conftest — hermetic environment for the WHOLE Friday test suite.

Kept deliberately light: it sets up isolation env vars and sys.path but does NOT
import `server` (which pulls in chromadb + sentence-transformers, ~18s). Unit
tests under tests/unit/ import only the single module they target and stay fast.
The heavyweight `server`/Flask fixtures live in tests/api/conftest.py, scoped to
the API tests that actually need them.

  * `FRIDAY_TESTING=1` — set before anything imports `server`, so the module's
    background daemon loops never start.
  * Windows home redirected to a throwaway temp dir — every `Path.home()/.friday`,
    creations dir, vault, settings.json resolves under isolation. Tests never
    touch the real user's data.

Run everything:   pytest tests/unit tests/api -q
Unit only (fast): pytest tests/unit -q
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# ── Hermetic environment — MUST run before any `import server` ────────────────
_TEST_HOME = Path(tempfile.mkdtemp(prefix="friday_test_home_"))
os.environ["FRIDAY_TESTING"] = "1"
os.environ["USERPROFILE"] = str(_TEST_HOME)
os.environ["HOMEDRIVE"] = _TEST_HOME.drive or "C:"
os.environ["HOMEPATH"] = str(_TEST_HOME)[len(_TEST_HOME.drive):] or "\\"
os.environ.setdefault("FRIDAY_PASSWORD", "test-vault-passphrase")
# FRIDAY_VAULT_PASSPHRASE is the canonical vault key env var; FRIDAY_PASSWORD is
# the backward-compat fallback.  Set both so tests exercise the new code path.
os.environ.setdefault("FRIDAY_VAULT_PASSPHRASE", "test-vault-passphrase")
# Quieten noisy optional-dep warnings during test runs.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

_ROOT = Path(__file__).resolve().parent.parent
# With the src/ layout, add both the project root (for `from tests.*` imports)
# and src/ (for `import agent_friday.*` imports without an editable install).
_SRC = _ROOT / "src"
for _p in (str(_SRC), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

# The canned model reply, shared so api tests can assert against it.
CANNED_TEXT = "[[friday-test-stub-response]]"


@pytest.fixture
def test_home():
    """Path to the isolated temp home for this run."""
    return _TEST_HOME


@pytest.fixture
def friday_dir():
    """The isolated ~/.friday directory (created on demand)."""
    d = _TEST_HOME / ".friday"
    d.mkdir(parents=True, exist_ok=True)
    return d
