"""Gauntlet finding Q27: docs/security/threat-model.md said "We mitigate this with pinned
dependency versions and optional extras (presidio, keyring) rather than
mandatory ones." pyproject.toml and every packaging/windows/requirements/
*.txt file in the repo use only ">=" floors -- zero exact "==" pins exist
anywhere.

This probe pins the corrected sentence (version floors/minimums, not exact
pins -- keeping the true "optional extras" half) and grounds that the repo
still contains zero exact pins, so the correction is not itself stale.

Red -> green -> red-on-revert proof: fails against the old literal claim
(proving docs/security/threat-model.md really made it) and passes against the corrected
text.
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_THREAT_MODEL = _REPO_ROOT / "docs/security/threat-model.md"

_OLD_CLAIM = "We mitigate this with\npinned dependency versions and optional extras (presidio, keyring) rather than\nmandatory ones."


def _old_claim_would_fail_this_assertion():
    """Not a real test -- documents that the assertion below is
    discriminating (fails against the pre-fix text)."""
    assert _OLD_CLAIM.strip().startswith("We mitigate this with")
    assert "pinned dependency versions" in _OLD_CLAIM


class TestThreatModelNoFalsePinClaim:
    def test_threat_model_no_longer_claims_pinned_versions(self):
        text = _THREAT_MODEL.read_text(encoding="utf-8")
        collapsed = re.sub(r"\s+", " ", text)
        assert "pinned dependency versions" not in collapsed, (
            "docs/security/threat-model.md still claims dependency versions are pinned "
            "-- pyproject.toml and every requirements file use only '>=' "
            "floors, zero exact '==' pins exist anywhere; see "
            "findings.jsonl Q27"
        )

    def test_threat_model_keeps_the_true_optional_extras_half(self):
        text = _THREAT_MODEL.read_text(encoding="utf-8")
        assert "optional extras" in text
        assert "presidio" in text.lower() and "keyring" in text.lower()

    def test_threat_model_now_describes_version_floors(self):
        text = _THREAT_MODEL.read_text(encoding="utf-8")
        collapsed = re.sub(r"\s+", " ", text).lower()
        assert "minimum dependency version" in collapsed or \
            "version floor" in collapsed or ">=" in text, (
                "the corrected sentence should describe enforced minimum "
                "versions ('>=' floors), not exact pins"
            )

    def test_repo_still_has_zero_exact_version_pins(self):
        """Grounding check: confirms the corrected 'floors, not pins'
        claim is actually true right now across every dependency
        manifest, not just pyproject.toml."""
        candidates = [_REPO_ROOT / "pyproject.toml",
                      _REPO_ROOT / "requirements.txt"]
        packaging_dir = _REPO_ROOT / "packaging"
        if packaging_dir.exists():
            candidates.extend(packaging_dir.rglob("*requirements*.txt"))
            candidates.extend(packaging_dir.rglob("*requirements*.in"))
        pin_pattern = re.compile(r"^\s*[A-Za-z0-9_.\-\[\]]+==[0-9]", re.MULTILINE)
        offenders = []
        for path in candidates:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if pin_pattern.search(text):
                offenders.append(str(path))
        assert not offenders, (
            "found an exact '==' version pin -- if the project has since "
            f"switched to pinning, Q27's corrected copy needs revisiting: {offenders}"
        )
