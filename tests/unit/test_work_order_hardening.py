"""CS-1 + CS-2 hardening: the critic's blockers and majors, each with an attack that failed before.

Unlike the other work-order tests, this file runs against the REAL egress gate
(only the floor model's embedding tier and the gate's rate pacing are stubbed)
and the REAL ``local_context.offer`` with a real conversation grant. What it holds:

* P1 never carries a value a model read out of a document (a number, a boolean, a
  handle-shaped string), only counts code computed from handle sets, bucketed.
* ``search`` stays inside the handles the cloud was given; only a P3 order searches
  wider, and what it finds is private-tier.
* Status is not an oracle: below P0 a threshold cannot move it, an empty filter does
  not fail an order, and a failure says only that it did not complete.
* A P2 summary always gets a card, even under a conversation grant.
* The gate judges the destination the turn really has (an Ollama ``:cloud`` model).
* The receipt lists every handle returned; results expire and are never re-minted;
  saved orders hold no document text.
Fixtures are synthetic. No model, no network, no server.
"""
import json
import time

import pytest

from agent_friday.services import cloud_intent as ci
from agent_friday.services import egress_gate as eg
from agent_friday.services import handles as H
from agent_friday.services import local_context as lc
from agent_friday.services import sensitivity_classifier as sc
from agent_friday.services import work_order_executor as ex
from agent_friday.services import work_order_returns as rt
from agent_friday.services import work_order_store as store
from agent_friday.services import work_orders as wo

CONV = "conv-harden"
CLOUD = "anthropic"
TURN = "turn-harden"

CANARY = "ACME-INVOICE-48213"
DOCS = {
    "money/invoice.md": "Invoice total for acme is 48213 dollars. Overdue by 41 days. %s" % CANARY,
    "money/notes.md": "Acme invoice notes. The total needs a review. %s" % CANARY,
    "people/tea.md": "Notes about gardening and tea.",
}


@pytest.fixture(autouse=True)
def world(monkeypatch, tmp_path):
    """The real gate and the real local_context; the floor model and pacing stubbed."""
    from agent_friday.governance import action_gate
    monkeypatch.setattr(action_gate, "_grants_file", lambda: tmp_path / "grants.json")
    monkeypatch.setattr(sc, "_embedding_tier", lambda t: (0, 0.0))
    monkeypatch.setattr(eg, "_rate_limit", lambda: None)
    monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: False)
    lc.register()
    lc.SENT_LOG.clear()
    monkeypatch.setattr(wo, "owner_class", lambda: "")
    monkeypatch.setattr(ex, "MODEL_WAIT_TRIES", 0)
    monkeypatch.setattr(ci, "routing_mode", lambda: "cloud_only")
    H.clear()
    wo.clear()
    ci.clear()
    ci.forget_recent()
    store.reset_for_tests()
    wo.AUTOSTART[0] = False
    yield
    store.reset_for_tests()
    H.clear()
    wo.clear()
    ci.clear()
    wo.AUTOSTART[0] = True


class Tools:
    def __init__(self, docs=DOCS):
        self.docs, self.calls = docs, []

    def __call__(self, name, args, ctx):
        self.calls.append((name, dict(args)))
        if name == "read_wiki":
            return self.docs.get(args["path"], "Wiki file not found: " + args["path"])
        if name == "search_wiki":
            q = args["query"].lower()
            return json.dumps({"query": q, "hits": [{"root": "wiki", "path": p, "excerpt": t[:40]}
                                                    for p, t in self.docs.items() if q in t.lower()]})
        if name == "propose_wiki_update":
            return "Wiki update proposed (id=abc123) - awaiting your approval."
        return "ok"


def pages(tier="normal", docs=DOCS, conv=CONV):
    return [H.issue("wiki_page", p, conv, tier=tier, title="t%d" % i, size=len(t),
                    dated=time.time() - 86400 * (i + 1)) for i, (p, t) in enumerate(docs.items())]


def order(inputs, **kw):
    o = {"goal": "invoice total acme", "inputs": inputs,
         "constraints": {"tools_allowed": ["read", "count"]}}
    o.update(kw)
    return o


