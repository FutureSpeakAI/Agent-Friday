"""CS-2 guard: private content never reaches a cloud payload through a work order.

Canary strings are seeded in the knowledge-graph, wiki, Library and profile
fixtures. Every order runs through the full pipeline (submit, execute, return)
under each privacy class, and every payload a cloud model could see is
collected: the submit reply, each status reply, each handle's metadata, and
everything the egress gate was asked to check. No canary may appear in any of
them, except inside a P2 summary that passed the local_context card. Handles
never reveal a path, and a tool's failure text stays on this PC.
"""
import json
import time

import pytest

from agent_friday.services import cloud_intent as ci
from agent_friday.services import handles as H
from agent_friday.services import work_order_executor as ex
from agent_friday.services import work_order_returns as rt
from agent_friday.services import work_order_store as store
from agent_friday.services import work_orders as wo

CONV = "conv-guard"
CLOUD = "anthropic"
TURN = "turn-guard"

C_KG = "CANARY-KG-5c0d41"
C_WIKI = "CANARY-WIKI-8a2e77"
C_LIB = "CANARY-LIB-31b9f0"
C_PROFILE = "CANARY-PROFILE-6d44e2"
C_TITLE = "CANARY-TITLE-0f9a13"
CANARIES = (C_KG, C_WIKI, C_LIB, C_PROFILE, C_TITLE)

# Synthetic paths shaped like the owner's: they belong to nobody.
WIKI_PATH = "projects/orchid-launch/plan.md"
PROFILE_PATH = "people/profile-of-someone.md"
LIB_PATH = "C:/Users/example/Library/secret-roadmap.txt"
KG_NODE = "ent-orchid-launch"
PATH_PIECES = ("orchid-launch", "plan.md", "profile-of-someone", "secret-roadmap", "example", "C:/",
               "Library", "ent-orchid")

BODIES = {
    WIKI_PATH: "The orchid launch plan: ship in spring. %s. Launch owner is in the plan." % C_WIKI,
    PROFILE_PATH: "Profile: prefers tea and quiet launches. %s." % C_PROFILE,
    LIB_PATH: "Roadmap for the orchid launch. %s. Launch dates are in here." % C_LIB,
    KG_NODE: json.dumps({"node": {"id": KG_NODE, "title": "orchid launch"},
                         "related": [{"id": "x1", "title": "launch note %s" % C_KG, "type": "fact"}]}),
}


class Cloud:
    """Everything a cloud model could have seen in one test."""

    def __init__(self):
        self.payloads = []

    def see(self, obj):
        self.payloads.append(obj if isinstance(obj, str) else json.dumps(obj, sort_keys=True, default=str))
        return obj

    def blob(self, skip_summary=False):
        out = []
        for p in self.payloads:
            if skip_summary:
                try:
                    d = json.loads(p)
                    if isinstance(d, dict) and "summary" in d:
                        d = {k: v for k, v in d.items() if k != "summary"}
                        p = json.dumps(d, sort_keys=True)
                except ValueError:
                    pass
            out.append(p)
        return "\n".join(out)


@pytest.fixture
def cloud(monkeypatch):
    from agent_friday.services import egress_gate
    c = Cloud()

    def gate(t, p, f="prompt", log_path=None):
        c.see(t)
        # A title carrying the canary is exactly what the real gate's slice planner may refuse.
        return "" if C_TITLE in t else t
    monkeypatch.setattr(egress_gate, "gate_text", gate)
    monkeypatch.setattr(ex, "_library_path", lambda ref: LIB_PATH)
    monkeypatch.setattr(ex, "MODEL_WAIT_TRIES", 0)
    monkeypatch.setattr(wo, "owner_class", lambda: "")
    monkeypatch.setattr(ci, "routing_mode", lambda: "cloud_only")
    H.clear()
    wo.clear()
    ci.clear()
    ci.forget_recent()
    store.reset_for_tests()
    wo.AUTOSTART[0] = False
    yield c
    store.reset_for_tests()
    H.clear()
    wo.clear()
    ci.clear()
    wo.AUTOSTART[0] = True


def tool_fn(name, args, ctx):
    """What the governed tools would return: the fixtures, or a failure that names a path and a canary."""
    if name == "read_wiki":
        return BODIES.get(args["path"], "Wiki file not found: %s %s" % (args["path"], C_WIKI))
    if name == "read_file":
        return BODIES.get(args["path"], "File not found: %s %s" % (args["path"], C_LIB))
    if name == "knowledge_related":
        return BODIES[KG_NODE]
    return "ok"


def issue_all(tier="normal", conv=CONV):
    ts = time.time() - 86400
    return [
        H.issue("wiki_page", WIKI_PATH, conv, tier=tier, title="Launch plan", dated=ts, size=2000),
        H.issue("wiki_page", PROFILE_PATH, conv, tier=tier, title="About the owner", dated=ts, size=800),
        H.issue("library_doc", {"doc_id": 7}, conv, tier=tier, title="Roadmap", dated=ts, size=9000),
        H.issue("kg_entity", KG_NODE, conv, tier=tier, title="Orchid", dated=ts, size=300),
    ]


