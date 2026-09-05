"""Gauntlet finding: sensitivity_classifier.py's own module docstring
claimed "Default on uncertainty: PRIVATE (fail-closed)... the egress gate
always uses PRIVATE as the default." egress_gate.py's _classify_cloud()
actually calls classify(text, default=Tier.PUBLIC, egress=True) -- the
OPPOSITE value -- and egress_gate.py's own docstring correctly documents
"Uses PUBLIC as the base default... fail-closed behaviour is provided by
the embedding layer". Low severity (the real fail-closed guarantee is
intact via Layer 3's embedding similarity check) but the module's own
docstring was factually wrong about its single most safety-relevant
parameter.

Pure documentation fix -- no behavior change -- so this probe pins the
corrected text rather than exercising a code path.
"""
from __future__ import annotations

import inspect

import agent_friday.services.sensitivity_classifier as sc


class TestSensitivityClassifierDocstringMatchesEgress:
    def test_module_docstring_no_longer_claims_private_default(self):
        # Normalize whitespace (the docstring wraps across lines) so a
        # phrase split by a line break doesn't defeat a plain substring
        # check -- exactly the bug this audit already found once tonight
        # in a different probe (findings.jsonl F11).
        doc = " ".join((inspect.getdoc(sc) or "").split())
        assert "egress gate always uses PRIVATE as the default" not in doc, (
            "sensitivity_classifier.py's module docstring still claims the "
            "egress gate defaults to PRIVATE -- egress_gate.py's own "
            "_classify_cloud() actually passes default=Tier.PUBLIC"
        )
        assert "PUBLIC as the base default" in doc, (
            "the corrected docstring should state the real default "
            "(PUBLIC), matching egress_gate.py's own accurate description"
        )
