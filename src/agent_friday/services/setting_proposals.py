"""Settings by sentence: a change to a setting is shown as a diff and waits for the owner's Yes.

HIG section 6.3 and 6.4: the voice or chat tool that changes a setting says what it would change (the row,
the old value, the new one, what it means for the owner) and raises ONE card. Nothing is applied before the
Yes; a conditional yes ("yes if it is cheaper") is not a Yes (the card's own decision path rules that, as for
every card); on the Yes the change is made, with a line of provenance and thirty days of Undo.

How it fits the tools. A tool that changes a setting asks `hold(...)` at the moment before it would write.
`hold` returns None when the write may go ahead (the owner already approved exactly this, or it is the
approved card being carried out) and a line to say otherwise: SETTING_NEEDS_YES (a card was raised) or
SETTING_UNCHANGED (it already reads that way). On the Yes the card's decision hook runs the same tool again
with the approval in effect, so there is one write path per setting, never two.

What is gated and what is not (the owner's rulings, per tool, are in the table below): changing how a
setting reads. Not gated: showing something (the start screen's cluster), a one-off measurement the owner asks
for (the hologram's calibrate), and standing back for a call now (a act, not a setting).
"""
from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
from pathlib import Path

_log = logging.getLogger(__name__)

HANDLER = "setting_change"
APPROVAL_KIND = "governed_action"
UNDO_DAYS = 30
HISTORY_MAX = 200

_APPROVED: contextvars.ContextVar[bool] = contextvars.ContextVar("setting_change_approved", default=False)
_LOCK = threading.Lock()


def approved() -> bool:
    """True while the tool is carrying out a change the owner approved on its card."""
    return _APPROVED.get()


class Refused(Exception):
    pass


# ── The rows ────────────────────────────────────────────────────────────────

#: path -> (the tool that writes it, plain label, what it means, the settings keys it writes)
#: One home per setting (HIG 6.2): these paths are the Settings rows' keys (`settings.<page>.<key>`).
TOOL_OF = {
    "settings.models.chat_model": "switch_model",
    "settings.display.workspace_layout": "set_workspace_layout",
    "settings.display.start_screen": "show_my_day",
    "settings.accessibility.big_mode": "big_mode",
    "settings.hologram.window": "hologram_window",
    "settings.calls.stand_back": "call_mode",
    "settings.podcasts.format": "podcast_format",
    "settings.podcasts.length": "podcast_length",
    "settings.laya.live_reflex": "live_laya",
    "settings.display.dock": "dock_visibility",
}
KEYS_OF = {
    "switch_model": ("capability_routing", "orchestrator_model"),
    "set_workspace_layout": ("workspace_layouts",),
    "show_my_day": ("landing_mode",),
    "big_mode": ("big_mode",),
    "hologram_window": ("tracking",),
    "call_mode": ("call_mode",),
    "podcast_format": ("podcasts",),
    "podcast_length": ("podcasts",),
    "live_laya": ("voice_live_reflex",),
    "dock_visibility": ("dock_custom",),
}
LABELS = {
    "switch_model": "the chat model",
    "set_workspace_layout": "a workspace's layout",
    "show_my_day": "when the start screen shows your day",
    "big_mode": "big mode",
    "hologram_window": "the hologram window",
    "call_mode": "standing back for calls",
    "podcast_format": "who hosts a show",
    "podcast_length": "how long a show runs",
    "live_laya": "Laya acting on her own during cloud calls",
    "dock_visibility": "which workspaces the dock shows",
}


def _path_of(tool: str) -> str:
    for p, t in TOOL_OF.items():
        if t == tool:
            return p
    return "settings." + tool


def _tell_pages(path: str) -> None:
    """An open Settings page re-reads its rows and its provenance lines."""
    try:
        from agent_friday.services import notice_registry as _nr
        _nr.bus_broadcast("E74", {"type": "settings_changed", "path": path}, kind="desktop")
    except Exception:
        pass


# ── The record of changes (thirty days of Undo) ─────────────────────────────

def _store() -> Path:
    from agent_friday import paths
    return paths.friday_home() / "setting_changes.json"


