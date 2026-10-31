"""Class 5, forbidden (NS-18.2-7): a model call that would reach Friday's own API from a program or
a shell session, drive Friday's code directly, or change Friday's governance records is denied
outright: never a card, and an approved card or a grant cannot authorise it.

Before: only `run_command` text naming the local API was forbidden. Code in the sandbox reaching
127.0.0.1/api, a shell session typing a request to it, `import agent_friday`, a file write into the
governance folder or approvals.json, and a command deleting the grants file were all one approval
card away. Synthetic paths only; nothing is run.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_friday.governance import action_gate as gate


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "friday_home", lambda: tmp_path)
    return tmp_path


@pytest.mark.parametrize("tool,args", [
    ("run_sandboxed", {"code": "import urllib.request\nurllib.request.urlopen('http://127.0.0.1:5000/api/approvals')"}),
    ("run_sandboxed", {"code": "from agent_friday.services import approvals\napprovals.decide('x', 'approve')"}),
    ("run_sandboxed", {"code": "import os, agent_friday.services.approvals as a\na.decide('x', 'approve')"}),
    ("send_to_session", {"session_id": "s1", "input": "iwr http://localhost:5000/api/stop/resume"}),
    ("run_command", {"command": "Remove-Item $HOME\\.friday\\governance\\grants.json"}),
    ("run_sandboxed", {"code": "import os\nos.remove(os.path.expanduser('~/.friday/governance/send_grants.json'))"}),
])
def test_reaching_the_gate_from_code_or_changing_governance_is_forbidden(tool, args):
    klass, why = gate.classify(tool, args)
    assert klass == "forbidden", (tool, klass, why)


@pytest.mark.parametrize("rel", ["governance/send_grants.json", "approvals.json", "ledger/actions.jsonl",
                                 "settings.json", "vault/.governance-key", "global_stop.json"])
def test_a_file_write_into_governance_state_is_forbidden(home, rel):
    klass, _why = gate.classify("write_file", {"path": str(home / rel), "content": "x"})
    assert klass == "forbidden", rel


def test_the_import_check_stays_fast_on_long_whitespace():
    import time
    t0 = time.monotonic()
    gate.forbidden_reason("run_sandboxed", {"code": "import " + "\t" * 60000 + "x"})
    assert time.monotonic() - t0 < 5, "the import check is polynomial on whitespace"


def test_ordinary_work_is_judged_as_before(home):
    assert gate.classify("write_file", {"path": str(home / "wiki" / "note.md"), "content": "x"})[0] != "forbidden"
    assert gate.classify("run_sandboxed", {"code": "import requests\nrequests.get('https://example.test/api/v1')"})[0] \
        != "forbidden", "another site's /api/ is not Friday's own"
    assert gate.classify("run_command", {"command": "Get-Content approvals.json"})[0] != "forbidden"
    assert gate.classify("read_file", {"path": str(home / "approvals.json")})[0] != "forbidden"


def test_forbidden_is_denied_before_any_card_or_grant(home, monkeypatch):
    receipts = []
    monkeypatch.setattr(gate, "_receipt", lambda entry: receipts.append(entry) or entry)
    v = gate.authorize("write_file", {"path": str(home / "governance" / "grants.json"), "content": "{}"},
                       {"approved_card": "appr_synthetic", "authenticated": True})
    assert v.action == "deny" and v.klass == "forbidden"
    assert receipts and receipts[-1].get("class") == "forbidden" and receipts[-1].get("decision") == "deny"
