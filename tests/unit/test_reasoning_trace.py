"""Reasoning traces: live feed, nesting, honesty labels, and the archive.

The archive's promises are the ones a user relies on when they open an old
trace: it is the record that was written (hash chain + HMAC), nobody could
read it off the disk (encrypted per line), and a restart or a launcher edit
does not make yesterday's records stop verifying (the key is the persisted
file-grants key, never the environment).
"""

import base64
import json
import threading

import pytest


@pytest.fixture
def rt(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.delenv("FRIDAY_SECRET_KEY", raising=False)
    import agent_friday.services.file_grants as fg
    fg._SIGNING_KEY_CACHE.clear()
    from agent_friday.services import reasoning_trace as mod
    mod._reset_for_tests()
    monkeypatch.setattr(mod, "BASE_DIR_OVERRIDE", tmp_path / "traces")
    monkeypatch.setattr(mod, "settings", lambda: {"capture": True, "retention_days": 0})
    yield mod
    mod._reset_for_tests()


def _lines(mod):
    return [json.loads(x) for x in mod.ledger_path().read_text(encoding="utf-8").splitlines() if x.strip()]


# ── live ─────────────────────────────────────────────────────────────────────

def test_deltas_coalesce_into_one_segment_and_reach_the_feed(rt):
    tid = rt.start("chat", "hello", model="bonsai2:27b", parent_id=None)
    with rt.activate(tid):
        for piece in ["Let ", "me ", "think", " about it."]:
            rt.reasoning(piece)
    live = rt.live_trace(tid)
    segs = [e for e in live["events"] if e["type"] == "reasoning"]
    assert len(segs) == 1 and segs[0]["text"] == "Let me think about it."
    assert segs[0]["source"] == rt.SOURCE_FULL
    fed = "".join(e["text"] for e in rt.feed(0)["events"] if e["type"] == "reasoning_delta")
    assert fed == "Let me think about it."


def test_feed_cursor_returns_only_new_events(rt):
    tid = rt.start("chat", "x", parent_id=None)
    first = rt.feed(0)
    rt.note("checking the calendar", trace_id=tid)
    nxt = rt.feed(first["cursor"])
    assert [e["type"] for e in nxt["events"]] == ["note"]
    assert rt.feed(nxt["cursor"])["events"] == []


def test_tool_calls_interleave_in_order_with_reasoning(rt):
    tid = rt.start("chat", "x", parent_id=None)
    with rt.activate(tid):
        rt.reasoning("first I look up the weather")
        cid = rt.tool_call("get_weather", {"city": "Austin"})
        rt.tool_result(cid, {"temp": 91}, ok=True)
        rt.reasoning("91 is hot")
    types = [e["type"] for e in rt.live_trace(tid)["events"]]
    assert types == ["reasoning", "tool_call", "tool_result", "reasoning"]


def test_subagent_nests_under_the_turn_that_spawned_it(rt):
    parent = rt.start("chat", "plan my week", parent_id=None)
    with rt.activate(parent):
        child = rt.start("subagent", "research flights")   # parent_id="auto"
    grand = rt.start("subagent", "compare fares", parent_id=child)
    assert rt.live_trace(child)["parent_id"] == parent
    assert rt.live_trace(grand)["root_id"] == parent
    tree = rt.get_tree(grand)["tree"]
    assert tree["trace_id"] == parent
    assert tree["children"][0]["trace_id"] == child
    assert tree["children"][0]["children"][0]["trace_id"] == grand


def test_trace_context_does_not_leak_into_an_unrelated_thread(rt):
    tid = rt.start("chat", "x", parent_id=None)
    seen = {}
    with rt.activate(tid):
        t = threading.Thread(target=lambda: seen.setdefault("cur", rt.current()))
        t.start(); t.join()
    assert seen["cur"] is None


def test_unreadable_reasoning_is_labelled_never_filled(rt):
    tid = rt.start("chat", "x", model="claude-opus-5-5", parent_id=None)
    rt.mark_source(rt.SOURCE_NOT_EXPOSED, trace_id=tid)
    rt.mark_source(rt.SOURCE_NOT_EXPOSED, trace_id=tid)
    rt.mark_source(rt.SOURCE_REDACTED, trace_id=tid)
    evs = rt.live_trace(tid)["events"]
    assert [e["type"] for e in evs] == ["source_marker", "source_marker"]
    assert evs[0]["label"] == "reasoning not exposed by provider" and evs[0]["count"] == 2
    assert not any(e["type"] == "reasoning" for e in evs)


def test_capture_off_records_nothing(rt, monkeypatch):
    monkeypatch.setattr(rt, "settings", lambda: {"capture": False})
    assert rt.start("chat", "x") is None
    rt.reasoning("should go nowhere")
    assert rt.feed(0)["events"] == []


def test_scope_reuses_an_active_trace_and_opens_one_otherwise(rt):
    with rt.scope("background", "📰 Front Page") as outer:
        with rt.scope("background", "inner call") as inner:
            assert inner == outer
        rt.note("did work")
    assert rt.live_trace(outer)["status"] == "complete"


def test_nested_scope_opens_a_child_with_or_without_an_active_trace(rt):
    with rt.scope("scheduled", "job", nested=True) as solo:
        assert solo and rt.live_trace(solo)["parent_id"] is None
        with rt.scope("scheduled", "sub-job", nested=True) as child:
            assert child != solo and rt.live_trace(child)["parent_id"] == solo


def test_a_trace_with_no_events_is_not_archived(rt):
    tid = rt.start("scheduled", "cleanup job", parent_id=None)
    assert rt.finish(tid) is None
    assert not rt.ledger_path().exists()


# ── archive ──────────────────────────────────────────────────────────────────

def _finished(rt, text="secret reasoning about Janet's appointment"):
    tid = rt.start("chat", "q", model="bonsai2:27b", seat="local", parent_id=None)
    with rt.activate(tid):
        rt.reasoning(text)
        cid = rt.tool_call("calendar_list", {"day": "tue"})
        rt.tool_result(cid, "2 events")
        rt.model_call("bonsai2:27b", seat="local", tokens_in=100, tokens_out=40)
    receipt = rt.finish(tid, reply="done")
    return tid, receipt


def test_archive_is_encrypted_chained_and_verifies(rt):
    tid, receipt = _finished(rt)
    assert receipt and receipt["seq"] == 1
    raw = rt.ledger_path().read_text(encoding="utf-8")
    assert "Janet" not in raw and "calendar_list" not in raw
    from agent_friday.services import credential_store as cs
    body = base64.b64decode(_lines(rt)[0]["body"])
    assert cs.looks_protected(body) is not None
    _finished(rt, "second")
    assert rt.verify() == {"valid": True, "records": 2, "break_at": None, "reason": None, "pending": 0}
    tree = rt.get_tree(tid)["tree"]
    assert tree["events"][0]["text"].startswith("secret reasoning")
    assert tree["tokens"] == {"in": 100, "out": 40, "reasoning": 0}


def test_editing_a_line_breaks_verification(rt):
    _finished(rt, "a"); _finished(rt, "b")
    lines = _lines(rt)
    lines[0]["ts"] = lines[0]["ts"] - 5          # any edit
    rt.ledger_path().write_text("\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")
    v = rt.verify()
    assert not v["valid"] and v["break_at"] == 1


def test_deleting_a_line_breaks_the_chain(rt):
    for t in "abc":
        _finished(rt, t)
    lines = _lines(rt)
    del lines[1]
    rt.ledger_path().write_text("\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")
    v = rt.verify()
    assert not v["valid"] and "chain" in v["reason"]


def test_records_verify_after_a_restart_and_ignore_the_env_secret(rt, monkeypatch):
    _finished(rt)
    import agent_friday.services.file_grants as fg
    fg._SIGNING_KEY_CACHE.clear()
    rt._KEY_CACHE.clear(); rt._TIP.clear(); rt._DECRYPTED.clear()
    monkeypatch.setenv("FRIDAY_SECRET_KEY", "a-different-launcher-secret")  # pragma: allowlist secret
    assert rt.verify()["valid"]
    _finished(rt, "after restart")
    assert rt.verify() == {"valid": True, "records": 2, "break_at": None, "reason": None, "pending": 0}


def test_locked_keystore_holds_records_in_memory_never_plaintext(rt, monkeypatch):
    from agent_friday.services import credential_store as cs
    from agent_friday.services import keystore as ks
    real = cs.protect

    def locked(_data):
        raise ks.KeystoreLocked("locked")
    monkeypatch.setattr(cs, "protect", locked)
    tid, receipt = _finished(rt, "private")
    assert receipt is None
    assert not rt.ledger_path().exists()
    assert rt.status()["pending"] == 1
    monkeypatch.setattr(cs, "protect", real)
    _finished(rt, "next")
    assert rt.status()["pending"] == 0
    assert rt.verify()["records"] == 2


def test_retention_prunes_old_records_and_chain_still_verifies(rt, monkeypatch):
    for t in "abc":
        _finished(rt, t)
    lines = _lines(rt)
    # Age the first two records by rewriting through the real signer, so the
    # file is a valid chain of old+new records.
    rt.ledger_path().unlink(); rt._TIP.clear()
    now = rt._now()
    for i, line in enumerate(lines):
        rt._write_line_locked({"v": 1, "type": "trace", "trace_id": line["trace_id"],
                               "ts": now - (40 * 86400 if i < 2 else 0), "body": line["body"]})
    res = rt.apply_retention(30)
    assert res["pruned"] == 2 and res["kept"] == 1
    v = rt.verify()
    assert v["valid"] and v["records"] == 1
    _finished(rt, "after prune")
    assert rt.verify()["records"] == 2 and rt.verify()["valid"]
    assert _lines(rt)[0]["pruned"] == 2


def test_retention_keep_all_by_default(rt):
    _finished(rt)
    assert rt.apply_retention()["pruned"] == 0


def test_search_filters_and_export(rt):
    _finished(rt, "about flights")
    tid = rt.start("scheduled", "☀️ Afternoon Briefing", model="claude-sonnet-5", parent_id=None)
    rt.mark_source(rt.SOURCE_NOT_EXPOSED, trace_id=tid)
    rt.finish(tid)
    assert rt.search(q="flights")["total"] == 1
    assert rt.search(model="sonnet")["total"] == 1
    assert rt.search(kind="scheduled")["traces"][0]["source_label"] == "reasoning not exposed by provider"
    out = list(rt.export_records())
    assert out[0]["type"] == "verification" and out[0]["valid"]
    assert len(out) == 3


def test_activity_ledger_gets_a_metadata_only_row(rt, tmp_path, monkeypatch):
    from agent_friday.services import activity_ledger as al
    monkeypatch.setattr(al, "LEDGER_FILE", tmp_path / "activity.jsonl")
    tid, receipt = _finished(rt)
    rows = al.read(kind="reasoning_trace")
    assert rows and rows[0]["trace_id"] == tid and rows[0]["ledger_seq"] == receipt["seq"]
    assert "Janet" not in json.dumps(rows)
