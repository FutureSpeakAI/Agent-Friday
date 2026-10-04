"""Forgetting a document reaches the reasoning traces: the model's own reasoning can quote a
passage it read, and the archive is encrypted and hash-chained, so the rewrite must keep the
chain verifying, say that it happened, and refuse to touch a chain that already fails."""
import json

import pytest

DOC = ("The tenant shall pay rent monthly to Margaret Ellison at the address given in schedule one and "
       "either party may end the lease with ninety days notice in writing.")


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


def _trace(rt, reasoning):
    tid = rt.start("chat", "what does my lease say", model="bonsai2:27b", seat="local", parent_id=None)
    with rt.activate(tid):
        rt.reasoning(reasoning)
        rt.model_call("bonsai2:27b", seat="local", tokens_in=10, tokens_out=4)
    rt.finish(tid, reply="done")
    return tid


def _lines(rt):
    return [json.loads(x) for x in rt.ledger_path().read_text(encoding="utf-8").splitlines() if x.strip()]


def _fp():
    from agent_friday.services.library import sweep
    return sweep.fingerprint(DOC), sweep


def test_a_quoted_passage_in_an_archived_trace_is_removed_and_the_chain_still_verifies(rt):
    fp, sweep = _fp()
    quoting = _trace(rt, "The lease says " + DOC + " so ninety days it is.")
    clean = _trace(rt, "Nothing from any document here, just the weather and a plan for the week ahead.")
    assert rt.verify()["records"] == 2
    out = rt.redact(lambda t: sweep.scrub(t, fp))
    assert out["archived"] == 1 and out["incomplete"] is None
    v = rt.verify()
    assert v["valid"] and v["records"] == 2, v                      # the redaction line is not a trace
    last = _lines(rt)[-1]
    assert last["type"] == "redaction" and last["records"] == 1 and "Margaret" not in json.dumps(last)
    tree = rt.get_tree(quoting)["tree"]
    assert "Margaret Ellison" not in json.dumps(tree) and sweep.FORGOTTEN in json.dumps(tree)
    assert "weather" in json.dumps(rt.get_tree(clean)["tree"])
    # a later trace still chains onto the redaction line, and retention still understands the file
    _trace(rt, "after the redaction, a note about gardens and soil and compost for the spring")
    assert rt.verify() == {"valid": True, "records": 3, "break_at": None, "reason": None, "pending": 0}


def test_live_pending_and_feed_copies_are_scrubbed_in_place(rt):
    fp, sweep = _fp()
    tid = rt.start("chat", "q", model="bonsai2:27b", seat="local", parent_id=None)
    with rt.activate(tid):
        rt.reasoning("Reading: " + DOC)
        rt.note("and again " + DOC)
    out = rt.redact(lambda t: sweep.scrub(t, fp))
    assert out["live"] >= 2
    assert "Margaret" not in json.dumps(rt.live_trace(tid))
    assert "Margaret" not in json.dumps(rt.feed(0))


def test_a_chain_that_already_fails_is_left_alone_and_reported(rt):
    fp, sweep = _fp()
    _trace(rt, "Quoting " + DOC)
    _trace(rt, "a second record with enough words to be a record of its own, nothing more")
    lines = _lines(rt)
    lines[0]["ts"] = lines[0]["ts"] - 5
    before = "\n".join(json.dumps(l) for l in lines) + "\n"
    rt.ledger_path().write_text(before, encoding="utf-8")
    out = rt.redact(lambda t: sweep.scrub(t, fp))
    assert out["archived"] == 0 and "does not verify" in out["incomplete"]
    assert rt.ledger_path().read_text(encoding="utf-8") == before


def test_a_keystore_that_cannot_re_encrypt_leaves_the_archive_as_it_was_and_says_so(rt, monkeypatch):
    fp, sweep = _fp()
    _trace(rt, "Quoting " + DOC)
    before = rt.ledger_path().read_text(encoding="utf-8")
    monkeypatch.setattr(rt, "_encrypt", lambda rec: (_ for _ in ()).throw(RuntimeError("locked")))
    out = rt.redact(lambda t: sweep.scrub(t, fp))
    assert out["archived"] == 0 and "re-encrypted" in out["incomplete"]
    assert rt.ledger_path().read_text(encoding="utf-8") == before
    assert rt.verify()["valid"]


def test_forget_reports_a_store_it_could_not_reach(monkeypatch):
    from agent_friday.services.library import sweep
    monkeypatch.setattr(sweep, "_conversations", lambda fp: 0)
    monkeypatch.setattr(sweep, "_memory_index", lambda fp: (0, "the conversation memory could not be opened"))
    monkeypatch.setattr(sweep, "_legacy_history", lambda fp: (0, None))
    monkeypatch.setattr(sweep, "_context_logs", lambda fp: (0, None))
    monkeypatch.setattr(sweep, "_traces", lambda fp: (0, "the reasoning traces: the archive could not be re-encrypted"))
    out = sweep.sweep_all(set(), None)
    assert out["incomplete"] == ["the conversation memory could not be opened",
                                 "the reasoning traces: the archive could not be re-encrypted"]
