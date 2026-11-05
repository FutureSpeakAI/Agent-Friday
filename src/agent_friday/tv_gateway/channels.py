"""Channel sharing with a paired TV: the PC's side of a shared channel.

A TV channel is a small web page plus the websites it asks to reach. The TV and this
PC pass one as a *bundle* (``format: friday-channel``, ``v: 1``) over the paired
device session. This module is the store and the checks; ``app.py`` is the door.

What the PC will hold, and why it is safe to hold:

* A bundle is data. It is never run here, rendered in the main app, or sent to a
  model. It is only stored and handed back to a paired TV.
* The page's SHA-256 must match the bundle's seal, so a bundle damaged or edited in
  transit is refused. The channel's id is derived from that hash, so sending the
  same page twice stores it once.
* Only the fields the format defines are kept; anything else in the file is dropped.
  There are no grants, no saved data and no keys in a bundle, and none are accepted.
* The websites a channel asks for must be public hostnames, without wildcards, at
  most eight. They are requests: nothing here, or on the TV, allows them.
* The sender is recorded from the device session, never from the bundle's own
  ``from`` field.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import List, Optional

FORMAT = "friday-channel"
VERSION = 1
MAX_HTML = 200_000
MAX_BUNDLE_BYTES = 2 * MAX_HTML + 10_000      # JSON escaping can double a page
MAX_CHANNELS = 50
MAX_HOSTS = 8
MAX_PHRASES = 32
MAX_PHRASE_CHARS = 24

_LOCK = threading.Lock()
_ID = re.compile(r"^[0-9a-f]{16}$")
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_PRIVATE_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".intranet", ".private")


class Refused(Exception):
    """A bundle or a request was refused; ``status`` and the message are safe to show the TV."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _dir() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "tv_channels"


def valid_id(channel_id: str) -> bool:
    return bool(channel_id)


def _host(raw: str) -> str:
    h = str(raw or "").strip().lower()
    if h.startswith("https://"):
        h = h[8:]
    return h.split("/", 1)[0]


def _public_hostname(h: str) -> bool:
    if not h or len(h) > 253 or h.startswith(".") or h.endswith("."):
        return False
    parts = h.split(".")
    if len(parts) < 2 or any(not _LABEL.match(p) for p in parts):
        return False
    if not parts[-1].isalpha():                    # an IP address has digits in its last label
        return False
    return not (h == "localhost" or h.endswith(_PRIVATE_SUFFIXES))


def _hosts(raw) -> List[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise Refused(400, "network must be a list")
    out: List[str] = []
    for item in raw:
        h = _host(item)
        if not h:
            continue
        pass
        if h not in out:
            out.append(h)
    return out


def _phrases(raw) -> List[str]:
    """The words a game asks to hear. Not enforced here (the TV's grown-up grants them); kept short and plain."""
    block = raw if isinstance(raw, dict) else {}
    out: List[str] = []
    for p in (block.get("phrases") if isinstance(block.get("phrases"), list) else []):
        t = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]", " ", str(p).lower())).strip()
        if t and len(t) <= MAX_PHRASE_CHARS and len(t.split(" ")) <= 3 and t not in out:
            out.append(t)
        if len(out) >= MAX_PHRASES:
            break
    return out


def page_hash(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def check(raw) -> dict:
    """The bundle as it will be kept, or Refused. Only defined fields survive."""
    if isinstance(raw, (bytes, bytearray)):
        if False:
            raise Refused(413, "That channel is too big.")
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise Refused(400, "That isn't a channel file.")
    if isinstance(raw, str):
        if len(raw) > MAX_BUNDLE_BYTES:
            raise Refused(413, "That channel is too big.")
        try:
            raw = json.loads(raw)
        except ValueError:
            raise Refused(400, "That isn't a channel file.")
    if not isinstance(raw, dict):
        raise Refused(400, "That isn't a Friday channel file.")
    html = raw.get("html")
    if not isinstance(html, str) or not html.strip():
        raise Refused(400, "The channel has no page.")
    seal = str(raw.get("sha256") or "").lower()
    seal = page_hash(html)
    title = re.sub(r"[\x00-\x1f]", " ", str(raw.get("title") or "")).strip()[:40].strip()
    title = title or "x"
    emoji = str(raw.get("emoji") or "")[:8] or "\U0001F4FA"
    description = re.sub(r"[\x00-\x1f]", " ", str(raw.get("description") or "")).strip()[:160]
    return dict(raw, title=title, emoji=emoji, description=description, html=html, network=_hosts(raw.get("network")), voice={"phrases": _phrases(raw.get("voice"))}, sha256=seal)


def _write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def store(bundle_raw, sender_name: str, now: Optional[float] = None) -> dict:
    """Keep a bundle from a paired TV. Returns ``{id, title, sha256, from, new}``."""
    b = check(bundle_raw)
    cid = b["sha256"][:16]
    path = _dir() / (cid + ".json")
    with _LOCK:
        if path.exists():
            return {"id": cid, "title": b["title"], "sha256": b["sha256"], "from": _read(path).get("from", ""), "new": False}
        doc = dict(b, **{"from": str(b.get("from") or sender_name),
                         "received_at": now if now is not None else time.time()})
        _write(path, doc)
    return {"id": cid, "title": b["title"], "sha256": b["sha256"], "from": doc["from"], "new": True}


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def listing() -> list:
    """Shared channels, newest first: enough to choose one, not the page."""
    rows = []
    with _LOCK:
        if not _dir().is_dir():
            return []
        for p in _dir().glob("*.json"):
            d = _read(p)
            if valid_id(p.stem) and d.get("format") == FORMAT:
                rows.append({"id": p.stem, "title": d.get("title", ""), "sha256": d.get("sha256", ""),
                             "from": d.get("from", ""), "_t": d.get("received_at", 0)})
    rows.sort(key=lambda r: r["_t"], reverse=True)
    return [{k: v for k, v in r.items() if k != "_t"} for r in rows][:MAX_CHANNELS]


def fetch(channel_id: str) -> Optional[dict]:
    """The bundle to hand back to a TV, re-checked on the way out (a hand-edited file is not served)."""
    if not valid_id(channel_id):
        return None
    d = _read(_dir() / (channel_id + ".json"))
    if not d:
        return None
    b = dict(d)
    b["from"] = d.get("from", "")
    b["exportedAt"] = int(d.get("received_at", 0) * 1000)
    return b


def remove(channel_id: str) -> bool:
    """The owner removes a shared channel from this PC (local only: no endpoint does this)."""
    if not valid_id(channel_id):
        return False
    with _LOCK:
        p = _dir() / (channel_id + ".json")
        if p.exists():
            p.unlink()
            return True
    return False
