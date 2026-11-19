"""POST /api/ledger/evidence-pack: the owner's evidence pack from the real ledger (K6 PR C).

Real entries written through the real action gate, a real signed zip back, the pack's own
checker run on it. Rules held here are named in services/evidence_pack_export.py."""
import hashlib
import io
import json
import subprocess
import sys
import time
import zipfile

from agent_friday.governance import action_gate
from agent_friday.services import action_ledger as L
from agent_friday.services import evidence_pack_checker as checker

URL = "/api/ledger/evidence-pack"


def _press():
    from agent_friday import core
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": core._current_api_token(),
            "X-Friday-Desk": __import__("agent_friday.services.desk_press", fromlist=["x"])._KEY}


def _day():
    return time.strftime("%Y-%m-%d", time.gmtime())


def _seed():
    for tool, decision in (("send_email", "allow"), ("create_calendar_event", "deny")):
        action_gate._receipt({"tool": tool, "class": "outward", "decision": decision,
                              "surface": "test"})


def _export(client, **kw):
    return client.post(URL, json={"since": _day(), "until": _day()}, headers=_press(), **kw)


def _files(response):
    with zipfile.ZipFile(io.BytesIO(response.data)) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_a_press_on_this_pc_gets_a_zip_the_checker_passes_with_the_owners_key_pinned(client):
    _seed()
    r = _export(client)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    files = _files(r)
    manifest = json.loads(files["manifest.json"])
    assert manifest["entries"] >= 2 and manifest["public_key"]
    problems, notes = checker.check_files(files, manifest["public_key"])
    assert problems == [], problems
    assert any("the key you gave" in n for n in notes)


def test_the_check_script_in_the_zip_passes_in_a_clean_interpreter(client, tmp_path):
    _seed()
    for name, data in _files(_export(client)).items():
        (tmp_path / name).write_bytes(data)
    run = subprocess.run([sys.executable, "-I", "-S", str(tmp_path / "check_pack.py"),
                          str(tmp_path)], capture_output=True, text=True, cwd=tmp_path)
    assert run.returncode == 0 and "everything checked holds" in run.stdout, run.stdout


def test_not_a_press_on_this_pc_is_refused_and_nothing_is_written(client):
    _seed()
    before = len(L.entries(["export"]))
    body = {"since": _day(), "until": _day()}
    assert client.post(URL, json=body).status_code == 403
    assert client.post(URL, json=body, headers=_press(),
                       environ_base={"REMOTE_ADDR": "203.0.113.7"}).status_code == 403
    assert len(L.entries(["export"])) == before


def test_a_missing_range_is_refused(client):
    assert client.post(URL, json={"since": _day()}, headers=_press()).status_code == 400


def test_the_export_writes_one_ledger_line_with_the_manifest_hash(client):
    _seed()
    before = len(L.entries(["export"]))
    r = _export(client)
    lines = L.entries(["export"], newest_first=False)
    assert len(lines) == before + 1
    rec = lines[-1]["rec"]
    manifest_bytes = _files(r)["manifest.json"]
    assert rec["event"] == "evidence_pack_exported" and rec["signed"] is True
    assert rec["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert L.verify(force=True)["ok"] is True


def test_an_unreadable_archive_writes_nothing(client, monkeypatch):
    _seed()
    before = len(L.entries(["export"]))

    def unreadable(*a, **k):
        raise L.LedgerError("the archive file is missing")

    monkeypatch.setattr(L, "entries", unreadable)
    r = _export(client)
    monkeypatch.undo()
    assert r.status_code == 409 and r.get_json()["code"] == "pack_refused"
    assert "nothing was exported" in r.get_json()["error"]
    assert len(L.entries(["export"])) == before


def test_a_break_in_range_still_gives_a_pack_with_the_break_first(client, monkeypatch):
    _seed()

    real_append = L.append

    def refuses(kind, rec, **k):
        if kind == "export":
            raise L.LedgerError("the chain has a break")
        return real_append(kind, rec, **k)    # everything else the request does still writes

    from agent_friday.governance import action_gate
    monkeypatch.setattr(action_gate, "ledger_grant_rows", lambda: [])   # no grants, whatever an earlier test left
    monkeypatch.setattr(L, "verify", lambda **k: {"ok": False, "first_break": "seq 2 was changed"})
    monkeypatch.setattr(L, "append", refuses)
    r = _export(client)
    monkeypatch.undo()
    assert r.status_code == 200 and r.headers["X-Friday-Export-Recorded"] == "no"
    files = _files(r)
    assert "seq 2 was changed" in files["SUMMARY.md"].decode().splitlines()[2]
    assert json.loads(files["manifest.json"])["ledger_verified"] is False
    problems, _ = checker.check_files(files, json.loads(files["manifest.json"])["public_key"])
    assert problems == [], "the pack is whole even though its own export line could not be written"


def test_a_normal_export_says_it_was_recorded(client):
    _seed()
    assert _export(client).headers["X-Friday-Export-Recorded"] == "yes"


def test_the_summary_in_a_real_pack_says_supports_and_makes_no_claim(client):
    _seed()
    text = _files(_export(client))["SUMMARY.md"].decode()
    assert "supports ISO 27001 evidence-gathering" in text and "makes no compliance claim" in text


def test_a_real_approval_is_listed_by_id_and_its_text_never_reaches_the_pack(client):
    from agent_friday.services import approvals
    canary = "CANARY-5c07"
    card = approvals.create_approval(kind="send_email", subject_type="evidence_test",
                                     subject_id="s1", title=canary, description=canary,
                                     action_description=canary)
    _seed()
    files = _files(_export(client))
    rows = [json.loads(line) for line in files["approvals.jsonl"].splitlines()]
    assert [r["approval_id"] for r in rows] == [card["approval_id"]]
    assert all(canary.encode() not in data for data in files.values())


def test_grants_that_cannot_be_read_refuse_the_pack_rather_than_leave_them_out(client, monkeypatch):
    from agent_friday.governance import action_gate

    def unreadable():
        raise L.LedgerError("the ledger has a break to review")
    _seed()
    monkeypatch.setattr(action_gate, "ledger_grant_rows", unreadable)
    before = len(L.entries(["export"]))
    r = _export(client)
    monkeypatch.undo()
    assert r.status_code == 409 and r.get_json()["code"] == "pack_refused"
    assert "nothing was exported" in r.get_json()["error"] and len(L.entries(["export"])) == before



def test_a_break_in_range_with_a_real_signed_grant_on_file_still_gives_the_pack(client, monkeypatch):
    # The rule "a break still gives a pack" held only for owners with no grant: reading a grant's uses is a strict ledger
    # read, which a break stops. A real grant is created here (no stub), so the break must not turn into a refusal.
    _seed()
    action_gate.create_grant(tools=["send_email"], scope="job-pack", expires_in_seconds=600, max_uses=1)
    real_entries = L.entries

    def strict_stops_on_a_break(kinds=None, **k):
        if k.get("strict") and k.get("gate", True):
            raise L.LedgerError("the chain has a break to review; strict readers wait")
        return real_entries(kinds, **k)
    monkeypatch.setattr(L, "verify", lambda **k: {"ok": False, "first_break": "seq 2 was changed"})
    monkeypatch.setattr(L, "entries", strict_stops_on_a_break)
    r = _export(client)
    monkeypatch.undo()
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    files = _files(r)
    assert json.loads(files["manifest.json"])["ledger_verified"] is False
    assert len(files["grants.jsonl"].decode().strip().splitlines()) == 1, "the grant is in the pack, usage unread"
