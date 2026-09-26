"""Friday's address on this PC: https://agent.<name>.

ONE SETTING, EVERY CONSUMER. The address comes from the agent's name chosen at
setup ("AGENT FRIDAY" -> agent.friday) unless the person picks another in
Settings, and everything that needs it asks here: the workspace-tab links (the
page is told at serve time, see routes/core_routes._serve_index), the tray's
"Open Friday Desktop", Friday's own listeners (services/local_proxy.py) and the
local-trust rule in core (`_forwarded_host_is_local`).

WHAT "WORKS" MEANS. An address is only ever used after Friday has reached ITSELF
through it: the name resolves to this machine, something answers on it, and
what answers is this very process (a per-process id on /api/local-address/ping).
So a test server never sends its tabs to the real Friday, a stale proxy pointed
at nothing is never linked to, and the page keeps working on whatever address
it is already on until the better one is proven.

TWO STEPS NEED THE PERSON, AND WINDOWS ASKS THEM BOTH TIMES.
  * Adding agent.<name> to this PC's hosts file changes a system file, so it
    runs elevated -- Windows shows its "Do you want to allow this app to make
    changes" (UAC) prompt.
  * Trusting Friday's certificate authority is a change to what this PC's
    browsers believe. It runs `certutil -user -addstore Root`, and Windows shows
    its own Security Warning naming the authority and its thumbprint, which the
    Settings card prints alongside so the two can be compared.
Both are started only by a button in Settings (routes/local_address.py: local
requests carrying the page's token), never at startup, and
`_run_system_command` refuses to run at all under a test.

THE PLAIN-HTTP FALLBACK IS HONEST ABOUT WHAT IT LOSES. Until the certificate is
trusted, http://agent.<name> works, but browsers treat any http address other
than localhost as insecure and switch off the microphone and camera there. The
Settings card says so; voice keeps working at http://localhost:3000.

GOOGLE SIGN-IN STAYS ON LOCALHOST. Google accepts only a loopback redirect for
a Desktop client, or an https redirect registered in the person's own Google
Cloud project for a Web client. The return trip keeps using
http://localhost:<port>, which completes wherever the sign-in was started
(the pending state is kept server-side), unless the person confirms they
registered https://agent.<name> AND the client is a Web client -- see
set_oauth_named().
"""
from __future__ import annotations

import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

from agent_friday.paths import friday_home
from agent_friday.user_errors import ExceptionText

DEFAULT_SLUG = "friday"
PING_PATH = "/api/local-address/ping"
MARK_BEGIN = "# >>> Agent Friday local address >>>"
MARK_END = "# <<< Agent Friday local address <<<"

#: This server process, as /api/local-address/ping reports it.
INSTANCE_ID = secrets.token_hex(8)

