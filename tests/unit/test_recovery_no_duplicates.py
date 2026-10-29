"""NS-34.16-4 (BD-16): the system recovers without duplicate actions or false
completion.

R1's harness (test_chaos_outward_actions._kill_after) runs a separate
interpreter on this run's Friday home and kills it with os._exit (no cleanup,
no finally) at the riskiest points of five outward paths:

  * after the outward call reached the (stub) service and before the action's
    terminal receipt was written ("after");
  * on the way to the service, after the decision was written ("before");
  * for a site publication, also after it was left provider_pending.

The paths: (a) the approval executor running an approved card a task waited
on, (b) a Gmail send from the held-mail queue, (c) a channel (Telegram)
reply from the poll loop, (d) a site publication through
sites_operations._perform, (e) an approved item_actions batch.

This process then starts from the same files the way Friday does at boot
(task journal, task_resume, card_recovery, the sweep of decisions the last run
never closed, the site reconcile and decision expiry), retries the way the
product would (the owner approving again, the hook fired again, the
scheduler or poll tick, a resumed task asking for the same message again), and
asserts through outward_audit.audit(): no duplicate terminal receipt, no
idempotency key executed twice, no task marked done without an executed or
completed_verified receipt, nothing left open; and the stub service saw each
request at most once.

Synthetic data only; nothing reaches a network.
"""
from __future__ import annotations

import hashlib
import json
import sys
import textwrap
import time
import types
from collections import Counter

import pytest

from agent_friday.governance import action_gate as G
from agent_friday.services import action_ledger as L
from agent_friday.services import approvals
from agent_friday.services import card_recovery
from agent_friday.services import outward_audit
from agent_friday.services import task_journal as tj
from agent_friday.services import task_resume
from tests.unit.test_chaos_outward_actions import _kill_after, restart  # noqa: F401  (fixture)

KILLS = ("before", "after")

# The doomed process's own setup: its integrity checks pass (as on a healthy
# install), and `service(key)` is the stub outward service, which records each
# request that reaches it on disk before it answers.
_PRELUDE = '''
import hashlib as _hashlib
import json as _json
from pathlib import Path as _Path
from agent_friday.governance import action_gate as G
G.verify_claws = lambda *a, **k: (True, "intact")
G.outward_hold = lambda: None
TMP = _Path(r"@TMP@")
KILL = "@KILL@"
def service(key):
    """KILL=before: the process dies on its way to the service. KILL=after: the
    service took it and the process dies before the terminal receipt."""
    if KILL == "before":
        os._exit(9)
    with open(TMP / "service.jsonl", "a", encoding="utf-8") as fh:
        fh.write(_json.dumps({"key": key}) + "\\n")
        fh.flush()
        os.fsync(fh.fileno())
    if KILL == "after":
        os._exit(9)
'''


def _run_killed(tmp_path, body, kill, **subs):
    """Run `body` in a fresh interpreter (R1's _kill_after) that dies at `kill`.
    Returns the approval id it printed (AID=...), if any."""
    script = _PRELUDE.replace("@TMP@", str(tmp_path)).replace("@KILL@", kill)
    body = textwrap.dedent(body)
    for k, v in subs.items():
        body = body.replace("@%s@" % k, v)
    lines = _kill_after(script + body, tmp_path)
    L.reset_caches()                                 # the ledger as the killed process left it
    return next((line[4:] for line in reversed(lines) if line.startswith("AID=")), None)


