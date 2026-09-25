"""Settings -> Privacy & Approvals offers the two owner-only controls, in the
served page and in its hand-maintained mirror, and they call the routes that
require this PC and the page token."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _privacy_tab(text: str) -> str:
    start = text.index("function SettingsTabPrivacy(")
    end = text.index("\nfunction ", start + 1)
    return text[start:end]


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/app.html"])
def test_privacy_tab_renders_both_panels(rel):
    text = (ROOT / rel).read_text(encoding="utf-8")
    tab = _privacy_tab(text)
    assert "ClawsRepinPanel" in tab and "KeystoreWrapPanel" in tab
    assert "function ClawsRepinPanel(" in text and "function KeystoreWrapPanel(" in text
    assert "'/api/governance/claws/repin'" in text
    assert "'/api/security/keystore/wrap'" in text
    assert "Re-confirm Friday" in text
