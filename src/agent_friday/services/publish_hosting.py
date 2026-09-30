"""Hosting "This PC": the static server, its own tunnel, the switch, the status.

docs/design/active/vibe-coding-salon.md §4.10.1. The owner's decision
(2026-09-29): "This PC" is the default host for published static artifacts,
with an isolated static server, its own tunnel hostname, the read-only
published/ folder, no route to Friday, a kill switch, a reachability line
and adversarial tests.

* **The server** is `services/published_server.py`, run as its own process
  from this module. It binds loopback only and serves the published/ folder.
* **The tunnel** is a cloudflared *quick tunnel* (`cloudflared tunnel --url
  http://127.0.0.1:<port>`), a second cloudflared process that points ONLY at
  the static server. Its hostname (`*.trycloudflare.com`) is distinct from
  anything Friday's own address uses, and there is no ingress rule, proxy or
  reverse route from it to Friday's app, API or websocket: cloudflared is
  given one URL, and that URL is the static server. Without cloudflared on
  the machine, pages serve on loopback only and the status line says so.
* **The switch** (`publish_this_pc_enabled`) stops both processes at once and
  refuses to start them while it is off.
* **Status** says whether the server process is alive, what the public
  address is, and whether a page was reachable through it on the last probe
  (the PC awake, the tunnel up). Nothing is asserted that was not probed.
* **No analytics, no access log.** The rate limit lives in the server's
  memory. This module writes one small state file beside, never inside, the
  served folder.

Hosted adapters (Cloudflare Pages, GitHub Pages) are connected by a token in
the credential store; `adapter_connected` says whether one is.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

_LOCK = threading.RLock()
_SERVER: Optional[subprocess.Popen] = None
_TUNNEL: Optional[subprocess.Popen] = None
_STATE: dict = {"serving": False, "url": None, "port": None, "tunnel": False,
                "reachable": None, "probed_at": None, "started_at": None}
_TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

#: A test must say so before this module may start a process. While a pytest
#: test is running (pytest sets PYTEST_CURRENT_TEST in the worker for the
#: test's duration) nothing is spawned otherwise: a unit test that approves a
#: publish card must never open a real tunnel to a temp folder. The hosting
#: tests set this after stubbing `_spawn`. FRIDAY_TESTING alone does not
#: count: a private server instance runs with it and must be able to host.
ALLOW_UNDER_TEST = False


def _under_test() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) and not ALLOW_UNDER_TEST


def _reset_for_tests() -> None:
    global _SERVER, _TUNNEL, ALLOW_UNDER_TEST
    _SERVER = None
    _TUNNEL = None
    ALLOW_UNDER_TEST = False
    _STATE.update({"serving": False, "url": None, "port": None, "tunnel": False,
                   "reachable": None, "probed_at": None, "started_at": None})


# ── places and settings ──────────────────────────────────────────────────────

def _home() -> Path:
    from agent_friday import core
    return Path(core.FRIDAY_DIR)


def _published_root() -> Path:
    return _home() / "published"


def _state_path() -> Path:
    return _home() / "publish-this-pc.json"


def _settings() -> dict:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def enabled() -> bool:
    return _settings().get("publish_this_pc_enabled", True) is not False


def tunnel_wanted() -> bool:
    """False keeps pages on loopback only: nothing leaves this PC."""
    return _settings().get("publish_this_pc_tunnel", True) is not False


def _save_setting(key: str, value) -> None:
    s = _settings()
    if isinstance(s, dict):
        s[key] = value
    try:
        from agent_friday.core import _save_settings
        _save_settings({key: value})
    except Exception as e:
        _log.warning("could not save %s: %s", key, e)


def _server_script() -> str:
    return str(Path(__file__).resolve().parent / "published_server.py")


def _cloudflared_path() -> Optional[str]:
    p = shutil.which("cloudflared")
    if p:
        return p
    for cand in (r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
                 r"C:\Program Files\cloudflared\cloudflared.exe"):
        if os.path.isfile(cand):
            return cand
    return None


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _spawn(argv: list, **kw) -> subprocess.Popen:
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, creationflags=flags, **kw)


def _read_tunnel_lines(proc: subprocess.Popen, timeout: float) -> list:
    """cloudflared prints the quick tunnel's hostname on stderr within a few
    seconds; read lines until it appears or the time is up."""
    lines: list = []
    deadline = time.time() + timeout

    def pump():
        try:
            for line in proc.stdout:          # stderr is merged into stdout
                lines.append(line)
                if _TUNNEL_URL_RE.search(line):
                    return
        except Exception:
            return

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    t.join(max(0.0, deadline - time.time()))
    return lines


def _probe(url: str, timeout: float = 4.0) -> bool:
    """Is a page reachable through the public address right now? A HEAD to
    a path that does not exist is enough: the server answers 404 with its own
    headers, and nothing is cached or logged."""
    try:
        req = urllib.request.Request(url.rstrip("/") + "/_reachability-probe/", method="HEAD",
                                     headers={"User-Agent": "Friday/reachability"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status in (200, 404)
    except urllib.error.HTTPError as e:      # 404 is the expected answer
        return e.code == 404
    except Exception:
        return False


def _write_state() -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_STATE, indent=1, default=str), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:
        _log.debug("state not written: %s", e)


def _alive(proc) -> bool:
    return proc is not None and proc.poll() is None


# ── lifecycle ────────────────────────────────────────────────────────────────

def start() -> dict:
    """Start the static server and its tunnel. Refused while the switch is off."""
    global _SERVER, _TUNNEL
    with _LOCK:
        if not enabled():
            _STATE.update({"serving": False, "url": None, "tunnel": False})
            _write_state()
            return status()
        if _under_test():
            _log.info("publish hosting: not started under a test run")
            return status()
        if _alive(_SERVER):
            return status()
        root = _published_root()
        root.mkdir(parents=True, exist_ok=True)
        port = _free_port()
        _SERVER = _spawn([sys.executable, _server_script(), "--root", str(root),
                          "--port", str(port), "--bind", "127.0.0.1"])
        _STATE.update({"serving": True, "port": port, "started_at": time.time(),
                       "url": "http://127.0.0.1:%d" % port, "tunnel": False, "reachable": None})
        cf = _cloudflared_path() if tunnel_wanted() else None
        if cf:
            # ONE url, and it is the static server. Never Friday's port.
            _TUNNEL = _spawn([cf, "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:%d" % port])
            lines = _read_tunnel_lines(_TUNNEL, timeout=25.0)
            for line in lines:
                m = _TUNNEL_URL_RE.search(line)
                if m:
                    _STATE.update({"url": m.group(0), "tunnel": True})
                    break
            if not _STATE["tunnel"]:
                _log.warning("cloudflared started but printed no tunnel hostname; pages serve on loopback only")
        _write_state()
        return status(refresh=True)


def stop() -> None:
    """Stop both processes. Idempotent."""
    global _SERVER, _TUNNEL
    with _LOCK:
        for proc in (_TUNNEL, _SERVER):
            if proc is None:
                continue
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        _TUNNEL = None
        _SERVER = None
        _STATE.update({"serving": False, "url": None, "tunnel": False, "reachable": None})
        _write_state()


def ensure_started() -> dict:
    with _LOCK:
        if enabled() and not _alive(_SERVER):
            return start()
        return status()


def set_enabled(on: bool) -> dict:
    """The owner's switch. Off stops everything now; on starts it again."""
    _save_setting("publish_this_pc_enabled", bool(on))
    if not on:
        stop()
        return status()
    return start()