_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_HOST = re.compile(r"^agent\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

#: Endings that already belong to the public internet. agent.<name> in the hosts
#: file would hide the real website of that name on this PC, and several of
#: these (app, dev, page, new, ...) are HTTPS-only in every browser, so the
#: plain address could never work either. Every two-letter ending is a country
#: code, so those are refused wholesale. Not the whole IANA list -- the common
#: ones and the browser-preloaded ones -- and a name that slips through only
#: ever affects agent.<that name> on this one PC.
_PUBLIC_ENDINGS = frozenset("""
com net org edu gov mil int info biz name pro mobi aero asia cat coop jobs museum
tel travel xxx post app dev page new day foo zip mov esq prof boo channel dad phd
ing meme nexus rsvp how soy fly eat android chrome google youtube gmail play search
io ai co me tv cc ws bot art blog cloud online site store shop tech xyz top club
live life world today one network email link click help news media web wiki
digital agency company solutions services systems center global group team zone
codes tools software computer technology gmbh inc llc ltd law bank insurance
amazon apple microsoft windows azure office bing xbox skype hotmail outlook
facebook meta instagram whatsapp netflix spotify twitter x adobe ibm intel
""".split())

#: Endings that are not websites but are spoken for all the same.
_SPECIAL_ENDINGS = {
    "local": "is how devices on a home network find each other",
    "onion": "belongs to the Tor network",
    "arpa": "is reserved for the internet's own plumbing",
    "localhost": "already means this PC in every browser",
}

_TRUTHY = {"1", "true", "yes", "on"}
_STATE_TTL_S = 300          # a proven address is re-checked every five minutes
_HOST_MEMO = {"key": None, "value": None}
_STATUS = {"value": None, "ts": 0.0, "refreshing": False, "again": False}
_STATUS_LOCK = threading.Lock()
_PROXY = {"proxy": None}
_JOB = {"kind": None, "state": "idle", "message": "", "started": 0.0, "finished": 0.0}
_JOB_LOCK = threading.Lock()


# ── the name ───────────────────────────────────────────────────────────────
def slug_from_name(name) -> str:
    """"AGENT FRIDAY" -> "friday", "Mr. Robot" -> "mr-robot", "Ágata" -> "agata".

    A leading "agent" word is dropped because the address already begins with
    it. Anything that leaves nothing usable (all punctuation, a script with no
    Latin letters) comes back as "" and the caller falls back to friday.
    """
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    s = s.lower().strip()
    s = re.sub(r"^agent(?:[\s._-]+|$)", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:63].strip("-")


def host_problem(host: str) -> str:
    """Why `host` cannot be Friday's address, in plain words; '' when it can."""
    h = str(host or "").strip().lower()
    if not _HOST.match(h):
        return ("An address here is agent. followed by one word of letters, digits "
                "or dashes -- for example agent.friday.")
    ending = h.split(".", 1)[1]
    if ending.isdigit():
        return "The part after agent. cannot be only digits."
    if len(ending) == 2 or ending in _PUBLIC_ENDINGS:
        return (f".{ending} is a real internet domain ending, so agent.{ending} "
                f"could hide a real website on this PC. Pick another word.")
    if ending in _SPECIAL_ENDINGS:
        return (f".{ending} {_SPECIAL_ENDINGS[ending]}, so agent.{ending} would not "
                f"work reliably. Pick another word.")
    return ""


def resolve_host(settings: dict | None = None) -> tuple:
    """(host, source, note). source is "custom" or "agent name"."""
    s = settings if settings is not None else _settings()
    block = (s.get("local_address") or {}) if isinstance(s, dict) else {}
    custom = str(block.get("host") or "").strip().lower()
    if custom and not host_problem(custom):
        return custom, "custom", ""
    name = (s or {}).get("agent_name") or "AGENT FRIDAY"
    slug = slug_from_name(name)
    note = ""
    if custom:
        note = f"The saved address {custom!r} is not usable ({host_problem(custom)}), so the agent name is used."
    host = f"agent.{slug}" if slug else f"agent.{DEFAULT_SLUG}"
    problem = host_problem(host)
    if problem:
        note = (f"Friday's name gives {host}, but {problem[0].lower() + problem[1:]} "
                f"Friday uses agent.{DEFAULT_SLUG} instead.")
        host = f"agent.{DEFAULT_SLUG}"
    elif not slug:
        note = f"The agent's name does not give an address of its own, so Friday uses agent.{DEFAULT_SLUG}."
    return host, "agent name", note


def configured_host(settings: dict | None = None) -> str:
    """The one address, e.g. "agent.friday". Cheap: memoised on its inputs."""
    s = settings if settings is not None else _settings()
    key = ((s or {}).get("agent_name"), json.dumps((s or {}).get("local_address") or {}, sort_keys=True))
    if _HOST_MEMO["key"] == key and _HOST_MEMO["value"]:
        return _HOST_MEMO["value"]
    host = resolve_host(s)[0]
    _HOST_MEMO.update(key=key, value=host)
    return host


def local_hosts() -> set:
    """Names a proxy on this machine may present as X-Forwarded-Host."""
    try:
        return {configured_host()}
    except Exception:
        return set()


def origin(scheme: str, host: str, port: int) -> str:
    default = 443 if scheme == "https" else 80
    return f"{scheme}://{host}" + ("" if int(port) == default else f":{int(port)}")


# ── settings & files ───────────────────────────────────────────────────────
def _settings() -> dict:
    """The server's settings when running inside it; the file otherwise (the tray)."""
    core = sys.modules.get("agent_friday.core")
    if core is not None and hasattr(core, "_load_settings"):
        try:
            return core._load_settings() or {}
        except Exception:
            pass
    try:
        return json.loads((friday_home() / "settings.json").read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _block(s: dict | None = None) -> dict:
    b = dict(((s if s is not None else _settings()) or {}).get("local_address") or {})
    b.setdefault("serve", False)
    b.setdefault("https_port", 443)
    b.setdefault("http_port", 80)
    return b


def save_block(patch: dict) -> dict:
    """Merge `patch` into settings.local_address (server process only)."""
    import agent_friday.core as core
    cur = _block(core._load_settings_raw())
    cur.update(patch)
    core._save_settings({"local_address": cur})
    _HOST_MEMO.update(key=None, value=None)
    return cur


def state_dir() -> Path:
    return friday_home() / "local-address"


# ── is it working? ─────────────────────────────────────────────────────────
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _opener(verify: bool):
    ctx = ssl.create_default_context() if verify else ssl._create_unverified_context()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(),
                                       urllib.request.HTTPSHandler(context=ctx))


def _short(e) -> str:
    r = getattr(e, "reason", None)
    return ExceptionText(str(r if r is not None else e)[:160])


def resolves_to_loopback(host: str) -> bool:
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(host, None)}
    except Exception:
        return False
    import ipaddress
    try:
        return bool(addrs) and all(ipaddress.ip_address(a.split("%")[0]).is_loopback for a in addrs)
    except Exception:
        return False


