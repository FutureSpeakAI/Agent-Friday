"""Research one email thread, by text and by voice (field report: twenty minutes, no answer).

The owner asked Friday to research ONE email conversation and it could not, by voice or in chat.
Reproduced here on a synthetic Gmail (fake Google service objects, no network):

* search_email, the model's only mail-read tool, returned a 160-character snippet per hit and no
  tool returned a message body, so "read every message" was impossible: the sentences past the
  snippet never reached the model (test_a_body_sentence_past_the_snippet_reaches_the_model);
* the router filed "research this email thread" as "a statement, not a request", and the local voice
  front answers a statement with no tool at all;
* the voice speaker would have listed a thread as "I found 4 emails" instead of answering from it.

Now search_email takes `thread` (a hit's ref, a thread id, "this" or "top") and `offset`.
"""
from __future__ import annotations

import base64
import json
import re

import pytest

from agent_friday.services import agent as agent_mod
from agent_friday.services import google_accounts as ga

ACCT = "acct_a"
TID = "t-venue"
REF = "mail:%s:%s" % (ACCT, TID)

# Sentences that sit past each message's snippet: only a real read can return them.
S1 = "The Harbour Hall quoted 2,400 for the day and can seat forty."
S2 = "My vote is Cedar House because the garden works for the afternoon session."
S3 = "Cedar House confirmed they can do 12 June and the deposit is due by 1 May."
S4 = "I will book the coach for 12 June and send the invoice to finance."
OPENER = "Thanks for the update on the plans for the spring offsite, picking this up now. " * 3


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _msg(mid, sender, date, ms, body, attachments=()):
    parts = [{"mimeType": "text/plain", "body": {"data": _b64(body), "size": len(body)}, "headers": []}]
    for a in attachments:
        parts.append({"mimeType": a["mime"], "filename": a["filename"], "headers": [],
                      "body": {"attachmentId": a["id"], "size": a["size"]}})
    return {"id": mid, "threadId": TID, "internalDate": str(ms), "labelIds": ["INBOX"],
            "snippet": body[:60],
            "payload": {"mimeType": "multipart/mixed", "parts": parts, "headers": [
                {"name": "From", "value": sender}, {"name": "To", "value": "owner@example.test"},
                {"name": "Subject", "value": "Venue options for the spring offsite"},
                {"name": "Date", "value": date}]}}


THREAD = [
    _msg("m1", "Robin Vale <robin@example.test>", "Tue, 6 Oct 2026 09:12:00 +0000", 1791277920000,
         OPENER + "\n\nI shortlisted three venues: Harbour Hall, the Mill Loft and Cedar House.\n" + S1 + "\n\nRobin",
         [{"id": "att-notes", "filename": "venue_notes.txt", "mime": "text/plain", "size": 120}]),
    _msg("m2", "Sam Okafor <sam@example.test>", "Tue, 6 Oct 2026 11:40:00 +0000", 1791286800000,
         OPENER + "\n\nThe Mill Loft has no lift, so it is out for us. " + S2 + "\n\n"
         "On Tue, 6 Oct 2026 at 09:12, Robin Vale <robin@example.test> wrote:\n"
         "> I shortlisted three venues: Harbour Hall, the Mill Loft and Cedar House.\n> " + S1 + "\n"),
    _msg("m3", "Robin Vale <robin@example.test>", "Wed, 7 Oct 2026 08:05:00 +0000", 1791360300000,
         OPENER + "\n\n" + S3 + "\n\n-----Original Message-----\nFrom: Sam Okafor\nSent: Tuesday\n\n" + S2 + "\n"),
    _msg("m4", "Lee Park <lee@example.test>", "Wed, 7 Oct 2026 10:30:00 +0000", 1791369000000,
         OPENER + "\n\nAgreed on Cedar House. " + S4 + "\n\n"
         "On Wed, 7 Oct 2026 at 08:05, Robin Vale <robin@example.test> wrote:\n> " + S3 + "\n"
         ">> On Tue, 6 Oct 2026 at 11:40, Sam Okafor wrote:\n>> " + S2 + "\n",
         [{"id": "att-quote", "filename": "coach_quote.pdf", "mime": "application/pdf", "size": 2_500_000}]),
]
ATTACHMENTS = {"att-notes": b"Harbour Hall: parking for 20 cars. Mill Loft: no lift. Cedar House: garden."}


