"""Gauntlet finding F42: docs/security/threat-model.md said "The `IntegrityEngine`
verifies HMAC and Ed25519 signatures before every action" -- framed as the
central defense against unauthorized modification of Friday's governing
constraints.

Reality: IntegrityEngine.verify_manifest()/sign_manifest()
(governance/proof_of_integrity.py) are reachable only from on-demand HTTP
routes (GET /api/integrity, POST /api/integrity/verify -- routes/insights.py)
and from federation/provenance modules -- never from a pre-action gate. The
function actually documented elsewhere as running before every tool call is
a wholly different mechanism, `_governance_check()` (services/agent.py): a
ring-based allow/deny gate that HMAC-signs its own audit-log entry but never
imports or calls IntegrityEngine, verify_manifest(), or CLAWS_TEXT.

This probe pins docs/security/threat-model.md's corrected text (attestation is on-demand
via the API, not automatic before every action) and grounds that
_governance_check() still does not call IntegrityEngine, so the correction
describes the real, current wiring rather than a new, differently-wrong
claim. The section itself is corrected, not deleted.

Red -> green -> red-on-revert proof: fails against the old literal claim
(proving docs/security/threat-model.md really made it) and passes against the corrected
text.
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_THREAT_MODEL = _REPO_ROOT / "docs/security/threat-model.md"
_AGENT_PY = (_REPO_ROOT / "src" / "agent_friday" / "services" / "agent.py")

_OLD_CLAIM = "The `IntegrityEngine` verifies HMAC and\nEd25519 signatures before every action."


def _old_claim_would_fail_this_assertion():
    """Not a real test -- documents that the assertion below is
    discriminating (fails against the pre-fix text)."""
    assert _OLD_CLAIM.strip() == (
        "The `IntegrityEngine` verifies HMAC and\nEd25519 signatures before every action."
    )


class TestThreatModelIntegrityEngineNotPreactionGate:
    def test_threat_model_no_longer_claims_automatic_preaction_verification(self):
        text = _THREAT_MODEL.read_text(encoding="utf-8")
        assert _OLD_CLAIM not in text, (
            "docs/security/threat-model.md still claims IntegrityEngine verifies "
            "signatures before every action -- it is only reachable via "
            "on-demand API routes and federation/provenance modules, "
            "never from a pre-action gate; see findings.jsonl F42"
        )
        # Collapse whitespace/newlines so a rewrap can't dodge this check.
        collapsed = re.sub(r"\s+", " ", text)
        assert "verifies hmac and ed25519 signatures before every action" \
            not in collapsed.lower(), (
                "docs/security/threat-model.md restates the automatic-before-every-action "
                "claim in re-wrapped form"
            )

    def test_threat_model_section_still_exists_and_is_corrected(self):
        """The finding says correct, don't delete -- confirm the section
        header and IntegrityEngine mention both survive."""
        text = _THREAT_MODEL.read_text(encoding="utf-8")
        assert "Unauthorized modification of behavioral constraints" in text
        assert "IntegrityEngine" in text
        assert "on demand" in text.lower() or "on-demand" in text.lower(), (
            "the corrected section should state that attestation runs on "
            "demand via the API, not automatically before every action"
        )
        assert "/api/integrity" in text

    def test_governance_check_still_does_not_call_integrity_engine(self):
        """Grounding check: confirms the corrected claim is actually true
        right now -- the real pre-action gate, _governance_check(), still
        never imports or calls IntegrityEngine/verify_manifest/CLAWS_TEXT."""
        source = _AGENT_PY.read_text(encoding="utf-8")
        # Isolate the _governance_check function body.
        start = source.index("def _governance_check(")
        # Find the next top-level `def ` after this one to bound the body.
        next_def = source.index("\ndef ", start + 1)
        body = source[start:next_def]
        for marker in ("IntegrityEngine", "verify_manifest", "CLAWS_TEXT"):
            assert marker not in body, (
                f"_governance_check() now references {marker!r} -- if it "
                "has been wired to IntegrityEngine, F42's corrected "
                "docs/security/threat-model.md text needs revisiting"
            )