def probe(base: str, timeout: float = 2.5) -> dict:
    """Ask `base` for Friday's ping. Never follows a redirect, never uses a proxy.

    trusted: for https, whether the certificate verified against what Windows
    trusts (None for http). friday: a Friday answered. instance: which one.
    redirect: where a 3xx pointed.
    """
    out = {"origin": base, "answers": False, "friday": False, "instance": None,
           "trusted": None, "redirect": None, "error": ""}
    https = base.startswith("https://")
    for verify in ((True, False) if https else (True,)):
        try:
            with _opener(verify).open(base + PING_PATH, timeout=timeout) as r:
                try:
                    body = json.loads(r.read(4096) or b"{}")
                except Exception:
                    body = {}
            out.update(answers=True, friday=bool(body.get("friday")),
                       instance=body.get("instance"))
        except urllib.error.HTTPError as e:
            out["answers"] = True
            if 300 <= e.code < 400:
                out["redirect"] = e.headers.get("Location")
            else:
                out["error"] = f"HTTP {e.code}"
        except Exception as e:
            reason = getattr(e, "reason", e)
            if https and verify and isinstance(reason, ssl.SSLCertVerificationError):
                out["trusted"] = False
                out["error"] = "the certificate is not trusted by Windows"
                continue                        # who answered, then, unverified?
            if not out["error"]:
                out["error"] = _short(e)
            return out
        if https and out["trusted"] is None:
            out["trusted"] = bool(verify)
        return out
    return out


def _proxy_status():
    p = _PROXY["proxy"]
    return p.status() if p is not None else None


def compute_status() -> dict:
    """Everything the Settings card shows. Blocking (probes); call off-request."""
    s = _settings()
    b = _block(s)
    host, source, note = resolve_host(s)
    hp, sp = int(b.get("http_port") or 80), int(b.get("https_port") or 443)
    https_o, http_o = origin("https", host, sp), origin("http", host, hp)
    resolves = resolves_to_loopback(host)
    if resolves:
        hs, hh = probe(https_o), probe(http_o)
    else:
        hs = {"origin": https_o, "answers": False, "error": "this PC does not know the name yet"}
        hh = {"origin": http_o, "answers": False, "error": "this PC does not know the name yet"}
    mine_s = hs.get("instance") == INSTANCE_ID
    mine_h = hh.get("instance") == INSTANCE_ID
    secure = bool(resolves and mine_s and hs.get("trusted"))
    plain = bool(resolves and (mine_h or (secure and hh.get("redirect"))))
    proxy = _proxy_status()

    def served_by(kind, pr, mine):
        mp = (proxy or {}).get(kind) or {}
        if mp.get("listening") and mine:
            return "friday"
        if pr.get("answers"):
            return "this Friday, through another program" if mine else "another program"
        return None

    ca = {}
    try:
        from agent_friday.services import local_ca
        ca = local_ca.info(state_dir())
        if ca.get("exists"):
            roots = local_ca.windows_root_thumbprints()
            ca["trusted"] = ca["sha1"] in roots
            ca["previous_trusted"] = [p for p in (ca.get("previous") or []) if p in roots]
        ca.pop("path", None)            # a file path names this PC's user; it stays here
    except Exception as e:
        ca = {"exists": False, "error": _short(e)}
    preferred = https_o if secure else (http_o if (plain and mine_h) else None)
    return {
        "host": host, "source": source, "note": note,
        "agent_name": (s or {}).get("agent_name") or "",
        "custom_host": str(b.get("host") or ""),
        "resolves": resolves,
        "hosts_entry": hosts_entry_state(host),
        "https": dict(hs, served_by=served_by("https", hs, mine_s), works=bool(resolves and mine_s)),
        "http": dict(hh, served_by=served_by("http", hh, mine_h), works=plain),
        "secure": secure,
        "preferred_origin": preferred,
        "serve": bool(b.get("serve")),
        "ports": {"https": sp, "http": hp},
        "listeners": proxy,
        "ca": ca,
        "job": job_status(),
        "oauth": oauth_info(s, https_o if secure else None),
        "platform": sys.platform,
        "checked_at": time.time(),
    }


