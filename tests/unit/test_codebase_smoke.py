"""The self-testing loop's first rung (docs/design/active/vibe-coding-salon.md
§4.11 item 1, Phase 2b): before a bundle is swapped in, its preview is loaded
in a headless browser and console errors are collected as evidence.
"""
from __future__ import annotations

import pytest

from agent_friday.services import codebases as cb

pw = pytest.importorskip("playwright.sync_api")


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    yield


def test_a_clean_bundle_passes_the_smoke_with_no_errors():
    rec = cb.create("Board", template="bundle")
    r = cb.smoke(rec["id"])
    assert r["ran"] is True and r["errors"] == [] and r["ok"] is True
    assert r["ms"] >= 0 and r["sha"]


def test_a_bundle_that_throws_fails_the_smoke_and_names_the_error():
    rec = cb.create("Board", template="bundle")
    html = cb.read(rec["id"], "index.html").replace("</body>", "<script>throw new Error('boom at load')</script></body>")
    cb.step(rec["id"], {"index.html": html}, "Broke it on purpose")
    r = cb.smoke(rec["id"])
    assert r["ran"] is True and r["ok"] is False
    assert any("boom at load" in e for e in r["errors"])


def test_smoke_without_a_browser_says_so_instead_of_pretending(monkeypatch):
    rec = cb.create("Board", template="bundle")
    monkeypatch.setattr(cb, "_playwright", lambda: None)
    r = cb.smoke(rec["id"])
    assert r["ran"] is False and r["ok"] is None and "not run" in r["note"].lower()