def _read() -> list:
    try:
        return json.loads(_store().read_text(encoding="utf-8")).get("changes") or []
    except Exception:
        return []


def _write(rows: list) -> None:
    p = _store()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"changes": rows[-HISTORY_MAX:]}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _snapshot(keys) -> dict:
    from agent_friday.core import _load_settings
    cur = _load_settings() or {}
    return {k: cur.get(k) for k in keys}


def record_change(tool: str, *, label: str, old: str, new: str, snapshot: dict, by: str, card_id: str = "") -> dict:
    row = {"id": "chg_%d_%s" % (int(time.time() * 1000), len(_read())), "tool": tool, "path": _path_of(tool),
           "label": label, "old": str(old)[:200], "new": str(new)[:200], "snapshot": snapshot,
           "by": by, "card_id": card_id, "at": time.time(), "undone": False}
    with _LOCK:
        rows = _read()
        rows.append(row)
        _write(rows)
    return row


def changes(path: str = "", limit: int = 20) -> list:
    """The recent changes (newest first), each with whether Undo still holds. The snapshot stays out of it."""
    now = time.time()
    out = []
    for r in reversed(_read()):
        if path and r.get("path") != path:
            continue
        out.append({k: r.get(k) for k in ("id", "path", "label", "old", "new", "by", "at", "undone")}
                   | {"undoable": (not r.get("undone")) and now - float(r.get("at") or 0) <= UNDO_DAYS * 86400})
        if len(out) >= limit:
            break
    return out


def last_change(path: str) -> dict | None:
    rows = changes(path, 1)
    return rows[0] if rows else None


def undo(change_id: str = "", path: str = "") -> dict:
    """Put a setting back as it was before a change, within thirty days. Only the newest change of a row can be
    undone, so an older Undo never overwrites a later choice."""
    from agent_friday.core import _save_settings
    with _LOCK:
        rows = _read()
        target = None
        for r in reversed(rows):
            if (change_id and r.get("id") == change_id) or (not change_id and path and r.get("path") == path and not r.get("undone")):
                target = r
                break
        if target is None:
            return {"ok": False, "text": "There is no change to undo."}
        if target.get("undone"):
            return {"ok": False, "text": "That change was already undone."}
        if time.time() - float(target.get("at") or 0) > UNDO_DAYS * 86400:
            return {"ok": False, "text": "That change is more than %d days old, so it can no longer be undone." % UNDO_DAYS}
        later = [r for r in rows if r.get("path") == target.get("path") and r.get("at", 0) > target.get("at", 0) and not r.get("undone")]
        if later:
            return {"ok": False, "text": "It has been changed again since, so undoing this one would overwrite the newer choice."}
        _save_settings({k: v for k, v in (target.get("snapshot") or {}).items() if v is not None})
        target["undone"] = True
        _write(rows)
    _tell_pages(target.get("path") or "")
    return {"ok": True, "text": "Put back: %s is %s again." % (target.get("label"), target.get("old")), "path": target.get("path")}


# ── The hold ────────────────────────────────────────────────────────────────

