"""The optional trained-head tier of the Laya router (DA-1a). Fakes only: no model, no GPU.

The head is a callable ``head(case) -> {"operation", "args", "score"}`` (tools/tool_head
eval_wiring.ToolHead's contract). Here it is scripted. What is pinned: the setting (off by
default), the certification receipt (no receipt, no head), that shadow changes nothing and logs
hashes only, that "on" replaces tier 1 only above the certified threshold, and that rules, the
mutation guard and read-only-ness stay in force whatever the head says. The five held-out-set-3
misses are red tests for the HEAD path: they pass only with a trained head (DA-1b).
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

from agent_friday.services import laya_router as lr
from agent_friday.services import laya_router_head as lh

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

PROTO = lr.Route("no_tool", confidence=0.5, layer="laya2", reason="PROTO")


@pytest.fixture(autouse=True)
def _local_hash_key(monkeypatch):
    """The text hashes are keyed with the install's sealed local key; these suites use a fixed one."""
    from agent_friday.services import laya_safe_writes as _sw
    monkeypatch.setattr(_sw, "_salt", lambda: b"k" * 32)
    monkeypatch.setattr(_sw, "_KEY_CACHE", None)
    monkeypatch.setattr(_sw, "_KEY_RETRY_AT", 0.0)


class FakeHead:
    def __init__(self, answer):
        self.answer, self.cases = answer, []

    def __call__(self, case):
        self.cases.append(case)
        out = self.answer(case) if callable(self.answer) else self.answer
        return dict(out)


def _certify(tmp_path, monkeypatch, head, threshold=0.9, tamper=False, certified=True):
    """A certified profile on disk (receipt bound to the checkpoint config) and a loader that returns ``head``."""
    d = tmp_path / "tool_head" / "owner"
    (d / "model").mkdir(parents=True, exist_ok=True)
    cfg = d / "model" / lh.CONFIG_NAME
    cfg.write_text('{"temperature": 1.3}', encoding="utf-8")
    (d / "catalogue.json").write_text("[]", encoding="utf-8")
    rec = {"v": 1, "certified": certified, "threshold": threshold, "target_error": 0.01, "delta": 0.05,
           "config_sha256": hashlib.sha256(cfg.read_bytes()).hexdigest()}
    (d / lh.RECEIPT_NAME).write_text(json.dumps(rec), encoding="utf-8")
    if tamper:
        cfg.write_text('{"temperature": 9.9}', encoding="utf-8")
    monkeypatch.setattr(lh, "root_dir", lambda: tmp_path / "tool_head")
    monkeypatch.setattr(lh, "RECEIPT_TTL_S", 0.0)
    monkeypatch.setattr(lh, "log_path", lambda: tmp_path / "shadow.jsonl")
    loads = []
    monkeypatch.setattr(lh, "_loader", lambda profile: loads.append(profile) or head)
    lh.reset()
    return loads


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    lh.reset()
    monkeypatch.setattr(lr, "_tier1", lambda text: PROTO)
    from agent_friday.services import laya2_encoder
    monkeypatch.setattr(laya2_encoder, "is_available", lambda: True)
    yield
    lh.reset()


def _route(text, mode, **kw):
    return lr.route(text, head_mode=mode, log=False, budget_ms=5000, **kw)


# ── the setting ─────────────────────────────────────────────────────────────

def test_the_setting_ships_in_shadow_and_is_read_strictly():
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["laya_tool_head"] == "shadow"      # ships in shadow: inert until a certified head exists
    assert lh.configured_mode({}) == "off"
    for v in ("on", "shadow"):
        assert lh.configured_mode({"laya_tool_head": v}) == v
    for v in (True, 1, "ON", "yes", None, "true", ["on"]):
        assert lh.configured_mode({"laya_tool_head": v}) == "off"


def test_the_setting_is_documented():
    doc = (ROOT / "docs" / "user-guide" / "configuration.md").read_text(encoding="utf-8")
    row = next(l for l in doc.splitlines() if "`laya_tool_head`" in l)
    assert "`off`" in row and "`shadow`" in row and "`on`" in row and "hashes" in row


# ── certification: no receipt, no head ──────────────────────────────────────

