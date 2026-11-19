"""NS-48.4-4 on the owner's own ledger: the pack holds labels and hashes only, run against entries the REAL action gate wrote.

tests/unit/test_evidence_pack.py proves the refusal with hand-built entries. This file feeds the pack what the running
Friday actually writes: real decisions from action_gate.authorize and real outcomes from record_outcome, for calls whose
arguments, title and description carry a canary string. If the gate ever puts argument text into a ledger line the pack
passes on, the canary shows up in the zip, or the export is refused whole; either way this test fails and says which.

The check is shown able to fail (Q2): the last test writes the canary into a ledger line the way a careless caller
would (a `body` key) and the export must refuse rather than pass it on. Limit, stated plainly: it covers the tools and
keys used here; a new kind of entry with a new text key is not seen until it is added to this list.
"""
import io
import json
import time
import zipfile

from agent_friday.governance import action_gate
from agent_friday.services import action_ledger as L

URL = "/api/ledger/evidence-pack"
CANARY = "CANARY-5f27c1"


def _press():
    from agent_friday import core
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": core._current_api_token(),
            "X-Friday-Desk": __import__("agent_friday.services.desk_press", fromlist=["x"])._KEY}


def _day():
    return time.strftime("%Y-%m-%d", time.gmtime())


def _export(client):
    return client.post(URL, json={"since": _day(), "until": _day()}, headers=_press())


def _files(response):
    with zipfile.ZipFile(io.BytesIO(response.data)) as z:
        return {n: z.read(n) for n in z.namelist()}


def _real_calls():
    """Calls through the real gate, each carrying the canary where a caller would put owner text."""
    for tool, args in (
        ("send_email", {"to": "x@example.invalid", "subject": CANARY, "body": CANARY}),
        ("create_calendar_event", {"title": CANARY, "description": CANARY, "notes": CANARY}),
        ("write_file", {"path": CANARY, "content": CANARY}),
    ):
        verdict = action_gate.authorize(tool, args, {"surface": "test"})
        action_gate.record_outcome(tool, getattr(verdict, "receipt", None) or {}, "ok", detail="done")


def test_real_gate_entries_reach_the_pack_and_none_of_them_carries_the_canary(client):
    _real_calls()
    r = _export(client)
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    files = _files(r)
    ledger = files["ledger.jsonl"].decode("utf-8")
    assert "send_email" in ledger and "write_file" in ledger, "the real entries must be in the pack, or this proves nothing"
    leaked = sorted(name for name, data in files.items() if CANARY.encode() in data)
    assert leaked == [], f"owner text from real calls reached the pack: {leaked}"


def test_every_real_ledger_line_in_the_pack_has_only_labels_and_hashes_for_its_text_fields(client):
    _real_calls()
    files = _files(_export(client))
    free = []
    for line in files["ledger.jsonl"].decode("utf-8").splitlines():
        entry = json.loads(line)
        for key, value in (entry.get("rec") or {}).items():
            if isinstance(value, str) and len(value) > 120:
                free.append((entry["seq"], entry["kind"], key, len(value)))
    assert free == [], f"a real ledger line carries a long text field: {free[:5]}"


def test_the_check_can_fail_a_ledger_line_with_the_canary_in_a_text_key_is_refused_whole(client):
    L.append("outcome", {"action_id": "act-canary-control", "tool": "send_email", "status": "ok", "body": CANARY})
    r = _export(client)
    assert r.status_code == 409 and r.get_json()["code"] == "pack_refused"
    assert CANARY not in r.get_data(as_text=True)