def hold(tool: str, inp: dict, *, old: str, new: str, consequence: str = "", label: str = "",
         run_as: str = "") -> str | None:
    """Ask before a tool writes a setting. None means go ahead and write; a string is what to say instead.

    `old` and `new` are the words the owner will read ("Gemma 4 12B", "off"). The card lists exactly that
    change. The tool is re-run on the Yes with the approval in effect; the change is recorded as it is made.
    `run_as` names the model tool the Yes re-runs when the row has no tool of its own (the dock rows are
    written by set_setting itself); `tool` still names the row for its label, keys and record."""
    label = label or LABELS.get(tool, tool)
    keys = KEYS_OF.get(tool, ())
    if _APPROVED.get():
        record_change(tool, label=label, old=old, new=new, snapshot=_snapshot(keys), by="Friday, by a proposal you accepted")
        return None
    if str(old) == str(new):
        return "SETTING_UNCHANGED: %s already reads %s." % (label, new)
    from agent_friday.governance import action_gate
    from agent_friday.services import agent as _ag
    detail = {"handler": HANDLER, "tool": run_as or tool, "input": dict(inp or {}), "path": _path_of(tool), "label": label,
              "old": str(old)[:200], "new": str(new)[:200],
              "conversation_id": _ag._CURRENT_CONVERSATION.get() or ""}
    said = "%s: %s → %s." % (label[:1].upper() + label[1:], old, new)
    v = action_gate.authorize_external(
        "change setting: " + detail["path"], detail, requested_by="friday",
        title=("Friday wants to change %s" % label)[:200],
        description=("Nothing changes until you approve. %s %s You can undo it for %d days.\n"
                     % (said, consequence, UNDO_DAYS)).strip()[:1800],
        action_description=("change the setting %s from %s to %s" % (label, old, new))[:1000])
    if v.action == "deny":
        return "SETTING_FAIL: Friday's governance check held this: %s" % v.reason
    if v.action == "allow":
        # An approved, unused card for exactly this change existed and the check used it: carry it out now.
        record_change(tool, label=label, old=old, new=new, snapshot=_snapshot(keys), by="Friday, by a proposal you accepted")
        return None
    return ("SETTING_NEEDS_YES: %s %s Nothing has changed. The card is waiting for the owner's own yes; "
            "a conditional yes does not count." % (said, consequence)).strip()


# ── On the Yes ──────────────────────────────────────────────────────────────

def _on_decision(record: dict) -> None:
    detail = record.get("payload") or {}
    if detail.get("handler") != HANDLER:
        return
    if (record.get("status") or "").lower() != "approved":
        return
    from agent_friday.governance import action_gate
    from agent_friday.services import approvals as ap
    from agent_friday.services import item_actions
    aid = record.get("approval_id")
    if not ap.claim_for_execution(aid):
        return
    name = "change setting: " + str(detail.get("path") or "")
    v = action_gate.authorize_external(name, detail, requested_by="owner approval", approval_id=aid)
    if v.action != "allow":
        ap.mark_used(aid, "setting_proposals", {"ok": False, "error": v.reason})
        item_actions._post_back(detail.get("conversation_id"),
                                "You approved it, but Friday's governance check held it: %s. Nothing changed." % v.reason, aid)
        return
    tool = str(detail.get("tool") or "")
    ok, text = False, ""
    try:
        from agent_friday.services import agent as _ag
        handler = _ag.CLAUDE_TOOL_HANDLERS.get(tool)
        if handler is None:
            raise Refused("the tool %s is not available" % tool)
        tok = _APPROVED.set(True)
        try:
            text = str(handler(dict(detail.get("input") or {})))
        finally:
            _APPROVED.reset(tok)
        ok = not text.upper().startswith(("SWITCH_FAIL", "LAYOUT_FAIL", "DAY_FAIL", "SETTING_FAIL")) and "error" not in text[:40].lower()
    except Exception as e:
        text = "%s: %s" % (type(e).__name__, e)
    try:
        ap.mark_used(aid, "setting_proposals", {"ok": ok, "summary": text[:300]})
    except Exception:
        pass
    said = ("Done. %s The Undo beside that row in Settings puts it back for %d days." % (text, UNDO_DAYS)) if ok else \
        "The approved change did not happen: %s" % text
    item_actions._post_back(detail.get("conversation_id"), said, aid)
    _tell_pages(str(detail.get("path") or ""))
    item_actions._tell_call(detail.get("conversation_id"), {"said": "the setting is changed", "undo": True} if ok else None, ok)


_REGISTERED = False


def register_hooks() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    try:
        from agent_friday.services import approvals as ap
        ap.register_decision_hook(APPROVAL_KIND, _on_decision)
        _REGISTERED = True
    except Exception as e:
        _log.warning("could not register the setting-change hook: %s", e)


register_hooks()


# ── The one tool: set a row by its path ─────────────────────────────────────