class _Req:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class _NotFound(Exception):
    """Shaped like googleapiclient's HttpError 404."""

    def __init__(self):
        super().__init__("Requested entity was not found.")
        self.resp = type("R", (), {"status": 404})()
        self.content = b'{"error": {"errors": [{"reason": "notFound"}]}}'


class FakeGmail:
    """The slice of the Gmail v1 service the readers call: list, batched metadata, threads.get,
    attachments.get. `threads` maps a thread id to its messages."""

    def __init__(self, threads):
        self.threads_by_id = threads
        self.calls = []

    def _all(self):
        return [m for msgs in self.threads_by_id.values() for m in msgs]

    def users(self):
        return self

    def messages(self):
        return _Messages(self)

    def threads(self):
        return _Threads(self)

    def new_batch_http_request(self, callback):
        return _Batch(callback)


class _Messages:
    def __init__(self, g):
        self.g = g

    def list(self, userId, q="", maxResults=25):
        self.g.calls.append(("list", q))

        def run():
            hits = []
            for m in self.g._all():
                hdr = {h["name"].lower(): h["value"].lower() for h in m["payload"]["headers"]}
                ok = True
                for tok in q.lower().split():
                    if tok.startswith("from:"):
                        ok &= tok[5:] in hdr.get("from", "")
                    elif tok.startswith("subject:"):
                        ok &= tok[8:] in hdr.get("subject", "")
                    elif tok.startswith(("after:", "before:", "newer_than:")):
                        continue
                    else:
                        ok &= tok in (hdr.get("from", "") + " " + hdr.get("subject", ""))
                if ok:
                    hits.append({"id": m["id"], "threadId": m["threadId"]})
            return {"messages": hits[::-1][:maxResults]}
        return _Req(run)

    def get(self, userId, id, format="full", metadataHeaders=None):
        def run():
            for m in self.g._all():
                if m["id"] == id:
                    if format == "metadata":
                        return {k: v for k, v in m.items() if k != "payload"} | {"payload": {
                            "mimeType": m["payload"]["mimeType"], "headers": m["payload"]["headers"]}}
                    return m
            raise _NotFound()
        return _Req(run)

    def attachments(self):
        g = self.g

        class _A:
            def get(self, userId, messageId, id):
                g.calls.append(("attachment", id))
                return _Req(lambda: {"data": base64.urlsafe_b64encode(ATTACHMENTS[id]).decode()})
        return _A()


class _Threads:
    def __init__(self, g):
        self.g = g

    def get(self, userId, id, format="full"):
        self.g.calls.append(("thread", id))

        def run():
            if id not in self.g.threads_by_id:
                raise _NotFound()
            return {"id": id, "messages": self.g.threads_by_id[id]}
        return _Req(run)


class _Batch:
    def __init__(self, cb):
        self.cb, self.reqs = cb, []

    def add(self, req, request_id):
        self.reqs.append((req, request_id))

    def execute(self):
        for req, rid in self.reqs:
            try:
                self.cb(rid, req.execute(), None)
            except Exception as e:  # noqa: BLE001
                self.cb(rid, None, e)


@pytest.fixture
def gmail(monkeypatch):
    """One connected account holding the synthetic thread; every Google call lands on FakeGmail."""
    import googleapiclient.discovery as disc
    fake = FakeGmail({TID: THREAD})
    monkeypatch.setattr(disc, "build", lambda *a, **k: fake)
    monkeypatch.setattr(ga, "_migrate_legacy_if_needed", lambda: None)
    monkeypatch.setattr(ga, "has_accounts", lambda: True)
    rec = {"id": ACCT, "label": "Work", "email": "owner@example.test"}
    monkeypatch.setattr(ga, "_accounts_with", lambda svc: [rec])
    monkeypatch.setattr(ga, "credentials_for", lambda aid: object())
    monkeypatch.setattr(ga, "_public_record", lambda r: dict(r))
    monkeypatch.setattr(ga, "_account_color", lambda r: "#888")
    monkeypatch.setattr(agent_mod, "_google_connectivity", lambda: (
        {"total": 1, "healthy": 1, "connected": True, "degraded": False, "needs_attention": [], "note": ""},
        {"connected": True}))
    try:
        from agent_friday.services import mail_thread
        mail_thread.reset()
    except ImportError:
        pass
    return fake


def _call(**inp):
    return agent_mod._tool_search_email(inp)


# ── reproduce: on the old code no tool result carries a body ────────────────────

