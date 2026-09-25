"""What the README and the privacy pages say leaves the PC on its own must
match what the code does by default."""
from __future__ import annotations

import re
from pathlib import Path

import agent_friday.core as core
from agent_friday.services import web_fonts

ROOT = Path(__file__).resolve().parents[2]


def _flat(rel: str) -> str:
    return re.sub(r"\s+", " ", (ROOT / rel).read_text(encoding="utf-8")).lower()


def test_no_source_claims_the_update_check_is_the_only_background_request():
    pattern = re.compile(r"only request friday would\s+(#\s*)?make on its own schedule", re.I)
    for base in ("src", "tests"):
        for p in (ROOT / base).rglob("*.py"):
            text = re.sub(r"\s+", " ", p.read_text(encoding="utf-8", errors="ignore"))
            assert not pattern.search(text), p


def test_the_readme_does_not_list_a_probe_that_sends_nothing():
    assert core.DEFAULT_SETTINGS["network_probe"] == "route"
    readme = _flat("README.md")
    assert "what leaves on its own:** a connectivity probe" not in readme
    assert "connectivity probe sends nothing unless you opt in" in readme


def test_the_readme_and_privacy_page_name_google_fonts_while_it_is_the_default():
    if web_fonts.vendored() or not core.DEFAULT_SETTINGS["web_fonts_from_google"]:
        return
    assert "google fonts" in _flat("README.md")
    assert "google fonts" in _flat("docs/user-guide/privacy.md")
    assert "google fonts" in _flat("docs/user-guide/background-network.md")


def test_background_network_page_documents_every_switch():
    page = _flat("docs/user-guide/background-network.md")
    for key in ("network_probe", "web_fonts_from_google"):
        assert key in core.DEFAULT_SETTINGS
        assert key in page
