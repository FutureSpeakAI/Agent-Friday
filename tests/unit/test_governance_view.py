"""The Governance workspace as an A2UI document: Friday's own builtin view of the receipt store, drawn on this PC only.

Held here:
* the builtin document passes the Friday profile (V1-V14) as Friday's own document and as a document he could
  approve, and carries no data of its own (V7);
* its two bindings are broker queries in the closed vocabulary, reading the receipt store's section;
* the broker's governance rows are plain words (when, what, model, where) from the store, and a row holding a URL
  is dropped and counted;
* the view is never served to a device: no device block declares it, so the phone, tablet and TV get no view.
Synthetic data only.
"""
import pytest

from agent_friday.services import a2ui_broker as broker
from agent_friday.services import a2ui_profile as prof
from agent_friday.services import a2ui_render as rend
from agent_friday.services import a2ui_workspaces as aws
from agent_friday.services import governance_receipts as gr


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(gr, "DB_PATH", tmp_path / "receipts.db")
    monkeypatch.setattr(gr, "_is_local", lambda provider, model: provider == "llama_cpp")
    monkeypatch.setattr(gr, "sync_from_activity", lambda: 0)
    gr.reset()


def _put_samples():
    gr.put([
        gr.from_activity({"kind": "model_invocation", "ts": 1_800_000_000.0, "seq": 1, "model": "sample-local:7b",
                          "provider": "llama_cpp"}),
        gr.from_activity({"kind": "tool_call", "ts": 1_800_000_060.0, "seq": 2, "tool": "search_files", "ok": True}),
        gr.from_activity({"kind": "model_invocation", "ts": 1_800_000_120.0, "seq": 3, "model": "sample-cloud",
                          "provider": "sample_cloud"}),
    ])


def test_the_builtin_document_passes_the_profile_and_carries_no_data():
    doc = aws.builtin("governance")
    checked = prof.check(doc, aws.ctx("governance"))
    assert checked["data"] == {}
    aws.check_document("governance", doc)          # as a document he could approve: data only through bindings


def test_its_bindings_are_closed_vocabulary_queries_on_the_receipts_section():
    doc = aws.builtin("governance")
    assert {q for _p, q, _a in broker.check_bindings(doc)} == {"governance.recent", "governance.models"}
    assert broker.sections_read(doc) == frozenset({"receipts"})


def test_the_recent_query_reads_the_store_in_plain_words():
    _put_samples()
    got = broker.resolve(aws.builtin("governance"), sensitivity="owner")
    recent = got["data"]["governance"]["recent"]
    assert [r["what"] for r in recent] == ["sample-cloud answered in the cloud", "Used search_files",
                                           "sample-local:7b answered on this PC"]
    assert recent[0] == {"when": "2027-01-15T08:02:00Z", "what": "sample-cloud answered in the cloud",
                         "model": "sample-cloud", "where": "in the cloud"}
    assert recent[1]["where"] == "" and recent[1]["model"] == ""
    assert got["data"]["governance"]["models"] == [
        {"model": "sample-cloud", "where": "in the cloud", "turns": 1},
        {"model": "sample-local:7b", "where": "on this PC", "turns": 1}]


def test_the_recent_query_takes_only_a_bounded_limit():
    doc = aws.builtin("governance")
    doc[0]["createSurface"]["metadata"]["friday"]["bindings"]["recent"]["params"] = {"limit": 500}
    with pytest.raises(broker.BrokerRefused):
        broker.check_bindings(doc)
    doc[0]["createSurface"]["metadata"]["friday"]["bindings"]["recent"]["params"] = {"family": "model"}
    with pytest.raises(broker.BrokerRefused):
        broker.check_bindings(doc)


def test_a_receipt_row_holding_a_url_is_dropped_and_counted(monkeypatch):
    _put_samples()
    real = gr.recent

    def with_odd_row(**kw):
        rows = real(**kw)
        return rows + [gr.Receipt(receipt_id="pc:activity:9", origin="pc", at=1.0, family="tool",
                                  kind="tool_call", line="Used www.example.test/x")]
    monkeypatch.setattr(gr, "recent", with_odd_row)
    got = broker.resolve(aws.builtin("governance"), sensitivity="owner")
    assert got["dropped"] == 1
    assert len(got["data"]["governance"]["recent"]) == 3


NAMES = ["hf.co/sample-org/Sample-12B-QAT:Q4_K_M", "registry.example.test/library/sample:8b", "sample-org/sample-7b",
         "sample-local:7b", "sample-4.1", "sample.model-v2", "www.sample.test/x", "hf.co"]


def test_every_name_a_receipt_keeps_survives_the_view():
    """The broker drops any row the profile reads as an address (V8). A model id from a hub (hf.co/...) is a local
    model, not a link: whatever name a receipt keeps reaches the view, and every model with a name is in both tables."""
    rows = [gr.from_activity({"kind": "model_invocation", "ts": 1_800_000_000.0 + i, "seq": i, "model": n,
                              "provider": "llama_cpp", "seat": "local"}) for i, n in enumerate(NAMES)]
    gr.put(rows)
    got = broker.resolve(aws.builtin("governance"), sensitivity="owner")
    assert got["dropped"] == 0
    named = {r.model for r in rows if r.model}
    assert "Sample-12B-QAT:Q4_K_M" in {m.split("/")[-1] for m in named}
    assert {m["model"] for m in got["data"]["governance"]["models"]} == named
    assert len(got["data"]["governance"]["recent"]) == len(NAMES)
    assert all(r.where == gr.ON_THIS_PC for r in rows)


def test_the_rendered_tree_draws_the_receipts():
    _put_samples()
    got = broker.resolve(aws.builtin("governance"), sensitivity="owner")
    out = rend.render(aws.builtin("governance"), aws.ctx("governance"), data=got["data"])
    assert out.ok, out.receipt
    assert "sample-local:7b answered on this PC" in out.html


def test_the_governance_view_is_never_served_to_a_device():
    from agent_friday.tv_gateway import ws_content
    assert "governance" in aws.DESKTOP_ONLY
    assert ws_content.view_block("governance") is None