#: path prefix -> how to say it to the tool that owns the row. The path is the Settings row's key.
PATHS = ("settings.models.chat_model", "settings.display.workspace_layout.<workspace>", "settings.display.start_screen",
         "settings.accessibility.big_mode", "settings.hologram.window.<dial>", "settings.calls.stand_back",
         "settings.podcasts.format.<show>", "settings.podcasts.length.<show>",
         "settings.podcasts.hosts.<a|b>", "settings.laya.live_reflex", "settings.display.dock.<workspace> (show / hide; op=list lists them)")
_LAYOUT_VALUES = ("normal", "fullscreen_chat")


def _route(path: str, value: str):
    """(tool, args) for a row path and a value, or an error string."""
    p = str(path or "").strip().lower().replace(" ", "_")
    v = str(value if value is not None else "").strip()
    if p == "settings.models.chat_model":
        return "switch_model", {"model": v}
    if p.startswith("settings.display.workspace_layout"):
        ws = p.split(".", 3)[3] if p.count(".") >= 3 else ""
        low = v.lower().replace(" ", "_").replace("-", "_")
        if low in ("normal", "off", "back"):
            args = {"fullscreen_chat": False}
        elif low in ("fullscreen_chat", "fullscreen", "on"):
            args = {"fullscreen_chat": True}
        else:
            args = {"position": low}
        if ws:
            args["workspace"] = ws
        return "set_workspace_layout", args
    if p == "settings.display.start_screen":
        return "show_my_day", {"mode": v}
    if p == "settings.accessibility.big_mode":
        return "big_mode", {"mode": v}
    if p == "settings.hologram.window":
        return "hologram_window", {"action": "reset"} if v.lower() == "reset" else "NOT DONE: say reset, or name a dial."
    if p.startswith("settings.hologram.window."):
        try:
            return "hologram_window", {"action": "set", p.rsplit(".", 1)[1]: float(v)}
        except ValueError:
            return "SETTING_FAIL: %s is a number." % p.rsplit(".", 1)[1]
    if p == "settings.calls.stand_back":
        return "call_mode", {"action": "set_mode", "mode": v}
    if p == "settings.laya.live_reflex":
        return "live_laya", {"action": "reflex", "mode": v.lower()}
    if p.startswith("settings.podcasts.hosts"):
        host = p.split(".", 3)[3] if p.count(".") >= 3 else ""
        host = {"main": "a", "her": "a", "co_host": "b", "cohost": "b"}.get(host, host)
        if host not in ("a", "b"):
            return "SETTING_FAIL: say which host: settings.podcasts.hosts.a (the main host) or .b (the co-host)."
        # The name as said, not lower-cased: a blank one puts the default back.
        return "podcast_format", {"host": host, "name": str(value if value is not None else "").strip()}
    if p.startswith("settings.podcasts.format"):
        show = p.split(".", 3)[3] if p.count(".") >= 3 else "any"
        return "podcast_format", {"routine": show, "format": v}
    if p.startswith("settings.podcasts.length"):
        show = p.split(".", 3)[3] if p.count(".") >= 3 else "any"
        # "10" or "10 minutes" is a number of minutes; anything else is a length's name.
        mins = v.lower().replace("minutes", "").replace("minute", "").replace("min", "").strip()
        if mins.replace(".", "", 1).isdigit():
            return "podcast_length", {"routine": show, "minutes": float(mins)}
        return "podcast_length", {"routine": show, "length": v.lower()}
    return "SETTING_FAIL: no setting is called %r. The ones I can change: %s." % (path, "; ".join(PATHS))


#: Workspace ids folded into another one (index.html WS_ALIASES): a dock saved before the fold may still
#: name the old id, and dockArrangement() reads it this way. A test holds the two tables together.
DOCK_FOLDED = {"wiki": "knowledge", "draft": "media", "content": "media", "studio": "media"}
DOCK_PATH = "settings.display.dock"
_SHOW_WORDS = ("show", "on", "visible", "unhide", "true", "yes")
_HIDE_WORDS = ("hide", "off", "hidden", "false", "no")