def _reach(tmp_path, key):
    with open(tmp_path / "service.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"key": key}) + "\n")


def _reached(tmp_path) -> Counter:
    p = tmp_path / "service.jsonl"
    if not p.exists():
        return Counter()
    return Counter(json.loads(line)["key"] for line in p.read_text(encoding="utf-8").splitlines()
                   if line.strip())


def _decisions(tool):
    return [e["rec"] for e in L.entries(["decision"], newest_first=False)
            if e["rec"].get("tool") == tool and e["rec"].get("class") == G.OUTWARD]


def _words(action_id):
    return [e["rec"].get("outcome") for e in L.entries(["outcome"], newest_first=False)
            if e["rec"].get("action_id") == action_id]


@pytest.fixture
def boot(restart, monkeypatch):  # noqa: F811
    """This process starting from what the killed one left: the recovery paths
    Friday runs at boot (agent._restore_tasks_from_journal). Its own integrity
    checks pass, as on a healthy install."""
    monkeypatch.setattr(G, "verify_claws", lambda *a, **k: (True, "intact"))
    monkeypatch.setattr(G, "outward_hold", lambda: None)

    def run():
        L.reset_caches()
        tj.reconcile_on_boot()
        task_resume.announce_may_have_acted(task_resume.may_have_acted_after_boot())
        started = time.time() + 5                  # this process started after the killed one
        cards = card_recovery.reconcile_at_boot(started_at=started, since=0)
        settle = getattr(card_recovery, "settle_interrupted_decisions", None)
        settled = settle(started_at=started) if settle else []
        # after the sweep, as at boot: an approved batch's run that started and never finished
        run.batches = card_recovery.settle_interrupted_batches(started_at=started)
        return cards, settled
    run.batches = []
    run.pushed = restart
    return run


def _invariants(tmp_path, tasks=()):
    """NS-34.16-4 through the outward audit, and the stub service's own count."""
    r = outward_audit.audit(now=time.time() + 2 * outward_audit.GRACE_S, tasks=list(tasks))
    c = r["counts"]
    assert c["duplicate"] == 0, r                  # no action has two terminal receipts
    assert c["duplicate_key"] == 0, r              # no idempotency key executed twice
    assert c["false_done"] == 0, r                 # no task done without executed/completed_verified
    assert c["open"] == 0, r                       # recovery left nothing open
    reached = _reached(tmp_path)
    assert all(n == 1 for n in reached.values()), "a request reached the service twice: %s" % reached
    return r


def _interrupted(tool, kill):
    """The decision the kill left, now closed unconfirmed: never executed."""
    ds = _decisions(tool)
    assert len(ds) == 1, ds
    aid = ds[0]["action_id"]
    assert _words(aid) == [G.UNCONFIRMED], (kill, _words(aid))
    return ds[0]


# ── (a) the approval executor running an approved card a task waited on ─────

_CARD = """
from agent_friday.services import approval_executor as ax
rec = approvals.create_approval(kind="tainted_action", subject_type="tool", subject_id="send_email-r2",
                                title="Send the invoice to the client", description="synthetic",
                                force_gate=True,
                                payload={"tool": "send_email", "task_id": "@TASK@",
                                         "input": {"to": "a@example.invalid", "title": "Invoice 42"}})
rec = approvals._patch(rec["approval_id"], status="approved", decided_at=time.time())
print("AID=" + rec["approval_id"], flush=True)
ax._run_tool = lambda name, args, session_ctx=None: service("card:" + session_ctx["approved_card"])
ax._decide(rec)                            # claim, checkpoint receipt, run: the kill lands in the run
"""


@pytest.mark.parametrize("kill", KILLS)
def test_an_approved_card_killed_mid_run_is_not_run_again_nor_its_task_done(tmp_path, boot, monkeypatch, kill):
    from agent_friday.services import agent
    from agent_friday.services import approval_executor as ax
    tid = "task-r2-card-" + kill
    aid = _run_killed(tmp_path, _CARD, kill, TASK=tid)
    monkeypatch.setitem(agent.TASKS, tid, {"task_id": tid, "name": "Send the invoice",
                                           "status": agent.WAITING_APPROVAL, "created": time.time()})
    cards, settled = boot()
    assert cards == [aid]                                       # card_recovery: outcome unknown, told once
    assert approvals.get_approval(aid)["outcome"] == "unknown"
    assert any(p.get("kind") == "approval_outcome_unknown" for p in boot.pushed)
    d = _interrupted("send_email", kill)
    assert settled == [d["action_id"]] and d.get("task_id") == tid
    assert card_recovery.settle_interrupted_decisions(started_at=time.time() + 5) == []   # once

    # Retry as the product would: the owner approves again, the hook fires again.
    monkeypatch.setattr(ax, "_run_tool", lambda name, args, session_ctx=None:
                        _reach(tmp_path, "card:" + session_ctx["approved_card"]) or "sent")
    assert approvals.decide(aid, "approve")["status"] == "approved"
    ax._decide(approvals.get_approval(aid))
    assert _reached(tmp_path) == (Counter({"card:" + aid: 1}) if kill == "after" else Counter())

    # The task that waited on the card: never done on an action nobody can confirm.
    agent.sweep_waiting_tasks(force=True)
    assert agent.TASKS[tid]["status"] not in outward_audit.DONE_STATUSES
    monkeypatch.setattr(agent, "_SETTLE_STALE_S", 0)            # long after the restart
    agent.sweep_waiting_tasks(force=True)
    assert agent.TASKS[tid]["status"] not in outward_audit.DONE_STATUSES
    _invariants(tmp_path, tasks=[agent.TASKS[tid]])
    # and the audit would catch the task had it been marked done
    fd = outward_audit.audit(now=time.time(), tasks=[{"task_id": tid, "status": "complete"}])
    assert fd["counts"]["false_done"] == 1 and fd["false_done"][0]["states"] == [G.UNCONFIRMED]


# ── (b) a Gmail send from the held-mail queue (stub transport) ──────────────

_MAIL_SETUP = """
import sys as _sys, types as _types
from agent_friday.services import gmail_send as gs, google_accounts
gs._held_path = lambda: TMP / "held.json"
gs._outbox_path = lambda: TMP / "sent_mail.jsonl"
gs.scope_granted = lambda account_id=None: True
google_accounts.credentials_for = lambda account_id: object()
class _Send:
    def __init__(self, body):
        self.body = body
    def execute(self):
        service("mail:" + _hashlib.sha256(self.body["raw"].encode()).hexdigest()[:16])
        return {"id": "m-1", "threadId": "t-1"}
class _Messages:
    def send(self, userId, body):
        return _Send(body)
class _Users:
    def messages(self):
        return _Messages()
class _Gmail:
    def users(self):
        return _Users()
_disc = _types.ModuleType("googleapiclient.discovery")
_disc.build = lambda *a, **k: _Gmail()
_pkg = _types.ModuleType("googleapiclient")
_pkg.discovery = _disc
_sys.modules["googleapiclient"] = _pkg
_sys.modules["googleapiclient.discovery"] = _disc
"""

_MAIL_CARD = _MAIL_SETUP + """
TO, SUBJECT, BODY = ["a@example.invalid"], "Invoice 42", "Synthetic invoice text."
fp = gs.message_fingerprint(TO, SUBJECT, BODY)
rec = approvals.create_approval(
    kind=gs.APPROVAL_KIND, subject_type=gs.SUBJECT_TYPE, subject_id=fp + ":r2a",
    title="Send email to a@example.invalid", description="synthetic", force_gate=True,
    action_description="Send mail as you.\\n\\nTo: a@example.invalid\\nSubject: Invoice 42\\n\\n" + BODY,
    payload={"handler": "gmail_send", "fingerprint": fp, "to": TO, "cc": [], "bcc": [],
             "subject": SUBJECT, "body": BODY, "account_id": "acct_owner",
             "from_email": "owner@example.invalid"})
rec = approvals._patch(rec["approval_id"], status="approved", decided_at=time.time())
print("AID=" + rec["approval_id"], flush=True)
gs.hold(rec, now=time.time() - 30)         # approved; its undo window is over
"""

_MAIL = _MAIL_CARD + """
gs._tick()                                 # the held-mail scheduler's tick sends it
"""


def _gmail_here(monkeypatch, tmp_path):
    """This process's Gmail: the same stub service, the same queue and outbox."""
    from agent_friday.governance import send_grants
    from agent_friday.services import gmail_send as gs, google_accounts

    class _Send:
        def __init__(self, body):
            self.body = body

        def execute(self):
            _reach(tmp_path, "mail:" + hashlib.sha256(self.body["raw"].encode()).hexdigest()[:16])
            return {"id": "m-again", "threadId": "t-1"}

    gmail = types.SimpleNamespace(users=lambda: types.SimpleNamespace(
        messages=lambda: types.SimpleNamespace(send=lambda userId, body: _Send(body))))
    disc = types.ModuleType("googleapiclient.discovery")
    disc.build = lambda *a, **k: gmail
    pkg = types.ModuleType("googleapiclient")
    pkg.discovery = disc
    monkeypatch.setitem(sys.modules, "googleapiclient", pkg)
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", disc)
    monkeypatch.setattr(gs, "_held_path", lambda: tmp_path / "held.json")
    monkeypatch.setattr(gs, "_outbox_path", lambda: tmp_path / "sent_mail.jsonl")
    monkeypatch.setattr(gs, "scope_granted", lambda account_id=None: True)
    monkeypatch.setattr(google_accounts, "credentials_for", lambda account_id: object())
    monkeypatch.setattr(gs, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(gs, "sendable_accounts",
                        lambda: [{"id": "acct_owner", "email": "owner@example.invalid", "label": "Owner"}])
    # The owner's standing pre-approval covers this recipient for an unattended task.
    monkeypatch.setattr(gs, "_pre_approval", lambda recipients: {"grant_id": "sgr_r2"})
    monkeypatch.setattr(gs, "_notify_pre_approved_hold", lambda *a, **k: None)
    monkeypatch.setattr(gs, "_tool_context", lambda: {"task_id": "task-r2-mail", "is_background_task": True})
    monkeypatch.setattr(send_grants, "record_use", lambda *a, **k: None)
    return gs


def _tick_due(gs):
    for row in gs.held():
        gs._tick(now=row["due"] + 1)


@pytest.mark.parametrize("kill", KILLS)
def test_a_mail_send_killed_mid_send_is_never_sent_twice(tmp_path, boot, monkeypatch, kill):
    aid = _run_killed(tmp_path, _MAIL, kill)
    gs = _gmail_here(monkeypatch, tmp_path)
    cards, settled = boot()
    assert cards == [aid] and approvals.get_approval(aid)["outcome"] == "unknown"
    d = _interrupted("gmail:send", kill)
    assert settled == [d["action_id"]]
    sent_before = sum(_reached(tmp_path).values())
    assert sent_before == (1 if kill == "after" else 0)

    # The scheduler's tick: the row the dead process left marked `sending` is never sent
    # again; it leaves the queue and the owner gets a card to decide whether to send it again.
    assert gs._tick() == [(aid, "interrupted")] and gs.held() == []
    again = [r for r in approvals.list_approvals(kind=gs.APPROVAL_KIND, subject_type=gs.SUBJECT_TYPE)
             if (r.get("payload") or {}).get("resend_of") == aid]
    assert [r["status"] for r in again] == ["pending"]
    assert gs._tick() == []
    # The owner approves again, and the decision hook fires again: the spent card sends nothing.
    assert approvals.decide(aid, "approve")["status"] == "approved"
    gs._on_decision(approvals.get_approval(aid))
    _tick_due(gs)
    # The task that asked is resumed and asks for the same message again under the
    # owner's pre-approval: it may already have gone, so the owner gets a card instead.
    res = gs.request_send(to=["a@example.invalid"], subject="Invoice 42", body="Synthetic invoice text.")
    _tick_due(gs)
    assert not res.get("pre_approved") and res["status"] == "pending", res
    card = approvals.get_approval(res["approval_id"])
    assert "may already have been sent" in card["action_description"]
    assert sum(_reached(tmp_path).values()) == sent_before
    _invariants(tmp_path)


# The held-mail queue itself (NS-34.16-4): the tick marks a due row `sending` on disk
# and takes it out only after the send's result is recorded. Killed after the mark and
# before the send, or after Gmail took it and before the result was recorded:
_MAIL_KILLS = {
    "mark": "gs._deliver = lambda approval_id, to: os._exit(9)    # marked, not yet sent\n",
    "record": "gs._record_egress = lambda *a, **k: os._exit(9)     # Gmail took it; nothing recorded\n",
}


def _resend_cards(gs, aid):
    return [r for r in approvals.list_approvals(kind=gs.APPROVAL_KIND, subject_type=gs.SUBJECT_TYPE)
            if (r.get("payload") or {}).get("resend_of") == aid]


@pytest.mark.parametrize("kill", sorted(_MAIL_KILLS))
def test_a_held_mail_row_cut_off_mid_send_is_never_lost_silently_nor_sent_twice(
        tmp_path, boot, monkeypatch, kill):
    aid = _run_killed(tmp_path, _MAIL_CARD + _MAIL_KILLS[kill] + "gs._tick()\n", kill)
    rows = json.loads((tmp_path / "held.json").read_text(encoding="utf-8"))
    assert [(r["approval_id"], r.get("state")) for r in rows] == [(aid, "sending")]   # kept, marked
    gs = _gmail_here(monkeypatch, tmp_path)
    told = []
    monkeypatch.setattr(gs, "_notify", lambda title, body, **k: told.append((title, body)))
    cards, settled = boot()
    went = kill == "record"
    assert sum(_reached(tmp_path).values()) == (1 if went else 0)
    if went:                                       # burned for its send, outcome unknown
        assert cards == [aid] and approvals.get_approval(aid)["outcome"] == "unknown"
        d = _interrupted("gmail:send", kill)       # its decision: unconfirmed, never executed
        assert settled == [d["action_id"]]
    else:                                          # never burned, never decided: nothing went
        assert cards == [] and settled == [] and _decisions("gmail:send") == []

    # The next tick: the row is not sent again; it leaves the queue, and the owner is
    # asked on a card (never under a pre-approval) whether to send it, and told.
    assert gs._tick() == [(aid, "interrupted")] and gs.held() == []
    again = _resend_cards(gs, aid)
    assert [r["status"] for r in again] == ["pending"]
    note = gs.INTERRUPTED_MAYBE_NOTE if went else gs.INTERRUPTED_NOT_SENT_NOTE
    assert again[0]["action_description"].startswith(note)
    assert len(told) == 1 and "Invoice 42" in told[0][1]
    old = approvals.get_approval(aid)
    assert old["consumed"] and (old["outcome"] == "unknown" if went else old.get("used_at"))
    assert gs._tick() == [] and len(_resend_cards(gs, aid)) == 1 and len(told) == 1   # once
    assert sum(_reached(tmp_path).values()) == (1 if went else 0)

    # The owner re-approving the old card sends nothing: it is spent.
    approvals.decide(aid, "approve")
    gs._on_decision(approvals.get_approval(aid))
    _tick_due(gs)
    # A resumed task asking for the same message gets that same card back, not a send.
    res = gs.request_send(to=["a@example.invalid"], subject="Invoice 42", body="Synthetic invoice text.")
    assert res["approval_id"] == again[0]["approval_id"] and not res.get("pre_approved")
    _tick_due(gs)
    assert sum(_reached(tmp_path).values()) == (1 if went else 0)
    if not went:
        # Never sent: the owner's yes on the new card sends it, once.
        approvals.decide(again[0]["approval_id"], "approve")
        gs._on_decision(approvals.get_approval(again[0]["approval_id"]))
        _tick_due(gs)
        assert sum(_reached(tmp_path).values()) == 1
        assert approvals.get_approval(again[0]["approval_id"]).get("used_at")
    _invariants(tmp_path)


def test_a_cut_off_send_keeps_only_its_own_message_off_a_pre_approval_and_not_for_ever(boot, monkeypatch):
    from agent_friday.services import gmail_send as gs
    fp = gs.message_fingerprint(["a@example.invalid"], "Weekly note", "Same text every week.")
    made = []

    def spent(outcome=None, found=None):
        made.append(1)
        rec = approvals.create_approval(kind=gs.APPROVAL_KIND, subject_type=gs.SUBJECT_TYPE,
                                        subject_id="%s:%d" % (fp, len(made)), title="Send email",
                                        description="synthetic", force_gate=True,
                                        payload={"handler": "gmail_send", "fingerprint": fp})
        return approvals._patch(rec["approval_id"], status="approved", consumed=True,
                                created_at=time.time() + len(made),       # newest last, whatever the clock
                                **({"outcome": outcome, "outcome_found_at": found} if outcome else {}))

    assert gs._maybe_sent(fp) is False
    spent("unknown", time.time() - gs.MAYBE_SENT_WINDOW_S - 60)
    assert gs._maybe_sent(fp) is False                       # a week on, a repeat is a normal send
    spent("unknown", time.time())
    assert gs._maybe_sent(fp) is True
    assert gs._maybe_sent("other-message") is False
    spent()                                                  # the owner sent it again since
    assert gs._maybe_sent(fp) is False


# ── (c) a channel reply from the poll loop (stub Telegram) ──────────────────

_UPDATE = {"update_id": 5001, "message": {"text": "where is the invoice", "chat": {"id": 4242}}}
_REPLY_KEY = "tg:Re: where is the invoice"

_CHANNEL = """
from agent_friday.services.channels import base
from agent_friday.services.channels.telegram_bridge import TelegramBridge
base._gate_channel_text = lambda text, ch: text
base.record_channel_send = lambda *a, **k: None
TelegramBridge._offset_file = lambda self: TMP / "telegram-offset.json"
def _call(self, method, params, timeout=30.0):
    srv = _json.loads((TMP / "telegram.json").read_text(encoding="utf-8"))
    if method == "getUpdates":
        off = int(params.get("offset") or 0)
        if off:                            # Telegram forgets what an offset past it confirmed
            srv["updates"] = [u for u in srv["updates"] if u["update_id"] >= off]
            (TMP / "telegram.json").write_text(_json.dumps(srv), encoding="utf-8")
        return {"ok": True, "result": srv["updates"]}
    service("tg:" + params["text"])
    return {"ok": True, "result": {"message_id": 7}}
TelegramBridge._call = _call
bot = TelegramBridge()
bot.on_message(lambda channel, chat, text: "Re: " + text)
bot._poll_once()                           # the poll loop's tick: read the message, answer it
"""


def _telegram_here(monkeypatch, tmp_path):
    from agent_friday.services.channels import base
    from agent_friday.services.channels.telegram_bridge import TelegramBridge
    monkeypatch.setattr(base, "_gate_channel_text", lambda text, ch: text)
    monkeypatch.setattr(base, "record_channel_send", lambda *a, **k: None)
    monkeypatch.setattr(TelegramBridge, "_offset_file", lambda self: tmp_path / "telegram-offset.json",
                        raising=False)

    def _call(self, method, params, timeout=30.0):
        path = tmp_path / "telegram.json"
        srv = json.loads(path.read_text(encoding="utf-8"))
        if method == "getUpdates":
            off = int(params.get("offset") or 0)
            if off:
                srv["updates"] = [u for u in srv["updates"] if u["update_id"] >= off]
                path.write_text(json.dumps(srv), encoding="utf-8")
            return {"ok": True, "result": srv["updates"]}
        _reach(tmp_path, "tg:" + params["text"])
        return {"ok": True, "result": {"message_id": 8}}
    monkeypatch.setattr(TelegramBridge, "_call", _call)
    bot = TelegramBridge()
    bot.on_message(lambda channel, chat, text: "Re: " + text)
    return bot


@pytest.mark.parametrize("kill", KILLS)
def test_a_channel_reply_killed_mid_send_is_not_sent_again_by_the_next_poll(tmp_path, boot, monkeypatch, kill):
    (tmp_path / "telegram.json").write_text(json.dumps({"updates": [_UPDATE]}), encoding="utf-8")
    _run_killed(tmp_path, _CHANNEL, kill)
    _cards, settled = boot()
    d = _interrupted("channel:telegram:send", kill)
    assert settled == [d["action_id"]]
    bot = _telegram_here(monkeypatch, tmp_path)
    bot._poll_once()                       # the restarted poll loop's first tick
    bot._poll_once()
    assert _reached(tmp_path) == (Counter({_REPLY_KEY: 1}) if kill == "after" else Counter())
    assert _decisions("channel:telegram:send") == [d]           # nothing was sent again
    _invariants(tmp_path)


# ── (d) a site publication (stub provider), left provider_pending ───────────

_SITE_ID = "site-" + "a" * 32
_OP_ID = "op-" + "b" * 32

_SITE = """
from agent_friday.services import sites_operations as so, publish_adapters
so._root = lambda: TMP / "sites"
so.sites_privacy.require_generation = lambda *a, **k: None
so.sites_privacy.require_operation = lambda *a, **k: None
so._durable = lambda: None
so.validate_site_owner = lambda site, context=None: site
so._build = lambda site, build_id: {"build_id": build_id, "status": "built"}
so._connection = lambda site, expected=None, **k: {"connection_id": "conn-r2"}
class _Bundle:
    def manifest(self):
        return {"index.html": "h1"}
so._bundle = lambda site, build, operation_id=None: (_Bundle(), {"ok": True})
def _publish(bundle, connection, hostname="", privacy_generation=None):
    service("site:@OP@")                   # the provider takes the upload
    return {"status": "provider_pending", "verified": False}
publish_adapters.publish_site = _publish
site = {"site_id": "@SITE@", "revision": 1, "conversation_id": "conv-r2", "project_id": None,
        "name": "Synthetic", "privacy_generation": 1}
(TMP / "sites" / "@SITE@").mkdir(parents=True, exist_ok=True)
(TMP / "sites" / "@SITE@" / "site.json").write_text(_json.dumps(site), encoding="utf-8")
op = {"operation_id": "@OP@", "site_id": "@SITE@", "site_revision": 1, "conversation_id": "conv-r2",
      "project_id": None, "action": "publish", "build_id": "build-" + "c" * 32, "privacy_generation": 1,
      "privacy_boot": "boot-r2", "connection": {"connection_id": "conn-r2"},
      "files": {"index.html": "h1"}, "created_at": time.time(), "status": "awaiting_approval",
      "verified": False}
op["plan_hash"] = so._plan_hash(op)
card = approvals.create_approval(kind=so.KIND, subject_type="site", subject_id="@OP@",
                                 title="Publish Synthetic", description="synthetic", force_gate=True,
                                 payload={"site_id": "@SITE@", "operation_id": "@OP@",
                                          "plan_hash": op["plan_hash"], "conversation_id": "conv-r2",
                                          "project_id": None, "build_id": op["build_id"]})
op["approval_id"] = card["approval_id"]
so._save_operation(op)
card = approvals._patch(card["approval_id"], status="approved", decided_at=time.time())
assert approvals.claim_for_execution(card["approval_id"])
op.update(status="applying", started_at=time.time(), executor_instance=so._INSTANCE)
so._save_operation(op)                     # as _on_decision does, before dispatch
print("AID=" + card["approval_id"], flush=True)
so._perform(card)                          # KILL=pending: it returns, left provider_pending
"""


@pytest.mark.parametrize("kill", KILLS + ("pending",))
def test_a_site_publication_killed_or_left_pending_closes_once_and_never_publishes_twice(
        tmp_path, boot, monkeypatch, kill):
    from agent_friday.services import publish_adapters
    from agent_friday.services import sites_operations as so
    aid = _run_killed(tmp_path, _SITE, kill, SITE=_SITE_ID, OP=_OP_ID)
    monkeypatch.setattr(so, "_root", lambda: tmp_path / "sites")
    monkeypatch.setattr(so.sites_privacy, "require_generation", lambda *a, **k: None)
    monkeypatch.setattr(so.sites_privacy, "require_operation", lambda *a, **k: None)
    started = []
    monkeypatch.setattr(so, "_perform", lambda record: started.append(record))
    monkeypatch.setattr(publish_adapters, "publish_site",
                        lambda *a, **k: _reach(tmp_path, "site:" + _OP_ID) or {"status": "provider_ready"})
    cards, settled = boot()
    assert aid not in cards                                     # a site claim ends its own way
    d = _decisions(so.KIND)
    assert len(d) == 1
    act = d[0]["action_id"]
    assert _words(act) == [G.UNCONFIRMED]                       # never executed on a guess
    assert settled == ([] if kill == "pending" else [act])
    path = tmp_path / "sites" / _SITE_ID / "operations" / (_OP_ID + ".json")
    assert json.loads(path.read_text(encoding="utf-8"))["decision_ref"]["action_id"] == act

    # Retry as the product would: an unresolved publication blocks a new write; the
    # owner's approval again and the hook fired again start nothing.
    rows = so._records({"site_id": _SITE_ID}, "operations", "op-*.json")
    assert [r["status"] for r in rows] == (["provider_pending"] if kill == "pending" else ["unknown"])
    assert approvals.decide(aid, "approve")["status"] == "approved"
    so._on_decision(approvals.get_approval(aid))
    assert started == []

    # The reconcile (deployment status) learns the final state from the provider.
    reached = _reached(tmp_path)
    op = so._operation({"site_id": _SITE_ID}, _OP_ID)
    op["status"] = "verified_live" if reached else "failed"
    kept = dict(op["decision_ref"])
    close = so._close_reconciled(op)
    so._save_operation(op)
    close()
    final = G.COMPLETED_VERIFIED if reached else G.FAILED
    assert _words(act) == [G.UNCONFIRMED, final]
    so._close_reconciled({"status": "verified_live", "decision_ref": kept})()   # a stale copy: nothing more
    assert so.expire_decisions(now=time.time() + so.DECISION_EXPIRE_S + 60) == 0
    assert _words(act) == [G.UNCONFIRMED, final]
    assert reached == (Counter() if kill == "before" else Counter({"site:" + _OP_ID: 1}))
    _invariants(tmp_path)


# ── (e) an approved item_actions batch ──────────────────────────────────────

_BATCH = """
from agent_friday.services import item_actions as ia
for _n in ("_post_back", "_tell_call", "_notify", "_tell_pages", "_tell_held"):
    setattr(ia, _n, lambda *a, **k: None)
DETAIL = {"handler": ia.HANDLER, "domain": "email", "action": "archive", "thread_ids": ["t1", "t2"],
          "conversation_id": ""}
NAME = ia._action_name("email", "archive")
card = approvals.create_approval(kind="governed_action", subject_type="external_action",
                                 subject_id=G.external_subject(NAME, DETAIL), title="Archive 2 conversations",
                                 description="synthetic", payload=DETAIL, force_gate=True)
card = approvals._patch(card["approval_id"], status="approved", decided_at=time.time())
print("AID=" + card["approval_id"], flush=True)
def _execute(detail, approval_id):
    service("batch:" + approval_id)
    return {"status": "complete", "summary": "Archived 2 conversations.", "receipt_id": "rcpt-r2"}
ia._execute = _execute
ia._on_decision(card)                      # the approval hook: claim, checkpoint, start the batch
time.sleep(60)                             # the batch thread dies with the process
"""


@pytest.mark.parametrize("kill", KILLS)
def test_an_approved_batch_killed_mid_run_is_not_run_again(tmp_path, boot, monkeypatch, kill):
    from agent_friday.services import item_actions as ia
    aid = _run_killed(tmp_path, _BATCH, kill)
    for name in ("_post_back", "_tell_call", "_notify", "_tell_pages", "_tell_held"):
        monkeypatch.setattr(ia, name, lambda *a, **k: None)
    monkeypatch.setattr(ia, "_execute", lambda detail, approval_id: _reach(tmp_path, "batch:" + approval_id)
                        or {"status": "complete", "summary": "Archived.", "receipt_id": "rcpt-again"})
    _cards, settled = boot()
    d = _interrupted(ia._action_name("email", "archive"), kill)   # unconfirmed once, never twice
    assert settled == [d["action_id"]]

    # The batch's own run markers: started on disk, never finished, so the restart found it.
    marks = [e["rec"] for e in L.entries([ia.RUN_KIND], newest_first=False)
             if e["rec"].get("approval_id") == aid]
    assert [m["state"] for m in marks] == ["started", "interrupted"]
    assert boot.batches == [marks[0]["run_id"]]
    assert marks[0]["decision_ref"]["action_id"] == d["action_id"] and marks[0]["count"] == 2
    card = approvals.get_approval(aid)
    assert card["outcome"] == "unknown" and card["consumed"] and not card.get("used_at")
    told = [p for p in boot.pushed if p.get("kind") == "approval_outcome_unknown"]
    assert len(told) == 1                                       # the owner is told once
    assert "2 conversations" in told[0]["body"] and "may have" in told[0]["body"]
    assert "Archive" not in told[0]["body"] + told[0]["title"]  # counts only: never the card's words
    # A second start finds nothing more: no second notice, no second outcome.
    assert card_recovery.settle_interrupted_batches(started_at=time.time() + 5) == []
    assert len([p for p in boot.pushed if p.get("kind") == "approval_outcome_unknown"]) == 1
    assert _words(d["action_id"]) == [G.UNCONFIRMED]

    assert approvals.decide(aid, "approve")["status"] == "approved"
    ia._on_decision(approvals.get_approval(aid))                # the hook fired again: the claim refuses
    assert ia.wait_for(aid, 0) is None
    assert _reached(tmp_path) == (Counter({"batch:" + aid: 1}) if kill == "after" else Counter())
    _invariants(tmp_path)


def test_an_interrupted_batch_whose_decision_was_not_swept_is_marked_unconfirmed_by_its_markers(
        tmp_path, boot, monkeypatch):
    """The batch's own markers close its decision when nothing else has (the decision sweep
    looks back only so far): unconfirmed, once, never executed."""
    from agent_friday.services import item_actions as ia
    aid = _run_killed(tmp_path, _BATCH, "after")
    monkeypatch.setattr(card_recovery, "settle_interrupted_decisions", lambda **k: [])
    _cards, settled = boot()
    assert settled == [] and len(boot.batches) == 1
    d = _interrupted(ia._action_name("email", "archive"), "after")
    assert approvals.get_approval(aid)["outcome"] == "unknown"
    assert card_recovery.settle_interrupted_batches(started_at=time.time() + 5) == []
    assert _words(d["action_id"]) == [G.UNCONFIRMED]
    assert _reached(tmp_path) == Counter({"batch:" + aid: 1})


def test_a_batch_that_finished_is_never_reported_interrupted(boot, monkeypatch):
    from agent_friday.services import item_actions as ia
    for name in ("_post_back", "_tell_call", "_notify", "_tell_pages", "_tell_held"):
        monkeypatch.setattr(ia, name, lambda *a, **k: None)
    monkeypatch.setattr(ia, "_execute", lambda detail, approval_id:
                        {"status": "complete", "summary": "Archived.", "receipt_id": "rcpt-ok"})
    detail = {"handler": ia.HANDLER, "domain": "email", "action": "archive", "thread_ids": ["t1"],
              "conversation_id": ""}
    assert ia._start(detail, "ap-r2-finished", None).wait(10)
    marks = [e["rec"]["state"] for e in L.entries([ia.RUN_KIND], newest_first=False)
             if e["rec"].get("approval_id") == "ap-r2-finished"]
    assert marks == ["started", "finished"]
    assert card_recovery.settle_interrupted_batches(started_at=time.time() + 5) == []
    assert not [p for p in boot.pushed if p.get("kind") == "approval_outcome_unknown"]


# ── a task waiting on a card whose outcome is not known ─────────────────────

@pytest.mark.parametrize("how", ["recovered", "stale"])
def test_a_waiting_task_whose_card_outcome_is_unknown_ends_outcome_unknown_not_failed(boot, monkeypatch, how):
    """Its approved action may have happened: the task is neither failed nor done. It ends
    'outcome unknown - check before retrying', and the audit never counts it as done."""
    from agent_friday.services import agent
    from agent_friday.services import task_states
    tid = "task-r2-unknown-" + how
    rec = approvals.create_approval(kind="tainted_action", subject_type="tool", subject_id="send_email-" + tid,
                                    title="Send the invoice to the client", description="synthetic",
                                    force_gate=True,
                                    payload={"tool": "send_email", "task_id": tid,
                                             "input": {"to": "a@example.invalid", "title": "Invoice 42"}})
    aid = rec["approval_id"]
    approvals._patch(aid, status="approved", decided_at=time.time() - 120, executing_at=time.time() - 60,
                     **({"executing_by": "approval_executor"} if how == "recovered" else {}))
    monkeypatch.setitem(agent.TASKS, tid, {"task_id": tid, "name": "Send the invoice",
                                           "status": agent.WAITING_APPROVAL, "created": time.time(),
                                           "log": []})
    if how == "recovered":
        cards, _settled = boot()                                # Friday stopped mid-run
        assert cards == [aid] and approvals.get_approval(aid)["outcome"] == "unknown"
        agent.sweep_waiting_tasks(force=True)                   # known now: no 30-minute wait
    else:
        # this process's start cannot be known, so which process claimed it cannot either
        monkeypatch.setattr(agent, "_process_started", lambda: (None, 0.0))
        agent.sweep_waiting_tasks(force=True)                   # claimed a minute ago: still running
        assert agent.TASKS[tid]["status"] == agent.WAITING_APPROVAL
        monkeypatch.setattr(agent, "_SETTLE_STALE_S", 0)        # it never reported back
        agent.sweep_waiting_tasks(force=True)
    t = agent.TASKS[tid]
    said = " ".join(t.get("log") or [])
    if how == "recovered":
        assert "Friday stopped while" in said
    else:                                                       # its own words: not "Friday stopped"
        assert "never reported how it ended" in said and "Friday stopped" not in said
    assert t["status"] == agent.OUTCOME_UNKNOWN == "outcome_unknown"
    assert not t["status"].startswith("failed") and t["verified"] is False
    assert "outcome unknown — check before retrying" in t["verification_evidence"]
    assert "did not go through" not in t["verification_evidence"]
    assert t["status"] not in outward_audit.DONE_STATUSES
    assert task_states.derive(t) == "outcome_unknown" and "outcome_unknown" in task_states.TERMINAL_STATES
    assert G.outcome_of_status(t["status"]) == G.UNCONFIRMED      # never executed
    assert tj.is_terminal(t["status"])                           # a restart never runs it again
    r = outward_audit.audit(now=time.time(), tasks=[t])
    assert r["counts"]["false_done"] == 0
    assert agent.settle_waiting_task(tid) is None                # ended once


# ── the recovery pieces themselves ──────────────────────────────────────────

def test_the_boot_sweep_judges_only_allowed_decisions_the_last_run_left_open(boot):
    closed = G.record_external("probe:closed", surface="test")
    G.close_external(closed, True)
    left = G.record_external("probe:left", surface="test")
    assert card_recovery.settle_interrupted_decisions(started_at=time.time() - 60) == []   # this run's own
    assert card_recovery.settle_interrupted_decisions(started_at=time.time() + 5) == [left["action_id"]]
    assert _words(left["action_id"]) == [G.UNCONFIRMED]
    assert _words(closed["action_id"]) == [G.EXECUTED]
    assert card_recovery.settle_interrupted_decisions(started_at=time.time() + 5) == []    # once


def test_one_approved_card_carried_out_twice_is_a_duplicate_key(boot):
    refs = [G.record_external("probe:send", surface="test", approval_id="ap-r2-twice", target="x", once=True),
            G.record_external("probe:send", surface="test", approval_id="ap-r2-twice", target="x", once=True),
            G.record_external("probe:send", surface="test", approval_id="ap-r2-other", target="x", once=True)]
    for ref in refs:
        G.close_external(ref, True)
    r = outward_audit.audit(now=time.time(), tasks=[])
    assert r["counts"]["duplicate_key"] == 1, r
    assert r["duplicate_key"][0]["count"] == 2
    assert sorted(r["duplicate_key"][0]["actions"]) == sorted(x["action_id"] for x in refs[:2])


def test_a_card_that_rightly_carries_two_decisions_is_not_a_duplicate_key(boot):
    # a site build: the operation's decision and its process launch's, same card, action and target
    for _ in range(2):
        G.close_external(G.record_external("probe:build", surface="test", approval_id="ap-r2-build",
                                           target="cb-1"), True)
    assert outward_audit.audit(now=time.time(), tasks=[])["counts"]["duplicate_key"] == 0


def test_the_boot_path_runs_the_sweep():
    import inspect
    from agent_friday.services import agent
    assert "settle_interrupted_decisions" in inspect.getsource(agent._restore_tasks_from_journal)


# ── review fixes: a claim this process holds, late reports, end states ──────

def _waiting_on_a_claimed_card(monkeypatch, tid, claimed_at):
    """A task waiting on an approved card the executor claimed at `claimed_at` and has not
    reported back on (no executing_by: card_recovery does not judge it)."""
    from agent_friday.services import agent
    rec = approvals.create_approval(kind="tainted_action", subject_type="tool", subject_id="send_email-" + tid,
                                    title="Send the invoice to the client", description="synthetic",
                                    force_gate=True,
                                    payload={"tool": "send_email", "task_id": tid,
                                             "input": {"to": "a@example.invalid", "title": "Invoice 42"}})
    approvals._patch(rec["approval_id"], status="approved", decided_at=claimed_at - 1, executing_at=claimed_at)
    monkeypatch.setitem(agent.TASKS, tid, {"task_id": tid, "name": "Send the invoice",
                                           "status": agent.WAITING_APPROVAL, "created": time.time(),
                                           "log": []})
    return rec["approval_id"]


def test_a_card_this_process_is_still_running_keeps_its_task_waiting_however_long(boot, monkeypatch):
    """Claimed by the executor in this process: it always records its end, so it is still
    running after 30 minutes. The task keeps waiting (said once), never outcome unknown."""
    from agent_friday.services import agent
    now = time.time()
    monkeypatch.setattr(agent, "_process_started", lambda: (now - 7200, 2.0))
    tid = "task-r2-long"
    _waiting_on_a_claimed_card(monkeypatch, tid, now - 3600)    # an hour ago, in this process
    for _ in range(3):
        agent.sweep_waiting_tasks(force=True)
        assert agent.TASKS[tid]["status"] == agent.WAITING_APPROVAL
    notes = [line for line in agent.TASKS[tid]["log"] if line.startswith("Still running")]
    assert len(notes) == 1 and "keeps waiting" in notes[0]
    assert agent.settle_waiting_task(tid) is None


def test_a_claim_from_before_this_process_ends_outcome_unknown_saying_friday_stopped(boot, monkeypatch):
    from agent_friday.services import agent
    now = time.time()
    monkeypatch.setattr(agent, "_process_started", lambda: (now, 2.0))
    tid = "task-r2-before"
    _waiting_on_a_claimed_card(monkeypatch, tid, now - 60)      # claimed by a process that is gone
    agent.sweep_waiting_tasks(force=True)                       # no 30-minute wait: it cannot report
    t = agent.TASKS[tid]
    assert t["status"] == agent.OUTCOME_UNKNOWN and t["verified"] is False
    assert any("Friday stopped while" in line for line in t["log"])


@pytest.mark.parametrize("ok", [True, False])
def test_a_late_report_moves_an_outcome_unknown_task_on_and_its_workflow_with_it(boot, monkeypatch, ok):
    """The executor reports after the task ended outcome unknown: the report wins. Ran ->
    complete (a workflow continues with its next step); did not go through -> failed."""
    from agent_friday.services import agent
    monkeypatch.setattr(agent, "_process_started", lambda: (None, 0.0))
    monkeypatch.setattr(agent, "_SETTLE_STALE_S", 0)
    tid = "task-r2-late-%s" % ok
    aid = _waiting_on_a_claimed_card(monkeypatch, tid, time.time() - 60)
    assert agent.settle_waiting_task(tid) == agent.OUTCOME_UNKNOWN
    assert agent.settle_waiting_task(tid) is None               # nothing new: it stays as it is
    advanced = []
    monkeypatch.setattr(agent, "_advance_task_chain", lambda task_id, result: advanced.append(task_id))
    approvals.mark_used(aid, "approval_executor", {"ok": ok, "took_ms": 5, "result": "synthetic"})
    want = "complete" if ok else "failed"
    assert agent.settle_waiting_task(tid) == want
    t = agent.TASKS[tid]
    assert t["status"] == want and t["verified"] is ok
    assert advanced == ([tid] if ok else [])
    assert any("reported back after this task had ended" in line for line in t["log"])
    assert agent.settle_waiting_task(tid) is None               # settled once


def test_a_workflow_whose_step_ended_outcome_unknown_ends_the_scheduled_run_without_a_retry(monkeypatch):
    from agent_friday.services import agent
    from agent_friday.services import scheduler
    st = {"state": "outcome_unknown", "run_id": "wfr_r2", "conversation_id": "conv-r2", "project_id": None,
          "schedule_id": "sch-r2", "verification": {"status": "pending"}, "outputs": [],
          "delivery": {"status": "pending"},
          "steps": [{"index": 0, "name": "Send", "status": "outcome_unknown", "result_tail": ""}]}
    patched = []
    monkeypatch.setattr(agent, "run_workflow_chain", lambda slug, **k: "task-r2-wf")
    monkeypatch.setattr(agent, "_task_run_context", lambda tid: {})
    monkeypatch.setattr(agent, "chain_run_status", lambda slug, run_id=None: dict(st, run_id=run_id))
    monkeypatch.setattr(scheduler, "_patch_record", lambda sid, **f: patched.append(f))
    out =scheduler._run_workflow({"id": "sch-r2", "_active_run_id": "wfr_r2"}, {"ref": "send-invoice"})
    assert isinstance(out, scheduler.WorkflowRunResult)         # returned, not raised: never retried
    assert out.status == "outcome_unknown" and out.run_id == "wfr_r2"
    assert patched[-1]["last_workflow_context"]["run_id"] == "wfr_r2"


def test_outcome_unknown_is_an_end_state_everywhere_a_task_or_run_is_listed():
    import inspect
    from agent_friday.routes import tasks as task_routes
    from agent_friday.services import desktop_card_sources, heartbeat, workflow_overview
    assert "outcome_unknown" in heartbeat._TERMINAL
    assert "outcome_unknown" in task_routes.ORB_TERMINAL_STATES
    assert workflow_overview._RESULT["outcome_unknown"] == "outcome unknown"
    last = workflow_overview._chain_last({"state": "outcome_unknown", "steps": [
        {"index": 0, "name": "Send", "status": "outcome_unknown", "ended": time.time(),
         "reason": "check before retrying"}]})
    assert last["status"] == "outcome unknown" and "Step 1 (Send)" in last["summary"]
    assert inspect.getsource(desktop_card_sources.automatic).count('"outcome_unknown"') == 2


def test_notice_registry_ids_are_unique_and_card_recovery_has_its_own():
    from agent_friday.services import notice_registry as reg
    ids = [e.id for e in reg._ALL]
    assert len(ids) == len(set(ids)), sorted(i for i in ids if ids.count(i) > 1)
    assert reg.get("CR1").kind == "calendar_reminder"
    for eid, where in (("CU1", "card_recovery.py:_announce"), ("CU2", "card_recovery.py:_announce_batches")):
        em = reg.get(eid)
        assert em is not None and em.where.endswith(where) and em.kind == "approval_outcome_unknown"


def test_card_recovery_pushes_under_its_own_emitters_and_keys(boot):
    from agent_friday.services import notice_registry as reg
    card_recovery._announce([{"approval_id": "ap-r2-x", "title": "Send the invoice"}])
    card_recovery._announce_batches([{"run_id": "brun_r2", "domain": "email", "count": 2}])
    cards, batches = boot.pushed[-2:]
    assert (cards["emitter"], batches["emitter"]) == ("CU1", "CU2")
    assert cards["dedupe_key"] == reg.get("CU1").key.format(approval_ids="ap-r2-x")
    assert batches["dedupe_key"] == reg.get("CU2").key.format(run_ids="brun_r2")


def _batch_detail():
    from agent_friday.services import item_actions as ia
    return {"handler": ia.HANDLER, "domain": "email", "action": "archive", "thread_ids": ["t1"],
            "conversation_id": ""}


def _quiet_batches(monkeypatch, told=None):
    from agent_friday.services import item_actions as ia
    for name in ("_post_back", "_tell_call", "_tell_pages", "_tell_held"):
        monkeypatch.setattr(ia, name, lambda *a, **k: None)
    monkeypatch.setattr(ia, "_notify", lambda *a, **k: (told.append(a) if told is not None else None))
    return ia


def test_a_batch_whose_started_marker_cannot_be_written_does_not_start(boot, monkeypatch):
    told, ran = [], []
    ia = _quiet_batches(monkeypatch, told)
    monkeypatch.setattr(ia, "_execute", lambda detail, approval_id: ran.append(approval_id) or
                        {"status": "complete", "summary": "Archived.", "receipt_id": "rcpt-no"})
    real = L.append

    def append(kind, rec, **kw):
        if kind == ia.RUN_KIND:
            raise L.LedgerError("disk full")
        return real(kind, rec, **kw)
    monkeypatch.setattr(L, "append", append)
    card = approvals.create_approval(kind="governed_action", subject_type="external_action",
                                     subject_id="r2-nomark", title="Archive 1 conversation",
                                     description="synthetic", payload=_batch_detail(), force_gate=True)
    aid = card["approval_id"]
    ref = G.record_external("probe:batch", surface="test", approval_id=aid)
    assert ia._start(_batch_detail(), aid, ref).wait(10)
    assert ran == []                                             # nothing was touched
    assert _words(ref["action_id"]) == [G.FAILED]                # its decision closed failed
    used = approvals.get_approval(aid)
    assert used.get("used_at") and used["used_detail"]["ok"] is False
    assert told and told[-1][0] == "Not done" and "did not start" in told[-1][1]
    assert ia.wait_for(aid, 0)["status"] == "failed"


def test_a_batch_writes_finished_only_after_its_decision_and_card_are_closed(boot, monkeypatch):
    ia = _quiet_batches(monkeypatch)
    order = []
    monkeypatch.setattr(ia, "_execute", lambda detail, approval_id:
                        {"status": "complete", "summary": "Archived.", "receipt_id": "rcpt-order"})
    real_mark, real_close = ia._run_mark, ia._close_batch
    monkeypatch.setattr(ia, "_run_mark", lambda state, *a, **k: order.append(state) or real_mark(state, *a, **k))
    monkeypatch.setattr(ia, "_close_batch", lambda *a, **k: order.append("close") or real_close(*a, **k))
    real_used = approvals.mark_used
    monkeypatch.setattr(approvals, "mark_used", lambda *a, **k: order.append("used") or real_used(*a, **k))
    assert ia._start(_batch_detail(), "ap-r2-order", None).wait(10)
    assert order == ["started", "close", "used", "finished"]


def _started_marker(ia, rid, aid="", ref=None, **extra):
    rec = {"run_id": rid, "state": "started", "approval_id": aid, "domain": "email", "action": "archive",
           "count": 1, "at": time.time(), **extra}
    if ref:
        rec["decision_ref"] = {k: ref.get(k) for k in ia._RUN_REF_KEYS}
    L.append(ia.RUN_KIND, rec, durable=True)


def _marks(ia, rid):
    return [e["rec"]["state"] for e in L.entries([ia.RUN_KIND], newest_first=False)
            if e["rec"].get("run_id") == rid]


def test_a_run_that_recorded_its_end_but_not_its_finished_marker_is_never_told(boot):
    """Killed between closing the card and the finished marker: it finished. Marked so, once."""
    from agent_friday.services import item_actions as ia
    card = approvals.create_approval(kind="governed_action", subject_type="external_action",
                                     subject_id="r2-ended", title="Archive 1 conversation",
                                     description="synthetic", payload=_batch_detail(), force_gate=True)
    aid = card["approval_id"]
    ref = G.record_external("probe:batch", surface="test", approval_id=aid)
    G.close_external(ref, True)
    approvals.mark_used(aid, "item_actions", {"ok": True})
    _started_marker(ia, "brun_r2_ended", aid, ref)
    assert card_recovery.settle_interrupted_batches(started_at=time.time() + 5) == []
    assert _marks(ia, "brun_r2_ended") == ["started", "finished"]
    assert not approvals.get_approval(aid).get("outcome")
    assert _words(ref["action_id"]) == [G.EXECUTED]
    assert not [p for p in boot.pushed if p.get("kind") == "approval_outcome_unknown"]


def test_a_run_whose_process_is_still_alive_is_not_called_interrupted(boot):
    psutil = pytest.importorskip("psutil")
    import os
    from agent_friday.services import item_actions as ia
    me = psutil.Process()
    _started_marker(ia, "brun_r2_alive", pid=os.getpid(), pid_started=me.create_time())
    _started_marker(ia, "brun_r2_reused", pid=os.getpid(), pid_started=me.create_time() - 3600)
    assert card_recovery.settle_interrupted_batches(started_at=time.time() + 5) == ["brun_r2_reused"]
    assert _marks(ia, "brun_r2_alive") == ["started"]             # left to the process running it
    assert _marks(ia, "brun_r2_reused") == ["started", "interrupted"]


def test_the_ledger_page_names_an_approved_change_run():
    from agent_friday.routes import ledger as page
    assert page.KIND_LABELS["item_batch_run"] == "Approved change run"
    line = page.plain_line({"kind": "item_batch_run", "rec": {"run_id": "brun_r2", "state": "interrupted",
                                                              "domain": "email", "count": 2}})
    assert line.startswith("Approved email change (2 items)") and "cut off" in line
    failed = page.plain_line({"kind": "item_batch_run", "rec": {"state": "finished", "ok": False, "count": 1}})
    assert failed == "Approved change (1 item) finished without going through."
