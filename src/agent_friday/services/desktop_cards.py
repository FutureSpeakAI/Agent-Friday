"""Durable, declarative Home cards with navigation-only actions.

Cards are plain text. Their buttons only open an enabled native workspace;
they never contain executable markup, URLs or automatic actions. Saving is
atomic and a failed read is never mistaken for an empty board.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import tempfile
import threading
import time
from pathlib import Path

from agent_friday.core import FRIDAY_DIR

CARDS_PATH = FRIDAY_DIR / "desktop_cards.json"
MAX_CARDS = 24
_MAX_BYTES = 512 * 1024
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_LOCK = threading.RLock()
_log = logging.getLogger("friday.desktop_cards")


class CardError(ValueError):
    """A card input or operation the owner can correct."""


def _text(value, field, limit, *, empty=False):
    if not isinstance(value, str) or len(value) > limit:
        raise CardError(f"{field} must be text of at most {limit} characters.")
    value = value.strip()
    if not empty and not value:
        raise CardError(f"{field} is required.")
    if any(ord(c) < 32 and c not in "\n\t\r" for c in value):
        raise CardError(f"{field} contains unsupported control characters.")
    return value


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise CardError("Card id must be 1–64 lowercase letters, digits, underscores or hyphens.")
    return value


def _card(payload, *, enabled=True):
    from agent_friday.services import workspace_registry

    if not isinstance(payload, dict) or set(payload) - {"id", "title", "body", "priority", "actions"}:
        raise CardError("A card accepts only id, title, body, priority and actions.")
    priority = payload.get("priority", 50)
    if type(priority) is not int or not 0 <= priority <= 100:
        raise CardError("Card priority must be a whole number between 0 and 100.")
    raw = payload.get("actions", [])
    if not isinstance(raw, list) or len(raw) > 3:
        raise CardError("A card can have at most three workspace actions.")
    actions = []
    for action in raw:
        if not isinstance(action, dict) or set(action) != {"label", "workspace"}:
            raise CardError("Each action needs only label and workspace.")
        workspace = action["workspace"]
        if not isinstance(workspace, str):
            raise CardError("An action needs a native workspace id.")
        target = workspace_registry.get(workspace)
        if not target or (target.get("boundary") or {}).get("kind", "native") != "native":
            raise CardError("Card actions can open only native workspaces.")
        if enabled and workspace_registry.is_held(target):
            raise CardError("That workspace is not available yet.")
        actions.append({"label": _text(action["label"], "Action label", 60), "workspace": workspace})
    return {"id": _id(payload.get("id")), "title": _text(payload.get("title"), "Title", 120),
            "body": _text(payload.get("body", ""), "Body", 2000, empty=True),
            "priority": priority, "actions": actions}


def _read():
    path = Path(CARDS_PATH)
    if not path.exists():
        return []
    with path.open("rb") as stream:
        raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise OSError("Home card store exceeds its size limit")
    try:
        doc = json.loads(raw)
        if not isinstance(doc, dict) or doc.get("version") != 1 or not isinstance(doc.get("cards"), list):
            raise ValueError("invalid card store")
        if len(doc["cards"]) > MAX_CARDS:
            raise ValueError("too many cards")
        cards, seen = [], set()
        for entry in doc["cards"]:
            if not isinstance(entry, dict):
                raise ValueError("invalid card")
            card = _card({k: v for k, v in entry.items() if k not in ("created_at", "updated_at")}, enabled=False)
            for key in ("created_at", "updated_at"):
                stamp = entry.get(key)
                if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0:
                    raise ValueError("invalid card timestamp")
                card[key] = stamp
            if card["id"] in seen:
                raise ValueError("duplicate card id")
            seen.add(card["id"])
            cards.append(card)
        return cards
    except (ValueError, TypeError, UnicodeError) as exc:
        raise OSError("Home card store could not be read") from exc


def _write(cards):
    path = Path(CARDS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"version": 1, "cards": cards}, ensure_ascii=False).encode("utf-8")
    if len(data) > _MAX_BYTES:
        raise CardError("The Home card board is full. Remove a card before saving another.")
    fd, name = tempfile.mkstemp(prefix=".desktop-cards-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _writable():
    from agent_friday.services import off_record
    if off_record.active():
        raise CardError("Off the record is on. Home cards were not changed because they are saved to disk.")


def _changed():
    try:
        from agent_friday.services import desktop_bus
        desktop_bus.broadcast({"type": "desktop_cards_changed"}, kind="desktop")
    except Exception:
        _log.exception("Could not notify the desktop about saved Home cards")


def list_cards():
    """Priority first, then original creation time and id; reads never write."""
    with _LOCK:
        return sorted(_read(), key=lambda c: (-c["priority"], c["created_at"], c["id"]))


def upsert_card(payload):
    """Create or replace one stable id, retaining its original position at ties."""
    card = _card(payload)
    with _LOCK:
        _writable()
        cards = _read()
        previous = next((c for c in cards if c["id"] == card["id"]), None)
        if previous is None and len(cards) >= MAX_CARDS:
            raise CardError(f"Home has {MAX_CARDS} cards. Remove one before adding another.")
        now = time.time()
        card.update(created_at=previous["created_at"] if previous else now, updated_at=now)
        _write([c for c in cards if c["id"] != card["id"]] + [card])
    _changed()
    return card


def remove_card(card_id):
    """Remove one id; return whether it existed, only after a durable write."""
    card_id = _id(card_id)
    with _LOCK:
        _writable()
        cards = _read()
        kept = [c for c in cards if c["id"] != card_id]
        if len(kept) == len(cards):
            return False
        _write(kept)
    _changed()
    return True