def go(o, *, tools=None, model=lambda t, g, d: "A summary.", provider=CLOUD, mdl="claude-x"):
    out = wo.submit(o, CONV, provider=provider, model=mdl)
    assert out["status"] == "queued", out
    oid = out["order_id"]
    ex.run(oid, tool_fn=tools or Tools(), model_fn=model)
    rt.after_run(wo.live(oid))
    return oid


def status(oid, **kw):
    return wo.status(oid, CONV, provider=kw.pop("provider", CLOUD), **kw)


# ── B1: P1 returns counts the plan produced, never a value read from a document ──

class TestP1CarriesOnlyBucketedCounts:
    def test_a_number_and_a_boolean_a_model_extracted_stay_here(self):
        o = order(pages(), constraints={"tools_allowed": ["read", "extract"]},
                  output_schema={"amount": "number", "overdue": "boolean", "age": "integer"})
        oid = go(o, model=lambda t, g, d: json.dumps({"amount": 48213.5, "overdue": True, "age": 41}))
        assert wo.get(oid)["result"]["amount"] == 48213.5          # the owner's local result is whole
        st = status(oid)
        assert st["status"] == "done" and st["result"] == {}
        blob = json.dumps(st["result"])
        assert "48213" not in blob and "41" not in blob and "true" not in blob.lower()

    def test_twenty_integer_fields_cannot_carry_characters(self):
        schema = {"c%d" % i: "integer" for i in range(1, 21)}
        codes = {k: 100 + i for i, k in enumerate(schema)}
        oid = go(order(pages(), constraints={"tools_allowed": ["read", "extract"]}, output_schema=schema),
                 model=lambda t, g, d: json.dumps(codes))
        assert status(oid)["result"] == {}

    def test_a_handle_shaped_string_the_model_writes_is_dropped(self):
        forged = "h_" + "Zz9" * 6 + "ab"                      # 20 characters, never issued
        assert H.looks_like_handle(forged)
        o = order(pages(), constraints={"tools_allowed": ["read", "extract"]},
                  output_schema={"note": "string", "ids": "list"})
        oid = go(o, model=lambda t, g, d: json.dumps({"note": forged, "ids": [forged]}))
        st = status(oid)
        assert st["result"] == {} and forged not in json.dumps(st)

    def test_a_count_is_a_bucket_not_a_number(self):
        docs = {"d/p%d.md" % i: "Acme invoice %d" % i for i in range(20)}
        oid = go(order(pages(docs=docs), goal="acme invoice", constraints={"tools_allowed": ["read", "filter", "count"]},
                       output_schema={"count": "integer"}), tools=Tools(docs))
        assert status(oid)["result"] == {"count": "20+"}
        assert wo.get(oid)["result"]["count"] == 20
        small = go(order(pages(), goal="acme invoice", constraints={"tools_allowed": ["read", "filter", "count"]},
                         output_schema={"count": "integer", "matching": "integer"}))
        assert status(small)["result"]["count"] == "1-4"

    def test_a_list_goes_back_as_its_size_not_its_handles(self):
        h = pages()
        oid = go(order(h, constraints={"tools_allowed": ["read", "list"]}, output_schema={"items": "list"}))
        st = status(oid)
        assert st["result"] == {"items": "1-4"}
        assert not any(x in json.dumps(st) for x in h)

    def test_the_submit_reply_does_not_show_the_class(self):
        out = wo.submit(order(pages(tier="private")), CONV, provider=CLOUD, model="claude-x")
        assert "privacy_class" not in out
        plain = wo.submit(order(pages(), goal="a different request entirely"), CONV, provider=CLOUD, model="claude-x")
        assert set(out) == set(plain)


# ── B1/B3: a conversation cannot probe without limit ────────────────────────