# ── status ───────────────────────────────────────────────────────────────────

def public_base_url() -> Optional[str]:
    with _LOCK:
        if not enabled() or not _alive(_SERVER):
            return None
        return _STATE.get("url")


def status(refresh: bool = False) -> dict:
    with _LOCK:
        alive = _alive(_SERVER)
        if _STATE.get("serving") and not alive:
            _STATE.update({"serving": False})
        if not alive:
            _STATE.update({"url": None if not _STATE.get("serving") else _STATE.get("url")})
        tunnel_alive = _alive(_TUNNEL)
        if _STATE.get("tunnel") and not tunnel_alive:
            _STATE["tunnel"] = False
            if alive and _STATE.get("port"):
                _STATE["url"] = "http://127.0.0.1:%d" % _STATE["port"]
        if refresh and alive and _STATE.get("url"):
            _STATE["reachable"] = bool(_probe(_STATE["url"]))
            _STATE["probed_at"] = time.time()
        elif not alive:
            _STATE["reachable"] = False if _STATE.get("reachable") is not None or not enabled() else None
        out = dict(_STATE)
        out["enabled"] = enabled()
        out["serving"] = alive
        out["under_test"] = _under_test()
        out["url"] = _STATE.get("url") if alive else None
        out["published"] = len([p for p in _published_root().iterdir() if p.is_dir() and not p.name.startswith(".")]) \
            if _published_root().exists() else 0
        return out