def test_no_receipt_means_no_head_in_any_mode(tmp_path, monkeypatch):
    head = FakeHead({"operation": "check_email", "args": None, "score": 0.99})
    monkeypatch.setattr(lh, "root_dir", lambda: tmp_path / "none")
    monkeypatch.setattr(lh, "RECEIPT_TTL_S", 0.0)
    monkeypatch.setattr(lh, "_loader", lambda p: head)
    lh.reset()
    assert lh.available() is False
    assert _route("anything in the mailbox lately", "on").reason == "PROTO"
    assert head.cases == []


@pytest.mark.parametrize("kw", [dict(tamper=True), dict(certified=False)])
def test_a_receipt_that_is_not_valid_for_this_checkpoint_gives_no_head(tmp_path, monkeypatch, kw):
    head = FakeHead({"operation": "check_email", "args": None, "score": 0.99})
    _certify(tmp_path, monkeypatch, head, **kw)
    assert lh.available() is False
    assert _route("anything in the mailbox lately", "on").reason == "PROTO"
    assert head.cases == []


@pytest.mark.parametrize("thr", [0, 0.0, -1, 1.5, True, "0.9", None])
def test_a_threshold_out_of_range_is_not_a_certification(tmp_path, monkeypatch, thr):
    head = FakeHead({"operation": "check_email", "args": None, "score": 0.99})
    _certify(tmp_path, monkeypatch, head)
    p = tmp_path / "tool_head" / "owner" / lh.RECEIPT_NAME
    rec = json.loads(p.read_text())
    rec["threshold"] = thr
    p.write_text(json.dumps(rec))
    lh.reset()
    assert lh.load_receipt() is None


# ── off: the router is exactly the router ───────────────────────────────────

def test_with_the_setting_off_the_head_is_never_loaded_or_asked(tmp_path, monkeypatch):
    head = FakeHead({"operation": "check_email", "args": None, "score": 0.99})
    loads = _certify(tmp_path, monkeypatch, head)
    r = _route("anything in the mailbox lately", "off")
    assert r.reason == "PROTO" and loads == [] and head.cases == []
    assert not (tmp_path / "shadow.jsonl").exists()


# ── on: replaces tier 1 above the certified threshold only ──────────────────

def test_on_replaces_the_prototypes_above_the_certified_threshold(tmp_path, monkeypatch):
    head = FakeHead({"operation": "check_email", "args": {"urgent_only": True}, "score": 0.95})
    _certify(tmp_path, monkeypatch, head, threshold=0.9)
    r = _route("anything in the mailbox lately", "on")
    assert (r.decision, r.tool, r.layer) == ("tool", "check_email", "head")
    assert r.args == {"urgent_only": True} and r.confidence == 0.95 and r.label


def test_on_leaves_the_turn_to_the_prototypes_below_the_threshold(tmp_path, monkeypatch):
    head = FakeHead({"operation": "check_email", "args": None, "score": 0.89})
    _certify(tmp_path, monkeypatch, head, threshold=0.9)
    assert _route("anything in the mailbox lately", "on").reason == "PROTO"
    assert len(head.cases) == 1


def test_a_head_that_hands_the_turn_over_leaves_it_to_the_prototypes(tmp_path, monkeypatch):
    head = FakeHead({"operation": "brain", "args": None, "score": None})
    _certify(tmp_path, monkeypatch, head)
    assert _route("anything in the mailbox lately", "on").reason == "PROTO"


def test_a_head_that_fails_never_breaks_the_turn(tmp_path, monkeypatch):
    def boom(case):
        raise RuntimeError("model gone")
    _certify(tmp_path, monkeypatch, FakeHead(boom))
    assert _route("anything in the mailbox lately", "on").reason == "PROTO"


def test_a_head_that_cannot_load_never_breaks_the_turn(tmp_path, monkeypatch):
    _certify(tmp_path, monkeypatch, None)
    monkeypatch.setattr(lh, "_loader", lambda p: (_ for _ in ()).throw(OSError("weights missing")))
    assert _route("anything in the mailbox lately", "on").reason == "PROTO"