class TestRateLimits:
    def test_a_fifth_open_order_is_refused(self):
        h = pages()
        goals = ["alpha budget", "bravo budget", "charlie budget", "delta budget", "echo budget"]
        out = [wo.submit(order(h, goal=g), CONV, provider=CLOUD, model="claude-x") for g in goals]
        assert [o["status"] for o in out] == ["queued"] * wo.MAX_OPEN_PER_CONV + ["rejected"]
        assert "order_id" not in out[-1]
        other = wo.submit(order(pages(conv="conv-other"), goal="echo budget"), "conv-other", provider=CLOUD, model="claude-x")
        assert other["status"] == "queued"                  # another conversation is not held back

    def test_the_same_request_over_and_over_is_refused(self):
        h = pages()
        results = []
        for _ in range(wo.MAX_SAME_GOAL_WINDOW + 1):
            r = wo.submit(order(h, goal="how many  invoices mention Acme?"), CONV, provider=CLOUD, model="claude-x")
            results.append(r["status"])
            if r["status"] == "queued":
                wo.update(r["order_id"], status="done", ended=time.time())      # ended, so the open cap is not the reason
        assert results == ["queued"] * wo.MAX_SAME_GOAL_WINDOW + ["rejected"]

    def test_room_is_made_only_from_orders_that_ended(self, monkeypatch):
        monkeypatch.setattr(wo, "MAX_ORDERS", 3)
        monkeypatch.setattr(wo, "MAX_OPEN_PER_CONV", 99)
        h = pages()
        ids = [wo.submit(order(h, goal="%s request" % w), CONV, provider=CLOUD, model="claude-x")["order_id"]
               for w in ("alpha", "bravo", "charlie")]
        assert wo.submit(order(h, goal="delta request"), CONV, provider=CLOUD, model="claude-x")["status"] == "rejected"
        assert all(wo.get(i) is not None for i in ids)      # nothing open was evicted
        wo.update(ids[0], status="done", ended=time.time())
        assert wo.submit(order(h, goal="delta request"), CONV, provider=CLOUD, model="claude-x")["status"] == "queued"
        assert wo.get(ids[0]) is None and wo.get(ids[1]) is not None


# ── B2 + M1: search stays inside the handles the cloud was given ────────────

class TestSearchIsScoped:
    def test_an_order_with_no_inputs_cannot_reach_the_wiki(self):
        before = len(H._STORE)
        tools = Tools()
        oid = go(order([], goal="acme invoice", constraints={"tools_allowed": ["search", "list", "count"]}),
                 tools=tools)
        st = status(oid)
        assert st["result"] == {"count": "0"}
        assert len(H._STORE) == before + 1                  # only the order's own result handle
        assert "search_wiki" not in [c[0] for c in tools.calls]

    def test_a_search_narrows_what_it_was_given_and_mints_nothing(self):
        h = pages()[:2]
        before = len(H._STORE)
        oid = go(order(h, goal="needs review", constraints={"tools_allowed": ["search", "count"]}))
        assert wo.get(oid)["result"]["count"] == 1          # only notes.md says "review"
        assert len(H._STORE) == before + 1

    def test_a_p3_order_may_search_wide_and_what_it_finds_is_private(self, monkeypatch):
        monkeypatch.setattr(wo, "owner_class", lambda: "P3_local_only")
        oid = go(order(pages()[:1], goal="gardening", constraints={"tools_allowed": ["search", "count"]}))
        items = wo.get(oid)["cp"]["ctx"]["items"]
        found = [i for i in items if i["src"] == "search"]
        assert found and all(i["tier"] == "private" for i in found)
        assert all(H.lookup(i["handle"], CONV).tier == "private" for i in found)
        assert set(status(oid)) == {"order_id", "status", "result_handle"}

    def test_p0_holds_only_while_every_input_is_still_public(self):
        h = pages(tier="public")
        oid = go(order(h, constraints={"tools_allowed": ["read", "summarise"]}, output_schema={"summary": "string"}))
        assert status(oid)["privacy_class"] == "P0_public"
        H._STORE[h[0]].tier = "private"                     # the page was made private since
        assert set(status(oid)) == {"order_id", "status", "result_handle"}
        H._STORE[h[0]].tier = "public"
        H.revoke(h[1])                                      # or an input is gone: not provably public
        assert status(oid)["privacy_class"] == "P1_aggregate"