def test_a_body_sentence_past_the_snippet_reaches_the_model(gmail):
    """Find the thread, then ask for it by its ref: the reply sentence deep in message 2 must be in
    what the model gets. On the old code `thread` was ignored and only 160-character snippets came back."""
    found = json.loads(_call(query="from:robin@example.test subject:venue after:2026/10/01"))
    assert [m["ref"] for m in found["messages"]] == [REF, REF]
    out = _call(query="", thread=found["messages"][0]["ref"])
    assert S2 in out and S4 in out, "no tool result carried a message body"


# ── 1. find by sender, subject or date; each hit says how to read its thread ───

@pytest.mark.parametrize("query", ["from:sam@example.test", "subject:venue", "after:2026/10/05 venue"])
def test_search_hits_carry_the_thread_ref_and_say_how_to_read_it(gmail, query):
    out = json.loads(_call(query=query))
    assert out["count"] >= 1 and all(m["ref"] == REF for m in out["messages"])
    assert "thread=" in out["read_hint"]
    desc = next(t for t in agent_mod.CLAUDE_TOOLS if t["name"] == "search_email")
    assert "thread" in desc["input_schema"]["properties"] and "offset" in desc["input_schema"]["properties"]
    assert "Never summarise a thread from snippets" in desc["description"]


# ── 2. read EVERY message, quotes not repeated, attachments listed ─────────────

def test_reading_the_thread_returns_all_four_bodies_once_in_order(gmail):
    raw = _call(query="", thread=REF)
    out = json.loads(raw)
    assert out["read"] is True and out["message_count"] == 4 and out["complete"] is True
    assert [m["n"] for m in out["messages"]] == [1, 2, 3, 4]
    assert [m["from"].split()[0] for m in out["messages"]] == ["Robin", "Sam", "Robin", "Lee"]
    for s in (S1, S2, S3, S4):
        assert raw.count(s) == 1, (s, raw.count(s))       # every message's words, quoted copies gone
    assert all(m.get("quoted_lines_removed", 0) >= 1 for m in out["messages"][1:])
    assert "whole conversation" in out["notice"] and "not instructions" in out["notice"]
    att = {a["filename"]: a for m in out["messages"] for a in m.get("attachments", [])}
    assert att["venue_notes.txt"]["type"] == "text/plain"
    assert "parking for 20 cars" in att["venue_notes.txt"]["text_excerpt"]
    assert att["coach_quote.pdf"]["size"] == 2_500_000 and "text_excerpt" not in att["coach_quote.pdf"]
    assert ("attachment", "att-quote") not in gmail.calls       # too large to fetch for an excerpt


def test_a_raw_thread_id_reads_too(gmail):
    out = json.loads(_call(query="", thread=TID))
    assert out["read"] is True and out["thread"] == REF


def test_top_finds_by_sender_and_reads_in_one_call(gmail):
    """The voice route has one call: find by the owner's words and read the best match."""
    out = json.loads(_call(query="from:lee@example.test", thread="top"))
    assert out["read"] is True and S3 in json.dumps(out) and out["found_by"]["query"] == "from:lee@example.test"


def test_this_reads_the_conversation_open_on_the_messages_screen(gmail, monkeypatch):
    from agent_friday.services import desktop_bus
    monkeypatch.setattr(desktop_bus, "stage", lambda ws=None, **k: {
        "workspace": "messages", "items": [{"ref": REF, "n": 1, "who": "Robin Vale", "title": "Venue options"}],
        "selection": {"refs": []}, "open": REF, "focus": None, "cursor": None, "at": 0})
    out = json.loads(_call(query="", thread="this"))
    assert out["read"] is True and "open" in out["resolved_as"] and S4 in json.dumps(out)


def test_the_open_conversation_is_named_with_its_ref_on_the_on_screen_block():
    import time
    from agent_friday.services import screen_stage as ss
    block = ss.on_screen_block({"workspace": "messages", "at": time.time(), "open": REF,
                                "items": [{"ref": REF, "n": 1, "who": "Robin Vale", "title": "Venue options"}],
                                "selection": {"refs": []}})
    assert REF in block and "thread=this" in block


# ── 3. a budget that fits the seat, and a cut that is said out loud ────────────

