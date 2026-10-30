"""One grant record for every kind of grant (NS-18.4-4, NS-30.12-1, slice A11).

Every grant names its principal, its subject (goal, task, schedule, session or
the owner), tools, argument rules, data and destination scope, uses, start and
expiry, money and time caps, what it does in quiet hours and whether a remote
channel may use it (governance/grant_record.py). Grants already on disk are
read into that record with the values that are each kind's behaviour today.

At the checkpoint a grant covers an action only when every field allows it.
When one does not, the action goes to its card exactly as with no grant (never
a deny), and the decision names the field that kept the grant out.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from agent_friday.governance import action_gate

TOOL = "create_calendar_event"
SCOPE = "sch_a11_weekly"
BG = {"is_background_task": True, "task_id": "task_a11", "schedule_id": SCOPE}
ARGS = {"title": "Weekly review", "start": "2026-11-02T10:00:00"}


@pytest.fixture
def gate(tmp_path, monkeypatch):
    home = tmp_path / ".friday"
    home.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    monkeypatch.setattr(action_gate, "_gov_dir", lambda: tmp_path)
    monkeypatch.setattr(action_gate, "verify_claws", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(action_gate, "outward_hold", lambda: None)
    from agent_friday.services import interruption_policy
    monkeypatch.setattr(interruption_policy, "quiet_state",
                        lambda now=None, s=None: {"active": False, "why": "", "until": 0.0})
    return tmp_path


def _decide(ctx=None, args=None, tool=TOOL):
    return action_gate._decide(tool, action_gate.OUTWARD, "it changes your calendar",
                               dict(BG if ctx is None else ctx), False,
                               dict(ARGS if args is None else args), False)


def _write_rows(rows):
    action_gate._grants_file().write_text(json.dumps(rows, indent=1), encoding="utf-8")


def _legacy_row(**over):
    """A grants.json row as every grant was written before A11."""
    row = {"grant_id": "grant_legacy01", "tools": [TOOL], "scope": SCOPE,
           "expires_at": time.time() + 3600, "uses_left": 3, "created_by": "owner",
           "note": "made in Settings", "created_at": time.time() - 60}
    row.update(over)
    return row


def _uses_left():
    return [g["uses_left"] for g in action_gate._read_grants()]


# ── the normaliser: every kind, with today's behaviour as its defaults ──────

def test_every_kind_reads_into_a_full_record_with_current_behaviour_defaults(gate):
    from agent_friday.governance import grant_record as gr
    now = time.time()
    want = set(gr.RECORD_FIELDS)

    scoped = gr.from_scoped(_legacy_row(), now)
    run = gr.from_scoped(_legacy_row(grant_id="grant_run01", scope="task_r1",
                                     note="run grant (A24) from card apv_1"), now)
    send = gr.from_send({"grant_id": "sgr_old01", "scope": "recipients", "actions": ["send_email"],
                         "recipients": ["pat@example.org", "@example.net"], "uses": 2,
                         "created_at": now - 100, "revoked_at": None}, now)
    job_send = gr.from_send({"grant_id": "sgr_job01", "scope": "job", "actions": ["send_email"],
                             "recipients": ["pat@example.org"], "schedule_id": SCOPE}, now)
    folder = gr.from_file({"event": "grant_scope", "id": "fg1", "type": "folder",
                           "path": "C:/Users/someone/Reports", "created_ts": now - 10,
                           "expires_ts": now + 86400}, now)
    library = gr.from_library({"event": "library_add", "id": "lib1", "principal": "owner",
                               "type": "folder", "path": "C:/Users/someone/Notes"})
    desktop = gr.from_desktop("notepad.exe", "act")
    crew = gr.from_crew({"id": "helper", "name": "Helper", "status": "active",
                         "allowed_tools": ["read_file"], "time_budget_s": 300},
                        0, {"path": "C:/Users/someone/Project", "access": "read"})

    for rec in (scoped, run, send, job_send, folder, library, desktop, crew):
        d = rec.to_dict()
        assert set(d) == want, rec.kind
        assert d["status"] == "active", rec.kind
        # what no store carried before: today's behaviour, never tighter or looser
        assert d["arg_constraints"] == {} and d["money_cap"] is None, rec.kind
        assert d["quiet_hours"] == "allow" and d["remote_allowed"] is True, rec.kind
        assert d["subject"]["kind"] in gr.SUBJECT_KINDS, rec.kind

    assert (scoped.kind, run.kind) == ("scoped", "run")
    assert scoped.principal == "owner" and scoped.subject == {"kind": "schedule", "id": SCOPE}
    assert scoped.tools == [TOOL] and scoped.max_uses == 3 and scoped.used_count == 0
    assert scoped.expires_at == pytest.approx(_legacy_row()["expires_at"], abs=5)
    assert run.subject["kind"] == "task"
    # a send grant: its recipients are its destination scope; no expiry, no use limit
    assert send.tools == ["draft_email"] and send.destination_scope == ["pat@example.org", "@example.net"]
    assert send.max_uses is None and send.used_count == 2 and send.expires_at is None
    assert send.subject == {"kind": "owner", "id": ""}
    assert job_send.subject == {"kind": "schedule", "id": SCOPE}
    assert folder.kind == "file" and folder.data_scope == ["C:/Users/someone/Reports"]
    assert folder.expires_at == pytest.approx(now + 86400)
    assert library.kind == "library" and library.principal == "owner"
    assert desktop.destination_scope == ["notepad.exe"] and "click" in desktop.tools
    assert gr.from_desktop("calc.exe", "observe").tools == ["screenshot"]
    assert crew.principal == "crew:helper" and crew.time_cap_s == 300

    # the stored status of an old row, read as the gate reads it today
    assert gr.from_scoped(_legacy_row(expires_at=now - 1), now).status == "expired"
    assert gr.from_scoped(_legacy_row(uses_left=0), now).status == "spent"
    assert gr.from_send({"grant_id": "sgr_x", "revoked_at": now - 5}, now).status == "revoked"
    assert gr.from_send({"grant_id": "sgr_y"}, now, revoked_ids={"sgr_y"}).status == "revoked"


def test_all_records_lists_every_store_and_new_grants_carry_the_fields(gate):
    from agent_friday.governance import grant_record as gr
    _write_rows([_legacy_row()])
    g = action_gate.create_grant(tools=[TOOL], scope=SCOPE, expires_in_seconds=600, max_uses=2,
                                 limits={"quiet_hours": "respect", "remote_allowed": False})
    for k in ("principal", "subject", "arg_constraints", "data_scope", "destination_scope",
              "starts_at", "money_cap", "time_cap_s", "quiet_hours", "remote_allowed",
              "used_count", "max_uses"):
        assert k in g, k
    assert g["quiet_hours"] == "respect" and g["remote_allowed"] is False and g["max_uses"] == 2
    view = gr.all_records()
    ids = {(r["kind"], r["id"]) for r in view["records"]}
    assert ("scoped", "grant_legacy01") in ids and ("scoped", g["grant_id"]) in ids
    assert all(set(r) == set(gr.RECORD_FIELDS) for r in view["records"])
    with pytest.raises(ValueError):
        action_gate.create_grant(tools=[TOOL], scope=SCOPE, expires_in_seconds=600,
                                 limits={"quiet_hours": "sometimes"})


# ── the checkpoint: each field keeps a grant out, and the action asks ───────

def _limited(**limits):
    return action_gate.create_grant(tools=[TOOL], scope=SCOPE, expires_in_seconds=3600,
                                    max_uses=3, limits=limits)


CASES = {
    "status": lambda: _write_rows([_legacy_row(revoked_at=time.time() - 5)]),
    "expires_at": lambda: _write_rows([_legacy_row(expires_at=time.time() - 5)]),
    "starts_at": lambda: _limited(starts_at=time.time() + 3600),
    "max_uses": lambda: _write_rows([_legacy_row(uses_left=0)]),
    "principal": lambda: _limited(principal="crew:helper"),
    "tools": lambda: action_gate.create_grant(tools=["delete_calendar_event"], scope=SCOPE,
                                              expires_in_seconds=3600, max_uses=3),
    "arg_constraints": lambda: _limited(arg_constraints={"title": {"prefix": "Standup"}}),
    "data_scope": lambda: _limited(data_scope=["calendar"]),
    "destination_scope": lambda: _limited(destination_scope=["@example.org"]),
    "money_cap": lambda: _limited(money_cap=10),
    "time_cap_s": lambda: _write_rows([_legacy_row(time_cap_s=60, first_used_at=time.time() - 3600)]),
}


@pytest.mark.parametrize("field", sorted(CASES))
def test_each_field_keeps_the_grant_out_and_the_action_goes_to_its_card(gate, field):
    CASES[field]()
    before = _uses_left()
    args = dict(ARGS, attendees=["someone@elsewhere.test"]) if field == "destination_scope" else None
    v = _decide(args=args)
    assert v.action == "card", (field, v.action, v.reason)
    assert v.grant is None
    assert v.detail.get("grant_excluded_by") == field, v.detail
    assert v.detail.get("grant_excluded")
    assert _uses_left() == before, "a grant that does not cover spends nothing"


def test_quiet_hours_keep_a_respecting_grant_out(gate, monkeypatch):
    from agent_friday.services import interruption_policy
    g = _limited(quiet_hours="respect")
    monkeypatch.setattr(interruption_policy, "quiet_state",
                        lambda now=None, s=None: {"active": True, "why": "quiet_hours", "until": 0.0})
    v = _decide()
    assert v.action == "card" and v.detail == {"grant_excluded": g["grant_id"],
                                               "grant_excluded_by": "quiet_hours"}
    # a grant that allows quiet hours (every grant made before A11) still covers
    _write_rows([_legacy_row()])
    assert _decide().action == "allow"


def test_a_remote_channel_cannot_use_a_grant_that_does_not_allow_it(gate):
    g = _limited(remote_allowed=False)
    v = _decide(ctx=dict(BG, origin="channel"))
    assert v.action == "card" and v.detail == {"grant_excluded": g["grant_id"],
                                               "grant_excluded_by": "remote_allowed"}
    # the same grant, asked from this PC, covers
    assert _decide().action == "allow"


def test_a_covering_grant_still_covers_and_records_its_use(gate):
    g = _limited(arg_constraints={"title": {"prefix": "Weekly"}, "start": {"max_len": 40}},
                 destination_scope=["@example.org"], money_cap=10, time_cap_s=3600)
    ctx = dict(BG, action_cost=4)
    v = _decide(ctx=ctx, args=dict(ARGS, attendees=["pat@example.org"]))
    assert v.action == "allow" and v.grant["grant_id"] == g["grant_id"]
    row = action_gate._read_grants()[0]
    assert row["uses_left"] == 2 and row["used_count"] == 1 and row["money_spent"] == 4
    assert row["first_used_at"]
    # the money cap counts what was spent: 4 + 7 is over 10
    v = _decide(ctx=dict(BG, action_cost=7), args=dict(ARGS, attendees=["pat@example.org"]))
    assert v.action == "card" and v.detail["grant_excluded_by"] == "money_cap"


def test_an_old_grant_with_none_of_the_new_fields_covers_as_before(gate):
    _write_rows([_legacy_row(uses_left=1)])
    v = _decide()
    assert v.action == "allow" and v.grant["grant_id"] == "grant_legacy01"
    v = _decide()
    assert v.action == "card" and v.detail["grant_excluded_by"] == "max_uses"


# ── pre-approved sends take the same record ─────────────────────────────────

def test_a_send_grant_field_keeps_it_out_and_the_send_keeps_its_own_card(gate):
    from agent_friday.governance import send_grants as sg
    g = sg.create(scope="recipients", recipients=["pat@example.org"],
                  limits={"remote_allowed": False})
    assert g["remote_allowed"] is False and g["subject"] == {"kind": "owner", "id": ""}
    args = {"to": "pat@example.org", "subject": "Notes", "body": "See you Monday."}
    assert sg.covering_tool("draft_email", args, {})["grant_id"] == g["grant_id"]
    remote = {"origin": "channel", "is_background_task": True, "task_id": "task_a11"}
    assert sg.covering_tool("draft_email", args, remote) is None
    assert sg.exclusion_for_tool("draft_email", args, remote) == {
        "grant_excluded": g["grant_id"], "grant_excluded_by": "remote_allowed"}
    v = _decide(ctx=remote, args=args, tool="draft_email")
    assert v.action == "allow" and "its own approval card" in v.reason, v.reason
    assert v.grant is None and v.detail.get("grant_excluded_by") == "remote_allowed"


def test_a_send_grant_written_before_the_record_fields_still_covers(gate):
    from agent_friday.governance import grant_record as gr
    from agent_friday.governance import send_grants as sg
    g = sg.create(scope="recipients", recipients=["@example.org"])
    rows = sg._read()
    for r in rows:
        for k in gr.STORED_FIELDS:
            r.pop(k, None)
        r["_resign"] = True
    sg._write(rows)
    args = {"to": "pat@example.org", "subject": "Notes", "body": "See you Monday."}
    assert sg.covering_tool("draft_email", args, {"origin": "channel"})["grant_id"] == g["grant_id"]
    assert sg.exclusion_for_tool("draft_email", args, {}) == {}


def test_a_send_grant_with_a_use_limit_is_spent(gate):
    from agent_friday.governance import send_grants as sg
    g = sg.create(scope="recipients", recipients=["pat@example.org"], limits={"max_uses": 1})
    args = {"to": "pat@example.org", "subject": "Notes", "body": "See you Monday."}
    assert sg.covering_tool("draft_email", args, {})["grant_id"] == g["grant_id"]
    sg.record_use(g, "send_email", ["pat@example.org"])
    assert sg.covering_tool("draft_email", args, {}) is None
    assert sg.exclusion_for_tool("draft_email", args, {})["grant_excluded_by"] == "max_uses"


# ── the owner's read-only view ──────────────────────────────────────────────

def _client(monkeypatch, principal=None):
    from flask import Flask, g as flask_g

    import agent_friday.core as core
    from agent_friday.routes import goals as route
    monkeypatch.setattr(core, "_is_local_request", lambda: True)
    app = Flask(__name__)
    app.secret_key = os.urandom(16)
    if principal:
        @app.before_request
        def _mark():
            flask_g.friday_principal = principal
    app.register_blueprint(route.goals_bp)
    return app.test_client()


def test_the_grants_list_shows_every_record_field_to_the_owner(gate, monkeypatch):
    from agent_friday.governance import grant_record as gr
    _write_rows([_legacy_row()])
    r = _client(monkeypatch).get("/api/governance/grants")
    assert r.status_code == 200
    body = r.get_json()
    assert [g["grant_id"] for g in body["grants"]] == ["grant_legacy01"]
    rec = next(x for x in body["records"] if x["id"] == "grant_legacy01")
    assert set(rec) == set(gr.RECORD_FIELDS)
    assert rec["principal"] == "owner" and rec["quiet_hours"] == "allow" and rec["remote_allowed"] is True


def test_the_grants_list_is_the_owners_only(gate, monkeypatch):
    _write_rows([_legacy_row()])
    r = _client(monkeypatch, principal="observer").get("/api/governance/grants")
    assert r.status_code == 403
    assert "records" not in (r.get_json() or {})
