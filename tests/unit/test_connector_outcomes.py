"""Per-connector fixtures for the Google changes (BD-06; NS-34.11-2, NS-34.11-3).

For each change Friday makes in a Google account, Google is faked three ways: access revoked
during the call, a reply that names nothing (an empty or malformed answer), and an ordinary
refusal. In each case the change must not be reported as done, the owner must hear what to do in
plain words (no exception text), a revoked grant must mark the account for reconnecting, and the
tool call must be recorded as not done (outcome "error", a receipt with ok=False, a result that
nothing counts as evidence). Before this, "Tasks write failed: <exception>" and
{"id": null, "title": "(untitled)"} both came back as a successful call.

Google is a fake here; no network, no real account. Synthetic ids only.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


class HttpError(Exception):
    """Shaped like googleapiclient.errors.HttpError: a status on .resp."""

    def __init__(self, status, text=""):
        super().__init__(text or "HttpError %s" % status)
        self.resp = SimpleNamespace(status=status)


RefreshError = type("RefreshError", (Exception,), {})     # google.auth's, by name


class _Exec:
    def __init__(self, outcome):
        self.outcome = outcome

    def execute(self, *a, **k):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class FakeGoogle:
    """One fake for Tasks, Calendar and People: every call answers with `outcome`; a read of an
    event (`get`) answers with an event so an update reaches its write."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.writes = []

    # Tasks / Calendar / People all hang their calls off one object
    def tasks(self):
        return self

    def events(self):
        return self

    def people(self):
        return self

    def insert(self, **kw):
        self.writes.append(("insert", kw))
        return _Exec(self.outcome)

    def patch(self, **kw):
        self.writes.append(("patch", kw))
        return _Exec(self.outcome)

    def delete(self, **kw):
        self.writes.append(("delete", kw))
        return _Exec(self.outcome)

    def createContact(self, **kw):
        self.writes.append(("createContact", kw))
        return _Exec(self.outcome)

    def get(self, **kw):
        return _Exec({"id": kw.get("eventId", "e1"), "summary": "Dentist",
                      "start": {"dateTime": "2026-11-02T10:00:00+00:00"},
                      "end": {"dateTime": "2026-11-02T11:00:00+00:00"}})


@pytest.fixture
def marked(monkeypatch):
    """Which accounts were marked, and how (google_accounts._mark_status)."""
    from agent_friday.services import google_accounts as ga
    seen = []
    monkeypatch.setattr(ga, "_mark_status", lambda aid, status, touch_sync=False: seen.append((aid, status)))
    monkeypatch.setattr(ga, "_LAST_PROBLEM", {})
    return seen


@pytest.fixture
def tasks(monkeypatch, marked):
    from agent_friday.services import google_accounts as ga
    import googleapiclient.discovery as discovery
    box = {}
    monkeypatch.setattr(ga, "credentials_for", lambda aid: object())
    monkeypatch.setattr(ga, "tasks_writable", lambda aid: True, raising=False)
    monkeypatch.setattr(ga, "_gate_task_text", lambda body: (dict(body), ""))
    monkeypatch.setattr(ga.cs, "audit_event", lambda *a, **k: None)
    monkeypatch.setattr(discovery, "build", lambda *a, **k: box["svc"])

    def run(op, outcome):
        box["svc"] = FakeGoogle(outcome)
        if op == "create_task":
            return ga.create_task(account_id="acct_t1", title="Buy stamps", tasklist_id="tl1")
        if op == "update_task":
            return ga.update_task(account_id="acct_t1", tasklist_id="tl1", task_id="k1", title="Renamed")
        if op == "complete_task":
            return ga.complete_task(account_id="acct_t1", tasklist_id="tl1", task_id="k1")
        return ga.delete_task(account_id="acct_t1", tasklist_id="tl1", task_id="k1")
    return run


@pytest.fixture
def calendar(monkeypatch, marked):
    from agent_friday.services import calendar_write as cw
    box = {}
    monkeypatch.setattr(cw, "write_ready", lambda: (True, ""))
    monkeypatch.setattr(cw, "resolve_write_account", lambda aid=None: ("acct_c1", None))
    monkeypatch.setattr(cw, "_service", lambda aid=None: (box["svc"], None))
    monkeypatch.setattr(cw, "_gate_calendar_field", lambda text, field: (text, ""))
    monkeypatch.setattr(cw, "_drop_calendar_cache", lambda *a, **k: None)
    monkeypatch.setattr(cw, "read_event", lambda eid, **k: {
        "ok": True, "id": eid, "account_id": "acct_c1",
        "before": {"calendar_id": "primary", "title": "Dentist", "guests": 0}})

    def run(op, outcome):
        box["svc"] = FakeGoogle(outcome)
        if op == "create_calendar_event":
            return cw.create_event(title="Dentist", start="2026-11-02T10:00:00+00:00",
                                   end="2026-11-02T11:00:00+00:00")
        if op == "update_calendar_event":
            return cw.update_event("e1", title="Dentist (moved)")
        return cw.delete_event("e1", notify_guests=False)
    return run


