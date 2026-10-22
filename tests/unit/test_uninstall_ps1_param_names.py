"""uninstall.ps1: no variable may shadow a parameter under a different case.

PowerShell variable names ignore case, so `$bundleMade = $null` cleared the
`-BundleMade` switch the setup program passes, and the uninstall receipt said
no bundle was made first (installer CI leg bundle-uninstall-restore).
"""
from __future__ import annotations

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "packaging" / "windows" / "uninstall.ps1"


def _params(text: str) -> list[str]:
    m = re.search(r"^param\s*\((.*?)^\)", text, re.S | re.M | re.I)
    assert m, "uninstall.ps1 has a script-level param block"
    return re.findall(r"\]\s*\$(\w+)", m.group(1))


def test_no_local_variable_shadows_a_parameter_by_case():
    text = SCRIPT.read_text(encoding="utf-8-sig")
    params = _params(text)
    assert "BundleMade" in params and "BundleTo" in params
    by_lower = {p.lower(): p for p in params}
    clashes = []
    for m in re.finditer(r"\$(\w+)\s*=(?!=)", text):
        name = m.group(1)
        want = by_lower.get(name.lower())
        if want and want != name:
            line = text.count("\n", 0, m.start()) + 1
            clashes.append(f"line {line}: ${name} is the parameter ${want}")
    assert not clashes, clashes


def test_receipt_reads_the_switch_the_setup_program_passes():
    text = SCRIPT.read_text(encoding="utf-8-sig")
    line = next(l for l in text.splitlines() if "bundle_first" in l and "=" in l)
    assert "$BundleMade" in line
