"""Chaos: Friday killed in the middle of writing a receipt (north star
§34.16-2, the receipt-write part; services/action_ledger.py).

A real child process appends to the action ledger and dies (os._exit) after
half of an entry's line has reached the disk. Then: every entry written before
it still verifies, the unfinished bytes are set aside and recorded as
torn_tail (never silently dropped or patched), and the next append continues
the chain from the last whole entry."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CHILD = r"""
import os, sys
from pathlib import Path
key = Path(sys.argv[1])
from agent_friday.governance import proof_of_integrity as poi
poi._GOV_KEY_FILE = key
from agent_friday.services import action_ledger as L
L.reset_caches()
for i in range(3):
    L.append("probe", {"i": i}, durable=True)
real_sign = L._sign
def dying_sign(k, h):
    sig = real_sign(k, h)
    with open(L.live_path(), "ab") as f:          # half a line reaches the disk
        f.write(b'{"v":1,"seq":4,"kind":"probe","rec":{"i":3},"pr')
        f.flush()
        os.fsync(f.fileno())
    os._exit(9)                                    # and the process is gone
L._sign = dying_sign
L.append("probe", {"i": 3}, durable=True)
sys.exit(0)
"""


def _lines(L):
    return [json.loads(x) for x in L.live_path().read_text(encoding="utf-8").splitlines() if x.strip()]


def test_a_receipt_write_killed_part_way_keeps_the_chain(tmp_path, monkeypatch):
    from agent_friday.governance import proof_of_integrity as poi
    from agent_friday.services import action_ledger as L
    key = tmp_path / "gk"
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", key)
    monkeypatch.setitem(sys.modules, "keyring", None)
    L.reset_caches()
    env = dict(os.environ, PYTHON_KEYRING_BACKEND="keyring.backends.fail.Keyring", HOME=str(tmp_path),
               USERPROFILE=str(tmp_path),
               PYTHONPATH=str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""))
    r = subprocess.run([sys.executable, "-c", CHILD, str(key)], env=env, cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 9, (r.returncode, r.stderr[-2000:])
    raw = L.live_path().read_bytes()
    assert raw.endswith(b'"pr'), "the child did not die mid-line"

    L.reset_caches()
    L.append("probe", {"i": 4})                       # Friday starts again and writes on
    es = _lines(L)
    assert [e["kind"] for e in es] == ["probe", "probe", "probe", "torn_tail", "probe"]
    assert [e["seq"] for e in es] == [1, 2, 3, 4, 5]
    assert es[3]["prev"] == es[2]["hash"] and es[4]["prev"] == es[3]["hash"]   # the chain goes on
    kept = list((L.ledger_dir() / "torn").iterdir())
    assert kept and kept[0].read_bytes().startswith(b'{"v":1,"seq":4')        # set aside, not lost
    rep = L.verify()
    assert rep["records"] == 5
    assert all(p.get("seq") not in (1, 2, 3) for p in rep["problems"]), rep["problems"]
    assert any("unfinished" in p["reason"] for p in rep["problems"])          # said, for the owner to see