@pytest.fixture
def contacts(monkeypatch, marked):
    from agent_friday.services import google_contacts_write as gcw
    box = {}
    monkeypatch.setattr(gcw, "_pick_account", lambda aid: ({"id": "acct_p1", "email": "owner@example.com"}, ""))
    monkeypatch.setattr(gcw, "_service", lambda aid: (box["svc"], ""))

    def run(op, outcome):
        box["svc"] = FakeGoogle(outcome)
        return gcw.save_contact(name="Ann Example", email="ann@example.com")
    return run


@pytest.fixture
def holds(monkeypatch, marked):
    from agent_friday.services import calendar_write as cw, scheduling as sch
    box = {}
    monkeypatch.setattr(sch, "_write_setup", lambda aid: ("acct_c1", box["svc"]))
    monkeypatch.setattr(cw, "_gate_calendar_field", lambda text, field: (text, ""))
    monkeypatch.setattr(cw, "_drop_calendar_cache", lambda *a, **k: None)

    def run(op, outcome):
        box["svc"] = FakeGoogle(outcome)
        return sch.hold_slots(title="Planning", slots=[
            {"start": "2026-11-02T10:00:00+00:00", "end": "2026-11-02T11:00:00+00:00"}])
    return run


TASK_OPS = ["create_task", "update_task", "complete_task", "delete_task"]
CAL_OPS = ["create_calendar_event", "update_calendar_event", "delete_calendar_event"]
CHANGES = ([("tasks", op, "acct_t1") for op in TASK_OPS]
           + [("calendar", op, "acct_c1") for op in CAL_OPS]
           + [("contacts", "save_google_contact", "acct_p1"), ("holds", "hold_slots", "acct_c1")])
IDS = [c[1] for c in CHANGES]


def _error(res):
    return str((res or {}).get("message") or (res or {}).get("error") or "")


def _reported_done(res):
    return bool(isinstance(res, dict) and (res.get("ok") is True or res.get("deleted")
                                           or (res.get("id") and not res.get("error"))))


# ── revoked access during the call ──────────────────────────────────────────

@pytest.mark.parametrize("fixture,op,account", CHANGES, ids=IDS)
@pytest.mark.parametrize("exc", [HttpError(401, "Request had invalid authentication credentials."),
                                 RefreshError("invalid_grant: Token has been expired or revoked.")],
                         ids=["http401", "refresh-invalid-grant"])
def test_revoked_access_is_named_and_the_account_is_marked(request, fixture, op, account, exc, marked):
    res = request.getfixturevalue(fixture)(op, exc)
    assert not _reported_done(res), res
    assert "Reconnect" in _error(res), res
    assert "invalid" not in _error(res).lower() and "token" not in _error(res).lower(), \
        "no exception text in what the owner hears"
    assert (account, "needs_reauth") in marked, "the account must say it needs reconnecting"


# ── a reply that names nothing ──────────────────────────────────────────────

WRITES_WITH_A_REPLY = [c for c in CHANGES if c[1] not in ("delete_task", "delete_calendar_event")]


@pytest.mark.parametrize("fixture,op,account", WRITES_WITH_A_REPLY, ids=[c[1] for c in WRITES_WITH_A_REPLY])
@pytest.mark.parametrize("reply", [{}, None, {"title": "no id"}], ids=["empty", "none", "no-id"])
def test_a_reply_that_names_nothing_is_never_reported_as_done(request, fixture, op, account, reply):
    res = request.getfixturevalue(fixture)(op, reply)
    assert not _reported_done(res), res
    assert "did not confirm" in _error(res), res
    assert "check" in _error(res).lower(), "the owner is told to look before trying again"


# ── an ordinary refusal, and the network ────────────────────────────────────

