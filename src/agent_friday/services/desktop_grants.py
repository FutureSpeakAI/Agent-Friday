"""Per-app grants for desktop control.

Computer Control (Friday's `click`, `type_text`, `press_key`, `scroll`,
`move_mouse`, `screenshot`, and the pointer and keyboard tools of the Windows
desktop connector) acts on whichever app is under the pointer or in front.
One switch for all of it would let a grant given for a spreadsheet reach a
banking site. So each app, named by its executable (`notepad.exe`), has its
own tier:

    none      Friday may not look at it or act in it
    observe   Friday may take a screenshot or read the accessibility tree
              while it is in front
    act       Friday may also click, type, scroll and press keys in it

`*` is "every app not listed". A new install lists nothing. An install that
already held the single Computer Control grant is carried over as
`* = act`, once, the first time this file is read, so nothing that worked
stops working; the owner can then narrow it.

Whatever the tier, some actions always wait for the owner's decision (a yes
in chat, or an approval card when nobody is in the chat):

  * typing into a password field (UI Automation IsPassword, or a Win32 edit
    with ES_PASSWORD). Typing is REFUSED when the focused control cannot be
    identified at all, because then a password field cannot be ruled out;
  * a click on, or Enter in, a control whose name says delete, send, pay,
    submit, buy, publish and the like (a conservative word list);
  * the Delete key outside a text field.

The target app is found at call time: the window under the point for a
click or a move, the window under the cursor for a scroll, the foreground
window for typing, keys and screenshots. The checkpoint looks once
(`classify`, called from governance/action_gate.py) and the tool's handler
looks again just before it acts (`recheck`), so switching windows between
the two does not carry a grant from one app to another.

Honest limits: a screenshot and the accessibility-tree snapshot capture the
whole screen, not just the app in front, so an `observe` grant on the front
app lets Friday see whatever else is visible. The word list reads the name a
control reports; a control with no accessible name is judged by its app's
tier alone. Identifying the focused control needs UI Automation (comtypes);
where that is missing, typing is refused except into a classic Win32 edit.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from agent_friday.paths import friday_home
from agent_friday.user_errors import UserFacingValueError

_log = logging.getLogger("friday.desktop_grants")
_LOCK = threading.RLock()

TIERS = ("none", "observe", "act")
_RANK = {"none": 0, "observe": 1, "act": 2}
ALL_APPS = "*"

#: The connector key and MCP server name of the Windows desktop connector
#: (services/connectors.py).
DESKTOP_SERVER = "windows_desktop"
_MCP_PREFIX = f"mcp_{DESKTOP_SERVER}_"

NATIVE_TOOLS = frozenset({"move_mouse", "click", "type_text", "press_key",
                          "screenshot", "scroll"})

# Desktop connector tools by what they do, from the tool's own name with any
# "-Tool" suffix removed. A name in none of these sets is not tied to one app
# (shell, launching apps, clipboard, web scraping, window management) and
# always needs a decision.
_MCP_OBSERVE = frozenset({"state", "snapshot", "screenshot"})
_MCP_WAIT = frozenset({"wait"})
_MCP_POINTER = frozenset({"click", "scroll", "move", "drag"})
_MCP_TYPE = frozenset({"type"})
_MCP_KEYS = frozenset({"shortcut", "key"})

#: Words in a control's name that make a click (or Enter) something the owner
#: decides. Conservative on purpose: "Sign in" asks too.
RISKY_WORDS = re.compile(
    r"\b(delete|remove|erase|discard|trash|wipe|uninstall|send|pay|payment|"
    r"purchase|buy|checkout|check\s*out|order|submit|transfer|publish|post|"
    r"confirm|sign)\b", re.I)
_SUBMIT_KEYS = frozenset({"enter", "return"})
_DELETE_KEYS = frozenset({"delete", "del"})
_TEXT_CONTROLS = frozenset({"edit", "document"})
_APP_RE = re.compile(r"^[a-z0-9 _.()+\-]{1,100}\.exe$")

INTERNAL, OUTWARD, FORBIDDEN = "internal", "outward", "forbidden"


# ── The grant list ──────────────────────────────────────────────────────────

def _grants_file() -> Path:
    return Path(friday_home()) / "governance" / "desktop_app_grants.json"


def _legacy_grant_file() -> Path:
    """The single Computer Control grant (services/agent.py `_CC_PERM_FILE`)."""
    return Path(friday_home()) / "cc_permission"


def normalize_app(name) -> Optional[str]:
    """`C:\\Apps\\Notepad.EXE` -> `notepad.exe`; `*` stays `*`; else None."""
    s = str(name or "").strip().strip('"').strip()
    if s == ALL_APPS:
        return ALL_APPS
    s = re.split(r"[\\/]", s)[-1].lower()
    if s and "." not in s:
        s += ".exe"
    return s if _APP_RE.match(s) else None


def _write(data: dict) -> None:
    p = _grants_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def load() -> dict:
    """{"apps": {exe: tier}, "origin": str}. Creates the file on first read."""
    with _LOCK:
        p = _grants_file()
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                apps = {}
                for k, v in (data.get("apps") or {}).items():
                    app = normalize_app(k)
                    if app and v in TIERS:
                        apps[app] = v
                return {"apps": apps, "origin": str(data.get("origin") or "")}
            except Exception as e:
                # Unreadable grants grant nothing.
                _log.warning("desktop app grants unreadable (%s): no app is granted", e)
                return {"apps": {}, "origin": "unreadable"}
        legacy = _legacy_grant_file().exists()
        data = {
            "version": 1,
            "apps": {ALL_APPS: "act"} if legacy else {},
            "origin": ("carried over from the single Computer Control grant"
                       if legacy else "new"),
            "created_at": time.time(),
        }
        try:
            _write(data)
        except Exception as e:
            _log.warning("could not write desktop app grants: %s", e)
        return {"apps": dict(data["apps"]), "origin": data["origin"]}


def grants() -> dict:
    return load()["apps"]


def set_grant(app, tier: str) -> dict:
    """Owner-only (the authenticated settings route). Returns the new list."""
    a = normalize_app(app)
    if not a:
        raise UserFacingValueError("name the app by its program file, for example notepad.exe")
    if tier not in TIERS:
        raise UserFacingValueError(f"tier must be one of {', '.join(TIERS)}")
    with _LOCK:
        cur = load()
        cur["apps"][a] = tier
        _write({"version": 1, "apps": cur["apps"], "origin": cur["origin"],
                "updated_at": time.time()})
        return dict(cur["apps"])


def remove_grant(app) -> dict:
    a = normalize_app(app)
    with _LOCK:
        cur = load()
        cur["apps"].pop(a, None)
        _write({"version": 1, "apps": cur["apps"], "origin": cur["origin"],
                "updated_at": time.time()})
        return dict(cur["apps"])


def tier_for(app: Optional[str]) -> str:
    """The app's own entry, else the `*` entry, else none. An app that cannot
    be identified gets only what `*` gives."""
    apps = grants()
    if app and app in apps:
        return apps[app]
    return apps.get(ALL_APPS, "none")


# ── Seeing the desktop ──────────────────────────────────────────────────────

@dataclass
class Control:
    identified: bool
    is_password: bool = False
    name: str = ""
    control_type: str = ""        # "edit", "document", "button", ...


_UNKNOWN = Control(identified=False)


class NullProbe:
    """No desktop to look at (not Windows): nothing can be identified."""

    def foreground_app(self):
        return None

    def app_at(self, x, y):
        return None

    def cursor_pos(self):
        return None

    def focused_control(self):
        return _UNKNOWN

    def control_at(self, x, y):
        return _UNKNOWN


class WindowsProbe(NullProbe):
    """Win32 for windows and processes; UI Automation for controls."""

    _UIA_TYPES = {50000: "button", 50004: "edit", 50030: "document",
                  50020: "text", 50011: "menuitem", 50005: "hyperlink",
                  50002: "checkbox", 50013: "radiobutton", 50003: "combobox",
                  50007: "listitem", 50019: "tabitem"}

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self._ct, self._wt = ctypes, wintypes
        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        self._u, self._k = u, k
        u.GetForegroundWindow.restype = wintypes.HWND
        u.WindowFromPoint.argtypes = [wintypes.POINT]
        u.WindowFromPoint.restype = wintypes.HWND
        u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetAncestor.restype = wintypes.HWND
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowLongW.restype = ctypes.c_long
        u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                 wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k.CloseHandle.argtypes = [wintypes.HANDLE]

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                        ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                        ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                        ("rcCaret", wintypes.RECT)]
        self._GTI = GUITHREADINFO
        u.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]

    # windows and processes
    def _exe_of_hwnd(self, hwnd):
        if not hwnd:
            return None
        pid = self._wt.DWORD(0)
        self._u.GetWindowThreadProcessId(hwnd, self._ct.byref(pid))
        if not pid.value:
            return None
        h = self._k.OpenProcess(0x1000, False, pid.value)   # QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            buf = self._ct.create_unicode_buffer(1024)
            n = self._wt.DWORD(1024)
            if not self._k.QueryFullProcessImageNameW(h, 0, buf, self._ct.byref(n)):
                return None
            return normalize_app(buf.value)
        finally:
            self._k.CloseHandle(h)

    def foreground_app(self):
        return self._exe_of_hwnd(self._u.GetForegroundWindow())

    def app_at(self, x, y):
        hwnd = self._u.WindowFromPoint(self._wt.POINT(int(x), int(y)))
        root = self._u.GetAncestor(hwnd, 2) if hwnd else None   # GA_ROOT
        return self._exe_of_hwnd(root or hwnd)

    def cursor_pos(self):
        pt = self._wt.POINT()
        if not self._u.GetCursorPos(self._ct.byref(pt)):
            return None
        return (pt.x, pt.y)

    # controls
    def _win32_focus(self):
        fg = self._u.GetForegroundWindow()
        if not fg:
            return None
        tid = self._u.GetWindowThreadProcessId(fg, None)
        gti = self._GTI()
        gti.cbSize = self._ct.sizeof(self._GTI)
        if not self._u.GetGUIThreadInfo(tid, self._ct.byref(gti)):
            return None
        return gti.hwndFocus

    def _win32_edit(self, hwnd):
        """A classic Win32 edit control is identified without UI Automation."""
        if not hwnd:
            return None
        buf = self._ct.create_unicode_buffer(256)
        self._u.GetClassNameW(hwnd, buf, 256)
        cls = buf.value.lower()
        if cls == "edit" or cls.startswith("richedit"):
            style = self._u.GetWindowLongW(hwnd, -16)            # GWL_STYLE
            return Control(True, bool(style & 0x20), "", "edit")  # ES_PASSWORD
        return None

    def _uia(self, fn):
        """Run one UI Automation query on its own thread, with a time limit:
        an app that is not responding must not hang the checkpoint."""
        out = {}

        def run():
            try:
                import ctypes
                ctypes.windll.ole32.CoInitializeEx(None, 2)
                import comtypes.client
                mod = comtypes.client.GetModule("UIAutomationCore.dll")
                uia = comtypes.client.CreateObject(
                    "{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=mod.IUIAutomation)
                el = fn(uia, mod)
                if el is not None:
                    out["c"] = Control(
                        True, bool(el.CurrentIsPassword), str(el.CurrentName or "")[:200],
                        self._UIA_TYPES.get(int(el.CurrentControlType), "other"))
            except Exception as e:
                out["e"] = e
        t = threading.Thread(target=run, daemon=True, name="uia-probe")
        t.start()
        t.join(2.0)
        return out.get("c") or _UNKNOWN

    def focused_control(self):
        w = self._win32_edit(self._win32_focus())
        if w is not None:
            return w
        return self._uia(lambda uia, mod: uia.GetFocusedElement())

    def control_at(self, x, y):
        def at(uia, mod):
            return uia.ElementFromPoint(mod.tagPOINT(int(x), int(y)))
        c = self._uia(at)
        if c.identified:
            return c
        return self._win32_edit(self._u.WindowFromPoint(self._wt.POINT(int(x), int(y)))) or c


_PROBE = None


def probe():
    global _PROBE
    if _PROBE is None:
        try:
            _PROBE = WindowsProbe() if sys.platform == "win32" else NullProbe()
        except Exception as e:
            _log.warning("desktop probe unavailable: %s", e)
            _PROBE = NullProbe()
    return _PROBE


def set_probe(p):
    """Replace the desktop probe (tests). Returns the previous one."""
    global _PROBE
    prev, _PROBE = _PROBE, p
    return prev


#: Maps a native tool's (x, y) -- given in the last screenshot's downscaled
#: space -- to real screen pixels. services/agent.py installs the real one.
_POINT_MAPPER: Callable = lambda x, y: (x, y)


def set_point_mapper(fn: Callable) -> None:
    global _POINT_MAPPER
    _POINT_MAPPER = fn


# ── Classification ─────────────────────────────────────────────────────────

def is_desktop_server(server_name) -> bool:
    return str(server_name or "").strip().lower() == DESKTOP_SERVER


def is_desktop_tool(tool_name) -> bool:
    t = str(tool_name or "")
    return t in NATIVE_TOOLS or t.lower().startswith(_MCP_PREFIX)


def _kind(tool: str) -> str:
    """observe | wait | pointer | type | keys | other"""
    if tool == "screenshot":
        return "observe"
    if tool in ("click", "move_mouse", "scroll"):
        return "pointer"
    if tool == "type_text":
        return "type"
    if tool == "press_key":
        return "keys"
    base = tool.lower()[len(_MCP_PREFIX):] if tool.lower().startswith(_MCP_PREFIX) else tool.lower()
    base = re.sub(r"[-_]?tool$", "", base)
    base = re.sub(r"[^a-z]", "", base)
    for kind, names in (("observe", _MCP_OBSERVE), ("wait", _MCP_WAIT),
                        ("pointer", _MCP_POINTER), ("type", _MCP_TYPE),
                        ("keys", _MCP_KEYS)):
        if base in names:
            return kind
    return "other"


def _is_click(tool: str) -> bool:
    if tool == "click":
        return True
    base = tool.lower()[len(_MCP_PREFIX):] if tool.lower().startswith(_MCP_PREFIX) else ""
    return base.startswith("click") or base.startswith("drag")


def _as_point(v):
    if isinstance(v, (list, tuple)) and len(v) == 2:
        try:
            return (int(float(v[0])), int(float(v[1])))
        except (TypeError, ValueError):
            return None
    if isinstance(v, dict) and "x" in v and "y" in v:
        return _as_point([v["x"], v["y"]])
    return None


def _points(tool: str, kind: str, args: dict) -> list:
    """Real screen points the action lands on."""
    a = args or {}
    if tool in ("click", "move_mouse"):
        try:
            return [tuple(int(round(float(c))) for c in
                          _POINT_MAPPER(float(a.get("x", 0)), float(a.get("y", 0))))]
        except (TypeError, ValueError):
            return []
    pts = []
    if tool.lower().startswith(_MCP_PREFIX):
        for k, v in a.items():
            kl = str(k).lower()
            if kl.endswith("loc") or kl in ("location", "coordinates", "point",
                                            "start", "end", "from", "to"):
                p = _as_point(v)
                if p:
                    pts.append(p)
        if "x" in a and "y" in a:
            p = _as_point([a["x"], a["y"]])
            if p:
                pts.append(p)
    if not pts and kind == "pointer":
        cur = probe().cursor_pos()
        if cur:
            pts.append(tuple(cur))
    return pts


def _key_words(args: dict) -> set:
    a = args or {}
    raw = a.get("key") or a.get("keys") or a.get("shortcut") or ""
    if isinstance(raw, (list, tuple)):
        raw = "+".join(str(x) for x in raw)
    return {w for w in re.split(r"[+\s,]+", str(raw).lower()) if w}


def _targets(tool: str, kind: str, args: dict, *, controls: bool = True) -> tuple:
    """([apps], control or None) the action lands on. `controls=False` skips
    the (slower) control lookup when only the apps are wanted."""
    p = probe()
    if kind in ("observe", "keys"):
        ctl = p.focused_control() if (kind == "keys" and controls) else None
        return [p.foreground_app()], ctl
    pts = _points(tool, kind, args)
    if kind == "type":
        if pts:
            return [p.app_at(*pts[0])], (p.control_at(*pts[0]) if controls else None)
        return [p.foreground_app()], (p.focused_control() if controls else None)
    if kind == "pointer":
        if not pts:
            return [None], None
        ctl = p.control_at(*pts[0]) if (controls and _is_click(tool)) else None
        return [p.app_at(*pt) for pt in pts], ctl
    return [], None


def _short_grant_hint(app, need) -> str:
    who = f"'{app}'" if app else "the app it would act on (it could not be identified)"
    return (f"{who} is not granted '{need}' for desktop control. The owner can grant "
            f"it under Settings \u2192 Privacy & Approvals \u2192 Computer Control "
            f"\u2192 Apps.")


def _grant_check(apps: list, need: str) -> tuple:
    if not apps:
        return False, "the target app could not be identified"
    for app in apps:
        if _RANK[tier_for(app)] < _RANK[need]:
            return False, _short_grant_hint(app, need)
    return True, ""


def classify(tool: str, args: Optional[dict]) -> tuple:
    """(internal | outward | forbidden, why) for one desktop action.

    forbidden: the app is not granted what the action needs, or Friday would
    type without knowing where. outward: granted, but the action is one the
    owner decides each time. internal: granted, and nothing about it asks.
    """
    a = args or {}
    kind = _kind(tool)
    if kind == "wait":
        return INTERNAL, "it only waits"
    if kind == "other":
        return OUTWARD, ("a desktop tool that is not tied to one app (shell, "
                         "launching apps, clipboard, web) always needs a decision")
    try:
        apps, ctl = _targets(tool, kind, a)
    except Exception as e:
        return FORBIDDEN, f"the target app could not be identified ({e})"
    need = "observe" if kind == "observe" else "act"
    ok, why = _grant_check(apps, need)
    if not ok:
        return FORBIDDEN, why
    shown = ", ".join(x or "unidentified app" for x in apps)
    if kind == "type":
        if ctl is None or not ctl.identified:
            return FORBIDDEN, ("Friday could not tell which control has the keyboard "
                               "focus, so it cannot rule out a password field and "
                               "will not type")
        if ctl.is_password:
            return OUTWARD, f"it types into a password field in {shown}"
    if kind == "keys":
        keys = _key_words(a)
        if ctl is not None and ctl.is_password:
            return OUTWARD, f"it presses keys in a password field in {shown}"
        if keys & _SUBMIT_KEYS and ctl is not None and RISKY_WORDS.search(ctl.name or ""):
            return OUTWARD, f"it presses Enter on \u201c{ctl.name}\u201d in {shown}"
        if keys & _DELETE_KEYS and not (ctl is not None and ctl.identified
                                        and ctl.control_type in _TEXT_CONTROLS):
            return OUTWARD, f"it presses Delete outside a text field in {shown}"
    if kind == "pointer" and ctl is not None and RISKY_WORDS.search(ctl.name or ""):
        return OUTWARD, f"it clicks \u201c{ctl.name}\u201d in {shown}"
    return INTERNAL, f"{shown}: granted '{tier_for(apps[0])}'"


def recheck(tool: str, args: Optional[dict]) -> tuple:
    """(ok, why), just before the handler acts: is the app it will land on
    NOW still granted? The checkpoint's other rulings stand; this only stops a
    grant for one app being spent on another after a window switch."""
    kind = _kind(tool)
    if kind in ("wait", "other"):
        return True, ""
    try:
        apps, _ctl = _targets(tool, kind, args or {}, controls=False)
    except Exception as e:
        return False, f"the target app could not be identified ({e})"
    return _grant_check(apps, "observe" if kind == "observe" else "act")