def status(refresh: bool = False) -> dict:
    with _STATUS_LOCK:
        cached, age = _STATUS["value"], time.time() - _STATUS["ts"]
    if refresh or cached is None:
        return _store(compute_status())
    out = dict(cached)
    out["job"] = job_status()
    if age > _STATE_TTL_S:
        refresh_in_background()
    return out


def _store(st: dict) -> dict:
    with _STATUS_LOCK:
        _STATUS.update(value=st, ts=time.time())
    return st


def refresh_in_background(delay: float = 0.0, wait_for_port: int | None = None) -> None:
    """Re-check off the request path. A request for a re-check that arrives
    while one is running is not dropped: that one runs again when it ends, so
    the answer after a Windows step is never the one from before it."""
    with _STATUS_LOCK:
        if _STATUS["refreshing"]:
            _STATUS["again"] = True
            return
        _STATUS["refreshing"] = True

    def run():
        try:
            if delay:
                time.sleep(delay)
            if wait_for_port:
                _wait_for_self(wait_for_port)
            while True:
                _store(compute_status())
                with _STATUS_LOCK:
                    if not _STATUS["again"]:
                        break
                    _STATUS["again"] = False
        except Exception:
            pass
        finally:
            with _STATUS_LOCK:
                _STATUS["refreshing"] = False

    threading.Thread(target=run, name="friday-local-address-check", daemon=True).start()


def _wait_for_self(port: int, limit_s: float = 300) -> None:
    """Until this process answers its own ping on loopback (boot can take minutes)."""
    end = time.time() + limit_s
    while time.time() < end:
        if probe(f"http://127.0.0.1:{int(port)}", timeout=2).get("instance") == INSTANCE_ID:
            return
        time.sleep(1.0)


def page_info() -> dict:
    """What the page needs to build tab links, from the last check. Never blocks.

    `origin` is set only when this very process was reached through it.
    """
    with _STATUS_LOCK:
        st, age = _STATUS["value"], time.time() - _STATUS["ts"]
    if st is None or age > _STATE_TTL_S:
        refresh_in_background()
    try:
        host = configured_host()
    except Exception:
        host = ""
    st = st or {}
    core = sys.modules.get("agent_friday.core")
    try:
        local = core.server_base_url() if core is not None else "http://localhost:3000"
    except Exception:
        local = "http://localhost:3000"
    return {"host": host, "origin": st.get("preferred_origin") or None,
            "secure": bool(st.get("secure")),
            # where voice always works: the plain loopback address
            "local": local}


def open_url(fallback: str, timeout: float = 1.5) -> str:
    """For the tray: the best address that reaches the same Friday as `fallback`."""
    try:
        s = _settings()
        b = _block(s)
        host = configured_host(s)
        if not resolves_to_loopback(host):
            return fallback
        here = probe(fallback.rstrip("/"), timeout=timeout).get("instance")
        if not here:
            return fallback
        for base in (origin("https", host, b.get("https_port") or 443),
                     origin("http", host, b.get("http_port") or 80)):
            p = probe(base, timeout=timeout)
            if p.get("instance") == here and (base.startswith("http://") or p.get("trusted")):
                return base + "/"
    except Exception:
        pass
    return fallback


