"""The channels the privacy map used to list as not recorded (BD-15 slice P5):
texts and calls, chat channels, mailbox changes and drafts, news feeds,
unsubscribes, linked Fridays and job-search pages are each a send in the
egress ledger. Synthetic fixtures only; nothing reaches a network."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from agent_friday.services import egress_gate as eg

KEY = b"synthetic-governance-key-for-tests-0000000000000000"
SRC = Path(__file__).resolve().parents[2] / "src" / "agent_friday"


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    import agent_friday.paths as paths
    from agent_friday.governance import proof_of_integrity as poi
    from agent_friday.services import credential_store as cs
    from agent_friday.services import judgment_gate as jg
    from agent_friday.services import off_record
    from agent_friday.services import sent_text_store as sts
    log = tmp_path / "vault" / "egress-log.jsonl"
    log.parent.mkdir(parents=True)
    monkeypatch.setattr(eg, "_DEFAULT_LOG", log)
    monkeypatch.setattr(paths, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(poi, "get_governance_key", lambda: KEY)
    # The checkpoint pins the cLaws under this key; action_gate binds friday_home
    # by name, so its pin folder is set here, or an earlier test's pin (another
    # key) would hold every send.
    from agent_friday.governance import action_gate
    gov = tmp_path / "governance"
    gov.mkdir()
    monkeypatch.setattr(action_gate, "_gov_dir", lambda: gov)
    monkeypatch.setattr(cs, "protect", lambda data: (b"SEALED:" + data[::-1], "keystore"))
    monkeypatch.setattr(cs, "unprotect", lambda blob: blob[len(b"SEALED:"):][::-1])
    monkeypatch.setattr(cs, "looks_protected", lambda blob: "keystore" if blob.startswith(b"SEALED:") else None)
    monkeypatch.setattr(sts, "_vault_key", lambda derive=True: None)
    monkeypatch.setattr(sts, "_passphrase_set", lambda: False)
    monkeypatch.setattr(off_record, "active", lambda *a, **k: False)
    monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: True)
    monkeypatch.setattr(jg, "never_send_hits", lambda text: [])
    monkeypatch.setattr(eg, "_never_send_covered_by_override", lambda text: False)
    return log


def _calls(log):
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if '"kind": "call"' in x] \
        if log.exists() else []


def test_a_text_is_a_send_and_a_verification_code_is_never_kept(ledger, monkeypatch):
    from agent_friday.phone import config, service
    from agent_friday.services import sent_text_store as sts

    class _Client:
        def send_sms(self, **kw):
            return {"sid": "SMsynthetic", "status": "queued", "num_segments": 1}
    monkeypatch.setattr(service, "_client", lambda: _Client())
    monkeypatch.setattr(config, "load", lambda: {"phone_number": "+15550000000"})
    monkeypatch.setattr(service, "_status_url", lambda p: "")
    monkeypatch.setattr(service.spool, "ledger_add", lambda **k: None)
    service._send_sms_now("+15550000001", "A synthetic note", detail="approved text")
    service._send_sms_now("+15550000001", "Your Friday verification code is 123456.", detail="verification code")
    rows = _calls(ledger)
    assert [r["channel"] for r in rows] == ["sms", "sms"] and rows[0]["destination"] == "api.twilio.com"
    assert "A synthetic note" in sts.read(rows[0]["payload_fingerprint"])["text"]
    assert "123456" not in sts.read(rows[1]["payload_fingerprint"])["text"]


def test_a_chat_channel_message_is_gated_and_a_send(ledger):
    from agent_friday.services.channels.base import ChannelAdapter

    class _Fake(ChannelAdapter):
        name = "telegram"

        def _send_raw(self, chat_id, text):
            return {"ok": True}
    _Fake().send("chat-1", "A synthetic reply")
    row = _calls(ledger)[-1]
    assert row["channel"] == "chat_channel" and row["destination"] == "api.telegram.org"


class _HttpError(Exception):
    """Shaped like googleapiclient's HttpError: the status is on .resp."""

    def __init__(self, status):
        super().__init__(f"synthetic {status}")
        self.resp = type("R", (), {"status": status, "get": lambda self, *a: None})()


class _Req:
    def __init__(self, fails=0):
        self.fails = fails

    def execute(self):
        if self.fails:
            self.fails -= 1
            raise _HttpError(503)            # retried by gmail_api
        return {"ok": True}


class Resource:
    def __init__(self, fails=0):
        self.fails = fails

    def batchModify(self, **kw):
        return _Req(self.fails)

    def messages(self):
        return self

    def users(self):
        return self


def test_a_mailbox_change_is_one_send_however_many_tries(ledger, monkeypatch):
    from agent_friday.services import egress_record as er
    from agent_friday.services import gmail_api
    svc = er.recorded(Resource(fails=1), "mail_changes", "gmail-mailbox", "gmail.googleapis.com")
    out = gmail_api.execute(svc.users().messages().batchModify(userId="me", body={"ids": ["m1"]}),
                            sleep=lambda s: None)
    assert out == {"ok": True}
    rows = _calls(ledger)
    assert [r["result"] for r in rows] == ["sent"], "a retried change was recorded more than once"