def test_the_head_can_say_no_tool_or_ask(tmp_path, monkeypatch):
    head = FakeHead({"operation": "none", "args": None, "score": 0.97})
    _certify(tmp_path, monkeypatch, head)
    r = _route("anything in the mailbox lately", "on")
    assert (r.decision, r.layer) == ("no_tool", "head")
    head.answer = {"operation": "ask", "args": None, "score": 0.97}
    r = _route("anything in the mailbox lately", "on")
    assert (r.decision, r.layer) == ("ask", "head") and r.question


# ── what stays in force ─────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "please send the report to the team", "delete the email from the spammer",
    "reply to my mom and tell her I'll call tonight", "undo that", "pause the podcast",
    "don't check my email right now"])
def test_rules_and_the_mutation_guard_decide_before_the_head_is_asked(tmp_path, monkeypatch, text):
    head = FakeHead({"operation": "check_email", "args": None, "score": 0.99})
    _certify(tmp_path, monkeypatch, head)
    r = _route(text, "on")
    assert r.layer == "rule" and head.cases == []


def test_the_head_can_only_run_the_routers_read_tools(tmp_path, monkeypatch):
    assert lh.HEAD_READ_TOOLS <= set(lr.TOOLS)
    for name in lh.HEAD_READ_TOOLS:
        assert not name.startswith(("send", "delete", "create", "update", "organize", "write", "navigate"))
    for op in ("send_email", "delete_file", "organize_email", "navigate_to", "undo_action", "answer_card",
               "media_play", "task_control"):
        head = FakeHead({"operation": op, "args": {"query": "x"}, "score": 0.99})
        _certify(tmp_path, monkeypatch, head)
        r = _route("anything in the mailbox lately", "on")
        assert r.decision == "defer" and r.tool is None and r.args == {}, op


def test_a_tool_this_session_does_not_hold_is_deferred_even_from_the_head(tmp_path, monkeypatch):
    head = FakeHead({"operation": "check_email", "args": None, "score": 0.99})
    _certify(tmp_path, monkeypatch, head)
    r = _route("anything in the mailbox lately", "on", tools=["query_calendar"])
    assert r.decision == "defer" and "not held" in r.reason


# ── arguments are chosen, never written ─────────────────────────────────────

def test_the_head_picks_its_query_among_spans_of_the_owners_words(tmp_path, monkeypatch):
    spans = lh.candidate_spans("any messages from work")
    assert "work" in [s.lower() for s in spans]
    assert all(s.lower() in "any messages from work" for s in spans)
    head = FakeHead(lambda case: {"operation": "search_email", "args": {"query": "work"}, "score": 0.97})
    _certify(tmp_path, monkeypatch, head)
    r = _route("any messages from work", "on")
    assert (r.tool, r.args, r.layer) == ("search_email", {"query": "work"}, "head")
    assert head.cases[0]["candidates"]["query"] == spans


def test_an_invented_query_is_dropped_and_the_template_fills_it(tmp_path, monkeypatch):
    head = FakeHead({"operation": "search_files", "args": {"query": "quarterly forecast", "body": "x"},
                     "score": 0.97})
    _certify(tmp_path, monkeypatch, head)
    r = _route("where did I put the insurance policy", "on")
    assert r.tool == "search_files" and "quarterly" not in json.dumps(r.args) and "body" not in r.args
    assert r.args.get("query")                      # the router's own template reading, from the words


def test_an_outward_search_without_a_subject_from_the_head_still_asks(tmp_path, monkeypatch):
    head = FakeHead({"operation": "search_news", "args": None, "score": 0.99})
    _certify(tmp_path, monkeypatch, head)
    assert "search_news" in lr.ASK_BEFORE_GUESSING
    r = _route("what's the latest on the wildfires", "on")
    assert (r.decision, r.tool, r.layer) == ("ask", "search_news", "head")
    assert r.args == {}


def test_an_outward_search_with_a_certified_subject_runs_with_only_the_subject(tmp_path, monkeypatch):
    head = FakeHead({"operation": "search_news", "args": {"query": "wildfires"}, "score": 0.99})
    _certify(tmp_path, monkeypatch, head)
    r = _route("what's the latest on the wildfires", "on")
    assert (r.decision, r.tool, r.args) == ("tool", "search_news", {"query": "wildfires"})
    assert "latest" not in json.dumps(r.args)       # never the whole utterance


