"""The threat model states what the browser-origin controls do and do not protect.

Each assertion pins a claim to the implementation it describes, so the document
cannot drift from the code in either direction.
"""
from __future__ import annotations

import re
from pathlib import Path

from agent_friday.core import OWN_PAGE_CSP
from agent_friday.services import origin_gate

_ROOT = Path(__file__).resolve().parent.parent.parent
_MODEL = (_ROOT / "docs/security/threat-model.md").read_text(encoding="utf-8")
_MATRIX = (_ROOT / "docs/design/north-star/GAP-MATRIX.md").read_text(encoding="utf-8")


def _flat(text):
    return re.sub(r"\s+", " ", text)


def _section():
    m = re.search(r"^### 7\. .*?(?=^## |\Z)", _MODEL, re.S | re.M)
    assert m, "threat-model.md has no section 7 on browser origin and sandboxed markup"
    return _flat(m.group(0))


def test_section_names_the_controls():
    s = _section()
    for term in ("DNS rebinding", "Host header", "Sec-Fetch-Site", "X-Friday-Token",
                 "sandbox", "postMessage", "Content-Security-Policy"):
        assert term in s, f"section 7 does not mention {term}"


def test_section_states_the_no_metadata_exemption_and_its_risk():
    s = _section()
    assert "no browser metadata" in s
    assert "/api/session/token" in s
    assert "any process on this machine" in s.lower()


def test_section_states_reads_need_no_token():
    s = _section()
    assert "GET" in s and "no token" in s


def test_section_names_each_residual():
    s = _section()
    assert "'unsafe-inline'" in s and "'unsafe-eval'" in s
    assert "style" in s and "class" in s and "look-alike" in s
    assert "/api/creations/" in s and "script or style" in s


def test_csp_claim_matches_the_shipped_csp():
    assert "'unsafe-inline'" in OWN_PAGE_CSP and "'unsafe-eval'" in OWN_PAGE_CSP


def test_creation_asset_exception_matches_the_gate():
    assert origin_gate.ASSET_PREFIXES == ("/api/creations/",)


def test_ns_26_18_2_row_is_not_claimed_shipped_without_the_section():
    row = next(l for l in _MATRIX.splitlines() if l.startswith("| NS-26.18-2 "))
    assert "threat-model.md" in row and "section 7" in row.lower()


def test_verified_date_is_current():
    assert "Last verified against the code: 2026-09-30" in _MODEL
