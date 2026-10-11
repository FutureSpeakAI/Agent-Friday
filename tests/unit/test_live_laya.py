"""Laya inside a cloud voice call (services/live_laya): LV-3, LV-4, LV-5.

Shadow never executes; the "on" door opens only for an allow-listed class that
is past the agreement gate; the same Live call twice runs once; the verifier
additions block what Laya's mutation guard rejects and a row that is not on the
screen; the receipt holds sizes and no content; a large result is cut locally;
and nothing here runs on the receive loop. No model, no network, no server.
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import threading
import time
from pathlib import Path

import pytest

import agent_friday.services.agent as agent
from agent_friday.governance import action_gate
from agent_friday.services import call_verifier, live_laya, laya_router, approvals, taint

ROOT = Path(__file__).resolve().parents[2]

VOICE = {"authenticated": True, "surface": "voice-live", "taint_key": "voice-live",
         "conversation_id": "c-live"}
READ = "find_calendar_events"          # read-only, internal
EVENT = "create_calendar_event"        # outward


def _agreement_tool():
    spec = importlib.util.spec_from_file_location("live_reflex_agreement_t", ROOT / "tools" / "live_reflex_agreement.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    agent._PENDING_CONFIRMATIONS.clear()
    agent._hooks.reset_rate_limiter()
    taint.reset()
    call_verifier.reset()
    live_laya.reset_idempotency()
    live_laya._ELIG_CACHE.update(at=0.0, classes=frozenset())
    yield
    live_laya.reset_idempotency()
    taint.reset()
    call_verifier.reset()


class Spy:
    def __init__(self, monkeypatch):
        self.mp, self.ran = monkeypatch, []

    def tool(self, name, result="fine", delay=0.0):
        def handler(inp, _n=name):
            self.ran.append((_n, dict(inp)))
            if delay:
                time.sleep(delay)
            return result(inp) if callable(result) else result
        self.mp.setitem(agent.CLAUDE_TOOL_HANDLERS, name, handler)

    def count(self, name):
        return sum(1 for n, _ in self.ran if n == name)


@pytest.fixture
def spy(monkeypatch):
    return Spy(monkeypatch)


def run(name, args, ctx=None):
    agent._hooks.reset_rate_limiter()
    return agent._execute_tool(name, args, session_ctx=dict(ctx if ctx is not None else VOICE))


def route_to(tool, args=None, label="opening it"):
    r = laya_router.Route("tool", tool=tool, args=dict(args or {}), confidence=0.9, layer="rule", label=label)
    return lambda text: r


def make(tmp_path, mode="shadow", route=None, classes=(), run_tool=None, notes=None, **kw):
    settings = {"voice_live_reflex": mode, "voice_live_reflex_classes": list(classes)}
    ll = live_laya.LiveLaya("t1", route_fn=route, run_tool=run_tool, settings_fn=lambda: settings,
                            stable_s=0, note_fn=(notes.append if notes is not None else None),
                            log_path=lambda: tmp_path / "shadow.jsonl", **kw)
    return ll, settings


def rows(tmp_path):
    p = tmp_path / "shadow.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def turn(ll, text, calls=()):
    ll.on_input(text)
    for name, args in calls:
        ll.on_gemini_call(name, args)
    ll.on_turn_end(text)
    assert ll.drain()


# ── LV-3: the reflex ─────────────────────────────────────────────────────────

def test_shadow_never_executes_and_writes_an_agreement_record(tmp_path):
    ran = []
    ll, _ = make(tmp_path, "shadow", route_to("navigate_to", {"kind": "workspace", "query": "calendar"}),
                 classes=("open",), run_tool=lambda n, a: ran.append(n))
    live_laya._ELIG_CACHE.update(at=time.time() + 1e6, classes=frozenset({"open"}))   # even if "cleared"
    turn(ll, "open my calendar", [("navigate_to", {"kind": "workspace", "query": "calendar"})])
    assert ran == []
    [row] = rows(tmp_path)
    assert row["agreement"] == "same" and row["executed"] is False and row["mode"] == "shadow"
    assert row["class"] == "open"


def test_the_record_says_different_or_none_and_holds_no_words(tmp_path):
    ll, _ = make(tmp_path, "shadow", route_to("navigate_to", {"query": "calendar"}))
    turn(ll, "open my calendar", [("navigate_to", {"query": "news"})])
    turn(ll, "open my calendar please", [])
    a, b = rows(tmp_path)
    assert a["agreement"] == "different"
    assert b["agreement"] == "none" and b["side"] == "laya_only" and b["unwanted"] is True
    raw = (tmp_path / "shadow.jsonl").read_text(encoding="utf-8")
    assert "calendar" not in raw and "open my" not in raw        # hashes and tool names only


def test_compare_is_exact_on_tool_and_equal_arguments():
    same = live_laya.compare({"tool": "t", "args": {"q": "Hello  there."}}, [{"tool": "t", "args": {"q": "hello there"}}])
    assert same["agreement"] == "same"
    assert live_laya.compare({"tool": "t", "args": {}}, [{"tool": "u", "args": {}}])["agreement"] == "different"
    assert live_laya.compare(None, [{"tool": "u", "args": {}}])["side"] == "gemini_only"
    assert live_laya.compare(None, [])["side"] == "neither"


def test_off_decides_nothing(tmp_path):
    called = []
    ll, _ = make(tmp_path, "off", lambda t: called.append(t) or None)
    turn(ll, "open my calendar", [("navigate_to", {})])
    assert called == [] and rows(tmp_path) == []


def test_on_with_an_empty_allow_list_runs_nothing(tmp_path):
    ran, notes = [], []
    ll, _ = make(tmp_path, "on", route_to("navigate_to", {"query": "calendar"}), classes=(),
                 run_tool=lambda n, a: ran.append(n), notes=notes)
    live_laya._ELIG_CACHE.update(at=time.time() + 1e6, classes=frozenset({"open"}))
    turn(ll, "open my calendar", [])
    assert ran == [] and notes == []


def test_on_runs_only_allow_listed_classes_past_the_agreement_gate(tmp_path):
    ran, notes = [], []
    live_laya._ELIG_CACHE.update(at=time.time() + 1e6, classes=frozenset({"open", "read"}))
    # allow-listed AND eligible: runs, and Gemini is told so it does not repeat it
    ll, _ = make(tmp_path, "on", route_to("navigate_to", {"query": "calendar"}), classes=("open",),
                 run_tool=lambda n, a: ran.append((n, a)), notes=notes)
    turn(ll, "open my calendar", [])
    assert ran == [("navigate_to", {"query": "calendar"})]
    assert len(notes) == 1 and notes[0].startswith("[Done:") and "Do not repeat" in notes[0]
    assert rows(tmp_path)[0]["executed"] is True
    # eligible but NOT on the owner's allow-list: not run
    ran.clear()
    ll2, _ = make(tmp_path, "on", route_to("check_email"), classes=("open",),
                  run_tool=lambda n, a: ran.append(n), notes=[])
    turn(ll2, "check my email", [])
    assert ran == []
    # allow-listed but NOT past the gate: not run
    live_laya._ELIG_CACHE.update(at=time.time() + 1e6, classes=frozenset())
    ll3, _ = make(tmp_path, "on", route_to("navigate_to", {"query": "x"}), classes=("open",),
                  run_tool=lambda n, a: ran.append(n), notes=[])
    turn(ll3, "open x", [])
    assert ran == []


def test_a_decision_tool_is_never_a_reflex_class(tmp_path):
    for t in ("answer_card", "undo_action", "task_control", "search_web"):
        assert live_laya.reflex_class(t) == "other"
    assert "other" not in live_laya.CLASSES
    assert live_laya.allowed_classes({"voice_live_reflex_classes": ["other", "open", "bogus"]}) == ["open"]


def test_the_default_is_shadow_with_an_empty_allow_list():
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["voice_live_reflex"] == "shadow"
    assert DEFAULT_SETTINGS["voice_live_reflex_classes"] == []
    assert DEFAULT_SETTINGS["voice_live_idempotency_s"] == 8
    assert live_laya.reflex_mode({}) == "shadow" and live_laya.reflex_mode({"voice_live_reflex": "junk"}) == "shadow"


def test_the_report_gate_is_95_percent_zero_unwanted_and_enough_rows(tmp_path):
    tool = _agreement_tool()
    log = tmp_path / "s.jsonl"

    def write(n_same, n_diff=0, n_unwanted=0, cls="open"):
        out = []
        out += [{"class": cls, "agreement": "same", "side": "both", "unwanted": False}] * n_same
        out += [{"class": cls, "agreement": "different", "side": "both", "unwanted": False}] * n_diff
        out += [{"class": cls, "agreement": "none", "side": "laya_only", "unwanted": True}] * n_unwanted
        log.write_text("\n".join(json.dumps(r) for r in out) + "\n", encoding="utf-8")
        return tool.report(log)["classes"][cls]
    assert write(40)["eligible"] is True
    assert write(29)["eligible"] is False                    # too few rows
    assert write(37, 3)["agreement"] == pytest.approx(0.925) and write(37, 3)["eligible"] is False
    assert write(38, 2)["eligible"] is True                  # 95.0%
    assert write(60, 0, 1)["eligible"] is False              # one unwanted action
    assert write(40, cls="other")["eligible"] is False       # "other" is never enabled


def test_the_gate_in_code_reads_the_report(tmp_path, monkeypatch):
    monkeypatch.setattr(live_laya, "eligible_classes_report",
                        lambda: {"classes": {"open": {"eligible": True}, "read": {"eligible": False}}})
    assert live_laya.eligible_classes(force=True) == frozenset({"open"})


# ── no added latency in the audio path ───────────────────────────────────────

def test_on_input_returns_at_once_and_thinks_off_the_receive_loop(tmp_path):
    gate, started = threading.Event(), threading.Event()

    def slow_route(text):
        started.set()
        gate.wait(5)
        return None
    ll, _ = make(tmp_path, "shadow", slow_route)
    t0 = time.perf_counter()
    ll.on_input("open my calendar")
    ll.on_gemini_call("navigate_to", {})
    ll.on_turn_end("open my calendar")
    elapsed = time.perf_counter() - t0
    assert started.wait(2)                         # the router is running...
    assert elapsed < 0.25                          # ...and the receive loop already got control back
    assert not gate.is_set()
    for fn in (ll.on_input, ll.on_gemini_call, ll.on_turn_end):
        assert not inspect.iscoroutinefunction(fn)
        assert "route(" not in inspect.getsource(fn)          # none of the three routes inline
    gate.set()
    assert ll.drain()


def test_the_voice_route_hooks_sit_in_the_transcript_and_tool_paths_not_the_audio_loop():
    src = (ROOT / "src" / "agent_friday" / "routes" / "voice.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        if "_live_laya." in line and "on_input" in line:
            assert "audio" not in line
    # the hook that sees a transcript fragment is the one inside the input_transcription branch
    i = src.index("_live_laya.for_call(_voice_session).on_input")
    assert "input_transcript" in src[i - 600:i]


# ── LV-3: the idempotency window ─────────────────────────────────────────────

def _window(monkeypatch, seconds=8):
    monkeypatch.setattr(live_laya, "idempotency_window", lambda settings=None: float(seconds))


def test_the_same_live_call_twice_inside_the_window_runs_once(spy, monkeypatch):
    _window(monkeypatch)
    spy.tool(READ, result=lambda inp: "result %d" % (len(spy.ran)))
    first = run(READ, {"query": "dentist"})
    second = run(READ, {"query": "dentist"})
    assert spy.count(READ) == 1
    assert second == first == "result 1"


def test_different_arguments_other_surfaces_and_a_closed_window_run_again(spy, monkeypatch):
    _window(monkeypatch)
    # the verifier's own same-turn repeat cache would answer a re-run read; it is not what is measured here
    monkeypatch.setattr(call_verifier, "_settings", lambda: dict(
        enabled=True, argument_sanity=True, repeat_cache=False, loop_breaker=True, loop_limit=3,
        injection_flag=True, privacy_vote=True))
    spy.tool(READ)
    run(READ, {"query": "dentist"})
    run(READ, {"query": "plumber"})
    assert spy.count(READ) == 2
    chat = {"authenticated": True, "session_id": "s", "confirmation_key": "s", "taint_key": "s"}
    assert live_laya._idem_key(READ, {"query": "x"}, chat) is None       # a typed chat is not a voice session
    assert live_laya._idem_key(READ, {"query": "x"}, dict(VOICE, surface="voice-local")) is None
    assert live_laya._idem_key(READ, {"query": "x"}, VOICE) is not None
    _window(monkeypatch, 0.05)
    run(READ, {"query": "late"})
    time.sleep(0.12)
    run(READ, {"query": "late"})
    assert spy.count(READ) == 4
    _window(monkeypatch, 0)
    run(READ, {"query": "off"})
    run(READ, {"query": "off"})
    assert spy.count(READ) == 6


def test_two_calls_in_flight_at_once_share_one_run(spy, monkeypatch):
    _window(monkeypatch)
    spy.tool(READ, result="slow answer", delay=0.4)
    out = []
    ts = [threading.Thread(target=lambda: out.append(run(READ, {"query": "q"}))) for _ in range(2)]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    assert spy.count(READ) == 1 and out == ["slow answer", "slow answer"]


def test_a_failure_is_not_the_first_result(spy, monkeypatch):
    _window(monkeypatch)
    answers = iter(["Tool error: boom", "fine"])
    spy.tool(READ, result=lambda inp: next(answers))
    assert run(READ, {"query": "q"}).startswith("Tool error")
    assert run(READ, {"query": "q"}) == "fine"
    assert spy.count(READ) == 2


def test_the_wrapper_sits_on_the_single_choke_point():
    assert getattr(agent._execute_tool, "__wrapped__", None) is not None
    src = (ROOT / "src" / "agent_friday" / "services" / "agent.py").read_text(encoding="utf-8")
    i = src.index("def _execute_tool(")
    assert "@_live_laya.idempotent_tool" in src[i - 300:i]


# ── LV-4: verifier additions ─────────────────────────────────────────────────

def pre(name, args, ctx, schema=None):
    call_verifier.reset()
    return call_verifier.preflight(name, args, schema=schema, session_ctx=dict(ctx))


def test_a_change_the_owners_words_did_not_ask_for_is_a_question(monkeypatch):
    ctx = dict(VOICE, owner_text="what is on my calendar tomorrow")
    out = pre(EVENT, {"title": "Dentist", "start": "2026-10-12T10:00:00-05:00"}, ctx)
    assert any(code == "laya_mutation" for code, _ in out.escalate), out
    ok = pre(EVENT, {"title": "Dentist", "start": "2026-10-12T10:00:00-05:00"},
             dict(VOICE, owner_text="put a dentist visit on my calendar for monday at ten"))
    assert not any(code == "laya_mutation" for code, _ in ok.escalate)
    # the guard is Laya's own: it is the router's, not a second copy
    assert live_laya.write_intent("put a dentist visit on my calendar") is True
    assert live_laya.write_intent("what is on my calendar") is False
    assert live_laya.write_intent("what's the best way to set up a calendar?") is False


def test_a_bare_yes_is_not_judged_by_the_mutation_guard():
    out = pre(EVENT, {"title": "Dentist", "start": "2026-10-12T10:00:00-05:00"},
              dict(VOICE, owner_text="yes go ahead"))
    assert not any(code == "laya_mutation" for code, _ in out.escalate)


def test_the_guard_does_not_touch_reads_other_surfaces_or_no_transcript():
    assert pre(READ, {"query": "q"}, dict(VOICE, owner_text="hello there")).escalate == []
    chat = {"authenticated": True, "session_id": "s", "confirmation_key": "s", "taint_key": "s",
            "owner_text": "what is on my calendar"}
    assert not any(c == "laya_mutation" for c, _ in pre(EVENT, {"title": "x", "start": "2026-10-12"}, chat).escalate)
    assert not any(c == "laya_mutation" for c, _ in
                   pre(EVENT, {"title": "x", "start": "2026-10-12"}, dict(VOICE)).escalate)


def test_a_mutation_the_guard_rejects_is_held_on_a_card_not_run(spy):
    spy.tool(EVENT)
    ctx = dict(VOICE, owner_text="how does my week look")
    out = run(EVENT, {"title": "Dentist", "start": "2026-10-12T10:00:00-05:00"}, ctx)
    assert spy.count(EVENT) == 0
    assert out.startswith("[VERIFIER HOLD]") or "NOT" in out or "approval" in out.lower(), out


def test_a_row_that_is_not_on_the_screen_is_a_fix_it(monkeypatch):
    stage = {"workspace": "messages", "items": [{"ref": "mail:a:t1"}, {"ref": "mail:a:t2"}], "open": None}
    monkeypatch.setattr(live_laya, "_screen_stage", lambda: stage)
    bad = pre("screen_select", {"refs": ["mail:a:t9"]}, dict(VOICE, owner_text="select those"))
    assert bad.kind == "fixit" and "not on the owner's screen" in bad.message
    good = pre("screen_select", {"refs": ["mail:a:t1"]}, dict(VOICE, owner_text="select those"))
    assert good.kind == "pass"
    # a typed chat is not this check's business
    chat = {"authenticated": True, "session_id": "s", "confirmation_key": "s", "taint_key": "s"}
    assert pre("screen_select", {"refs": ["mail:a:t9"]}, chat).kind == "pass"


def test_the_deixis_check_names_the_row_the_words_resolve_to(monkeypatch):
    stage = {"workspace": "messages", "items": [{"ref": "mail:a:t1"}, {"ref": "mail:a:t2"}], "open": None}
    pa = type("PA", (), {"decision": "tool", "refs": ["mail:a:t2"]})()
    fixes, asks = live_laya.verify("screen_select", {"refs": ["mail:a:t1"]},
                                   dict(VOICE, owner_text="select the second one"),
                                   stage=stage, resolve=lambda t, s: pa)
    assert fixes and "mail:a:t2" in fixes[0]
    fixes, _ = live_laya.verify("screen_select", {"refs": ["mail:a:t2"]},
                                dict(VOICE, owner_text="select the second one"),
                                stage=stage, resolve=lambda t, s: pa)
    assert fixes == []


def test_the_additions_never_loosen_what_was_already_checked():
    out = pre(EVENT, {"title": "Dentist", "start": "tmrw"},
              dict(VOICE, owner_text="put a dentist visit on my calendar tomorrow"),
              schema=agent._schema_for_tool(EVENT))
    assert out.kind == "fixit" and "'start' must be an ISO 8601" in out.message
    src = inspect.getsource(live_laya.verify)
    assert "escalate.clear" not in src and ".pop(" not in src       # it appends; it never removes


def test_a_held_call_gets_the_spoken_checking_cue(tmp_path):
    sent = []
    ll = live_laya.LiveLaya("t", send=sent.append, settings_fn=lambda: {})
    out = ll.shape_result(EVENT, {}, "[VERIFIER HOLD] 'x' was NOT executed: a reason.", "")
    assert out.endswith(live_laya.CHECKING_CUE) and {"type": "status", "text": "checking…"} in sent
    assert ll.shape_result(READ, {}, "plain", "") == "plain"
    ll.shutdown()


# ── LV-5: the egress receipt ─────────────────────────────────────────────────

PRIVATE_BODY = "ZEBRA QUOKKA: the contract total is confidential"


def test_the_receipt_has_sizes_and_no_content():
    ll = live_laya.LiveLaya("sess-1", settings_fn=lambda: {})
    ll.note_sent("search_email", PRIVATE_BODY * 10)
    ll.note_sent("search_email", PRIVATE_BODY)
    ll.ledger.add("context_slice", "system_instruction", 5000)
    ll.ledger.add("pin", "client_content", 120)
    summary = ll.close_leg(audio_bytes=3_200_000)
    ll.shutdown()
    assert summary["kinds"]["tool_result"] == {"count": 2, "chars": len(PRIVATE_BODY) * 11, "reduced_from": 0}
    rows_ = [json.loads(x) for x in action_gate.receipts_path().read_text(encoding="utf-8").splitlines() if x.strip()]
    mine = [r for r in rows_ if r.get("decision") == "live_egress_receipt"]
    assert mine and action_gate.verify_receipt(mine[-1])
    assert mine[-1]["kinds"]["context_slice"]["chars"] == 5000 and mine[-1]["audio_bytes"] == 3_200_000
    raw = action_gate.receipts_path().read_text(encoding="utf-8")
    assert "ZEBRA" not in raw and "QUOKKA" not in raw and "confidential" not in raw


def test_the_session_attach_records_sizes_of_instruction_tools_and_pins():
    import asyncio

    class Part:
        def __init__(self, text): self.text = text

    class FD:
        def __init__(self, name): self.name = name
        def model_dump(self, exclude_none=True): return {"name": self.name, "description": "x" * 50}

    class Tool:
        function_declarations = [FD("a"), FD("b")]

    class Cfg:
        class system_instruction:
            parts = [Part("p" * 700)]
        tools = [Tool()]

    class Sess:
        def __init__(self): self.sent = []
        async def send_client_content(self, **kw): self.sent.append(kw)

    sess = Sess()
    ll = live_laya.LiveLaya("s", settings_fn=lambda: {})
    ll.attach(sess, Cfg())
    asyncio.run(sess.send_client_content(turns={"role": "user", "parts": [{"text": "pin " * 10}]}, turn_complete=False))
    k = ll.ledger.summary()["kinds"]
    assert k["context_slice"]["chars"] == 700 and k["tool_declarations"]["chars"] > 100
    assert k["pin"] == {"count": 1, "chars": 40, "reduced_from": 0}
    assert ll.held == ["a", "b"] and len(sess.sent) == 1
    ll.shutdown()


def test_what_did_you_send_reads_the_receipt_in_fixed_words(monkeypatch):
    sid = "read-1"
    ll = live_laya.for_call({"conversation_id": sid}, settings_fn=lambda: {})
    ll.note_sent("search_email", PRIVATE_BODY)
    ll.ledger.add("context_slice", "system_instruction", 4000)
    ll.ledger.audio_bytes = 1_920_000
    monkeypatch.setattr(agent, "_CURRENT_TOOL_CONTEXT", type("V", (), {"get": staticmethod(lambda: {"conversation_id": sid})})())
    said = live_laya.handle({"action": "sent"})
    assert "tool results" in said and "context slices" in said and "minutes of it" in said
    assert "ZEBRA" not in said and "never the content" in said
    ll.shutdown()


def test_a_large_result_is_reduced_to_the_fields_the_question_needs():
    filler = " ".join("Quarterly logistics boilerplate sentence number %d." % i for i in range(120))
    mails = [{"id": "m%d" % i, "from": "sender%d@example.test" % i, "subject": "Subject %d" % i,
              "date": "2026-10-0%d" % (i % 9 + 1),
              "body": filler + " The invoice from Acme is due on the fifteenth. " + filler,
              "headers": {"x": "y" * 500}} for i in range(8)]
    big = json.dumps({"messages": mails, "total": 8})
    assert len(big) > 20000
    out, original = live_laya.reduce_result(big, "when is the Acme invoice due", threshold=3000, settings={})
    assert original == len(big) and len(out) < 4500
    assert "due on the fifteenth" in out and "sender0@example.test" in out and "m0" in out
    assert "x" * 100 not in out and "boilerplate sentence number 77" not in out
    assert "Reduced on this computer from %d" % original in out
    data = json.loads(out.split("\n[Reduced")[0].split("\n[")[0])
    assert len(data["messages"]) == 8 and all("headers" not in m and "body" not in m for m in data["messages"])


def test_a_large_plain_text_result_keeps_the_header_and_the_matching_paragraphs():
    paras = ["Document: lease terms"] + ["Filler paragraph %d about nothing in particular." % i * 3 for i in range(80)]
    paras[40] = "The deposit is two months of rent and is returned within thirty days."
    text = "\n\n".join(paras)
    out, n = live_laya.reduce_result(text, "how big is the deposit", threshold=2000, settings={})
    assert "Document: lease terms" in out and "deposit is two months" in out
    assert len(out) < n / 2


def test_small_results_and_non_strings_pass_through_untouched():
    assert live_laya.reduce_result("short", "q", threshold=3000, settings={}) == ("short", 5)
    assert live_laya.reduce_result({"a": 1}, "q", threshold=3000, settings={})[0] == {"a": 1}


def test_the_local_model_is_used_only_if_the_owner_allows_it_and_a_seat_is_already_up():
    big = "A paragraph that matters to nothing at all here.\n\n" * 200
    calls = []

    def summ(text, q, seat):
        calls.append(seat)
        return "short local summary"
    off = live_laya.reduce_result(big, "q", threshold=1000, settings={"voice_live_reduce_local_model": "off"},
                                  seat_fn=lambda: "seat", summarize=summ)
    assert calls == [] and off[0] != "short local summary"
    live_laya.reduce_result(big, "q", threshold=1000, settings={"voice_live_reduce_local_model": "seat_if_up"},
                            seat_fn=lambda: None, summarize=summ)
    assert calls == []                                        # no seat up: never loaded
    got = live_laya.reduce_result(big, "q", threshold=100, settings={"voice_live_reduce_local_model": "seat_if_up"},
                                  seat_fn=lambda: "seat", summarize=summ)
    assert calls == ["seat"] and got[0].startswith("short local summary")
    src = inspect.getsource(live_laya._seat_up)
    assert "serving()" in src and "arm(" not in src and "start" not in src.lower().replace("startswith", "")


def test_the_result_is_reduced_before_the_egress_gate_and_sized_after_it():
    src = (ROOT / "src" / "agent_friday" / "routes" / "voice.py").read_text(encoding="utf-8")
    a = src.index(".shape_result(")
    b = src.index("_gate_voice_tool_result(result, fname)", a)
    c = src.index(".note_sent(", b)
    assert a < b < c


# ── the Laya contract: "stop doing things yourself" and "what did you send?" ──

@pytest.mark.parametrize("text", [
    "stop doing things yourself", "Friday, stop doing things on your own", "stop acting on your own",
    "quit running things by yourself", "don't do things on your own", "don't act by yourself",
    "turn off the reflexes", "no more reflexes",
])
def test_the_owner_can_say_stop_in_their_own_words(text):
    assert live_laya.is_stop_phrase(text)


@pytest.mark.parametrize("text", [
    "stop doing that", "stop the music", "do it yourself please", "what can you do on your own",
    "open my calendar", "stop", "I'll do it myself", "reflexes are fast",
])
def test_ordinary_speech_is_not_the_stop_phrase(text):
    assert not live_laya.is_stop_phrase(text)


def test_saying_stop_turns_the_reflex_off_whatever_the_mode(tmp_path, monkeypatch):
    saved = []
    monkeypatch.setattr(live_laya, "set_reflex_mode", lambda m: saved.append(m))
    notes, ran = [], []
    ll, _ = make(tmp_path, "on", route_to("navigate_to", {"query": "x"}), classes=("open",),
                 run_tool=lambda n, a: ran.append(n), notes=notes)
    live_laya._ELIG_CACHE.update(at=time.time() + 1e6, classes=frozenset({"open"}))
    turn(ll, "stop doing things yourself")
    assert saved == ["off"] and ran == [] and notes == [live_laya.OFF_NOTE]


def test_the_tool_narrows_without_a_card_and_widens_only_through_one(monkeypatch):
    cur = {"mode": "on"}
    monkeypatch.setattr(live_laya, "_settings", lambda: {"voice_live_reflex": cur["mode"]})
    monkeypatch.setattr(live_laya, "set_reflex_mode", lambda m: cur.update(mode=m))
    assert live_laya.handle({"action": "reflex", "mode": "off"}).startswith("Done")
    assert cur["mode"] == "off"
    from agent_friday.services import setting_proposals as sp
    held = []
    monkeypatch.setattr(sp, "hold", lambda *a, **k: held.append((a, k)) or "SETTING_NEEDS_YES: card")
    assert live_laya.handle({"action": "reflex", "mode": "on"}).startswith("SETTING_NEEDS_YES")
    assert cur["mode"] == "off" and held[0][0][0] == "live_laya"


def test_the_tool_is_registered_with_a_ring_and_a_gate_class():
    assert "live_laya" in agent.CLAUDE_TOOL_HANDLERS
    assert agent.TOOL_RINGS["live_laya"] == 1
    klass, _ = action_gate.classify("live_laya", {"action": "sent"}, VOICE)
    assert klass == action_gate.INTERNAL
    assert live_laya.VOICE_CONTRACT["stop"]["effect"] == "voice_live_reflex = off"


def test_the_live_declarations_gain_no_tool_so_the_voice_contract_budget_is_untouched():
    from agent_friday.services import voice_engine
    assert "live_laya" not in voice_engine._VOICE_SHARED_TOOLS


@pytest.mark.parametrize("text", [
    "what did you send Google", "hey what did you send to Google this call", "what have you sent to the cloud",
    "what's been sent to Gemini", "what went to Google", "show me the receipt",
])
def test_the_owner_can_ask_what_was_sent_in_their_own_words(text):
    assert live_laya.is_receipt_question(text)


@pytest.mark.parametrize("text", ["what did you send to my mother", "send google a note", "what is google",
                                  "open the receipt folder please"])
def test_other_speech_is_not_the_receipt_question(text):
    assert not live_laya.is_receipt_question(text)


def test_asking_what_was_sent_has_laya_hand_gemini_the_fixed_receipt_in_every_mode(tmp_path):
    for mode in ("off", "shadow", "on"):
        notes = []
        ll, _ = make(tmp_path, mode, lambda t: None, notes=notes)
        ll.note_sent("search_email", PRIVATE_BODY)
        turn(ll, "what did you send Google")
        assert len(notes) == 1 and "This call I sent Google:" in notes[0] and "1 tool results" in notes[0], (mode, notes)
        assert "ZEBRA" not in notes[0]
        ll.shutdown()


def test_the_setting_is_validated_and_routed_like_its_siblings():
    from agent_friday.routes import core_routes
    assert core_routes._VOICE_ENUMS["voice_live_reflex"] == ("off", "shadow", "on")
    from agent_friday.services import setting_proposals as sp
    assert sp._route("settings.laya.live_reflex", "Off") == ("live_laya", {"action": "reflex", "mode": "off"})
    assert set(sp.TOOL_OF.values()) == set(sp.KEYS_OF) == set(sp.LABELS)
