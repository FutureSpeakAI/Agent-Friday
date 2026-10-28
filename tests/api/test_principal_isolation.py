"""No principal reaches what is not theirs (release gate: NS-34.2-5, NS-34.8-1,
NS-34.5-10, NS-34.17-2).

Friday has two request principals: the owner ("user") and the read-only observer
credential an orchestrator uses. A live call adds a third party: on a call Friday
placed, the person on the line is not the owner.

* Every route the app has is walked, from the URL map, not a hand list: the observer
  reaches only the exact read routes that seal their free text
  (observer_access.READ_ONLY_RULES), and no write anywhere. Before, the observer's
  allowlist was a path prefix, so any route added under /api/tasks (the scoped-agent
  list with raw prompt text, a resumability reason) was open to it unsealed.
* On a call with someone else, Friday reads only the owner's calendar (to arrange a
  time); the owner's files, notes, library, memory and mail stay closed. Before, every
  ring-0 read was open on such a call.

All data is synthetic.
"""
from __future__ import annotations

import re
import time

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import observer_access as obs
from agent_friday.services import task_journal as tj

SECRET_PROMPT = "synthetic-owner-prompt: wire 4,000 to the Okafor account"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    monkeypatch.setattr(obs, "_hash_path", lambda: tmp_path / "security" / "observer_token.sha256")
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    from agent_friday.services import activity_ledger as al
    monkeypatch.setattr(al, "LEDGER_FILE", tmp_path / "activity.jsonl")
    with ag.TASKS_LOCK:
        saved = dict(ag.TASKS)
        ag.TASKS.clear()
    yield
    with ag.TASKS_LOCK:
        ag.TASKS.clear()
        ag.TASKS.update(saved)


def _task(tid="iso-0001"):
    now = time.time()
    with ag.TASKS_LOCK:
        ag.TASKS[tid] = {"task_id": tid, "name": "Isolation task", "description": "", "prompt": SECRET_PROMPT,
                         "status": "complete", "created": now - 30, "started": now - 29, "ended": now - 1,
                         "log": [], "result": "done", "chain": None, "chain_step": 0, "model": None}
    tj.append(tid, "created", name="Isolation task", prompt=SECRET_PROMPT)
    return tid


def _hdr(client):
    r = client.post("/api/tasks/observer-token")
    assert r.status_code == 200, r.get_data(as_text=True)
    return {obs.HEADER: r.get_json()["token"]}


def _sample_path(rule: str, tid: str) -> str:
    def fill(m):
        name = m.group(2) if m.group(2) else m.group(1)
        return tid if "id" in name.lower() else "x"
    return re.sub(r"<(?:[a-z_]+:)?([a-zA-Z_]+)>|<([a-zA-Z_]+)>", fill, rule)


def _rules(client):
    app = client.application
    for r in app.url_map.iter_rules():
        if r.endpoint == "static" or r.rule.startswith("/static"):
            continue
        yield r


def test_the_observer_reaches_only_the_sealed_read_routes(client):
    tid = _task()
    hdr = _hdr(client)
    allowed = getattr(obs, "READ_ONLY_RULES", None)
    assert allowed is not None, "the observer is allowed by path prefix on this tree, not by route"
    rules = {r.rule for r in _rules(client)}
    assert set(allowed) <= rules, "the observer list names routes the app does not have"
    opened = []
    for r in _rules(client):
        if "GET" not in (r.methods or ()):
            continue
        path = _sample_path(r.rule, tid)
        resp = client.get(path, headers=hdr)
        if r.rule not in allowed:
            # Refused by the credential itself, whatever the route would have said.
            assert resp.status_code == 403, "the observer got %s on %s" % (resp.status_code, r.rule)
            continue
        if resp.status_code < 400:
            opened.append(r.rule)
            body = resp.get_data(as_text=True)
            assert SECRET_PROMPT not in body, "%s served the owner's prompt to the observer" % r.rule
    assert "/api/tasks/<task_id>/journal" in opened and "/api/activity" in opened


def test_the_activity_feed_is_sealed_for_the_observer(client, monkeypatch):
    from agent_friday.services import activity_ledger as al
    from agent_friday.services import egress_gate as eg
    al.record("subagent_spawn", task_id="iso-0002", description=SECRET_PROMPT, model="m")
    monkeypatch.setattr(eg, "_gate_text", lambda text, provider, where: "[sealed]")
    hdr = _hdr(client)
    seen = client.get("/api/activity", headers=hdr).get_data(as_text=True)
    assert SECRET_PROMPT[:40] not in seen and "[sealed]" in seen
    own = client.get("/api/activity").get_data(as_text=True)
    assert SECRET_PROMPT[:40] in own                       # the owner sees their own feed