# ── shadow: logs, hashes only, changes nothing ──────────────────────────────

def test_shadow_row_has_hashes_operations_and_numbers_only(tmp_path, monkeypatch):
    text = "any messages from the purple elephant society"
    head = FakeHead({"operation": "search_email", "args": {"query": "purple elephant society"}, "score": 0.97})
    _certify(tmp_path, monkeypatch, head)
    final = lr.Route("no_tool", confidence=0.5, layer="laya2")
    assert lh.shadow(text, final, final, "voice", sync=True) is True
    row = json.loads((tmp_path / "shadow.jsonl").read_text().splitlines()[-1])
    blob = json.dumps(row)
    for word in ("purple", "elephant", "society", "messages"):
        assert word not in blob
    assert len(row["text_sha"]) == 16 and len(row["head"]["args_sha"]) == 16
    assert row["head"]["op"] == "search_email" and row["head"]["score"] == 0.97
    assert row["head"]["above_threshold"] is True and row["threshold"] == 0.9
    assert row["proto"]["decision"] == "no_tool" and row["final"]["layer"] == "laya2"
    assert row["agree"] is False and row["mode"] == "shadow"
    assert row["head"]["args_keys"] == ["query"]


def test_in_shadow_mode_the_route_is_the_prototypes_and_a_row_is_written(tmp_path, monkeypatch):
    head = FakeHead({"operation": "check_email", "args": None, "score": 0.97})
    _certify(tmp_path, monkeypatch, head)
    real = lh.shadow
    monkeypatch.setattr(lh, "shadow", lambda *a, **k: real(*a, sync=True, **k))
    r = _route("anything in the mailbox lately", "shadow")
    assert r.reason == "PROTO" and r.layer == "laya2"
    row = json.loads((tmp_path / "shadow.jsonl").read_text().splitlines()[-1])
    assert row["head"]["tool"] == "check_email" and row["proto"]["decision"] == "no_tool"
    assert row["agree"] is False


def test_shadow_agreement_is_recorded_when_head_and_prototype_match(tmp_path, monkeypatch):
    head = FakeHead({"operation": "none", "args": None, "score": 0.97})
    _certify(tmp_path, monkeypatch, head)
    real = lh.shadow
    monkeypatch.setattr(lh, "shadow", lambda *a, **k: real(*a, sync=True, **k))
    _route("anything in the mailbox lately", "shadow")
    row = json.loads((tmp_path / "shadow.jsonl").read_text().splitlines()[-1])
    assert row["agree"] is True


def test_shadow_with_a_failing_head_still_answers_the_turn_and_logs_no_head(tmp_path, monkeypatch):
    def boom(case):
        raise RuntimeError("x")
    _certify(tmp_path, monkeypatch, FakeHead(boom))
    real = lh.shadow
    monkeypatch.setattr(lh, "shadow", lambda *a, **k: real(*a, sync=True, **k))
    r = _route("anything in the mailbox lately", "shadow")
    assert r.reason == "PROTO"
    row = json.loads((tmp_path / "shadow.jsonl").read_text().splitlines()[-1])
    assert row["head"] is None and "agree" not in row


def test_shadow_runs_off_the_turns_path_and_drops_rows_when_backlogged(tmp_path, monkeypatch):
    import threading
    gate = threading.Event()
    head = FakeHead(lambda case: (gate.wait(5), {"operation": "none", "args": None, "score": 0.9})[1])
    _certify(tmp_path, monkeypatch, head)
    final = lr.Route("no_tool")
    queued = [lh.shadow("turn %d" % i, final, None, "voice") for i in range(lh.MAX_SHADOW_BACKLOG + 3)]
    assert queued.count(True) == lh.MAX_SHADOW_BACKLOG and queued.count(False) == 3
    gate.set()
    for _ in range(100):
        if lh._shadow_pending == 0:
            break
        import time
        time.sleep(0.05)
    assert lh._shadow_pending == 0


