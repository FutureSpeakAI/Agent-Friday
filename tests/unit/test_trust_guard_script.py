"""scripts/check_trust_in_governance.py fails when governance reads trust.

Red by the named mutation: add `from agent_friday import trust` to
`governance/action_gate.py`; the script (and this test's live run) fail.
The test proves the script can fail by running it over a mutated copy.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_trust_in_governance as guard  # noqa: E402


def test_the_tree_is_clean(capsys):
    assert guard.main() == 0
    assert "OK" in capsys.readouterr().out


@pytest.mark.parametrize("line", [
    "from agent_friday import trust",
    "from agent_friday.trust import people",
    "import agent_friday.people_graph",
    "from agent_friday.source_trust_graph import get_source_trust_graph",
    "def f():\n    from agent_friday.trust.log import read\n",
    "PATH = 'people_graph.json'",
    "x = open(FRIDAY_DIR / 'trust/log/people.jsonl')",
])
def test_a_governance_module_that_reads_trust_fails_the_guard(tmp_path, line, capsys):
    src = (ROOT / "src" / "agent_friday" / "governance" / "action_gate.py").read_text(encoding="utf-8")
    mutated = tmp_path / "action_gate.py"
    mutated.write_text(src + "\n" + line + "\n", encoding="utf-8")
    assert guard.main([str(mutated)]) == 1
    assert "FAILED" in capsys.readouterr().err


def test_the_future_seam_is_the_only_exemption():
    assert guard.ALLOWED_SEAM.name == "trust_hints.py"
    assert not guard.ALLOWED_SEAM.exists(), "the seam is a Phase 2 change with its own review"