def test_the_scoped_and_resumable_task_routes_are_closed_to_the_observer(client):
    tid = _task()
    hdr = _hdr(client)
    for path in ("/api/tasks/scoped", "/api/tasks/scoped/%s" % tid, "/api/tasks/%s/resumable" % tid):
        r = client.get(path, headers=hdr)
        assert r.status_code == 403, (path, r.status_code)


def test_the_observer_writes_nothing_anywhere(client):
    tid = _task()
    hdr = _hdr(client)
    for r in _rules(client):
        for method in sorted((r.methods or set()) & {"POST", "PUT", "PATCH", "DELETE"}):
            path = _sample_path(r.rule, tid)
            resp = client.open(path, method=method, headers=hdr, json={})
            assert resp.status_code in (401, 403), (method, r.rule, resp.status_code)


# ── a call with someone who is not the owner ─────────────────────────────────

OWNER_PRIVATE_READS = ("read_file", "search_files", "read_wiki", "search_wiki", "search_library",
                       "query_trust_graph", "get_briefing", "list_workspace_history",
                       "personality_show", "check_situation", "query_calendar",
                       "find_calendar_events")


@pytest.mark.parametrize("tool", OWNER_PRIVATE_READS)
def test_on_a_call_with_someone_else_the_owners_records_stay_closed(tool):
    ok, why = ag._governance_check(tool, {}, {"origin": "phone", "authenticated": False,
                                              "call_party": "other"})
    assert ok is False, "%s was allowed on a call with someone else" % tool


def test_an_outbound_call_marks_the_other_party_and_carries_none_of_the_owners_context(monkeypatch):
    from agent_friday.phone import live_call as lc
    seen = []

    def fake_generate(msgs, session_ctx=None, system_builder=None, **k):
        seen.append((dict(session_ctx or {}), system_builder("anthropic") if system_builder else ""))
        return "ok", None
    monkeypatch.setattr(ag, "_generate_agent", fake_generate)
    from agent_friday.services.channels import manager
    monkeypatch.setattr(manager, "_gated_system_prompt",
                        lambda provider, keywords="": "OWNER CONTEXT: wiki and memory")
    bridge = lc.CallBridge.__new__(lc.CallBridge)
    for direction, expect_other in (("outbound", True), ("inbound", False), ("", True),
                                    ("unverified", True)):
        bridge.params = {"direction": direction} if direction else {}
        lc.CallBridge._default_agent(bridge, [{"who": "caller", "text": "hello"}])
        ctx, prompt = seen[-1]
        assert (ctx.get("call_party") == "other") is expect_other, direction
        if expect_other:
            assert "OWNER CONTEXT" not in prompt, "the owner's context went into a call with someone else"


def _start(bridge, params):
    bridge.say = lambda text: None
    bridge.history = []
    return lc_handle(bridge, {"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1",
                                                          "customParameters": params}})


def lc_handle(bridge, msg):
    from agent_friday.phone import live_call as lc
    return lc.CallBridge.handle(bridge, msg)


def test_an_inbound_stream_needs_its_own_single_use_ticket():
    from agent_friday.phone import live_call as lc
    mint = getattr(lc, "mint_ticket", None)
    assert mint is not None, "an inbound stream is trusted on its say-so on this tree"
    b = lc.CallBridge.__new__(lc.CallBridge)
    _start(b, {"direction": "inbound"})                              # no ticket: a forged start
    assert b.params["direction"] != "inbound"
    t = mint("inbound", "CA1")
    b2 = lc.CallBridge.__new__(lc.CallBridge)
    _start(b2, {"direction": "inbound", "ticket": t})
    assert b2.params["direction"] == "inbound" and "ticket" not in b2.params
    b3 = lc.CallBridge.__new__(lc.CallBridge)
    _start(b3, {"direction": "inbound", "ticket": t})                # the same ticket again
    assert b3.params["direction"] != "inbound"
    twiml = lc.connect_twiml({"public_base_url": "https://example.invalid"}, "CA2")
    assert "ticket" in twiml


def test_a_text_from_the_owners_cell_can_still_read():
    ok, _ = ag._governance_check("read_file", {"path": "x"}, {"origin": "phone", "authenticated": False})
    assert ok is True