# ── Friday's own listeners ─────────────────────────────────────────────────
def start_listeners(upstream_port: int | None = None) -> dict:
    """Make or renew the certificate and open agent.<name> on 443 and 80.

    No system change: files in Friday's folder and loopback sockets. Whether a
    browser trusts the certificate is a separate step the person takes.
    """
    from agent_friday.services import local_ca, local_proxy
    if upstream_port is None:
        core = sys.modules.get("agent_friday.core")
        upstream_port = getattr(core, "SERVER_PORT", 3000) if core else 3000
    s = _settings()
    b = _block(s)
    host = configured_host(s)
    info = local_ca.ensure(state_dir(), host)
    stop_listeners()
    d = state_dir()
    proxy = local_proxy.LocalProxy(
        host=host, upstream_port=upstream_port,
        https_port=int(b.get("https_port") or 443), http_port=int(b.get("http_port") or 80),
        cert_file=str(d / local_ca.LEAF_CERT), key_file=str(d / local_ca.LEAF_KEY),
        redirect_http=_redirect_http)
    st = proxy.start()
    _PROXY["proxy"] = proxy
    return {"certificate": info, "listeners": st}


def stop_listeners() -> None:
    p = _PROXY.pop("proxy", None)
    _PROXY["proxy"] = None
    if p is not None:
        try:
            p.stop()
        except Exception:
            pass


def _redirect_http() -> bool:
    with _STATUS_LOCK:
        st = _STATUS["value"] or {}
    return bool(st.get("secure")) and ((st.get("https") or {}).get("served_by") == "friday")


def boot(upstream_port: int) -> None:
    """Server start: open Friday's listeners if the person turned them on, then
    find out which address works. Never prompts, never changes the system."""
    try:
        if _block().get("serve"):
            start_listeners(upstream_port)
    except Exception as e:
        print(f"  Local address: could not open agent.<name> ({_short(e)})")
    # once app.run() is serving, so the self-check can reach this process
    refresh_in_background(wait_for_port=upstream_port)

    def renew():
        while True:
            time.sleep(24 * 3600)
            try:
                if _PROXY["proxy"] is not None:
                    from agent_friday.services import local_ca
                    local_ca.ensure(state_dir(), configured_host())
                    _PROXY["proxy"].reload_certificate()
            except Exception:
                pass

    threading.Thread(target=renew, name="friday-local-address-renew", daemon=True).start()


# ── the hosts file ─────────────────────────────────────────────────────────
def _hosts_path() -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"


def hosts_entry_state(host: str) -> dict:
    """Read-only look at the hosts file: is the name there, and is it ours?"""
    try:
        text = _hosts_path().read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {"readable": False, "listed": False, "ours": False}
    listed = bool(re.search(r"(?mi)^[ 	]*(?:127\.0\.0\.1|::1)[ 	]+(?:\S+[ 	]+)*"
                            + re.escape(host) + r"(?=[ 	]|#|$)", text))
    ours = MARK_BEGIN in text and bool(re.search(
        re.escape(MARK_BEGIN) + r".*?" + re.escape(host) + r".*?" + re.escape(MARK_END), text, re.S))
    return {"readable": True, "listed": listed, "ours": ours}


def hosts_script(host: str, add: bool = True) -> str:
    """The PowerShell the elevated step runs. `host` is validated first, so it
    can only be agent.<letters, digits, dashes>."""
    if host_problem(host):
        raise ValueError(host_problem(host))
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "$hosts = Join-Path $env:SystemRoot 'System32\\drivers\\etc\\hosts'",
        f"$name = '{host}'",
        f"$begin = '{MARK_BEGIN}'",
        f"$end = '{MARK_END}'",
        "$text = ''",
        "if (Test-Path -LiteralPath $hosts) { $text = [IO.File]::ReadAllText($hosts) }",
        "$rx = '(?ms)^' + [regex]::Escape($begin) + '.*?^' + [regex]::Escape($end) + '[^\\r\\n]*(\\r?\\n)?'",
        "$text = [regex]::Replace($text, $rx, '')",
        f"if (${'true' if add else 'false'}) {{",
        "  if ($text.Length -gt 0 -and -not $text.EndsWith(\"`n\")) { $text += \"`r`n\" }",
        "  $text += \"$begin`r`n127.0.0.1`t$name`r`n::1`t`t$name`r`n$end`r`n\"",
        "}",
        "$item = Get-Item -LiteralPath $hosts -Force",
        "$ro = $item.IsReadOnly",
        "if ($ro) { $item.IsReadOnly = $false }",
        "$bak = \"$hosts.agent-friday.bak\"",
        "if (-not (Test-Path -LiteralPath $bak)) { Copy-Item -LiteralPath $hosts -Destination $bak -Force }",
        "[IO.File]::WriteAllText($hosts, $text, (New-Object System.Text.UTF8Encoding($false)))",
        "if ($ro) { (Get-Item -LiteralPath $hosts -Force).IsReadOnly = $true }",
        "ipconfig /flushdns | Out-Null",
        "exit 0",
    ])