def test_a_long_thread_is_cut_to_the_budget_and_the_cut_is_announced(gmail):
    budget = 7000           # services/mail_thread.BUDGET_CHARS (asserted equal below, after the behaviour)
    long = [_msg("L%d" % i, "Pat %d <p%d@example.test>" % (i, i), "Thu, 8 Oct 2026", 1791400000000 + i,
                 ("Point %d. " % i) + ("word " * 700) + ("END-OF-%d" % i)) for i in range(12)]
    gmail.threads_by_id["t-long"] = long
    raw = _call(query="", thread="mail:%s:t-long" % ACCT)
    out = json.loads(raw)
    assert out.get("complete") is False and out["message_count"] == 12
    assert len(raw) <= budget + 200
    assert "offset" in out["notice"] and ("NOT included" in out["notice"] or "Shortened" in out["notice"])
    shown = [m["n"] for m in out["messages"]]
    cut = [m for m in out["messages"] if "text_cut" in m]
    assert cut or shown[-1] < 12
    if shown[-1] < 12:
        nxt = json.loads(_call(query="", thread="mail:%s:t-long" % ACCT, offset=shown[-1]))
        assert nxt["messages"][0]["n"] == shown[-1] + 1 and "starts at message" in nxt["notice"]
    from agent_friday.services import mail_thread as mt
    assert mt.BUDGET_CHARS == budget


# ── 5. honest failures: nothing read is never described ────────────────────────

def test_a_thread_gmail_cannot_find_says_nothing_was_read(gmail):
    out = json.loads(_call(query="", thread="mail:%s:t-missing" % ACCT))
    assert out["read"] is False and out["read_failed"] is True and out["kind"] == "not_found"
    assert "Nothing from this conversation was read" in out["error"]


def test_with_gmail_not_connected_a_thread_read_says_so(monkeypatch):
    monkeypatch.setattr(ga, "has_accounts", lambda: False)
    out = json.loads(_call(query="", thread=REF))
    assert out["read"] is False and "connected Gmail account" in out["error"]
    assert "Nothing from this conversation was read" in out["error"]


def test_this_with_nothing_open_or_read_asks_which_thread(gmail, monkeypatch):
    from agent_friday.services import desktop_bus
    monkeypatch.setattr(desktop_bus, "stage", lambda ws=None, **k: None)
    out = json.loads(_call(query="", thread="this"))
    assert out["read"] is False and "sender, subject or date" in out["error"]


def test_top_with_no_match_says_nothing_was_read(gmail):
    out = json.loads(_call(query="from:nobody@example.test", thread="top"))
    assert out["read"] is False and out["count"] == 0 and "Nothing from this conversation was read" in out["error"]


# ── 4. a cloud seat under the owner's recorded cloud-only consent ──────────────

