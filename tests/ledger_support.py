"""Reading and tampering with the action ledger in tests.

Receipts that used to be lines in files of their own (decision-bom.jsonl,
activity_ledger.jsonl, ...) are entries in services/action_ledger.py. These
helpers give tests the old shapes back: each `rec` as the writer signed it.
"""
from __future__ import annotations

import json

from agent_friday.services import action_ledger


def rows(kind: str) -> list[dict]:
    """Every record of one kind, oldest first, as its writer stored it."""
    out = []
    for e in action_ledger.entries([kind], newest_first=False):
        r = e.get("rec")
        if isinstance(r, dict):
            out.append(dict(r))
    return out


def decisions() -> list[dict]:
    return rows("decision")


def text(kind: str) -> str:
    """The records of one kind as JSON lines (for 'never contains' checks)."""
    return "\n".join(json.dumps(r) for r in rows(kind))


def tamper(kind: str, match, mutate) -> None:
    """Change the record of the first entry of `kind` that `match(rec)` picks,
    in place on disk, leaving its hash and signature as they were."""
    p = action_ledger.live_path()
    lines = p.read_text(encoding="utf-8").splitlines()
    for i, ln in enumerate(lines):
        e = json.loads(ln)
        if e.get("kind") == kind and match(e.get("rec") or {}):
            mutate(e["rec"])
            lines[i] = json.dumps(e)
            break
    else:
        raise AssertionError("no %s entry matched" % kind)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    action_ledger.reset_caches()


def forge(kind: str, rec: dict) -> None:
    """Append a line that looks like an entry but was not written by the ledger."""
    p = action_ledger.live_path()
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"v": 1, "seq": 10 ** 9, "kind": kind, "rec": rec,
                            "prev": "0" * 64, "hash": "0" * 64, "sig": "0" * 64}) + "\n")
    action_ledger.reset_caches()
