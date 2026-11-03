"""LAN discovery: answer a TV's broadcast with the gateway's port and pin.

The reply is a hint, never trust. A TV that is already paired checks the
certificate it then meets against the SPKI it pinned; a TV that is pairing
compares six digits with the owner (D2). So a spoofed reply can at worst
send the TV to a door it will refuse.
"""
from __future__ import annotations

import json
import socket
import threading
from typing import Optional

DISCOVERY_PORT = 47811
PROBE = b"FRIDAY-TV-DISCOVER v1"


def reply_for(data: bytes, gateway_port: int, spki_sha256: str) -> Optional[bytes]:
    """The answer to one datagram, or None for anything that is not our probe."""
    if data.strip() != PROBE:
        return None
    return json.dumps({"service": "agent-friday-tv-gateway", "v": 1,
                       "port": int(gateway_port), "spki_sha256": spki_sha256},
                      separators=(",", ":")).encode("utf-8")


class Responder:
    def __init__(self, host: str, gateway_port: int, spki_sha256: str, port: int = DISCOVERY_PORT):
        if host in ("", "0.0.0.0", "::"):
            raise ValueError("discovery binds one address, never a wildcard")
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((host, port))
        self._sock.settimeout(1.0)
        self._gateway_port, self._spki = gateway_port, spki_sha256
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="tv-discovery", daemon=True)

    @property
    def port(self) -> int:
        return self._sock.getsockname()[1]

    def start(self) -> "Responder":
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                return
            out = reply_for(data, self._gateway_port, self._spki)
            if out is not None:
                try:
                    self._sock.sendto(out, addr)
                except OSError:
                    pass

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