def test_a_news_feed_request_is_a_send_but_a_cached_feed_is_not(ledger, monkeypatch):
    import urllib.request
    from agent_friday.services import news_engine as ne

    class _Resp:
        headers = {}

        def read(self):
            return b"<rss><channel><item><title>Synthetic</title></item></channel></rss>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    import sys
    import types
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _Resp())
    fake = types.ModuleType("feedparser")          # the test does not depend on it being installed
    fake.parse = lambda raw: types.SimpleNamespace(entries=[])
    monkeypatch.setitem(sys.modules, "feedparser", fake)
    url = "https://news.example.invalid/rss?q=synthetic+town"
    ne._RSS_CACHE.pop(url, None)
    ne._parse_feed(url)
    ne._parse_feed(url)                  # served from the cache: nothing leaves
    rows = [r for r in _calls(ledger) if r["channel"] == "news_feed"]
    assert len(rows) == 1 and rows[0]["destination"] == "news.example.invalid"


def test_words_spoken_on_a_live_call_are_each_a_send(ledger):
    from agent_friday.phone import live_call
    from agent_friday.services import sent_text_store as sts

    class _Ws:
        def send(self, frame):
            pass

    class _Engine:
        def synthesize(self, text):
            return b"\x00\x00" * 480

    bridge = live_call.CallBridge(_Ws(), engine=_Engine(), vad=object(), agent=lambda *a: "")
    bridge.party = "+15550000001"
    bridge.say("A synthetic spoken answer")
    row = [r for r in _calls(ledger) if r["channel"] == "phone_call"][-1]
    assert row["result"] == "sent" and row["destination"] == "api.twilio.com"
    assert "A synthetic spoken answer" in sts.read(row["payload_fingerprint"])["text"]


def test_a_timed_out_channel_send_is_unconfirmed_and_keeps_only_what_fits(ledger):
    from agent_friday.services import sent_text_store as sts
    from agent_friday.services.channels.base import ChannelAdapter

    class _Slow(ChannelAdapter):
        name = "telegram"
        max_chars = 10

        def _send_raw(self, chat_id, text):
            from agent_friday.services.channels.base import send_unsure
            return {"ok": False, "unsure": send_unsure(TimeoutError("synthetic"))}
    _Slow().send("chat-1", "0123456789 and the words that never left")
    row = _calls(ledger)[-1]
    assert row["result"] == "sent_unconfirmed"
    kept = sts.read(row["payload_fingerprint"])["text"]
    assert "0123456789" in kept and "never left" not in kept


def test_a_server_error_then_a_refusal_is_still_unconfirmed(ledger):
    from agent_friday.services import egress_record as er
    from agent_friday.services import gmail_api

    class _R:
        n = 0

        def execute(self):
            _R.n += 1
            raise _HttpError(503) if _R.n == 1 else _HttpError(400)

    class Resource:
        def batchModify(self, **kw):
            return _R()

        def users(self):
            return self

        def messages(self):
            return self

    svc = er.recorded(Resource(), "mail_changes", "gmail-mailbox", "gmail.googleapis.com")
    req = svc.users().messages().batchModify(userId="me", body={"ids": ["m1"]})
    with pytest.raises(Exception):
        gmail_api.execute(req, tries=2, sleep=lambda s: None)
    assert [r["result"] for r in _calls(ledger)] == ["sent_unconfirmed"]


def test_a_peer_that_errors_after_receiving_is_one_honest_row(ledger, monkeypatch):
    import urllib.error
    from agent_friday.services import egress_gate
    from agent_friday.services import federation_transport as ft
    assert egress_gate.result_for_error(urllib.error.HTTPError("u", 503, "x", {}, None)) == "sent_unconfirmed"
    assert egress_gate.result_for_error(urllib.error.HTTPError("u", 403, "x", {}, None)) == "failed"
    ft._record_peer_send("https://peer.example.invalid/inbox", {"sealed": "x"},
                         urllib.error.HTTPError("u", 503, "x", {}, None))
    rows = [r for r in _calls(ledger) if r["channel"] == "federation"]
    assert [r["result"] for r in rows] == ["sent_unconfirmed"]


def test_a_local_error_before_anything_leaves_records_nothing(ledger, monkeypatch):
    from agent_friday.services import federation_transport as ft
    from agent_friday.services import web_safety
    monkeypatch.setattr(web_safety, "check_peer_endpoint", lambda u: (True, ""))
    out = ft.send_to_peer("https://peer.example.invalid", {"cannot": object()})   # will not serialise
    assert out["ok"] is False
    assert [r for r in _calls(ledger) if r["channel"] == "federation"] == []