def order(handles, **kw):
    o = {"goal": "Summarise what the launch material says and count the sources.",
         "inputs": handles,
         "constraints": {"tools_allowed": ["read", "count", "list", "summarise", "date_math"]},
         "output_schema": {"count": "integer", "items": "list", "summary": "string", "newest_days": "integer"}}
    o.update(kw)
    return o


def drive(cloud, handles, *, model_text=None, o=None, tools=tool_fn, after=True):
    """Submit, run, return: the whole pipeline, with every cloud-visible payload collected."""
    for h in handles:
        cloud.see(H.metadata(h, CONV, provider=CLOUD, turn_id=TURN))
    out = wo.submit(o or order(handles), CONV, provider=CLOUD, model="claude-x")
    cloud.see(out)
    oid = out["order_id"]
    cloud.see(wo.status(oid, CONV, provider=CLOUD, turn_id=TURN))                 # queued
    text = model_text if model_text is not None else "Summary: the launch ships in spring."
    ex.run(oid, tool_fn=tools, model_fn=lambda t, g, d: text)
    if after:
        rt.after_run(wo.live(oid))
    st = cloud.see(wo.status(oid, CONV, provider=CLOUD, turn_id=TURN))
    cloud.see(wo.status(oid, CONV, provider=CLOUD, turn_id=TURN))                 # polled again
    return oid, st


def assert_no_canary(text, allowed=()):
    for c in CANARIES:
        if c in allowed:
            continue
        assert c not in text, c


def assert_no_path(text):
    for piece in PATH_PIECES:
        assert piece not in text, piece


# ── every class, every store ─────────────────────────────────────────────────