@pytest.mark.parametrize("fixture,op,account", CHANGES, ids=IDS)
def test_a_refusal_says_nothing_was_changed_without_quoting_google(request, fixture, op, account, marked):
    res = request.getfixturevalue(fixture)(op, HttpError(400, "Invalid value at 'secret_field' (tok_abc123)"))
    assert not _reported_done(res), res
    assert "nothing was changed" in _error(res).lower(), res
    assert "tok_abc123" not in json.dumps(res, default=str), "exception text stays out of the result"
    assert not marked, "a refusal is not a revoked grant"


@pytest.mark.parametrize("fixture,op,account", CHANGES, ids=IDS)
def test_google_unreachable_is_not_reconnect(request, fixture, op, account, marked):
    res = request.getfixturevalue(fixture)(op, ConnectionError("Name or service not known"))
    assert not _reported_done(res), res
    assert "could not reach Google" in _error(res), res
    assert not marked


def test_a_draft_gmail_does_not_confirm_is_neither_saved_nor_nothing_saved(monkeypatch):
    from agent_friday.services import gmail_api, gmail_send as gs, google_accounts as ga
    import googleapiclient.discovery as discovery

    class Gmail:
        def users(self):
            return self

        def drafts(self):
            return self

        def create(self, **kw):
            return _Exec({})
    monkeypatch.setattr(ga, "get_account", lambda aid: {"id": aid, "email": "owner@example.com",
                                                        "label": "Me", "scopes": [ga.GMAIL_MODIFY]})
    monkeypatch.setattr(ga, "credentials_for", lambda aid: object())
    monkeypatch.setattr(discovery, "build", lambda *a, **k: Gmail())
    monkeypatch.setattr(gmail_api, "execute", lambda req, **k: req.execute())
    with pytest.raises(gs.SendRefused) as err:
        gs.save_draft(account_id="acct_g1", to=["ann@example.com"], subject="Hi", body="Hello")
    text = str(err.value)
    assert "did not confirm" in text and "Nothing was saved" not in text


# ── the shared classifier, and the call record ──────────────────────────────

def _co():
    from agent_friday.services import connector_outcome
    return connector_outcome


@pytest.mark.parametrize("exc,kind", [
    (HttpError(401), "revoked"), (RefreshError("x"), "revoked"), (Exception("invalid_grant"), "revoked"),
    (HttpError(403, "Request had insufficient authentication scopes."), "scope"),
    (HttpError(403, "rateLimitExceeded"), "busy"), (HttpError(429), "busy"),
    (HttpError(500), "unconfirmed"), (HttpError(503), "unconfirmed"), (TimeoutError(), "unconfirmed"),
    (HttpError(404), "refused"), (HttpError(409), "refused"), (HttpError(403, "forbidden"), "refused"),
    (ConnectionError(), "unreachable"), (ValueError("odd"), "failed"),
    # a connection that broke after the request went out may have been applied
    (ConnectionResetError(), "unconfirmed"), (BrokenPipeError(), "unconfirmed"),
    # an id the model chose, in the URL of a 404, does not make the grant revoked
    (HttpError(404, "<HttpError 404 when requesting .../tasks/invalid_grant?alt=json>"), "refused"),
], ids=lambda v: v if isinstance(v, str) else type(v).__name__)
def test_each_failure_has_one_kind(exc, kind):
    assert _co().classify(exc) == kind


def test_not_done_is_never_counted_as_a_completed_call():
    from agent_friday.services import completion_receipts as cr
    entry = {"name": "create_task", "result": _co().NOT_DONE + '{"error": "Google refused"}'}
    assert cr.receipt_ok(entry) is False, "an approved card or a claim must not count it as done"
    assert cr.receipt_status(entry) != "ok"


def test_a_result_that_says_ok_is_done_even_with_a_side_note():
    with _co().call() as notes:
        _co().settle({"ok": True, "error": "the other holds could not be released"})
    assert notes == [], "the booking happened; a note about the other holds does not undo it"


class _Sequence(FakeGoogle):
    """Answers the first insert, then raises."""

    def insert(self, **kw):
        self.writes.append(("insert", kw))
        return _Exec({"id": "h1"} if len(self.writes) == 1 else HttpError(503))


def test_holds_partly_placed_keep_their_series_and_are_not_not_done(holds, monkeypatch):
    from agent_friday.services import scheduling as sch
    svc = _Sequence(None)
    monkeypatch.setattr(sch, "_write_setup", lambda aid: ("acct_c1", svc))
    with _co().call() as notes:
        res = sch.hold_slots(title="Planning", slots=[
            {"start": "2026-11-02T10:00:00+00:00", "end": "2026-11-02T11:00:00+00:00"},
            {"start": "2026-11-03T10:00:00+00:00", "end": "2026-11-03T11:00:00+00:00"}])
    assert res["ok"] is True and res["partial"] is True and res["series_id"]
    assert [h["id"] for h in res["holds"]] == ["h1"] and len(res["not_held"]) == 1
    assert "1 of 2" in res["warning"] and notes == []


