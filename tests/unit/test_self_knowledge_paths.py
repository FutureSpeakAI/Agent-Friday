"""Regression: self-knowledge docs must actually resolve and load.

After the src/ restructure, SELF.md and VOICE_DEMO.md stayed at the repo root
while core resolved them against the package root (src/agent_friday/) — both
loaders silently returned "" and Friday lost all knowledge of itself and its
own UI (chat AND voice). _res_file() now falls back to the repo root; these
tests pin that the files resolve, load, and describe the real UI surface.
"""
from agent_friday import core


def test_self_md_resolves_and_loads():
    assert core.SELF_MD_PATH.exists(), (
        f"SELF.md not found at {core.SELF_MD_PATH} — self-knowledge would "
        f"silently vanish from every system prompt")
    text = core._load_self_knowledge()
    assert len(text) > 1000
    # Must describe the REAL dock, not the retired Seeds/Gardens metaphor.
    assert "Studio" in text and "Marketplace" in text
    assert "Seeds & Gardens" not in text


def test_voice_demo_resolves_and_loads():
    assert core.VOICE_DEMO_MD_PATH.exists(), (
        f"VOICE_DEMO.md not found at {core.VOICE_DEMO_MD_PATH} — voice mode "
        f"would lose its ungated product knowledge")
    text = core._load_voice_demo()
    assert len(text) > 1000
    # The demo sheet must cover the creation capabilities the demos rely on.
    low = text.lower()
    assert "slide deck" in low or "presentation" in low
    assert "website" in low
    assert "video" in low
