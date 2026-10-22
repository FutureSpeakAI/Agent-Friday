"""Safe mode: a start with extensions and the cloud off, so Friday can be
repaired (north star §28.5, §33.5-3; burn-down BD-03).

In safe mode Friday starts a minimal local interface:

* no extensions: MCP servers are not started or reloaded, user and learned
  skills are not loaded (the skills shipped with the app still are), tool
  hooks do not run, connectors are not polled and connected-service reads are
  held, remote channels, the phone line and published-page hosting stay off;
* no cloud: every cloud model call is refused (even with a per-turn intent),
  routing is local only for this run, web search and web fetch are refused,
  nothing is downloaded at start and no update check is made; underneath all
  of these, no connection leaves the machine from this process (a socket-level
  guard: loopback only, no proxy), and commands and programs (run_command and
  the like) run nothing, since a child process is outside that guard. The
  local Ollama daemon is a separate program with its own settings; Friday asks
  it for no download in safe mode;
* schedules paused, outward actions held (the action gate's outward_hold);
* still there: chat with a local model if one is installed, read-only data,
  the Activity ledger, the Doctor report, backup (the Friday Bundle) and the
  owner's way out.

How it is entered, in order of precedence:

* ``FRIDAY_SAFE_START=1`` in the environment of the process (``friday safe``
  and the tray's "Restart in safe mode" set it);
* ``<home>/safe_mode/ON``, written by "Start in safe mode next time" (CLI
  ``friday safe-mode on`` or the Doctor page); it stays until the owner leaves
  safe mode, so a start that keeps failing cannot slip out of it;
* automatically, when the last three starts failed (services/boot_guard keeps
  the count): safe mode must stay reachable after a failed migration or a
  runtime that will not load (§28.5-2).

The mode is decided once per process, at the first question, and does not
change while it runs: leaving takes a restart, like entering.

It also implies services/boot_guard's self-modification lock (FRIDAY_SAFE_MODE),
which is a narrower, older switch with a similar name.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

#: What safe mode turns off. Each name is asked about where that part starts.
DISABLED = (
    "mcp", "skills", "tool_hooks", "connectors", "connector_reads", "channels",
    "phone", "publish_hosting", "cloud", "web", "first_run_models",
    "update_check", "scheduler", "reconcile_resume", "publisher", "call_watch",
    "notifications_loop", "news", "podcasts", "media_index", "library_runtime",
    "network_monitor", "predictive_prewarm", "credential_sweep", "model_seats",
    "voice_warm", "local_address", "machine_monitor",
)

FAILED_STARTS = 3
_LOCK = threading.Lock()
_DECIDED: dict = {}


def marker_path() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "safe_mode" / "ON"


def _failed_starts() -> int:
    try:
        from agent_friday.services import boot_guard
        return int(boot_guard._read_attempt().get("consecutive_failures") or 0)
    except Exception:
        return 0


def _decide() -> dict:
    if os.environ.get("FRIDAY_SAFE_START", "").strip().lower() in ("1", "true", "yes", "on"):
        return {"active": True, "why": "started in safe mode", "how": "start"}
    try:
        if marker_path().exists():
            return {"active": True, "why": "safe mode was chosen for the next start",
                    "how": "marker"}
    except OSError:
        pass
    n = _failed_starts()
    if n >= FAILED_STARTS:
        return {"active": True, "why": "the last %d starts failed" % n, "how": "failed_starts"}
    return {"active": False, "why": "", "how": ""}


def state() -> dict:
    """{active, why, how, since}: decided once per process."""
    with _LOCK:
        if not _DECIDED:
            _DECIDED.update(_decide(), since=time.time())
        return dict(_DECIDED)


def active() -> bool:
    return bool(state().get("active"))


def allows(part: str) -> bool:
    """False when safe mode keeps `part` (one of DISABLED) off."""
    return not (part in DISABLED and active())


def refusal(part: str) -> Optional[str]:
    """The sentence a refused part returns, or None when it may run."""
    if allows(part):
        return None
    return ("Friday is in safe mode (%s): extensions and the cloud are off until "
            "you leave safe mode and restart." % state().get("why"))


def set_next_start(on: bool) -> dict:
    """Write or remove the marker for the next start. Returns the new plan.
    The running process keeps its mode until it restarts."""
    p = marker_path()
    if on:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"chosen_at": time.time()}), encoding="utf-8")
    else:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    return plan()


def plan() -> dict:
    """What is true now and what the next start will do."""
    now = state()
    try:
        nxt = marker_path().exists() or _failed_starts() >= FAILED_STARTS
    except OSError:
        nxt = False
    return {"active": now["active"], "why": now.get("why"), "how": now.get("how"),
            "next_start_safe": bool(nxt), "disabled": list(DISABLED) if now["active"] else []}


_GUARD = {"installed": False}


def _local_host(host) -> bool:
    import ipaddress
    h = str(host or "").strip().strip("[]").lower()
    if h in ("localhost", "") or h.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(h.split("%")[0]).is_loopback
    except ValueError:
        return False


def install_network_guard() -> bool:
    """In safe mode, refuse every connection that leaves this machine, for the
    whole process: one seam under every library (model SDKs, Google APIs, web
    search, downloads, peers), so a path nobody listed still cannot reach the
    cloud. Loopback stays open (the page, a local model). Returns whether the
    guard is in place."""
    if not active() or _GUARD["installed"]:
        return _GUARD["installed"]
    import socket
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_gai = socket.getaddrinfo

    def _check(addr):
        host = addr[0] if isinstance(addr, tuple) and addr else addr
        if isinstance(host, (bytes, bytearray)):
            host = host.decode("ascii", "replace")
        if isinstance(host, str) and not _local_host(host) and "/" not in host:
            raise OSError("safe mode: no connection leaves this machine (%s)" % host)

    def connect(self, addr, *a, **k):
        if getattr(self, "family", None) in (socket.AF_INET, getattr(socket, "AF_INET6", -1)):
            _check(addr)
        return real_connect(self, addr, *a, **k)

    def connect_ex(self, addr, *a, **k):
        if getattr(self, "family", None) in (socket.AF_INET, getattr(socket, "AF_INET6", -1)):
            _check(addr)
        return real_connect_ex(self, addr, *a, **k)

    def getaddrinfo(host, *a, **k):
        h = host.decode("ascii", "replace") if isinstance(host, (bytes, bytearray)) else host
        if h is not None and not _local_host(h):
            raise socket.gaierror("safe mode: no name lookup for %s" % h)
        return real_gai(host, *a, **k)

    real_ghbn = socket.gethostbyname
    real_ghbn_ex = socket.gethostbyname_ex
    real_sendto = socket.socket.sendto
    real_sendmsg = getattr(socket.socket, "sendmsg", None)

    def gethostbyname(host):
        if not _local_host(host):
            raise socket.gaierror("safe mode: no name lookup for %s" % host)
        return real_ghbn(host)

    def gethostbyname_ex(host):
        if not _local_host(host):
            raise socket.gaierror("safe mode: no name lookup for %s" % host)
        return real_ghbn_ex(host)

    def sendto(self, data, *rest):
        addr = rest[-1] if rest else None
        if getattr(self, "family", None) in (socket.AF_INET, getattr(socket, "AF_INET6", -1)) and addr:
            _check(addr)
        return real_sendto(self, data, *rest)

    def sendmsg(self, buffers, ancdata=(), flags=0, address=None):
        if address is not None and getattr(self, "family", None) in (
                socket.AF_INET, getattr(socket, "AF_INET6", -1)):
            _check(address)
        return real_sendmsg(self, buffers, ancdata, flags, *(() if address is None else (address,)))

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo
    socket.gethostbyname = gethostbyname
    socket.gethostbyname_ex = gethostbyname_ex
    socket.socket.sendto = sendto
    if real_sendmsg is not None:
        socket.socket.sendmsg = sendmsg
    # A proxy on this machine (a VPN agent, a corporate proxy, the system
    # proxy in the registry) would carry a request out through a loopback
    # connection: no proxy is used in safe mode.
    names = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
    saved_env = {k: os.environ.get(k) for k in names + ("NO_PROXY", "no_proxy")}
    for k in names:
        os.environ.pop(k, None)
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "*"
    import urllib.request as _ur
    real_getproxies = _ur.getproxies
    _ur.getproxies = lambda: {}
    _GUARD.update(installed=True, real=(real_connect, real_connect_ex, real_gai),
                  real2=(real_ghbn, real_ghbn_ex, real_sendto, real_sendmsg, real_getproxies),
                  env=saved_env)
    return True


def remove_network_guard() -> None:
    """Tests only: put the socket functions back."""
    if not _GUARD.get("installed"):
        return
    import socket
    import urllib.request as _ur
    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = _GUARD["real"]
    ghbn, ghbn_ex, sendto, sendmsg, getproxies = _GUARD["real2"]
    socket.gethostbyname, socket.gethostbyname_ex, socket.socket.sendto = ghbn, ghbn_ex, sendto
    if sendmsg is not None:
        socket.socket.sendmsg = sendmsg
    _ur.getproxies = getproxies
    for k, v in (_GUARD.get("env") or {}).items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _GUARD.update(installed=False, real=None, real2=None, env=None)


def reset_for_tests() -> None:
    with _LOCK:
        _DECIDED.clear()