def test_shadow_does_nothing_without_a_certified_head(tmp_path, monkeypatch):
    monkeypatch.setattr(lh, "root_dir", lambda: tmp_path / "nothing")
    monkeypatch.setattr(lh, "log_path", lambda: tmp_path / "shadow.jsonl")
    lh.reset()
    assert lh.shadow("anything", lr.Route("no_tool"), None, "voice", sync=True) is False
    assert not (tmp_path / "shadow.jsonl").exists()


# ── the production head: spans are choices ──────────────────────────────────

def test_the_span_head_fills_a_query_by_choosing_among_the_supplied_spans():
    sys.path.insert(0, str(ROOT / "tools"))
    pytest.importorskip("tool_head", reason="tools/tool_head (the DA-1 training package) is not on this chain")
    from tool_head import eval_wiring as ew

    class Scripted(ew.Runner):
        def predict(self, state, questions):
            out = {}
            for qid, q in questions.items():
                if qid == "operation":
                    out[qid] = {"choice": "search_news", "confidence": 0.9, "probabilities": {}}
                else:
                    pick = next(k for k, v in q["criteria"].items() if v == "wildfires")
                    out[qid] = {"choice": pick, "confidence": 0.8, "probabilities": {}}
            return {"answers": out}

    cat = {"search_news": {"name": "search_news", "description": "news", "group": "news", "writes": False,
                           "required": ["query"], "properties": {"query": {"type": "string"}}}}
    head = lh.make_span_head(Scripted(), cat)
    text = "what's the latest on the wildfires"
    out = head({"id": "x", "kind": "tool", "user": text, "history": [],
                "candidates": {"query": lh.candidate_spans(text)}})
    assert out["operation"] == "search_news" and out["args"] == {"query": "wildfires"}
    assert abs(out["score"] - 0.9 * 0.8) < 1e-9
    none = head({"id": "x", "kind": "tool", "user": text, "history": [], "candidates": {}})
    assert none["args"] is None                      # nothing to choose from: no invented argument


# ── the five set-3 misses on the HEAD path ──────────────────────────────────

FIVE = [
    ("how packed is tomorrow", "query_calendar", {}),
    ("when am I seeing the doctor", "query_calendar", {}),
    ("any messages from work", "search_email", {"query": "work"}),
    ("what's the latest on the wildfires", "search_news", {"query": "wildfires"}),
    ("remind me what you said about my sleep", "search_past_conversations", {"query": "sleep"}),
]


@pytest.mark.parametrize("text,tool,args", FIVE)
def test_the_five_misses_reach_tier_one_so_only_a_better_decider_can_fix_them(text, tool, args):
    """Not fixed by a rule: tier 0 declines all five. Rules tuned on set 3 are refused by design."""
    assert lr._tier0(" ".join(text.split())) is None


@pytest.mark.xfail(strict=True, reason="needs trained head (DA-1b)")
@pytest.mark.parametrize("text,tool,args", FIVE)
def test_the_five_set3_misses_are_answered_by_the_trained_head(text, tool, args):
    r = lr.route(text, head_mode="on", log=False, budget_ms=5000)
    assert r.layer == "head", "no certified trained head answered this turn"
    assert (r.decision, r.tool) == ("tool", tool)
    assert all(str(r.args.get(k, "")).lower() == v for k, v in args.items())


@pytest.mark.parametrize("text,tool,args", FIVE)
def test_the_head_path_can_satisfy_all_five_given_a_head_that_gets_them_right(tmp_path, monkeypatch, text, tool, args):
    """The plumbing: a certified head that proposes the right tool and subject yields exactly the wanted
    route, including a clear-subject news search with ASK_BEFORE_GUESSING in force."""
    import datetime
    monkeypatch.setattr(lr, "_today", lambda: datetime.date(2026, 10, 14))
    head = FakeHead({"operation": tool, "args": args or None, "score": 0.98})
    _certify(tmp_path, monkeypatch, head)
    r = _route(text, "on")
    assert (r.decision, r.tool, r.layer) == ("tool", tool, "head")
    want = dict(args)
    if tool == "query_calendar" and lr.spoken_day(text) is not None:
        want["day"] = lr.spoken_day(text).isoformat()     # the day the words name, a declared argument
    assert {k: str(v).lower() for k, v in r.args.items()} == want


