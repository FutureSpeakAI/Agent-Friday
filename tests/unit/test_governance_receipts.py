"""The governance receipt store: one typed Receipt per activity row, stored once, read back in plain words.

Held here:
* an activity row is parsed into a Receipt at the boundary, or refused (None); never a raw dict past that point;
* where a model ran is said only when it was recorded: on this PC, in the cloud, or "not recorded", never a guess;
* the plain line is built from fixed words plus model and tool names only: no free text from any field reaches it;
* the store keeps each receipt once (insert-or-ignore on its origin-scoped id), so a re-sync or a retry stores nothing;
* reads are newest first, bounded, and filter by family and by words in the line.
Synthetic data only.
"""
import pytest

from agent_friday.services import activity_ledger
from agent_friday.services import governance_receipts as gr

SECRET = "a private sentence about the garden budget"


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(gr, "DB_PATH", tmp_path / "governance" / "receipts.db")
    monkeypatch.setattr(gr, "_is_local", lambda provider, model: provider in ("llama_cpp", "ollama"))
    gr.reset()


def _model(seq, *, model="sample-local:7b", provider="llama_cpp", ts=1_800_000_000.0):
    return {"kind": "model_invocation", "ts": ts + seq, "seq": seq, "model": model, "provider": provider,
            "seat": "brain", "tokens_in": 10, "tokens_out": 5, "duration_ms": 900, "turn_id": "t%d" % seq}


def test_a_model_row_that_ran_here_says_so_by_name():
    r = gr.from_activity(_model(7))
    assert isinstance(r, gr.Receipt)
    assert (r.receipt_id, r.origin, r.family, r.where, r.model) == (
        "pc:activity:7", "pc", "model", gr.ON_THIS_PC, "sample-local:7b")
    assert r.line == "sample-local:7b answered on this PC"
    assert r.turn_id == "t7"


def test_a_cloud_model_row_says_the_cloud():
    r = gr.from_activity(_model(8, model="sample-cloud-model", provider="sample_cloud"))
    assert r.where == gr.CLOUD
    assert r.line == "sample-cloud-model answered in the cloud"


def test_where_a_model_ran_is_never_guessed_when_the_provider_was_not_recorded():
    r = gr.from_activity(_model(9, provider=""))
    assert r.where is None
    assert r.line == "sample-local:7b answered (where it ran was not recorded)"


def test_a_tool_row_carries_its_outcome():
    ok = gr.from_activity({"kind": "tool_call", "ts": 1.0, "seq": 1, "tool": "search_files", "ok": True})
    bad = gr.from_activity({"kind": "tool_call", "ts": 2.0, "seq": 2, "tool": "send_email", "ok": False,
                            "reason": "timeout"})
    assert (ok.family, ok.outcome, ok.line) == ("tool", "done", "Used search_files")
    assert (bad.outcome, bad.line) == ("failed", "Tried send_email; it failed")


def test_a_trace_row_points_at_its_trace():
    r = gr.from_activity({"kind": "reasoning_trace", "ts": 3.0, "seq": 3, "trace_id": "tr_abc123", "model": "m1",
                          "provider": "x"})
    assert (r.family, r.trace_id) == ("trace", "tr_abc123")
    assert r.line == "m1 kept a reasoning trace"


@pytest.mark.parametrize("row", [
    None, "text", {}, {"kind": "model_invocation", "ts": 1.0},             # no seq
    {"kind": "model_invocation", "seq": "x", "ts": 1.0},                     # seq not an int
    {"kind": "model_invocation", "seq": True, "ts": 1.0},
    {"kind": "model_invocation", "seq": 4, "ts": "yesterday"},              # no usable time
    {"kind": "not_a_kind", "seq": 4, "ts": 1.0},
])
def test_a_row_that_is_not_a_receipt_is_refused(row):
    assert gr.from_activity(row) is None


def test_no_free_text_from_any_activity_field_ever_reaches_the_line():
    """Every activity kind, every one of its fields set to the same private sentence: the line never holds it, and
    the only free-standing names it may hold are the model and the tool (which are names, not content)."""
    for kind, fields in activity_ledger._ALLOWED_FIELDS.items():
        for extra in ({}, {"ok": True}, {"ok": False}, {"provider": "llama_cpp"}, {"provider": "sample_cloud"}):
            row = {f: SECRET for f in fields}
            row.update({"kind": kind, "ts": 1.0, "seq": 1, **extra})
            row.pop("model", None), row.pop("tool", None)
            r = gr.from_activity(row)
            assert r is not None, kind
            assert SECRET not in r.line and "garden" not in r.line, (kind, extra)
            assert r.family in gr.FAMILIES, kind


def test_the_store_keeps_each_receipt_once():
    receipts = [gr.from_activity(_model(i)) for i in (1, 2, 3)]
    assert gr.put(receipts) == 3
    assert gr.put(receipts) == 0
    assert [r.receipt_id for r in gr.recent()] == ["pc:activity:3", "pc:activity:2", "pc:activity:1"]


def test_a_receipt_reads_back_the_same_as_it_went_in():
    r = gr.from_activity(_model(5))
    gr.put([r])
    assert gr.recent() == [r]


def _record_models(n, model):
    for _ in range(n):
        assert activity_ledger.record("model_invocation", model=model, provider="llama_cpp", seat="local")


def test_sync_reads_the_activity_ledger_and_a_second_sync_stores_nothing():
    _record_models(2, "sample-local:7b")
    assert activity_ledger.record("tool_call", tool="search_files", ok=True)
    assert gr.sync_from_activity() == 3
    assert gr.sync_from_activity() == 0
    assert [r.line for r in gr.recent()][0] == "Used search_files"


