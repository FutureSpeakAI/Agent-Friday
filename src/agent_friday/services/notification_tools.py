"""The notification tray, by voice and chat: the notifications tool.

One tool, declared once in the text registry and shared into voice
(voice_engine._VOICE_SHARED_TOOLS): "what's in my notifications", "clear
them", "mute these", "unmute the scheduler ones". It uses the same engine
calls as the tray's buttons. Approvals are never cleared or muted by it.
Replies are written for the ear.
"""
from __future__ import annotations

ACTIONS = ("summary", "clear", "mute", "unmute")


def _engine():
    from agent_friday import notifications_engine as ne
    return ne


def _summary() -> str:
    ne = _engine()
    cards = ne.list_notifications(limit=200)
    if not cards:
        return "Your notifications are clear."
    needs = [c for c in cards if c.get("tier", "needs_you") == "needs_you"]
    fyi = [c for c in cards if c.get("tier") == "fyi"]

    def said(c):
        n = int(c.get("count") or 1)
        return str(c.get("title") or "").strip() + (" (%d times)" % n if n > 1 else "")
    parts = []
    if needs:
        parts.append("%d need you: %s." % (len(needs), "; ".join(said(c) for c in needs[:5])))
    if fyi:
        parts.append("%d for your information: %s." % (len(fyi), "; ".join(said(c) for c in fyi[:4])))
    return " ".join(parts)


def handle(inp: dict) -> str:
    inp = inp if isinstance(inp, dict) else {}
    action = str(inp.get("action") or "summary").strip().lower()
    if action not in ACTIONS:
        return "notifications error: action must be one of " + ", ".join(ACTIONS)
    ne = _engine()
    if action == "summary":
        return _summary()
    if action == "clear":
        n = ne.dismiss_all()
        waiting = sum(1 for c in ne.list_notifications(limit=200)
                      if c.get("kind") == "approval_pending")
        tail = (" %d approval%s still waiting for you." % (waiting, "" if waiting == 1 else "s")
                if waiting else "")
        return "Cleared %d notification%s.%s" % (n, "" if n == 1 else "s", tail)
    from agent_friday.services import notification_policy as pol
    kind, source = str(inp.get("kind") or ""), str(inp.get("source") or "")
    if action == "unmute":
        if not kind:
            return "Which kind should I unmute? Settings, Notifications lists them."
        ne.unmute(kind, source)
        return "Unmuted. Those will show again."
    # mute: a named kind, or "these" = the kinds of the FYI cards showing now.
    if kind:
        targets = {(kind, source)}
    else:
        targets = {(c.get("kind"), c.get("source")) for c in ne.list_notifications(limit=200)
                   if c.get("tier") == "fyi"}
    targets = {(k, s) for k, s in targets if k and not pol.is_approval(k)}
    if not targets:
        return "There is nothing to mute there. Approvals always reach you."
    for k, s in targets:
        ne.mute(k, s)
    return ("Muted %d kind%s. They go to the activity log now; you can unmute them in "
            "Settings, Notifications. Approvals still always reach you."
            % (len(targets), "" if len(targets) == 1 else "s"))


def _tool_notifications(inp):
    return handle(inp if isinstance(inp, dict) else {})


TOOLS = [
    {"name": "notifications",
     "description": (
         "The owner's notification tray. action=summary says what is in it (what needs "
         "the owner, then what is just for information); clear clears it (approvals "
         "stay, they are decisions); mute stops a kind of notification reaching the tray "
         "(it goes to the activity log instead) - give kind/source, or none to mute the "
         "kinds of the information-only cards showing now ('mute these'); unmute "
         "reverses it. Approvals can never be muted. Say the result in one or two plain "
         "sentences."),
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": list(ACTIONS)},
         "kind": {"type": "string"},
         "source": {"type": "string"}},
         "required": ["action"]}},
]
RINGS = {"notifications": 1}
HANDLERS = {"notifications": _tool_notifications}


def register(claude_tools, handlers, rings):
    known = {t["name"] for t in claude_tools}
    for t in TOOLS:
        if t["name"] not in known:
            claude_tools.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)