def test_deleting_what_is_already_gone_is_done(calendar, tasks):
    res = calendar("delete_calendar_event", HttpError(410))
    assert res.get("ok") is True and res.get("already_gone")
    res = tasks("delete_task", HttpError(410))
    assert res.get("deleted") is True and res.get("already_gone")


def test_a_task_id_google_does_not_know_is_not_a_delete_that_happened(tasks):
    # nothing is read before a task delete and the ids are the model's: a 404 is a wrong id
    with _co().call() as notes:
        res = tasks("delete_task", HttpError(404))
    assert not res.get("deleted") and "not found" in res["error"] and notes


def test_revoked_access_at_the_read_before_a_calendar_delete_is_named(monkeypatch, marked):
    from agent_friday.services import calendar_write as cw
    svc = FakeGoogle(None)
    svc.get = lambda **kw: _Exec(HttpError(401, "GET .../events/e1?alt=json returned 401"))
    monkeypatch.setattr(cw, "write_ready", lambda: (True, ""))
    monkeypatch.setattr(cw, "resolve_write_account", lambda aid=None: ("acct_c1", None))
    monkeypatch.setattr(cw, "_service", lambda aid=None: (svc, None))
    res = cw.delete_event("e1", notify_guests=False)
    assert not res.get("ok") and "Reconnect" in res["error"] and "alt=json" not in res["error"]
    assert ("acct_c1", "needs_reauth") in marked and not svc.writes


@pytest.mark.parametrize("fixture,op,account", CHANGES, ids=IDS)
def test_what_did_not_happen_is_noted_for_the_call(request, fixture, op, account):
    with _co().call() as notes:
        request.getfixturevalue(fixture)(op, HttpError(401))
    assert notes and notes[0]["kind"] == "revoked"
    with _co().call() as notes:
        request.getfixturevalue(fixture)(op, {"id": "ok1", "resourceName": "people/c1"})
    assert notes == [], "a confirmed change notes nothing"


@pytest.fixture
def fixture_tool(monkeypatch):
    from agent_friday.governance import action_gate as gate
    from agent_friday.services import agent as ag
    box = {}
    monkeypatch.setitem(ag.CLAUDE_TOOL_HANDLERS, "fixture_change", lambda inp: box["fn"](inp))
    monkeypatch.setitem(ag.TOOL_RINGS, "fixture_change", 0)
    monkeypatch.setattr(gate, "INTERNAL_TOOLS", frozenset(gate.INTERNAL_TOOLS) | {"fixture_change"})
    monkeypatch.setattr(ag, "CLAUDE_TOOLS", list(ag.CLAUDE_TOOLS) + [{
        "name": "fixture_change", "description": "fixture",
        "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}}])

    def run(fn):
        from agent_friday.services import tool_receipts
        box["fn"] = fn
        tool_receipts.begin_turn()
        out = ag._execute_tool("fixture_change", {"q": "x"})
        return out, tool_receipts.receipts()[-1]
    return run


def test_a_change_that_did_not_happen_is_recorded_as_not_done(fixture_tool):
    from agent_friday.services import agent as ag

    def handler(inp):
        return json.dumps({"error": _co().not_done("revoked", "Google Tasks")})
    out, receipt = fixture_tool(handler)
    assert out.startswith(_co().NOT_DONE) and ag._tool_call_status(out) == "error"
    assert receipt["ok"] is False and "revoked" in (receipt["detail"] or "")


def test_an_error_the_service_did_not_name_is_still_not_done(fixture_tool):
    from agent_friday.services import agent as ag
    out, receipt = fixture_tool(lambda inp: json.dumps(_co().settle({"error": "account_id is required"})))
    assert ag._tool_call_status(out) == "error" and receipt["ok"] is False


def test_a_change_that_happened_is_recorded_as_done(fixture_tool):
    from agent_friday.services import agent as ag
    out, receipt = fixture_tool(lambda inp: json.dumps(_co().settle({"id": "k1", "ok": True})))
    assert not out.startswith(_co().NOT_DONE) and ag._tool_call_status(out) == "ok"
    assert receipt["ok"] is True