class TestNoCanaryReachesTheCloud:
    def test_p1_default_even_when_the_model_summary_is_full_of_canaries(self, cloud):
        h = issue_all()
        everything = " ".join(CANARIES)
        oid, st = drive(cloud, h, model_text="It says %s" % everything)
        assert st["privacy_class"] == "P1_aggregate" and st["status"] == "done"
        assert st["result"]["count"] == 4 and "summary" not in st
        assert_no_canary(cloud.blob())
        assert_no_path(cloud.blob())

    def test_p3_for_private_material_returns_only_a_handle(self, cloud):
        oid, st = drive(cloud, issue_all(tier="private"), model_text="x " + C_WIKI)
        assert set(st) == {"order_id", "status", "result_handle"} and st["status"] == "done"
        assert_no_canary(cloud.blob())
        assert_no_path(cloud.blob())

    def test_vault_material_is_never_read_and_a_failed_p3_order_says_only_that_it_failed(self, cloud):
        calls = []
        oid, st = drive(cloud, issue_all(tier="vault"), tools=lambda n, a, c: calls.append(n))
        assert calls == [] and st == {"order_id": oid, "status": "failed"}
        assert_no_canary(cloud.blob())
        assert_no_path(cloud.blob())

    def test_p0_public_returns_the_result_but_never_the_private_stores(self, cloud):
        pub = {"web/news.md": "A public note about orchids."}
        h = [H.issue("wiki_page", "web/news.md", CONV, tier="public", title="News")]
        oid, st = drive(cloud, h, o=order(h, constraints={"tools_allowed": ["read", "summarise"]}),
                        tools=lambda n, a, c: pub[a["path"]], model_text="A public summary of orchids.")
        assert st["privacy_class"] == "P0_public" and st["result"]["summary"] == "A public summary of orchids."
        assert_no_canary(cloud.blob())

    def test_p2_a_canary_appears_only_inside_a_summary_that_passed_the_card(self, cloud, monkeypatch):
        from agent_friday.services import local_context
        monkeypatch.setattr(wo, "owner_class", lambda: "P2_summary")
        cards = []

        def offer(raw, **kw):
            text = local_context.scrub(raw)[0]                # the regex scrub; the floor loads a model, so it is stubbed
            cards.append(text)
            return {"status": "sent", "text": text}
        monkeypatch.setattr(local_context, "offer", offer)
        h = issue_all()
        oid, st = drive(cloud, h, model_text="The launch ships in spring. Note: %s %s" % (C_WIKI, C_PROFILE))
        assert st["privacy_class"] == "P2_summary" and st["summary_state"] == "sent"
        assert C_WIKI in st["summary"] and cards and st["summary"] == cards[0]
        assert_no_canary(cloud.blob(skip_summary=True))
        assert_no_path(cloud.blob())
        # The canaries that were never in the summary are not anywhere else either.
        assert C_KG not in cloud.blob() and C_LIB not in cloud.blob()

    def test_p2_before_the_owner_decides_nothing_leaves(self, cloud, monkeypatch):
        from agent_friday.services import local_context
        monkeypatch.setattr(wo, "owner_class", lambda: "P2_summary")
        monkeypatch.setattr(local_context, "offer", lambda raw, **kw: {"status": "pending", "approval_id": "ap"})
        oid, st = drive(cloud, issue_all(), model_text="Secret: %s" % C_WIKI)
        assert st["summary_state"] == "awaiting_owner" and "summary" not in st
        assert_no_canary(cloud.blob())

    def test_p2_where_the_mode_does_not_allow_it_is_aggregates_only(self, cloud, monkeypatch):
        from agent_friday.services import local_context
        monkeypatch.setattr(wo, "owner_class", lambda: "P2_summary")
        monkeypatch.setattr(local_context, "offer", lambda raw, **kw: {"status": "pending", "approval_id": "ap"})
        oid, st = drive(cloud, issue_all(), model_text="Secret: %s" % C_WIKI)
        monkeypatch.setattr(ci, "routing_mode", lambda: "cloud_preferred")
        cloud.see(wo.status(oid, CONV, provider=CLOUD, turn_id=TURN))
        assert_no_canary(cloud.blob(skip_summary=True), allowed=())
        later = wo.status(oid, CONV, provider=CLOUD)
        assert later["privacy_class"] == "P1_aggregate" and "summary" not in later

    def test_a_free_text_field_the_cloud_named_is_dropped_at_p1(self, cloud):
        h = issue_all()
        o = order(h, output_schema={"count": "integer", "title": "string", "quote": "string", "note": "list"},
                  constraints={"tools_allowed": ["read", "extract"]})
        oid, st = drive(cloud, h, o=o, model_text=json.dumps(
            {"title": C_WIKI, "quote": C_LIB, "note": [C_KG, C_PROFILE], "count": 4}))
        assert st["result"] == {"count": 4}
        assert_no_canary(cloud.blob())

    def test_a_failure_does_not_carry_the_tool_text_or_path(self, cloud):
        h = issue_all()
        oid, st = drive(cloud, h, tools=lambda n, a, c: "Wiki file not found: %s %s %s" % (a.get("path"), C_WIKI, LIB_PATH))
        assert st["status"] == "failed"
        assert_no_canary(cloud.blob())
        assert_no_path(cloud.blob())
        assert C_WIKI in wo.get(oid)["error"] or "read failed" in wo.get(oid)["error"]     # kept locally

    def test_a_failed_acceptance_names_the_check_only(self, cloud):
        h = issue_all()
        oid, st = drive(cloud, h, o=order(h, acceptance=[{"check": "count_at_least", "value": 99}]))
        assert st["status"] == "failed" and st["failed_checks"] == ["count_at_least"]
        assert_no_canary(cloud.blob())

    def test_the_result_handle_resolves_only_here_and_only_in_this_conversation(self, cloud):
        h = issue_all(tier="private")
        oid, st = drive(cloud, h)
        rh = st["result_handle"]
        assert H.lookup(rh, CONV) is not None and H.lookup(rh, "another-conversation") is None
        assert_no_path(json.dumps(H.metadata(rh, CONV, provider=CLOUD)))

    def test_orders_chained_by_a_result_handle_stay_local(self, cloud):
        h = issue_all(tier="private")
        oid, st = drive(cloud, h)
        second = order([st["result_handle"]], constraints={"tools_allowed": ["read", "count"]},
                       output_schema={"count": "integer"})
        oid2, st2 = drive(cloud, [], o=second)
        assert st2["status"] == "done" and set(st2) == {"order_id", "status", "result_handle"}
        assert_no_canary(cloud.blob())


# ── handles never reveal paths ───────────────────────────────────────────────

class TestHandlesRevealNoPaths:
    def test_metadata_snapshots_and_the_reprs_name_no_path(self, cloud):
        h = issue_all()
        for x in h:
            blob = json.dumps(H.metadata(x, CONV, provider=CLOUD)) + x + repr(H.lookup(x, CONV))
            assert_no_path(blob)
            assert set(H.metadata(x, CONV, provider=CLOUD)) <= {"handle", "kind", "date", "size", "title"}

    def test_a_title_the_gate_refuses_is_not_shown(self, cloud):
        h = H.issue("wiki_page", WIKI_PATH, CONV, title="Plan %s" % C_TITLE)
        meta = H.metadata(h, CONV, provider=CLOUD)
        assert "title" not in meta
        assert_no_canary(json.dumps(meta))

    def test_the_encrypted_store_holds_no_path_or_canary_in_the_clear(self, cloud):
        h = issue_all()
        drive(cloud, h)
        assert store.save_now()
        raw = store.path().read_bytes()
        for needle in [c.encode() for c in CANARIES] + [p.encode() for p in PATH_PIECES]:
            assert needle not in raw, needle
