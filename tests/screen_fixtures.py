"""Shared helpers for the See & Touch tests: a Message Center stage, a page that
reports it, a page that answers commands, and a fake Gmail behind organize_email."""
from __future__ import annotations

from agent_friday.services import desktop_bus

ACCOUNTS = [
    {"id": "acct_work", "label": "Work", "email": "me@work.example", "services": {"gmail": True},
     "mail": {"read": True, "modify": True}, "health": {"healthy": True}},
]


def ref(i, acct="acct_work"):
    return "mail:%s:t%d" % (acct, i)


def item(i, *, lane="all", category="primary", unread=False, bulk=False, domain="friends.example",
         age_h=5.0, awaiting=False, starred=False, title=None, who=None, acct="acct_work"):
    return {"ref": ref(i, acct), "n": i,
            "facets": {"lane": lane, "category": category, "unread": unread, "bulk": bulk,
                       "from_domain": domain, "age_h": age_h, "awaiting": awaiting, "starred": starred},
            "title": title if title is not None else "Subject %d" % i,
            "who": who if who is not None else "Sender %d" % i}


def newsletter_stage(selected=(), rev=1, extra_items=()):
    """Six rows: 1-3 newsletters from substack, 4 a promotion, 5 and 6 people."""
    items = [item(1, lane="subscriptions", bulk=True, domain="substack.com", unread=True),
             item(2, lane="subscriptions", bulk=True, domain="substack.com"),
             item(3, lane="subscriptions", bulk=True, domain="news.example", age_h=100),
             item(4, category="promotions", domain="shop.example"),
             item(5, domain="editor.example", awaiting=True, unread=True),
             item(6, lane="family", domain="home.example")]
    items += list(extra_items)
    refs = [r if isinstance(r, str) else ref(r) for r in selected]
    return {"workspace": "messages", "rev": rev, "items": items, "loaded": len(items),
            "total_hint": len(items),
            "selection": {"id": "sel_t1", "refs": refs, "count": len(refs), "label": "Newsletters",
                          "source": "friday", "beyond_loaded": 0},
            "filters": [], "focus": None, "open": None, "cursor": None, "fields": [], "held": []}


def report(stage, cid="pg1", focused=True, kind="desktop"):
    desktop_bus.report_state(cid, {"kind": kind, "focused": focused, "visible": True, "stage": stage})


class Page:
    """Stands in for the desktop page: records every command and answers with `answer`."""

    def __init__(self, monkeypatch, answer=None, stage_on_ask=None):
        self.sent, self.pushed = [], []
        self.answer = answer if answer is not None else {"ok": True, "applied": 0, "missing": 0, "rev": 2}
        self.stage_on_ask = stage_on_ask
        monkeypatch.setattr(desktop_bus, "send", self._send)
        monkeypatch.setattr(desktop_bus, "push", self._push)

    def _send(self, actions, verify=None, timeout=6.0):
        self.sent.append(actions)
        kind = actions[0].get("type")
        if kind == "stage_request":
            if self.stage_on_ask is None:
                return {"delivered": True, "acked": False, "ack": {}}
            report(self.stage_on_ask)
            return {"delivered": True, "acked": True, "ack": {"stage_seen": True}}
        ans = self.answer(actions) if callable(self.answer) else self.answer
        return {"delivered": True, "acked": True, "ack": {"result": ans}, "page": "desktop"}

    def _push(self, actions):
        self.pushed.append(actions)
        return True

    def types(self):
        return [a[0].get("type") for a in self.sent]


class FakeGmail:
    def __init__(self):
        self.found = {"acct_work": ["t1", "t2", "t3", "t4", "t5", "t6"]}
        self.labels = {"acct_work": [{"id": "Label_7", "name": "Receipts"}]}
        self.calls = []

    def search(self, acct, q, limit):
        self.calls.append(("search", acct, q))
        return list(self.found.get(acct, []))[:limit], False

    def heads(self, acct, tids):
        return {t: {"subject": "Subject %s" % t, "sender": "Sender", "date": ""} for t in tids}

    def apply_action(self, acct, tids, action):
        self.calls.append(("apply", acct, tuple(tids), action))
        return {"changed": {t: {"added": [], "removed": ["INBOX"]} for t in tids}, "failed": {}}

    def modify_threads(self, acct, tids, add=(), remove=(), _allow=()):
        self.calls.append(("modify", acct, tuple(tids), tuple(add), tuple(remove)))
        return {"changed": {t: {"added": list(add), "removed": list(remove)} for t in tids}, "failed": {}}

    def undo(self, acct, changed):
        self.calls.append(("undo", acct, dict(changed)))
        return {"failed": {}}

    def list_labels(self, acct):
        return list(self.labels.get(acct, []))

    def create_label(self, acct, name):
        lab = {"id": "Label_new", "name": name}
        self.labels.setdefault(acct, []).append(lab)
        return lab

    def changes(self):
        return [c for c in self.calls if c[0] in ("apply", "modify", "undo")]


def install_fake_gmail(tmp_path, monkeypatch):
    """organize_email's mail backend, an approvals file and a receipts journal in tmp_path."""
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import action_journal as journal
    from agent_friday.services import approvals as ap
    from agent_friday.services import dissent_gate as dg
    from agent_friday.services import gmail_mailbox as gm
    from agent_friday.services import google_accounts as G
    from agent_friday.services import item_actions as ia
    fake = FakeGmail()
    monkeypatch.setattr(G, "list_accounts", lambda: [dict(a) for a in ACCOUNTS])
    monkeypatch.setattr(gm, "can_modify", lambda acct: True)
    monkeypatch.setattr(ia, "_search_threads", fake.search)
    monkeypatch.setattr(ia, "_thread_heads", fake.heads)
    for name in ("apply_action", "modify_threads", "undo", "list_labels", "create_label"):
        monkeypatch.setattr(gm, name, getattr(fake, name))
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    monkeypatch.setattr(journal, "_path", lambda: tmp_path / "receipts.jsonl")
    journal.reset()
    return fake