# ── B3: status is not an oracle ─────────────────────────────────────────────

class TestStatusIsNotAnOracle:
    def test_p3_done_does_not_depend_on_whether_anything_matched(self):
        h = pages(tier="private")
        tools_allowed = {"tools_allowed": ["read", "filter", "summarise"]}
        hit = go(order(h, goal="invoice acme", constraints=tools_allowed))
        miss = go(order(h, goal="walrus migration", constraints=tools_allowed))
        a, b = status(hit), status(miss)
        assert a["status"] == b["status"] == "done"
        assert set(a) == set(b) == {"order_id", "status", "result_handle"}

    def test_a_cloud_threshold_over_private_data_cannot_flip_the_status(self):
        h = pages(tier="private")
        low = go(order(h, acceptance=[{"check": "count_at_least", "value": 1}]))
        high = go(order(h, goal="invoice acme total", acceptance=[{"check": "count_at_least", "value": 99999}]))
        assert status(low)["status"] == status(high)["status"] == "done"
        # ...and the same for a P1 order, where the count itself is only a bucket.
        p1 = pages()
        a = go(order(p1, goal="acme invoice", acceptance=[{"check": "count_at_most", "value": 0}]))
        assert status(a)["status"] == "done" and "failed_checks" not in status(a)

    def test_an_emptied_filter_does_not_fail_a_summary_order(self):
        o = order(pages(), goal="walrus migration", constraints={"tools_allowed": ["read", "filter", "summarise"]},
                  output_schema={"count": "integer"})
        assert status(go(o))["status"] == "done"

    def test_a_failure_carries_no_step_and_no_reason(self):
        oid = go(order(pages()), tools=lambda n, a, c: "Wiki file not found: " + a.get("path", ""))
        st = status(oid)
        assert st["status"] == "did_not_complete"
        assert not ({"failed_step", "failed_checks", "error"} & set(st))
        assert "money" not in json.dumps(st)


# ── B4: a work order's summary always gets a card ───────────────────────────

class TestP2AlwaysGetsACard:
    def test_a_conversation_grant_does_not_send_a_cloud_steered_summary(self, monkeypatch):
        monkeypatch.setattr(wo, "owner_class", lambda: "P2_summary")
        lc.grant_for_conversation(CONV)                     # the owner allowed one share in this conversation
        oid = go(order(pages(), constraints={"tools_allowed": ["read", "summarise"]},
                       output_schema={"summary": "string"}),
                 model=lambda t, g, d: "The invoice for the account is overdue.")
        st = status(oid)
        assert st["summary_state"] == "awaiting_owner" and "summary" not in st
        assert wo.get(oid)["p2"]["state"] == "pending" and not lc.SENT_LOG

    def test_the_offer_is_made_with_force_card(self, monkeypatch):
        monkeypatch.setattr(wo, "owner_class", lambda: "P2_summary")
        seen = []
        monkeypatch.setattr(lc, "offer", lambda raw, **kw: (seen.append(kw), {"status": "pending", "approval_id": "a"})[1])
        go(order(pages(), constraints={"tools_allowed": ["read", "summarise"]}))
        assert seen and seen[0]["force_card"] is True


# ── M2: the gate judges the destination the turn really has ─────────────────