def _seal(result, monkeypatch, choice):
    from agent_friday import core
    from agent_friday.services import egress_gate as eg, tool_hooks
    monkeypatch.setattr(core, "_load_settings", lambda: {"model_routing": {
        "mode": "cloud_only", "mode_chosen": "cloud_only",
        "cloud_consent": {"answered": True, "choice": choice}}})
    monkeypatch.setattr(core, "_load_privacy_watchlist", lambda: [])
    monkeypatch.setattr(core, "_owner_emails", lambda: [])
    ctx = tool_hooks.HookContext(tool_name="search_email", input={"thread": REF}, pii_lookup={})
    processed = agent_mod._hook_pii_scrub(ctx, result)
    payload = {"messages": [{"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "call-thread", "content": processed}]}]}
    return eg.seal_outbound(payload, "anthropic", pii_lookup=ctx.pii_lookup)["messages"][0]["content"][0]["content"]


def test_cloud_only_with_unrestricted_consent_gets_the_whole_thread(gmail, monkeypatch):
    from agent_friday.services import egress_gate as eg
    monkeypatch.setattr(eg, "_classify_cloud", lambda _: pytest.fail("unrestricted consent still classified"))
    sent = _seal(_call(query="", thread=REF), monkeypatch, "cloud_unrestricted")
    for s in (S1, S2, S3, S4):
        assert s in sent


def test_cloud_only_with_guarded_consent_gets_the_content_or_a_stated_withholding(gmail, monkeypatch):
    """Guarded consent keeps the gate on. One sensitive message may be withheld, but the model is told
    which and why; the rest of the thread arrives, and nothing is dropped silently or emptied."""
    from agent_friday.services import egress_gate as eg
    monkeypatch.setattr(eg, "_classify_cloud",
                        lambda t: eg.Tier.SENSITIVE if "deposit" in str(t) else eg.Tier.PUBLIC)
    monkeypatch.setattr(eg, "_local_model_available", lambda: True)
    monkeypatch.setattr(eg, "_run_appeals", lambda *a, **k: 0)
    sent = _seal(_call(query="", thread=REF), monkeypatch, "cloud_guarded")
    data = json.loads(sent)
    assert data["read"] is True and len(data["messages"]) == 4
    assert S2 in sent and S4 in sent                          # the rest of the thread reached the model
    assert S3 not in sent                                     # the sensitive message did not
    notice = data.get("privacy_gate") or ""
    assert "withheld" in notice and "sensitive" in notice.lower() and "message" in notice.lower()
    m3 = data["messages"][2]["text"]
    assert "withheld" in m3.lower()


# ── 6. voice: the same tool, the same argument, a contract that still fits ─────

def test_the_voice_contract_declares_thread_and_still_fits():
    from agent_friday.services import voice_engine as ve
    c = ve.build_voice_tool_contract()
    se = next(t for t in c["tools"] if t["function"]["name"] == "search_email")["function"]
    assert {"thread", "offset"} <= set(se["parameters"]["properties"])
    assert "description" not in se["parameters"]["properties"]["thread"]
    assert "thread" in se["description"]
    assert c["fits"] and c["tokens"] <= ve.VOICE_CONTRACT_MAX_TOKENS - 300, c["tokens"]


def test_the_voice_speaker_answers_from_a_thread_instead_of_listing_it(gmail):
    from agent_friday.services import voice_spoken
    from agent_friday.services.voice_front import result_kind
    raw = _call(query="", thread=REF)
    assert result_kind(raw) == "found"
    assert voice_spoken.structured_reply(raw, "search_email") is None
    hits = _call(query="subject:venue")
    assert voice_spoken.structured_reply(hits, "search_email").startswith("I found")


def test_a_failed_thread_read_is_spoken_as_a_failure(gmail):
    from agent_friday.services.voice_front import fixed_reply
    said = fixed_reply(_call(query="", thread="mail:%s:t-missing" % ACCT), "search_email")
    assert said and said.startswith("I couldn't check your email")


VOICE_AND_TEXT = [
    ("research this email thread", {"thread": "this"}),
    ("what did everyone say in that thread", {"thread": "this"}),
    ("summarise the thread with Robin Vale about the venue", {"thread": "top", "query": "Robin Vale venue"}),
    ("read me the whole email thread from Sam", {"thread": "top", "query": "Sam"}),
]


@pytest.mark.parametrize("text,want", VOICE_AND_TEXT)
def test_text_research_this_email_thread_routes_to_a_thread_read(text, want):
    """Text chat: Laya's rule hands the turn to search_email with `thread` (not 'a statement')."""
    from agent_friday.services import laya_router as lr
    r = lr.rule_route(text)
    assert r is not None and r.decision == "tool" and r.tool == "search_email", r
    for k, v in want.items():
        assert r.args.get(k) == v, (k, r.args)


@pytest.mark.parametrize("text,want", VOICE_AND_TEXT)
def test_voice_research_this_email_thread_reaches_search_email(text, want, monkeypatch):
    """Voice (local front): the routed plan runs search_email with `thread`; the speaker never answers
    a thread request with no tool. Held tools are the voice contract's own names."""
    from agent_friday.routes import voice as rv
    from agent_friday.services import laya_router as lr
    from agent_friday.services import voice_engine as ve
    held = ve.build_voice_tool_contract()["names"]
    assert "search_email" in held
    plan = rv._voice_route_plan(text, held, route_fn=lambda t, **kw: lr.rules_route(t, tools=kw.get("tools")))
    assert plan["tool"] == "search_email" and plan["args"]["thread"] == want["thread"], plan
    from agent_friday.services.voice_front import validate_tool_call
    contract = ve.build_voice_tool_contract()
    call = {"function": {"name": "search_email", "arguments": dict(plan["args"], offset=0)}}
    assert validate_tool_call(call, contract)[2] is None, "the voice contract refuses the thread call"


def test_a_gemini_live_kit_for_a_thread_request_holds_search_email():
    from agent_friday.services import live_session_plan as lsp
    from agent_friday.services import voice_engine as ve
    names = ve.build_voice_tool_contract()["names"]
    kit = lsp.choose_kit(names, size=18, opening="research this email thread with Robin about the venue")
    assert "search_email" in kit
