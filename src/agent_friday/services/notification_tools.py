"""The notification tray, by voice and chat: the notifications tool.

One tool, declared once in the text registry and shared into voice
(voice_engine._VOICE_SHARED_TOOLS): "what's in my notifications", "clear
them", "mute these", "unmute the scheduler ones", "read me the memory
proposals", "clear the scheduler group". It uses the same engine calls as the
tray's buttons. Approvals are never cleared or muted by it; clearing a group
leaves its approvals waiting. Replies are written for the ear.
"""
from __future__ import annotations

ACTIONS = ("summary", "clear", "mute", "unmute", "groups", "read_group", "clear_group")


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


def _said(c) -> str:
    n = int(c.get("count") or 1)
    return str(c.get("title") or "").strip() + (" (%d times)" % n if n > 1 else "")


def _find_group(ne, name: str):
    """The group the owner named: its key, or words from its title, kind or
    source ("memory", "scheduler"). The first match in tray order."""
    name = str(name or "").strip().lower()
    groups = ne.groups(limit=200)
    if not name:
        return None, groups
    for g in groups:
        if str(g.get("key") or "").lower() == name:
            return g, groups
    for g in groups:
        hay = " ".join(str(g.get(k) or "") for k in ("key", "title", "kind", "source")).lower()
        if name in hay or ("memory" in name and g.get("memory")):
            return g, groups
    return None, groups


def _groups_said(groups) -> str:
    if not groups:
        return "Your notifications are clear."
    parts = []
    for g in groups[:6]:
        k = len(g.get("items") or [])
        parts.append(str(g.get("title") or "") + (" (%d cards)" % k if k > 1 else ""))
    return "%d group%s: %s." % (len(groups), "" if len(groups) == 1 else "s", "; ".join(parts))


def handle(inp: dict) -> str:
    inp = inp if isinstance(inp, dict) else {}
    action = str(inp.get("action") or "summary").strip().lower()
    if action not in ACTIONS:
        return "notifications error: action must be one of " + ", ".join(ACTIONS)
    ne = _engine()
    if action == "summary":
        return _summary()
    if action == "groups":
        return _groups_said(ne.groups(limit=200))
    if action in ("read_group", "clear_group"):
        g, groups = _find_group(ne, inp.get("group") or inp.get("kind") or inp.get("source"))
        if g is None:
            return "Which group? " + _groups_said(groups)
        items = g.get("items") or []
        if action == "read_group":
            said = "; ".join(_said(c) for c in items[:8])
            more = " And %d more." % (len(items) - 8) if len(items) > 8 else ""
            tail = (" Each one is yours to keep or skip." if g.get("memory") else "")
            return "%s: %s.%s%s" % (g.get("title") or "That group", said, more, tail)
        n = ne.dismiss_group(g.get("key"))
        waiting = int(g.get("approvals") or 0)
        tail = (" %d approval%s in it still waiting for you." % (waiting, "" if waiting == 1 else "s")
                if waiting else "")
        return "Cleared %d notification%s from that group.%s" % (n, "" if n == 1 else "s", tail)
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
         "reverses it. groups lists the tray's groups (by job, then kind, then source); "
         "read_group reads one group's cards (name it with group, e.g. 'memory' or "
         "'scheduler'); clear_group clears one group, leaving its approvals waiting. "
         "Approvals can never be muted. Say the result in one or two plain sentences."),
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": list(ACTIONS)},
         "kind": {"type": "string"},
         "source": {"type": "string"},
         "group": {"type": "string"}},
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