class TestGateUsesTheRealDestination:
    @pytest.fixture
    def judged(self, monkeypatch):
        calls = []
        real = eg._gate_text
        monkeypatch.setattr(eg, "_gate_text", lambda text, provider, field, log_path=None: (
            calls.append((provider, field)), real(text, provider, field, log_path))[1])
        return calls

    def test_an_ollama_cloud_model_is_gated_as_cloud(self, judged):
        oid = go(order(pages()), provider="ollama", mdl="glm-4.6:cloud")
        st = wo.status(oid, CONV, provider="ollama", model="glm-4.6:cloud")
        assert st["status"] == "done" and any(f == "work_order_return" for _, f in judged)

    def test_a_truly_local_model_is_not(self, judged, monkeypatch):
        monkeypatch.setattr(ci, "routing_mode", lambda: "cloud_only")
        assert not wo.cloud_turn_allowed("ollama", "qwen3:8b")[0]       # never admitted at all
        assert H.gate_for("a note", "ollama", "qwen3:8b", "work_order_return") == "a note"
        assert judged == []

    def test_the_status_tool_passes_the_turns_model_to_the_gate(self, judged, monkeypatch):
        from agent_friday.services import agent
        monkeypatch.setattr(agent, "_work_orders_enabled", lambda: True)
        oid = go(order(pages()), provider="ollama", mdl="glm-4.6:cloud")
        judged.clear()
        t = [agent._CURRENT_PROVIDER.set("ollama"), agent._CURRENT_CONVERSATION.set(CONV),
             agent._CURRENT_MODEL.set("glm-4.6:cloud")]
        try:
            out = json.loads(agent.CLAUDE_TOOL_HANDLERS["work_order_status"]({"order_id": oid}))
        finally:
            agent._CURRENT_MODEL.reset(t[2])
            agent._CURRENT_CONVERSATION.reset(t[1])
            agent._CURRENT_PROVIDER.reset(t[0])
        assert out["status"] == "done" and any(f == "work_order_return" for _, f in judged)

    def test_the_real_gate_lets_the_bucketed_payloads_through_unchanged(self):
        oid = go(order(pages(), output_schema={"count": "integer"}))
        assert status(oid)["result"] == {"count": "1-4"}                # not "withheld": the h_ and wo_ strings pass
        p3 = go(order(pages(tier="private")))
        assert "result_handle" in status(p3)


# ── M3: no settings write from a work order ─────────────────────────────────

class TestNoSettingsTarget:
    def test_a_settings_intent_is_refused_at_validation(self):
        res = wo.validate(order(pages(), writes=[{"target": "settings", "op": "update",
                                                  "summary": "settings.laya.live_reflex = on"}]), CONV)
        assert not res["ok"]


# ── M5: the receipt lists every handle returned inside a result ─────────────

class TestReceiptListsReturnedHandles:
    def test_a_p3_result_handle_is_on_the_turn_receipt(self):
        oid = go(order(pages(tier="private")))
        st = status(oid, turn_id="t-m5")
        assert st["result_handle"] in wo.receipt_section("t-m5")["handles_seen"]

    def test_p0_handles_in_a_result_are_on_the_receipt(self):
        h = pages(tier="public")
        oid = go(order(h, constraints={"tools_allowed": ["read", "list"]}, output_schema={"items": "list"}))
        status(oid, turn_id="t-m5b")
        assert set(h) <= set(wo.receipt_section("t-m5b")["handles_seen"])


# ── M7: no text at rest, a TTL, and results that stay expired ───────────────

class TestMinimisation:
    def test_a_finished_order_holds_no_document_text_and_neither_does_the_saved_copy(self):
        oid = go(order(pages()))
        assert all(it["text"] == "" for it in wo.get(oid)["cp"]["ctx"]["items"])
        assert CANARY not in json.dumps(wo.snapshot(), default=str)

    def test_a_result_handle_is_never_minted_again_once_it_expires(self):
        oid = go(order(pages(tier="private")))
        first = status(oid)["result_handle"]
        n = len(H._STORE)
        H._STORE[first].expires = time.time() - 1
        again = status(oid)
        assert again == {"order_id": oid, "status": "expired"}
        assert len(H._STORE) <= n                          # nothing new was issued
        assert H.lookup(first, CONV) is None

    def test_a_record_is_dropped_after_its_ttl(self):
        oid = go(order(pages()))
        wo.update(oid, ended=time.time() - wo.RECORD_TTL_S - 60)
        assert status(oid) == {"order_id": oid, "status": "unknown"}

    def test_one_conversation_cannot_fill_the_handle_registry(self, monkeypatch):
        monkeypatch.setattr(H, "MAX_HANDLES_PER_CONV", 3)
        for i in range(3):
            H.issue("wiki_page", "a%d.md" % i, CONV)
        with pytest.raises(H.HandleError):
            H.issue("wiki_page", "a9.md", CONV)
        assert H.issue("wiki_page", "b.md", "another-conversation")
