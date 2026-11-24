"""Off-LAN reach for already-paired devices, over Tailscale (WireGuard) and nothing else.

Stephen approved it on 2026-11-14: his phone, tablet and watch-via-phone must reach this PC away from home
without anything being exposed to the internet. The promises:

* a second gateway listener binds ONE address, this PC's Tailscale one (100.64.0.0/10, on the adapter named
  Tailscale). ``serve`` refuses any other address, so it can never be a wildcard, the LAN address or a public
  one. Discovery (UDP) stays on the LAN listener's own address and is never offered here.
* pairing is LAN-only. The tailnet listener refuses every /tv/v1/pair/* path, so a new device can only be
  paired from the home network, the same as before. It also refuses a peer whose address is not a tailnet one.
  What crosses it is the same device-session door: every request is still checked by device token, member
  and rights (``check_session``); a receipt records ``via``: ``lan`` or ``tailnet``.
* the interfaces are chosen by adapter, never by the default route, so a VPN that takes the default route
  (PIA) cannot make the gateway pick its tunnel address as "the LAN" or lose the Tailscale one.
* a paired device is told, once it holds a session, which tailnet address and name to fail over to
  (``info()``, put in session/open and whoami). Nothing about the tailnet is shown before a session.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import subprocess
import threading
from typing import Optional

_log = logging.getLogger(__name__)

TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
_CLI = (r"C:\Program Files\Tailscale\tailscale.exe", "/usr/bin/tailscale", "/usr/local/bin/tailscale",
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale")
# Adapters that are never "the home network": VPN tunnels, virtual switches, containers, Bluetooth, loopback.
_NOT_LAN = re.compile(r"pia|wintun|openvpn|tailscale|wireguard|vpn|tap-|vethernet|hyper-v|virtualbox|vmware|"
                      r"loopback|bluetooth|wsl|docker|utun|zerotier|tun\d", re.I)

_state: dict = {"server": None, "address": None, "port": None, "name": None}
_lock = threading.RLock()                # serve() reads info() while holding it


# ── who is on the tailnet ────────────────────────────────────────────────────

def is_tailnet_address(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(str(addr or "").strip())
    except ValueError:
        return False
    return ip in TAILNET_V4 if ip.version == 4 else ip in TAILNET_V6


def _nics() -> list:
    """(adapter name, is up, [IPv4 strings]) for every adapter; [] when it cannot be listed."""
    try:
        import psutil
        stats = psutil.net_if_stats()
        out = []
        for name, addrs in psutil.net_if_addrs().items():
            v4 = [a.address for a in addrs if getattr(a.family, "name", str(a.family)) == "AF_INET"]
            out.append((name, bool(stats.get(name) and stats[name].isup), v4))
        return out
    except Exception:
        return []


def _cli(*args, timeout: float = 4.0) -> Optional[str]:
    exe = next((c for c in _CLI if os.path.exists(c)), None)
    if exe is None:
        return None
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def address() -> Optional[str]:
    """This PC's Tailscale IPv4, or None when Tailscale is not installed, not signed in or not connected.
    Read from the adapter named Tailscale (so a VPN's default route cannot change the answer), then from
    ``tailscale ip -4``. Always checked to be a tailnet address."""
    for name, up, v4 in _nics():
        if up and "tailscale" in name.lower():
            for a in v4:
                if is_tailnet_address(a):
                    return a
    out = (_cli("ip", "-4") or "").split()
    return next((a for a in out if is_tailnet_address(a)), None)


def dns_name() -> Optional[str]:
    """This PC's MagicDNS name (Self.DNSName), or None."""
    raw = _cli("status", "--json")
    try:
        name = ((json.loads(raw).get("Self") or {}).get("DNSName") or "").rstrip(".") if raw else ""
    except ValueError:
        name = ""
    return name or None


def lan_address() -> Optional[str]:
    """This PC's IPv4 on the home network. Chosen from the adapters (private, up, not a VPN, container,
    virtual switch or link-local address), so PIA being on cannot make a tunnel address "the LAN"."""
    cands = []
    for name, up, v4 in _nics():
        if not up or _NOT_LAN.search(name):
            continue
        for a in v4:
            ip = ipaddress.ip_address(a)
            if ip.is_private and not ip.is_link_local and not ip.is_loopback and ip not in TAILNET_V4:
                cands.append(a)
    if not cands:
        return None
    # Several home addresses (Ethernet and Wi-Fi): prefer the one the system would use to reach a private net.
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.255.255", 9))
        routed = s.getsockname()[0]
    except OSError:
        routed = None
    finally:
        s.close()
    if routed in cands:
        return routed
    return sorted(cands, key=lambda a: (not a.startswith("192.168."), a))[0]


# ── the listener ─────────────────────────────────────────────────────────────

def serve(host: str, port: int) -> dict:
    """Serve the gateway on the Tailscale address only; ValueError for any other address."""
    from agent_friday.tv_gateway import app as gateway, tls
    host = str(host or "").strip()
    if not is_tailnet_address(host):
        raise ValueError("the tailnet listener binds only this PC's Tailscale address, never a LAN, public or wildcard one")
    with _lock:
        _stop_locked()
        srv = gateway.make_bounded_server(host, int(port), gateway.create_app(tailnet=True),
                                          ssl_context=tls.server_context())
        threading.Thread(target=srv.serve_forever, name="tv-gateway-tailnet", daemon=True).start()
        _state.update(server=srv, address=host, port=srv.port, name=dns_name())
        _log.info("TV gateway also listening on the tailnet at %s:%s", host, srv.port)
        return info() or {}


def _stop_locked() -> None:
    srv = _state.get("server")
    if srv is not None:
        from agent_friday.tv_gateway import app as gateway
        gateway.close_server(srv)
    _state.update(server=None, address=None, port=None, name=None)


def stop() -> None:
    with _lock:
        _stop_locked()


def info() -> Optional[dict]:
    """What a device that holds a session is told to fail over to; None when the tailnet door is not open."""
    with _lock:
        if _state.get("server") is None:
            return None
        return {"address": _state["address"], "name": _state["name"], "port": _state["port"]}


def ensure(port: int) -> Optional[dict]:
    """Open, move or close the tailnet listener to match Tailscale right now. Never raises."""
    try:
        addr = address()
        if addr == _state.get("address") and (addr is None or _state.get("server") is not None):
            return info()
        if addr is None:
            stop()
            return None
        return serve(addr, port)
    except Exception as e:
        _log.warning("tailnet listener not started: %s", e)
        return None


def watch(port: int, every_s: float = 30.0) -> threading.Thread:
    """Follow Tailscale coming up after the PC does (sign-in, reconnect, a new address)."""
    stop_flag = threading.Event()

    def run():
        while not stop_flag.wait(every_s):
            ensure(port)
    t = threading.Thread(target=run, name="tv-gateway-tailnet-watch", daemon=True)
    t.start()
    return t


def via() -> str:
    """``tailnet`` or ``lan``: which listener carries the request being served (for receipts)."""
    try:
        from flask import g, has_request_context
        return str(getattr(g, "via", "lan")) if has_request_context() else "lan"
    except Exception:
        return "lan"