def dock_state(settings: dict | None = None) -> dict:
    """The dock as the page's dockArrangement() draws it, read on the server: {ids, visible, hidden,
    configured}. `ids` is every workspace that can be in the dock (held ones never are), in dock order."""
    from agent_friday.services import workspace_registry as wr
    if settings is None:
        from agent_friday.core import _load_settings
        settings = _load_settings() or {}
    cust = settings.get("dock_custom") if isinstance(settings.get("dock_custom"), dict) else {}
    saved_order = [str(x) for x in (cust.get("order") or []) if x]
    saved_hidden = [str(x) for x in (cust.get("hidden") or []) if x]
    hidden = {DOCK_FOLDED.get(x, x) for x in saved_hidden}
    for old, new in DOCK_FOLDED.items():
        named = old in saved_order or old in saved_hidden
        if named and not (old in saved_hidden and new in saved_hidden):
            hidden.discard(new)
    configured = bool(saved_order or saved_hidden)
    real = [w for w in wr.workspaces()]
    by_id = {w["id"]: w for w in real}
    ids: list = []
    for x in [DOCK_FOLDED.get(x, x) for x in saved_order] + [w["id"] for w in real]:
        if x in by_id and x not in ids:
            ids.append(x)
    show_all = settings.get("show_all_workspaces") is not False
    if configured:
        visible = [i for i in ids if i not in hidden]
    else:
        visible = [i for i in ids if show_all or by_id[i].get("core")]
    return {"ids": ids, "visible": visible, "hidden": [i for i in ids if i not in visible], "configured": configured}


def _dock_list() -> str:
    from agent_friday.services import workspace_registry as wr
    st = dock_state()
    name = lambda i: "%s (%s)" % (wr.label(i), i)
    return ("DOCK: shown: %s. Hidden: %s." % (", ".join(name(i) for i in st["visible"]) or "none",
                                             ", ".join(name(i) for i in st["hidden"]) or "none"))


def _dock(inp: dict, path: str, value: str) -> str:
    """show, hide or list the dock's workspaces. A change is a diff the owner says Yes to, like every row."""
    from agent_friday.services import workspace_registry as wr
    words = value.lower().split()
    op = str(inp.get("op") or "").strip().lower()
    ws_word = path[len(DOCK_PATH) + 1:] if path.startswith(DOCK_PATH + ".") else ""
    if words and words[0] in _SHOW_WORDS + _HIDE_WORDS + ("list",) and not ws_word:
        ws_word = " ".join(words[1:])          # "show trust" given as the value
        words = words[:1]
    if op == "list" or (words[:1] == ["list"]) or (not ws_word and not words):
        return _dock_list()
    if not ws_word:
        return "SETTING_FAIL: say which workspace, e.g. settings.display.dock.trust with show or hide."
    ws = wr.resolve(ws_word.replace("_", " ")) or (ws_word if ws_word in wr.ids() else None)
    if ws is None:
        return ("SETTING_FAIL: no workspace is called %r. The dock's workspaces are: %s."
                % (ws_word, wr.tool_list()))
    want = words[0] if words else ""
    if want not in _SHOW_WORDS + _HIDE_WORDS:
        return "SETTING_FAIL: the dock row takes show or hide (or op=list)."
    show = want in _SHOW_WORDS
    st = dock_state()
    label = wr.label(ws)
    was = "shown" if ws in st["visible"] else "hidden"
    now = "shown" if show else "hidden"
    held = hold("dock_visibility", {"path": "%s.%s" % (DOCK_PATH, ws), "value": "show" if show else "hide"},
                old=was, new=now, label="whether %s is in the dock" % label, run_as="set_setting",
                consequence=("%s gets its icon in the dock again." % label) if show else
                ("%s leaves the dock; it still opens by name or from Settings." % label))
    if held:
        return held
    hidden = [i for i in st["hidden"] if i != ws] + ([] if show else [ws])
    # A COMPLETE arrangement, seeded from what the dock shows now (as the Dock panel writes it): once an
    # arrangement exists show_all_workspaces stops applying, so writing one id alone would reveal or hide others.
    from agent_friday.core import _save_settings
    _save_settings({"dock_custom": {"order": list(st["ids"]), "hidden": [i for i in st["ids"] if i in hidden]}})
    _tell_pages("%s.%s" % (DOCK_PATH, ws))
    return ("DOCK_SHOWN: %s is in the dock now." % label) if show else ("DOCK_HIDDEN: %s is out of the dock now." % label)