def status_line(refresh: bool = False) -> str:
    """One plain sentence for the header and the card."""
    st = status(refresh=refresh)
    if not st["enabled"]:
        return "Published pages are offline: local hosting is switched off."
    if not st["serving"]:
        return "Published pages are not reachable: the page server is not running."
    if not st.get("tunnel"):
        why = "the tunnel is switched off" if not tunnel_wanted() else "no tunnel"
        return "Published pages are served on this PC only (%s): %s" % (why, st["url"])
    if st.get("reachable") is False:
        return "Published pages are not reachable right now at %s (the tunnel may be down)." % st["url"]
    if st.get("reachable") is True:
        return "Published pages are reachable at %s while this PC is on." % st["url"]
    return "Published pages are served at %s while this PC is on (not yet probed)." % st["url"]


# ── hosted adapters: connected or not ────────────────────────────────────────

def _stored_secret(name: str) -> Optional[str]:
    try:
        from agent_friday.services import credential_store as _cs
        get = getattr(_cs, "get_provider_key", None)
        if callable(get):
            return get("publish_" + name) or None
    except Exception:
        return None
    return None


def adapter_connected(adapter: str) -> bool:
    return bool(_stored_secret(adapter))


def _store_secret(name: str, value: Optional[str]) -> None:
    from agent_friday.services import credential_store as _cs
    if value is None:
        _cs.delete_provider_key("publish:" + name)
    else:
        _cs.set_provider_key("publish:" + name, value)


def connect_adapter(adapter: str, token: str, *, account_id: str = "", project: str = "",
                    repo: str = "", branch: str = "") -> None:
    """Store a hosted adapter's connection: the token plus the account or
    repository it publishes to, as one encrypted record. Values are never
    logged or returned."""
    if adapter == "cloudflare_pages":
        if not account_id or not project:
            raise ValueError("Cloudflare Pages needs an account id and a project name")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,57}", project):
            raise ValueError("a Pages project name is lowercase letters, digits and dashes")
        rec = {"token": token, "account_id": account_id, "project": project}
    elif adapter == "github_pages":
        if not repo or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("GitHub Pages needs a repository as owner/name")
        rec = {"token": token, "repo": repo, "branch": branch or "gh-pages"}
    else:
        raise ValueError("unknown adapter %r" % adapter)
    _store_secret(adapter, json.dumps(rec))


def disconnect_adapter(adapter: str) -> None:
    _store_secret(adapter, None)


def connection(adapter: str) -> Optional[dict]:
    raw = _stored_secret(adapter)
    if not raw:
        return None
    try:
        rec = json.loads(raw)
        return rec if isinstance(rec, dict) and rec.get("token") else None
    except Exception:
        return None


def publish_remote(adapter: str, bundle) -> str:
    """Hosted adapters land in the next increment; until then they refuse."""
    raise RuntimeError("%s is not available yet" % adapter)


def unpublish_remote(entry: dict) -> None:
    raise RuntimeError("%s is not available yet" % entry.get("adapter"))