def test_a_verification_text_that_fails_still_keeps_no_code(ledger, monkeypatch):
    from agent_friday.phone import config, service
    from agent_friday.services import sent_text_store as sts

    class _Client:
        def send_sms(self, **kw):
            raise TimeoutError("synthetic")
    monkeypatch.setattr(service, "_client", lambda: _Client())
    monkeypatch.setattr(config, "load", lambda: {"phone_number": "+15550000000"})
    monkeypatch.setattr(service, "_status_url", lambda p: "")
    monkeypatch.setattr(service.spool, "ledger_add", lambda **k: None)
    monkeypatch.setattr(service, "log_failure", lambda *a, **k: "E1")
    with pytest.raises(service.PhoneRefused):
        service._send_sms_now("+15550000001", "Your code is 654321.", detail="code", secret=True)
    row = _calls(ledger)[-1]
    assert row["result"] == "sent_unconfirmed"
    assert "654321" not in sts.read(row["payload_fingerprint"])["text"]


def test_a_feed_that_will_not_parse_is_still_one_row(ledger, monkeypatch):
    import sys
    import types
    import urllib.request
    from agent_friday.services import news_engine as ne

    class _Resp:
        headers = {}

        def read(self):
            return b"not a feed"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _Resp())
    bad = types.ModuleType("feedparser")
    bad.parse = lambda raw: (_ for _ in ()).throw(ValueError("synthetic"))
    monkeypatch.setitem(sys.modules, "feedparser", bad)
    url = "https://news.example.invalid/broken"
    ne._RSS_CACHE.pop(url, None)
    assert ne._parse_feed(url) == []
    assert [r["result"] for r in _calls(ledger) if r["channel"] == "news_feed"] == ["sent"]


def test_the_map_names_only_what_is_still_outside():
    from agent_friday.services import privacy_map as pm
    assert {n["key"] for n in pm.NOT_RECORDED} == {"browser", "fetch_hops", "connector_reads"}
    assert pm.destination_words("twilio").startswith("Texts and calls")
    assert pm.destination_words("channel:discord") == "Messages on discord"
    assert pm.destination_words("feed:news.example.invalid") == "News feed from news.example.invalid"


# ── the guard, extended to these channels ─────────────────────────────────────

#: The calls that put bytes on the wire in the watched modules.
_SENDS = {"send_sms", "create_call", "_send_raw", "send", "_post", "urlopen", "get", "post", "request",
          "safe_get", "fetch", "execute"}
#: The receivers those names must be called on to count (``requests.get``,
#: ``self.ws.send``...), so ``dict.get`` and friends are not sends.
_ON = {"get": {"requests", "httpx", "session", "_session"}, "post": {"requests", "httpx", "session", "_session"},
       "request": {"requests", "httpx", "session", "_session"}, "send": {"ws", "channel"},
       "fetch": {"page_reader"}, "execute": set(),
       "send_sms": None, "create_call": None, "_send_raw": None, "_post": None, "urlopen": None,
       "safe_get": None}
_RECORDERS = {"record_service_send", "record_address", "_record_egress", "record_channel_send",
              "_record_feed", "_record_peer_send", "_record_card_fetch", "_record_spoken", "_record",
              "_record_unsubscribe", "_record_fetch"}
_WATCHED = ("phone/service.py", "phone/live_call.py", "services/channels/base.py",
            "services/channels/telegram_bridge.py", "services/channels/discord_bridge.py",
            "services/news_engine.py", "services/news_links.py", "services/mail_unsubscribe.py",
            "services/federation.py", "services/federation_transport.py", "services/career_ops.py")
#: Exact waivers, file:function -> why the send is recorded elsewhere or
#: carries nothing of the owner's.
_WAIVED = {
    "services/channels/base.py:send": "records via record_channel_send in the same function",
    "services/channels/telegram_bridge.py:_call": "sendMessage recorded by ChannelAdapter.send; getUpdates is a poll",
    "services/channels/telegram_bridge.py:_send_raw": "recorded by ChannelAdapter.send, its only caller",
    "services/channels/discord_bridge.py:_send_raw": "recorded by ChannelAdapter.send, its only caller",
    "phone/live_call.py:_send": "audio frames of words say() records",
    "services/mail_unsubscribe.py:_post": "recorded by unsubscribe(), its only caller",
}


def _name(func) -> tuple[str, str]:
    if isinstance(func, ast.Attribute):
        recv = func.value
        base = recv.attr if isinstance(recv, ast.Attribute) else getattr(recv, "id", "")
        return func.attr, base
    return getattr(func, "id", ""), ""


def _functions():
    for rel in _WATCHED:
        p = SRC / rel
        text = p.read_text(encoding="utf-8-sig")
        tree = ast.parse(text)                      # a file that does not parse fails the guard
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield rel, node


def test_every_send_in_these_channels_records_itself():
    bad = []
    for rel, fn in _functions():
        sends, records = [], False
        for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call)):
            name, recv = _name(call.func)
            if name in _RECORDERS:
                records = True
            if name in _SENDS:
                on = _ON.get(name)
                if on is None or recv in on:
                    sends.append(f"{recv + '.' if recv else ''}{name}")
        key = f"{rel}:{fn.name}"
        if sends and not records and key not in _WAIVED:
            bad.append(f"{key} ({', '.join(sorted(set(sends)))})")
    assert bad == [], f"a send with no egress record in: {bad}"


def test_the_waivers_are_still_real():
    keys = {f"{rel}:{fn.name}" for rel, fn in _functions()}
    assert set(_WAIVED) <= keys, "a waiver names a function that no longer exists"