def tool(inp) -> str:
    """Tool handler: set exactly one Settings row by its path, as a diff the owner says Yes to. The row's own
    tool holds the change and raises the card. op=undo writes nothing: putting a setting back is a change to
    settings too, so Friday points to the row's own Undo, which is the owner's click."""
    inp = inp or {}
    _p = str(inp.get("path") or "").strip().lower().replace(" ", "_")
    if (_p == DOCK_PATH or _p.startswith(DOCK_PATH + ".")) and str(inp.get("op") or "").strip().lower() != "undo":
        return _dock(inp, _p, str(inp.get("value") if inp.get("value") is not None else "").strip())
    if str(inp.get("op") or "").strip().lower() == "undo":
        path = str(inp.get("path") or "").strip().lower().replace(" ", "_")
        if path.startswith(DOCK_PATH + "."):
            path = DOCK_PATH          # every dock change is one row's record
        row = next((r for r in reversed(_read()) if not r.get("undone") and (not path or r.get("path") == path)), None)
        if row is None:
            return "SETTING_FAIL: There is no change to undo."
        return ("SETTING_UNDO_ON_SCREEN: %s was changed to %s; the Undo beside that row in Settings puts it back "
                "to %s. Nothing has changed: Friday does not change a setting on her own word."
                % (row.get("label"), row.get("new"), row.get("old")))
    routed = _route(inp.get("path"), inp.get("value"))
    if isinstance(routed, str):
        return routed
    name, args = routed
    from agent_friday.services import agent as _ag
    handler = _ag.CLAUDE_TOOL_HANDLERS.get(name)
    if handler is None:
        return "SETTING_FAIL: that setting is not available here."
    return str(handler(args))


# ── Where a row lives, for "take me to the wake word setting" ───────────────

#: row path -> (Settings tab id, the row's plain name, phrases that name it). Only rows that carry a
#: data-st-key on the page are listed here: a row the page cannot outline is not promised.
ROW_HOMES = {
    "settings.display.start_screen": ("appearance", "Show my day", ("show my day", "start screen", "landing screen", "my day on the start screen")),
    "settings.calls.stand_back": ("voice", "Calls", ("calls", "call mode", "stand back for calls", "standing back for calls")),
    "settings.laya.personal_learning": ("privacy", "Laya learns from my corrections", ("laya learning", "personal learning", "learn from my corrections", "what laya learned")),
    "settings.accessibility.big_mode": ("voice", "Big mode", ("big mode", "large targets", "big buttons")),
    "settings.hologram.window.depth_strength": ("voice", "Depth effect", ("depth effect", "depth strength", "hologram depth")),
    "settings.hologram.window.parallax_strength": ("voice", "Parallax strength", ("parallax", "parallax strength")),
    "settings.hologram.window.zoom_in_max": ("voice", "Lean in, at most", ("lean in", "zoom in")),
    "settings.hologram.window.zoom_out_max": ("voice", "Lean back, at most", ("lean back", "zoom out")),
    "settings.hologram.window.head_smoothing": ("voice", "Head smoothing", ("head smoothing",)),
    "settings.hologram.window.head_response": ("voice", "Head response", ("head response",)),
    "settings.hologram.window.holo_cues": ("voice", "Holographic cues", ("holographic cues", "holo cues")),
    "settings.hologram.window.viewing_distance_cm": ("voice", "Viewing distance", ("viewing distance",)),
    "settings.hologram.window.screen_width_cm": ("voice", "Screen width", ("screen width",)),
}


def match_row(phrase: str) -> tuple | None:
    """(path, tab, name) for the row a phrase names, by its path, its name or one of its phrases; None if none."""
    q = " ".join(str(phrase or "").lower().replace("_", " ").split())
    if not q:
        return None
    for path, (tab, name, phrases) in ROW_HOMES.items():
        if q == path or q == name.lower() or q in phrases:
            return path, tab, name
    for path, (tab, name, phrases) in ROW_HOMES.items():
        if any(p in q for p in phrases):
            return path, tab, name
    return None
