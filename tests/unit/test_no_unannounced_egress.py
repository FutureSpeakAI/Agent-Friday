"""No unannounced egress (north star §26.7, §29.6; BD-15 slice P4): mail,
tool-server calls, Calendar/Tasks/Contacts changes, posts, published pages,
pages Friday opens and searches each leave one ledger row with its context
manifest, and show on the privacy map. Synthetic fixtures only; nothing
reaches a network.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent_friday.services import egress_gate as eg

KEY = b"synthetic-governance-key-for-tests-0000000000000000"
SRC = Path(__file__).resolve().parents[2] / "src" / "agent_friday"


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    import agent_friday.paths as paths
    from agent_friday.governance import proof_of_integrity as poi
    from agent_friday.services import credential_store as cs
    from agent_friday.services import off_record
    from agent_friday.services import sent_text_store as sts
    log = tmp_path / "vault" / "egress-log.jsonl"
    log.parent.mkdir(parents=True)
    monkeypatch.setattr(eg, "_DEFAULT_LOG", log)
    monkeypatch.setattr(paths, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(poi, "get_governance_key", lambda: KEY)
    monkeypatch.setattr(cs, "protect", lambda data: (b"SEALED:" + data[::-1], "keystore"))
    monkeypatch.setattr(cs, "unprotect", lambda blob: blob[len(b"SEALED:"):][::-1])
    monkeypatch.setattr(cs, "looks_protected", lambda blob: "keystore" if blob.startswith(b"SEALED:") else None)
    monkeypatch.setattr(sts, "_vault_key", lambda derive=True: None)
    monkeypatch.setattr(sts, "_passphrase_set", lambda: False)
    monkeypatch.setattr(off_record, "active", lambda *a, **k: False)
    return log


def _calls(log):
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if '"kind": "call"' in x] \
        if log.exists() else []


def test_a_service_send_is_one_row_with_its_manifest_and_kept_text(ledger):
    from agent_friday.services import context_manifest as cm
    from agent_friday.services import sent_text_store as sts
    eg.record_service_send("mail", "gmail", destination="gmail.googleapis.com",
                           content="Subject: synthetic\r\n\r\nSynthetic body words", recipients=2, part="message")
    row = _calls(ledger)[0]
    assert row["channel"] == "mail" and row["destination"] == "gmail.googleapis.com"
    assert row["recipients"] == 2 and row["result"] == "sent" and row["via"] == "service:mail"
    assert row["payload_fingerprint"] and row["text_kept"] is True
    assert "Synthetic body words" not in json.dumps(row)
    assert "Synthetic body words" in sts.read(row["payload_fingerprint"])["text"]
    assert cm.read(row["manifest_id"])["sources"][0]["part"] == "message"


def test_a_failed_service_send_keeps_nothing(ledger):
    eg.record_service_send("mail", "gmail", content="Synthetic body", result="failed")
    row = _calls(ledger)[0]
    assert row["result"] == "failed" and "payload_fingerprint" not in row and row["text_kept"] is False


class _Req:
    def __init__(self, fail=False):
        self.fail = fail

    def execute(self):
        if self.fail:
            raise RuntimeError("synthetic API error")
        return {"id": "evt-synthetic"}


class _Events:
    def insert(self, **kw):
        return _Req()

    def patch(self, **kw):
        return _Req(fail=True)

    def list(self, **kw):
        return _Req()


class _Svc:
    def events(self):
        return _Events()


def test_a_calendar_write_is_recorded_and_a_read_is_not(ledger):
    from agent_friday.services import egress_record as er
    svc = er.calendar(_Svc())
    svc.events().list(calendarId="primary").execute()
    assert _calls(ledger) == []
    svc.events().insert(calendarId="primary", body={"summary": "Synthetic", "attendees": [{"email": "a@example.invalid"}]}).execute()
    with pytest.raises(RuntimeError):
        svc.events().patch(calendarId="primary", eventId="x", body={"summary": "Changed"}).execute()
    rows = _calls(ledger)
    assert [r["result"] for r in rows] == ["sent", "failed"]
    assert rows[0]["channel"] == "calendar" and rows[0]["recipients"] == 1


def test_every_tool_server_call_is_recorded(ledger):
    from agent_friday import mcp_client

    class _Server:
        status = "running"
        url = "https://tools.example.invalid/mcp"

        def call_tool(self, tool, arguments, timeout=None):
            return "synthetic result"
    mgr = mcp_client.MCPManager.__new__(mcp_client.MCPManager)
    mgr.servers = {"synthetic": _Server()}
    assert mgr.call("synthetic", "lookup", {"q": "synthetic"}) == "synthetic result"
    row = _calls(ledger)[0]
    assert row["channel"] == "mcp" and row["provider"] == "mcp:synthetic"
    assert row["destination"] == "tools.example.invalid" and row["result"] == "sent"


def test_a_page_friday_opens_is_recorded_unless_it_came_from_the_cache(ledger, monkeypatch):
    from agent_friday.services import web_fetch as wf
    monkeypatch.setattr(wf, "_fetch", lambda url, **k: wf.FetchResult({"ok": True, "url": url, "via": "direct"}))
    wf.fetch("https://news.example.invalid/story")
    monkeypatch.setattr(wf, "_fetch", lambda url, **k: wf.FetchResult({"ok": True, "url": url, "from_cache": True}))
    wf.fetch("https://news.example.invalid/story")
    monkeypatch.setattr(wf, "_fetch", lambda url, **k: wf._fail(url, "a private address", "refused_unsafe"))
    wf.fetch("http://127.0.0.1/admin")
    rows = _calls(ledger)
    assert len(rows) == 1 and rows[0]["channel"] == "web_fetch" and rows[0]["destination"] == "news.example.invalid"


def test_a_search_is_recorded_for_each_remote_backend_it_went_to(ledger):
    from agent_friday.services import web_search as ws
    ws._record_query("wigolo", "synthetic query", {"status": ws.SearchStatus.OK})
    ws._record_query("brave", "synthetic query", {"status": ws.SearchStatus.NO_BACKEND})
    ws._record_query("brave", "synthetic query", {"status": ws.SearchStatus.OK})
    rows = _calls(ledger)
    assert len(rows) == 1 and rows[0]["destination"] == "api.search.brave.com"


def test_the_map_names_service_destinations(ledger):
    from agent_friday.services import privacy_map as pm
    assert pm.destination_words("gmail") == "Gmail (email you sent)"
    assert pm.destination_words("mcp:files") == "Tool server “files” (MCP)"
    keys = {n["key"] for n in pm.NOT_RECORDED}
    assert not {"mail", "mcp", "connectors", "publishing", "fetches"} & keys


# ── the guard: a new send path cannot be added without its record ────────────

_GOOGLE_WRITE = re.compile(r"\.(events|tasks|tasklists|people|calendars|calendarList)\(\)\s*\.\s*"
                           r"(insert|patch|update|delete|quickAdd|move|createContact|updateContact|deleteContact)\(")
_SENDS = (
    (re.compile(r"\.messages\(\)\s*\.\s*send\("), "record_service_send", "a Gmail send"),
    (re.compile(r"\bsp\.call_tool\("), "record_service_send", "a tool-server call"),
    (re.compile(r"\badapter\.publish\("), "record_service_send", "a post"),
    (re.compile(r"(?<!def )\bpublish_remote\("), "record_service_send", "a published page"),
    (re.compile(r"api\.search\.brave\.com"), "record_service_send", "a Brave search"),
)


def _sources():
    for p in SRC.rglob("*.py"):
        yield p, p.read_text(encoding="utf-8", errors="replace")


def test_every_google_write_goes_through_a_recorded_client():
    bad = [str(p.relative_to(SRC)) for p, t in _sources()
           if _GOOGLE_WRITE.search(t) and "egress_record" not in t]
    assert bad == [], "Calendar/Tasks/Contacts writes with no egress record: %s" % bad


@pytest.mark.parametrize("pattern,needs,what", _SENDS, ids=[s[2] for s in _SENDS])
def test_every_known_send_path_records_itself(pattern, needs, what):
    bad = [str(p.relative_to(SRC)) for p, t in _sources()
           if pattern.search(t) and needs not in t and p.name != "egress_gate.py"
           and not str(p.relative_to(SRC)).startswith(("services/platforms", "services\\platforms"))]
    assert bad == [], f"{what} with no egress record in: {bad}"