# ── the head is a visible CPU job, reads its setting rarely, and touches no sys.path ─────

def test_the_mode_setting_is_read_from_disk_at_most_once_per_ttl(monkeypatch):
    import agent_friday.core as core
    reads = []
    monkeypatch.setattr(core, "_load_settings", lambda: reads.append(1) or {"laya_tool_head": "shadow"})
    lh.reset()
    assert [lh.configured_mode() for _ in range(6)] == ["shadow"] * 6
    assert len(reads) == 1


def test_a_head_proposal_runs_inside_a_visible_job_or_not_at_all(monkeypatch, tmp_path):
    from agent_friday.services import visible_jobs as vj
    seen = []

    class Door:
        def __init__(self, name, **kw):
            seen.append((name, kw.get("kind")))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(vj, "visible_job", Door)
    monkeypatch.setattr(vj, "note_inference", lambda *a, **k: None)
    monkeypatch.setattr(lh, "_get_head", lambda profile="owner": (lambda case: {"operation": "none", "args": None, "score": 0.9}))
    assert lh.propose("what's on my calendar") is not None
    assert seen == [("Laya tool head", "laya_head")]
    monkeypatch.setattr(vj, "visible_job", None)           # the door is unavailable: the head does not run
    assert lh.propose("what's on my calendar") is None


def test_the_default_loader_pins_the_runner_to_the_cpu_or_refuses():
    import inspect
    src = inspect.getsource(lh._default_loader)
    assert 'device="cpu"' in src and "cannot be pinned to the CPU" in src


def test_importing_the_training_package_leaves_sys_path_alone(monkeypatch, tmp_path):
    pkg = tmp_path / "tools" / "tool_head"
    pkg.mkdir(parents=True)
    (pkg / "common.py").write_text("MARK = 7\n", encoding="utf-8")
    monkeypatch.setattr(lh, "_tools_dir", lambda: tmp_path / "tools")
    monkeypatch.delitem(sys.modules, "tool_head", raising=False)
    before = list(sys.path)
    try:
        assert lh.import_tool_head("common").MARK == 7
    finally:
        for k in [k for k in sys.modules if k == "tool_head" or k.startswith("tool_head.")]:
            sys.modules.pop(k, None)
    assert sys.path == before
    assert "sys.path.insert" not in __import__("inspect").getsource(lh)


def test_shadow_runs_in_one_visible_job_per_burst_not_one_card_per_turn(tmp_path, monkeypatch):
    """Router N4: every shadow-mode routed turn used to open its own 'Laya tool head' card. A burst of turns is
    now drained inside ONE visible job, and the next idle window opens a new one."""
    import threading
    import time
    from agent_friday.services import visible_jobs as vj
    tops = []

    class Counting(vj.visible_job):
        def __enter__(self):
            if vj.CURRENT.get() is None:
                tops.append(self._kw["name"])
            return super().__enter__()
    monkeypatch.setattr(vj, "visible_job", Counting)
    monkeypatch.setattr(lh, "SHADOW_IDLE_WINDOW_S", 0.3)
    gate = threading.Event()
    head = FakeHead(lambda case: (gate.wait(5), {"operation": "none", "args": None, "score": 0.9})[1])
    _certify(tmp_path, monkeypatch, head)
    final = lr.Route("no_tool")
    assert all(lh.shadow("turn %d" % i, final, None, "voice") for i in range(lh.MAX_SHADOW_BACKLOG))
    gate.set()
    for _ in range(200):
        if lh._shadow_pending == 0 and not lh._shadow_runner:
            break
        time.sleep(0.05)
    assert len(tops) == 1, "one card for the burst: %r" % tops
    assert len(head.cases) == lh.MAX_SHADOW_BACKLOG
    assert lh.shadow("later", final, None, "voice") is True                      # the next window: a new card
    for _ in range(200):
        if lh._shadow_pending == 0 and not lh._shadow_runner:
            break
        time.sleep(0.05)
    assert len(tops) == 2