def _powershell() -> str:
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" /
               "WindowsPowerShell" / "v1.0" / "powershell.exe")


def _certutil() -> str:
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "certutil.exe")


def hosts_command(host: str, add: bool = True) -> list:
    """An unelevated PowerShell that starts the elevated one and waits for it.
    Windows shows its UAC prompt; declining it ends the step with nothing changed."""
    import base64
    enc = base64.b64encode(hosts_script(host, add).encode("utf-16-le")).decode()
    ps = _powershell()
    outer = (
        "try { $p = Start-Process -FilePath '" + ps + "' -Verb RunAs -PassThru "
        "-WindowStyle Hidden -ArgumentList @('-NoProfile','-NonInteractive',"
        "'-ExecutionPolicy','Bypass','-EncodedCommand','" + enc + "') } "
        "catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1223 }; "
        # Holding the handle before waiting is what makes ExitCode readable
        # for an elevated child in Windows PowerShell 5.1.
        "$null = $p.Handle; $p.WaitForExit(); exit $p.ExitCode")
    return [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", outer]


# ── the two steps Windows asks about ───────────────────────────────────────
def _under_test() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST")
                or str(os.environ.get("FRIDAY_TESTING", "")).lower() in _TRUTHY
                or str(os.environ.get("FRIDAY_NO_SYSTEM_CHANGES", "")).lower() in _TRUTHY)


def _run_system_command(argv: list, timeout: float = 300) -> tuple:
    """The ONLY place this feature starts a process that changes the system.

    Refuses under pytest, under a FRIDAY_TESTING server and when
    FRIDAY_NO_SYSTEM_CHANGES is set: trust and the hosts file are changed by a
    person clicking in Settings and then answering Windows, never by a test.
    """
    if _under_test():
        raise RuntimeError("Friday never changes Windows' trusted certificates or "
                           "hosts file from a test or a test server.")
    if sys.platform != "win32":
        raise RuntimeError("This step is only available on Windows.")
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return p.returncode, ((p.stdout or "") + (p.stderr or ""))[-2000:]


def job_status() -> dict:
    with _JOB_LOCK:
        return dict(_JOB)


def _start_job(kind: str, waiting: str, fn) -> tuple:
    with _JOB_LOCK:
        if _JOB["state"] == "waiting":
            return False, "Another step is still waiting for your answer in Windows."
        _JOB.update(kind=kind, state="waiting", message=waiting,
                    started=time.time(), finished=0.0)

    def run():
        try:
            state, message = fn()
        except subprocess.TimeoutExpired:
            state, message = "failed", "No answer came from Windows within five minutes. Nothing changed."
        except Exception as e:
            state, message = "failed", _short(e)
        with _JOB_LOCK:
            _JOB.update(state=state, message=message, finished=time.time())
        refresh_in_background()

    threading.Thread(target=run, name=f"friday-local-address-{kind}", daemon=True).start()
    return True, ""


def start_hosts_job(add: bool = True) -> tuple:
    host = configured_host()
    problem = host_problem(host)
    if problem:
        return False, problem

    def work():
        rc, out = _run_system_command(hosts_command(host, add))
        known = resolves_to_loopback(host)
        if add and known:
            return "done", f"This PC now knows {host}."
        if not add and not hosts_entry_state(host).get("ours"):
            return "done", f"{host} was removed from this PC's hosts file."
        if rc == 1223:
            return "cancelled", "Windows' permission prompt was declined. Nothing changed."
        return "failed", f"The change did not take (code {rc}). {out.strip()[:200]}"

    verb = "add" if add else "remove"
    return _start_job("hosts", f"Waiting for you to answer Windows' permission prompt to {verb} {host}. "
                               "It may be behind this window -- check the taskbar.", work)


