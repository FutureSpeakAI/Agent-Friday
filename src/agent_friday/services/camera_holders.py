"""Which app holds the webcam right now.

The page cannot see past the browser: a failed camera open is a bare
NotReadableError. Windows can. Its capability access manager records, per
app, when it last started and stopped using the webcam (the ConsentStore keys
under HKCU), and a stop time of zero means the app is using it now. That is
the certain answer. When the store names nobody, the running processes of
the usual camera apps are offered as candidates, marked as such, so the page
can say "camera busy in another app, maybe Zoom" rather than nothing.

Everything here is read-only and never raises: on another OS, or with the
registry unreadable, the answer is simply empty.
"""
from __future__ import annotations

import sys

#: Executable name (lower case) -> what to call it.
KNOWN = {
    "zoom.exe": "Zoom", "cpthost.exe": "Zoom", "aomhost64.exe": "Zoom",
    "ms-teams.exe": "Microsoft Teams", "teams.exe": "Microsoft Teams",
    "chrome.exe": "Chrome", "msedge.exe": "Edge", "firefox.exe": "Firefox",
    "obs64.exe": "OBS", "obs32.exe": "OBS", "skype.exe": "Skype",
    "discord.exe": "Discord", "slack.exe": "Slack", "webexmta.exe": "Webex",
    "atmgr.exe": "Webex", "nvidia broadcast.exe": "NVIDIA Broadcast",
    "camerahelper.exe": "Camera", "windowscamera.exe": "Camera",
}
#: Packaged (Store) app id prefixes -> name.
PACKAGED = {"msteams": "Microsoft Teams", "microsoftteams": "Microsoft Teams",
            "microsoft.windowscamera": "Camera", "microsoft.skypeapp": "Skype",
            "windows.immersivecontrolpanel": "Settings"}
#: Processes that mean a camera app is open, as candidates only.
CANDIDATE_PROCESSES = ("zoom.exe", "cpthost.exe", "ms-teams.exe", "teams.exe",
                       "obs64.exe", "obs32.exe", "skype.exe", "webexmta.exe", "atmgr.exe")


def friendly(key: str) -> str:
    """A consent-store key ('C:#Program Files#...#Zoom.exe', or a packaged id)
    as a name a person would say."""
    raw = str(key or "").replace("#", "\\")
    base = raw.rsplit("\\", 1)[-1].lower()
    if base in KNOWN:
        return KNOWN[base]
    for prefix, name in PACKAGED.items():
        if base.startswith(prefix):
            return name
    if base.endswith(".exe"):
        base = base[:-4]
    return base.split("_")[0] or "another app"


def parse(entries) -> list[str]:
    """Entries are (key, LastUsedTimeStart, LastUsedTimeStop); a started entry
    with no stop time is using the camera now. Names, deduplicated, in order."""
    out: list[str] = []
    for key, start, stop in entries or ():
        try:
            if not start or int(start) <= 0 or (stop and int(stop) > 0):
                continue
        except (TypeError, ValueError):
            continue
        name = friendly(key)
        if name not in out:
            out.append(name)
    return out


def read_registry(kind: str = "webcam") -> list:
    """(key, start, stop) for every app Windows has seen use the `kind`
    device: "webcam" or "microphone"."""
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:  # pragma: no cover
        return []
    if kind not in ("webcam", "microphone"):
        return []
    root = (r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
            "\\ConsentStore\\" + kind)
    out = []

    def walk(path):
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return
        with k:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                if sub.lower() == "nonpackaged":
                    walk(path + "\\" + sub)
                    continue
                try:
                    with winreg.OpenKey(k, sub) as s:
                        start = _value(winreg, s, "LastUsedTimeStart")
                        stop = _value(winreg, s, "LastUsedTimeStop")
                except OSError:
                    continue
                out.append((sub, start, stop))

    try:
        walk(root)
    except Exception:
        return out
    return out


def _value(winreg, key, name):
    try:
        v, _t = winreg.QueryValueEx(key, name)
        return int(v)
    except (OSError, TypeError, ValueError):
        return 0


def running_camera_apps(names=None) -> list[str]:
    """Names of the usual camera apps that are running: candidates, not proof."""
    if names is None:
        try:
            names = _process_names()
        except Exception:
            names = []
    out: list[str] = []
    for n in names:
        n = str(n or "").lower()
        if n in CANDIDATE_PROCESSES:
            name = KNOWN.get(n, n)
            if name not in out:
                out.append(name)
    return out


def _process_names() -> list[str]:
    try:
        import psutil
        return [p.info.get("name") or "" for p in psutil.process_iter(["name"])]
    except Exception:
        pass
    if sys.platform != "win32":
        return []
    try:
        import subprocess
        r = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=5)
        return [line.split('","')[0].strip('"') for line in r.stdout.splitlines() if line.startswith('"')]
    except Exception:
        return []


def snapshot(kind: str = "webcam") -> dict:
    """{'holders': [...certain...], 'candidates': [...running camera apps...]}."""
    holders = parse(read_registry(kind))
    candidates = [n for n in running_camera_apps() if n not in holders]
    return {"holders": holders, "candidates": candidates}