def test_a_busy_ledger_is_indexed_whole_across_syncs(monkeypatch):
    """More rows than one page arrived while nobody looked: every one is indexed, none skipped, none twice."""
    monkeypatch.setattr(gr, "SYNC_PAGE", 4)
    _record_models(6, "sample-a")
    _record_models(5, "sample-b")
    assert gr.sync_from_activity() == 4                 # the newest page first
    _record_models(2, "sample-a")                       # and more arrive between syncs
    while gr.sync_from_activity():
        pass
    assert {m["model"]: m["turns"] for m in gr.models()} == {"sample-a": 8, "sample-b": 5}
    assert gr.sync_from_activity() == 0


def test_rebuild_refills_the_index_from_the_ledger(monkeypatch):
    monkeypatch.setattr(gr, "SYNC_PAGE", 3)
    _record_models(7, "sample-a")
    gr.put([gr.from_activity(_model(10_000))])         # a row the ledger does not hold
    assert gr.rebuild() == 7
    assert {m["model"]: m["turns"] for m in gr.models()} == {"sample-a": 7}


def _failing_after(monkeypatch, n):
    from agent_friday.services import action_ledger
    real = action_ledger._reverse_lines

    def flaky(path, *a, **kw):
        for i, line in enumerate(real(path, *a, **kw)):
            if i == n:
                raise PermissionError("a file is locked part way through")
            yield line
    monkeypatch.setattr(action_ledger, "_reverse_lines", flaky)
    return lambda: monkeypatch.setattr(action_ledger, "_reverse_lines", real)


@pytest.mark.parametrize("indexed_first", [True, False])
def test_a_read_that_fails_part_way_moves_nothing(monkeypatch, indexed_first):
    """A read cut short (a locked file, an unreadable segment) is never taken for the whole ledger: the sync stores
    nothing and the cursor stays, so the next good sync indexes every row."""
    monkeypatch.setattr(gr, "SYNC_PAGE", 4)
    if indexed_first:
        _record_models(3, "sample-old")
        while gr.sync_from_activity():
            pass
    _record_models(6, "sample-new")
    restore = _failing_after(monkeypatch, 2)
    assert gr.sync_from_activity() == 0
    restore()
    while gr.sync_from_activity():
        pass
    want = {"sample-new": 6, **({"sample-old": 3} if indexed_first else {})}
    assert {m["model"]: m["turns"] for m in gr.models()} == want


def test_a_replaced_ledger_is_indexed_again():
    """The cursor is ahead of the ledger's newest row (a ledger restored or replaced): what was indexed from this PC's
    old ledger goes, and the ledger as it is now is indexed, so a reused seq never keeps a stale receipt."""
    _record_models(2, "sample-a")
    gr.sync_from_activity()
    with gr._db() as con:
        con.execute("UPDATE cursor SET value = 1000000 WHERE name = 'top'")
    gr.put([gr.from_activity(_model(999_999, model="sample-stale"))])
    gr.sync_from_activity()
    assert {m["model"]: m["turns"] for m in gr.models()} == {"sample-a": 2}


def test_a_local_seat_is_on_this_pc_whatever_dialect_it_spoke():
    r = gr.from_activity({**_model(11, provider="openai"), "seat": "local"})
    assert (r.where, r.line) == (gr.ON_THIS_PC, "sample-local:7b answered on this PC")


@pytest.mark.parametrize("name", ["C:/Users/someone/models/x.gguf", "C:\\models\\x.gguf", "/home/someone/x.gguf",
                                  "~/models/x", "someone@example.test"])
def test_a_path_or_address_is_never_taken_for_a_name(name):
    for row in (_model(12, model=name), {"kind": "tool_call", "ts": 1.0, "seq": 12, "tool": name, "ok": True}):
        r = gr.from_activity(row)
        assert name not in r.line and r.model is None
    assert gr.from_activity(_model(13, model="org/model:Q4")).model == "org/model:Q4"
    assert gr.from_activity(_model(14, model="hf.co/org/model:Q4")).model == "org/model:Q4"   # the hub host is not shown


def test_reads_filter_by_family_and_words_and_are_bounded():
    gr.put([gr.from_activity(_model(1)),
            gr.from_activity({"kind": "tool_call", "ts": 1_800_000_005.0, "seq": 2, "tool": "search_files",
                              "ok": True}),
            gr.from_activity(_model(3, model="sample-cloud-model", provider="sample_cloud"))])
    assert [r.family for r in gr.recent(family="tool")] == ["tool"]
    assert [r.model for r in gr.recent(words="cloud")] == ["sample-cloud-model"]
    assert len(gr.recent(limit=1)) == 1
    assert len(gr.recent(limit=10_000)) == 3            # a huge limit is clamped, not an error
    with pytest.raises(ValueError):
        gr.recent(family="not_a_family")


def test_words_are_matched_literally_not_as_a_pattern():
    gr.put([gr.from_activity(_model(1))])
    assert gr.recent(words="%") == []
    assert gr.recent(words="_") == []


def test_models_counts_each_model_by_where_it_ran():
    gr.put([gr.from_activity(_model(1)), gr.from_activity(_model(2)),
            gr.from_activity(_model(3, model="sample-cloud-model", provider="sample_cloud")),
            gr.from_activity({"kind": "tool_call", "ts": 1.0, "seq": 4, "tool": "x", "ok": True})])
    assert gr.models() == [
        {"model": "sample-local:7b", "where": "on this PC", "turns": 2},
        {"model": "sample-cloud-model", "where": "in the cloud", "turns": 1},
    ]