def start_trust_job() -> tuple:
    from agent_friday.services import local_ca
    info = local_ca.info(state_dir())
    if not info.get("exists"):
        return False, "Friday's certificate has not been made yet. Turn on the local address first."

    def work():
        rc, _out = _run_system_command([_certutil(), "-user", "-addstore", "Root", info["path"]])
        if info["sha1"] in local_ca.windows_root_thumbprints():
            return "done", "Windows now trusts Friday's certificate. Reload the page at the https address."
        if rc == 0:
            return "failed", "certutil finished, but Windows does not list the certificate as trusted."
        return "cancelled", "Windows did not add the certificate (the warning was answered No). Nothing changed."

    return _start_job("trust", "Waiting for you to answer Windows' Security Warning. It names "
                               f"\"{info['subject']}\" and shows the thumbprint {info['sha1']}. "
                               "It may be behind this window -- check the taskbar.", work)


def start_untrust_job() -> tuple:
    from agent_friday.services import local_ca
    info = local_ca.info(state_dir())
    roots = local_ca.windows_root_thumbprints()
    targets = [t for t in [info.get("sha1")] + list(info.get("previous") or []) if t and t in roots]
    if not targets:
        return False, "Windows does not trust any certificate Friday made, so there is nothing to remove."

    def work():
        for t in targets:
            _run_system_command([_certutil(), "-user", "-delstore", "Root", t.replace(":", "")])
        left = [t for t in targets if t in local_ca.windows_root_thumbprints()]
        if not left:
            return "done", "Windows no longer trusts Friday's certificate."
        return "cancelled", "Windows still trusts it (the confirmation was answered No)."

    return _start_job("untrust", "Waiting for you to confirm the removal in Windows. It may be "
                                 "behind this window -- check the taskbar.", work)


# ── Google sign-in ─────────────────────────────────────────────────────────
def _client_type():
    try:
        from agent_friday.services import google_accounts as ga
        from agent_friday.services.calendar_engine import _google_client_type
        cfg, _src = ga._google_client_config()
        return _google_client_type(cfg) if cfg else None
    except Exception:
        return None


def _callbacks(base: str) -> list:
    try:
        from agent_friday.services.calendar_engine import GOOGLE_CALLBACK_PATH
        from agent_friday.services.google_accounts import MULTI_CALLBACK_PATH
        return [base + MULTI_CALLBACK_PATH, base + GOOGLE_CALLBACK_PATH]
    except Exception:
        return []


def oauth_info(settings: dict | None = None, https_origin: str | None = None) -> dict:
    s = settings if settings is not None else _settings()
    override = str(((s or {}).get("google_oauth") or {}).get("redirect_base_override") or "")
    core = sys.modules.get("agent_friday.core")
    local_base = core.server_base_url() if core and hasattr(core, "server_base_url") else "http://localhost:3000"
    return {
        "override": override,
        "returns_to": override or local_base,
        "local_callbacks": _callbacks(local_base),
        "named_callbacks": _callbacks(https_origin) if https_origin else [],
        "client_type": _client_type(),
    }


def set_oauth_named(use: bool, confirmed: bool = False) -> tuple:
    """Point Google's return trip at https://agent.<name>, or back at localhost."""
    import agent_friday.core as core
    if not use:
        core._save_settings({"google_oauth": {"redirect_base_override": ""}})
        return True, "Google sign-in returns to localhost again."
    st = status(refresh=True)
    if not st.get("secure"):
        return False, "The secure address has to work first."
    if _client_type() != "web":
        return False, ("Friday's Google client is a Desktop client, and Google accepts only a "
                       "localhost return address for those. Nothing changed.")
    if not confirmed:
        return False, "Confirm that both addresses are registered in your Google Cloud project first."
    base = origin("https", st["host"], st["ports"]["https"])
    core._save_settings({"google_oauth": {"redirect_base_override": base}})
    return True, f"Google sign-in now returns to {base}."
