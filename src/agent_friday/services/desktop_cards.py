"""Durable, declarative Home cards with navigation-only actions.

Cards are plain text. Their buttons only open an enabled native workspace;
they never contain executable markup, URLs or automatic actions. Saving is
atomic and a failed read is never mistaken for an empty board.
"""
from __future__ import annotations

import json
import copy
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
MAX_PRESENTATION = 256
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_LOCK = threading.RLock()
_log = logging.getLogger("friday.desktop_cards")


class CardError(ValueError):
    """A card input or operation the owner can correct."""


class BoardConflict(CardError):
    """The caller must re-read the board before applying this change."""


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
        if not isinstance(action, dict) or set(action) not in ({"label", "workspace"}, {"label", "view"}):
            raise CardError("Each action needs a label and exactly one native workspace or Home view.")
        if "view" in action:
            if action["view"] not in ("projects", "activity"):
                raise CardError("Choose the Projects or Activity Home view.")
            actions.append({"label": _text(action["label"], "Action label", 60), "view": action["view"]})
            continue
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


def _empty_document():
    return {"version": 2, "revision": 0, "cards": [], "trackers": [], "pins": [], "preferences": {}, "order": []}


def _durable_count(document):
    return len(document["cards"]) + len(document["trackers"]) + len(document["pins"])


def _retained_ids(document):
    return set(document["preferences"]) | set(document["order"]) | {
        card["id"] for card in document["cards"] + document["trackers"] + document["pins"]}


def _check_capacity(document, before):
    retained = _retained_ids(document)
    # Older stores remain readable and may be reduced or edited in place.
    # A full board never silently discards an existing retained placement.
    if len(retained) > MAX_PRESENTATION and retained - before:
        raise CardError("Home's retained choices are full. Reset unpinned suggestions in Sources, or delete a saved card, before adding another choice.")


def _stamp(value, name, *, nullable=False):
    if nullable and value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise CardError(name + " must be a finite nonnegative timestamp.")
    return value


def _scope(value):
    from agent_friday.services.desktop_card_sources import KINDS
    if not isinstance(value, dict) or set(value) != {"kind", "id"} or not isinstance(value.get("kind"), str) or value["kind"] not in KINDS:
        raise CardError("Choose one supported local source and its exact id from the source list.")
    return {"kind": value["kind"], "id": _text(value["id"], "Source id", 240)}


def _snapshot(value, scope, *, prefix="track"):
    from agent_friday.services.desktop_card_sources import stable_id
    if not isinstance(value, dict):
        raise CardError("Invalid tracked snapshot.")
    base = _card({key: value[key] for key in ("id", "title", "body", "priority", "actions") if key in value}, enabled=False)
    expected = stable_id(prefix, scope["kind"], scope["id"])
    if prefix == "auto" and scope["kind"] == "activity":
        from datetime import date
        date.fromisoformat(base["id"].removeprefix("auto-activity-"))
        expected = base["id"] if base["id"].startswith("auto-activity-") and scope["id"] == "today" else expected
    if base["id"] != expected:
        raise CardError("Tracked snapshot identity does not match its source.")
    source = value.get("source")
    if not isinstance(source, dict) or source.get("kind") != scope["kind"] or source.get("id") != scope["id"]:
        raise CardError("Tracked snapshot source is missing.")
    clean_source = {"kind": scope["kind"], "id": scope["id"],
        "label": _text(source.get("label"), "Source label", 120),
        "detail": _text(source.get("detail", ""), "Source detail", 1000, empty=True),
        "status": _text(source.get("status"), "Source status", 40),
        "checked_at": _stamp(source.get("checked_at"), "Checked time"),
        "updated_at": _stamp(source.get("updated_at"), "Source update time", nullable=True)}
    for key in ("workspace", "view"):
        if source.get(key) is not None:
            clean_source[key] = _text(source[key], "Source destination", 64)
    return {**base, "type": scope["kind"], "origin": "tracked" if prefix == "track" else "automatic", "source": clean_source,
            "created_at": _stamp(value.get("created_at"), "Created time"),
            "updated_at": _stamp(value.get("updated_at"), "Update time", nullable=True),
            "expires_at": _stamp(value.get("expires_at"), "Expiry", nullable=True)}


def _read_document():
    path = Path(CARDS_PATH)
    if not path.exists():
        return _empty_document()
    with path.open("rb") as stream:
        raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise OSError("Home card store exceeds its size limit")
    try:
        doc = json.loads(raw)
        if not isinstance(doc, dict) or doc.get("version") not in (1, 2) or not isinstance(doc.get("cards"), list):
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
        if doc["version"] == 1:
            return {**_empty_document(), "cards": cards}
        if set(doc) - {"pins"} != {"version", "revision", "cards", "trackers", "preferences", "order"}:
            raise ValueError("invalid board fields")
        if type(doc["revision"]) is not int or not 0 <= doc["revision"] < 2**53:
            raise ValueError("invalid board revision")
        trackers = doc["trackers"]
        if not isinstance(trackers, list) or len(cards) + len(trackers) > MAX_CARDS:
            raise ValueError("too many tracked cards")
        clean_trackers = []
        for tracker in trackers:
            if not isinstance(tracker, dict) or set(tracker) != {"id", "scope", "enabled", "created_at", "updated_at", "stopped_at", "snapshot"}:
                raise ValueError("invalid tracker")
            scope = _scope(tracker["scope"])
            saved = _snapshot(tracker["snapshot"], scope)
            if tracker["id"] != saved["id"] or tracker["id"] in seen or type(tracker["enabled"]) is not bool:
                raise ValueError("invalid tracker identity")
            seen.add(tracker["id"])
            clean_trackers.append({**tracker, "scope": scope, "snapshot": saved,
                "created_at": _stamp(tracker["created_at"], "Tracker created time"),
                "updated_at": _stamp(tracker["updated_at"], "Tracker update time"),
                "stopped_at": _stamp(tracker["stopped_at"], "Tracking stopped time", nullable=True)})
        pins = doc.get("pins", [])
        if not isinstance(pins, list) or len(cards) + len(trackers) + len(pins) > MAX_CARDS:
            raise ValueError("too many saved pins")
        clean_pins = []
        for pin in pins:
            if not isinstance(pin, dict) or not isinstance(pin.get("source"), dict):
                raise ValueError("invalid saved pin")
            scope = _scope({key: pin["source"].get(key) for key in ("kind", "id")})
            saved = _snapshot(pin, scope, prefix="auto")
            if saved["id"] in seen:
                raise ValueError("duplicate saved pin")
            seen.add(saved["id"])
            clean_pins.append(saved)
        preferences = doc["preferences"]
        if not isinstance(preferences, dict) or len(preferences) > MAX_PRESENTATION:
            raise ValueError("invalid card preferences")
        for key, value in preferences.items():
            _id(key)
            if not isinstance(value, dict) or set(value) - {"pinned", "dismissed", "snoozed_until", "restored_at"}:
                raise ValueError("invalid presentation fields")
            for flag in ("pinned", "dismissed"):
                if flag in value and type(value[flag]) is not bool:
                    raise ValueError("invalid presentation flag")
            for stamp in ("snoozed_until", "restored_at"):
                if stamp in value:
                    _stamp(value[stamp], stamp, nullable=stamp == "snoozed_until")
        order = doc["order"]
        if not isinstance(order, list) or len(order) > MAX_PRESENTATION or len(set(order)) != len(order):
            raise ValueError("invalid board order")
        for key in order:
            _id(key)
        return {**doc, "cards": cards, "trackers": clean_trackers, "pins": clean_pins}
    except (ValueError, TypeError, UnicodeError) as exc:
        raise OSError("Home card store could not be read") from exc


def _read():
    return _read_document()["cards"]


def _write(document):
    path = Path(CARDS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(document, ensure_ascii=False).encode("utf-8")
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


def _admit(origin=None):
    from agent_friday.core import _SETTINGS_WRITE_LOCK
    from agent_friday.services import off_record
    with _SETTINGS_WRITE_LOCK:
        _writable()
        generation = off_record.generation()
        if origin is not None and (type(origin) is not int or origin != generation):
            raise CardError("The privacy session changed. Home was not changed; review the request again.")
        return generation if origin is None else origin


def _commit(document, generation):
    from agent_friday.core import _SETTINGS_WRITE_LOCK
    from agent_friday.services import off_record
    with _SETTINGS_WRITE_LOCK:
        _writable()
        if type(generation) is not int or off_record.generation() != generation:
            raise CardError("The privacy session changed. Home was not changed; review the request again.")
        document["revision"] += 1
        _write(document)


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


def upsert_card(payload, *, origin=None):
    """Create or replace one stable id, retaining its original position at ties."""
    generation = _admit(origin)
    card = _card(payload)
    with _LOCK:
        document = _read_document()
        retained_before = _retained_ids(document)
        cards = document["cards"]
        previous = next((c for c in cards if c["id"] == card["id"]), None)
        if previous is None and _durable_count(document) >= MAX_CARDS:
            raise CardError(f"Home has {MAX_CARDS} cards. Remove one before adding another.")
        now = time.time()
        card.update(created_at=previous["created_at"] if previous else now, updated_at=now)
        if previous is None and card["id"].startswith(("auto-", "track-")):
            raise CardError("That id is reserved for a source card.")
        document["cards"] = [c for c in cards if c["id"] != card["id"]] + [card]
        _check_capacity(document, retained_before)
        _commit(document, generation)
    _changed()
    return card


def remove_card(card_id):
    """Remove one id; return whether it existed, only after a durable write."""
    generation = _admit()
    card_id = _id(card_id)
    with _LOCK:
        document = _read_document()
        cards = document["cards"]
        kept = [c for c in cards if c["id"] != card_id]
        if len(kept) == len(cards):
            return False
        document["cards"] = kept
        document["preferences"].pop(card_id, None)
        document["order"] = [key for key in document["order"] if key != card_id]
        _commit(document, generation)
    _changed()
    return True


def _observations(now):
    from agent_friday.services import desktop_card_sources as sources
    return sources.snapshot(now)


def _paused_sources(now):
    from agent_friday.services.desktop_card_sources import KINDS, destination
    return {kind: {"kind": kind, "label": label, **destination(kind), "status": "paused",
            "detail": "Personal source cards are hidden while off the record. Saved tracking is unchanged.",
            "checked_at": now, "updated_at": None, "options": [], "records": {}}
            for kind, (label, _workspace) in KINDS.items()}


def _board(document, observations, now, *, private=False):
    from agent_friday.services import desktop_card_sources as sources
    candidates = sources.automatic(observations, now) if not private else []
    # A ranking window cannot erase an explicit pin, hide or ordering choice.
    # Reads derive current content; only owner mutations save snapshots.
    chosen = set(document["preferences"]) | set(document["order"])
    known = {card["id"] for card in candidates}
    for kind, info in observations.items() if not private else []:
        for source_id in info["records"]:
            key = sources.automatic_id(kind, source_id, now)
            if key in chosen and key not in known:
                candidates.append(sources.card_for(kind, source_id, observations, now=now))
                known.add(key)
    for saved in document["pins"] if not private else []:
        scope = saved["source"]
        fresh = sources.card_for(scope["kind"], scope["id"], observations, now=now)
        if fresh is None or fresh["id"] != saved["id"]:
            fresh = copy.deepcopy(saved)
            status = observations[scope["kind"]]["status"]
            fresh["source"].update(status="unavailable" if status == "unavailable" else "snapshot",
                detail="Pinned last saved snapshot. The current source is unavailable or no longer describes this item.")
        fresh["created_at"] = saved["created_at"]
        candidates = [card for card in candidates if card["id"] != saved["id"]] + [fresh]
    tracked_scopes = {(t["scope"]["kind"], t["scope"]["id"]) for t in document["trackers"]}
    saved_ids = {card["id"] for card in document["cards"]}
    candidates = [c for c in candidates if c["id"] not in saved_ids and not (
        (c["source"]["kind"], c["source"]["id"]) in tracked_scopes
        and c["id"] == sources.automatic_id(c["source"]["kind"], c["source"]["id"], now))]
    for tracker in document["trackers"] if not private else []:
        scope = tracker["scope"]
        card = sources.card_for(scope["kind"], scope["id"], observations, prefix="track", now=now) if tracker["enabled"] else None
        if card is None:
            card = copy.deepcopy(tracker["snapshot"])
            source = observations[scope["kind"]]
            card["source"].update(status="stopped" if not tracker["enabled"] else
                ("unavailable" if source["status"] == "unavailable" else "missing"),
                detail="Tracking stopped. This is the last saved snapshot; the underlying work is unchanged."
                if not tracker["enabled"] else "The source is unavailable or missing. This is the last saved snapshot.")
        card["created_at"] = tracker["created_at"]
        card["tracking"] = {"enabled": tracker["enabled"], "scope": scope, "stopped_at": tracker["stopped_at"]}
        candidates.append(card)
    candidates.extend({**copy.deepcopy(card), "type": "note", "origin": "saved", "source": {
        "kind": "saved", "id": card["id"], "label": "Saved card", "status": "saved",
        "checked_at": now, "updated_at": card["updated_at"], "detail": "Saved on this Friday account."},
        "tracking": None, "expires_at": None} for card in document["cards"])
    retained = _retained_ids(document)
    remaining = max(0, MAX_PRESENTATION - len(retained))
    bounded = []
    for card in candidates:
        if card["id"] in retained or card["origin"] != "automatic":
            bounded.append(card)
        elif remaining:
            bounded.append(card)
            remaining -= 1
    candidates = bounded
    order = {key: index for index, key in enumerate(document["order"])}
    visible, hidden = [], []
    for card in candidates:
        pref = document["preferences"].get(card["id"], {})
        expiry = card.get("expires_at")
        expired = bool(expiry is not None and expiry <= now and pref.get("restored_at", 0) < expiry)
        snoozed = pref.get("snoozed_until")
        card.update(pinned=pref.get("pinned", False), order=order.get(card["id"]),
            dismissed=pref.get("dismissed", False), snoozed_until=snoozed,
            expired=expired, tracking=card.get("tracking"))
        if expired and card["pinned"] and card["source"]["status"] == "ready":
            card["source"]["status"] = "expired"
        card["hidden_reason"] = "dismissed" if card["dismissed"] else "snoozed" if snoozed and snoozed > now else "expired" if expired and not card["pinned"] else None
        (hidden if card["hidden_reason"] else visible).append(card)
    key = lambda card: (not card["pinned"], order.get(card["id"], MAX_PRESENTATION),
                        -card["priority"], card["created_at"], card["id"])
    visible.sort(key=key)
    hidden.sort(key=key)
    return {"status": "ok", "revision": document["revision"], "generated_at": now,
            "cards": visible, "hidden_cards": hidden,
            "sources": [{k: v for k, v in item.items() if k != "records"} for item in observations.values()],
            "summary": {"visible": len(visible), "hidden": len(hidden), "saved": len(document["cards"]),
                "tracking": sum(t["enabled"] for t in document["trackers"]), "private": private,
                "durable": _durable_count(document), "limit": MAX_CARDS,
                "retained": len(retained), "retained_limit": MAX_PRESENTATION,
                "suggestion_capacity_full": len(retained) >= MAX_PRESENTATION,
                "cadence": "Updates from local activity while Home is open."}}


def read_board():
    """Read local observations without persisting refresh time or starting work."""
    from agent_friday.core import _SETTINGS_WRITE_LOCK
    from agent_friday.services import off_record
    now = time.time()
    with _SETTINGS_WRITE_LOCK:
        generation, private = off_record.generation(), off_record.active()
    with _LOCK:
        document = _read_document()
    observations = _paused_sources(now) if private else _observations(now)
    with _SETTINGS_WRITE_LOCK:
        if off_record.active() or off_record.generation() != generation:
            private = True
            observations = _paused_sources(now)
        return _board(document, observations, now, private=private)


def change_board(payload, *, origin=None):
    """Apply one revision-checked presentation or tracking mutation atomically."""
    from agent_friday.services import desktop_card_sources as sources
    generation = _admit(origin)
    if not isinstance(payload, dict):
        raise CardError("A board change must be an object.")
    op = payload.get("op")
    fields = {"save": {"card"}, "remove": {"id"}, "pin": {"id", "pinned"},
        "snooze": {"id", "until"}, "dismiss": {"id"}, "restore": {"id"},
        "reorder": {"ids"}, "track": {"source"}, "stop_tracking": {"id"}, "reset_suggestions": set()}
    if not isinstance(op, str) or op not in fields or set(payload) != {"op", "expected_revision"} | fields[op]:
        raise CardError("Choose a supported board action with exactly its required fields.")
    if type(payload["expected_revision"]) is not int or payload["expected_revision"] < 0:
        raise CardError("Read the board revision before changing it.")
    now = time.time()
    observations = _observations(now)
    with _LOCK:
        document = _read_document()
        retained_before = _retained_ids(document)
        if payload["expected_revision"] != document["revision"]:
            raise BoardConflict("Home changed. Refresh the board and review this action again.")
        board = _board(document, observations, now)
        all_cards = {card["id"]: card for card in board["cards"] + board["hidden_cards"]}
        card_id = _id(payload["id"]) if "id" in payload else None
        if card_id is not None and card_id not in all_cards:
            raise BoardConflict("That card is no longer available. Refresh Home before changing it.")
        if op == "save":
            card = _card(payload["card"])
            previous = next((c for c in document["cards"] if c["id"] == card["id"]), None)
            if previous is None:
                if card["id"].startswith(("auto-", "track-")):
                    raise CardError("Source cards update from their source. Save a new note with another id.")
                if _durable_count(document) >= MAX_CARDS:
                    raise CardError("The saved board is full. Delete a saved card or stopped snapshot, or unpin an automatic card, to free a slot.")
            card.update(created_at=previous["created_at"] if previous else now, updated_at=now)
            document["cards"] = [c for c in document["cards"] if c["id"] != card["id"]] + [card]
        elif op == "remove":
            selected = all_cards[card_id]
            if selected["origin"] != "saved" and not (selected.get("tracking") and not selected["tracking"]["enabled"]):
                raise CardError("Only saved cards and stopped snapshots can be deleted. Stop tracking first, or dismiss the card.")
            document["cards"] = [c for c in document["cards"] if c["id"] != card_id]
            document["trackers"] = [t for t in document["trackers"] if t["id"] != card_id]
            document["preferences"].pop(card_id, None)
            document["order"] = [key for key in document["order"] if key != card_id]
        elif op == "track":
            scope = _scope(payload["source"])
            snapshot = sources.card_for(scope["kind"], scope["id"], observations, prefix="track", now=now)
            if snapshot is None or snapshot["source"]["status"] in {"unavailable", "unimplemented"}:
                raise CardError("That source is not available to track. Choose a current item from the source list.")
            existing = next((t for t in document["trackers"] if t["id"] == snapshot["id"]), None)
            if any(c["id"] == snapshot["id"] for c in document["cards"]):
                raise CardError("A legacy saved card already uses this source id. Rename or delete that saved card first.")
            automatic_id = sources.automatic_id(scope["kind"], scope["id"], now)
            old_pins = [pin for pin in document["pins"] if pin["id"] == automatic_id]
            if existing is None and _durable_count(document) - len(old_pins) >= MAX_CARDS:
                raise CardError("The saved board is full. Delete a saved card or stopped snapshot, or unpin an automatic card, to free a slot.")
            document["pins"] = [pin for pin in document["pins"] if pin not in old_pins]
            tracker = {"id": snapshot["id"], "scope": scope, "enabled": True,
                "created_at": existing["created_at"] if existing else now, "updated_at": now,
                "stopped_at": None, "snapshot": _snapshot(snapshot, scope)}
            # Converting an automatic suggestion to explicit tracking keeps
            # its position and visibility choices instead of resurfacing it.
            automatic = next((c for c in all_cards.values() if c["origin"] == "automatic" and c["id"] == automatic_id), None)
            if existing is None and automatic is not None:
                old_id = automatic["id"]
                if old_id in document["preferences"]:
                    document["preferences"][tracker["id"]] = document["preferences"].pop(old_id)
                document["order"] = [tracker["id"] if key == old_id else key for key in document["order"]]
            document["trackers"] = [t for t in document["trackers"] if t["id"] != tracker["id"]] + [tracker]
        elif op == "stop_tracking":
            tracker = next((t for t in document["trackers"] if t["id"] == card_id), None)
            if tracker is None:
                raise CardError("This card has no tracking subscription. Dismiss it to hide it.")
            scope = tracker["scope"]
            fresh = sources.card_for(scope["kind"], scope["id"], observations, prefix="track", now=now)
            if tracker["enabled"] and fresh is not None:
                tracker["snapshot"] = _snapshot(fresh, scope)
            if tracker["enabled"]:
                tracker.update(enabled=False, stopped_at=now, updated_at=now)
        elif op == "reorder":
            ids = payload["ids"]
            if not isinstance(ids, list) or any(not isinstance(key, str) for key in ids) or len(ids) != len(set(ids)):
                raise CardError("Order must be a list of unique visible card ids.")
            if set(ids) != {c["id"] for c in board["cards"]}:
                raise BoardConflict("Visible cards changed. Refresh Home before reordering them.")
            previous_hidden = [key for key in document["order"] if key not in ids]
            document["order"] = ids + previous_hidden
            if len(document["order"]) > MAX_PRESENTATION:
                raise CardError("Home's saved order is full. Reset unpinned suggestions in Sources before reordering; existing positions were kept.")
        elif op == "reset_suggestions":
            keep = {card["id"] for card in document["cards"] + document["trackers"] + document["pins"]}
            document["preferences"] = {key: value for key, value in document["preferences"].items()
                if key in keep or not key.startswith("auto-") or value.get("pinned")}
            document["order"] = [key for key in document["order"]
                if key in keep or not key.startswith("auto-") or document["preferences"].get(key, {}).get("pinned")]
        else:
            pref = document["preferences"].setdefault(card_id, {})
            if len(document["preferences"]) > MAX_PRESENTATION:
                raise CardError("Home's suggestion history is full. Reset unpinned suggestions in Sources to make room; no change was saved.")
            if op == "pin":
                if type(payload["pinned"]) is not bool:
                    raise CardError("Pinned must be true or false.")
                pref["pinned"] = payload["pinned"]
                selected = all_cards[card_id]
                if selected["origin"] == "automatic":
                    previous_pin = next((pin for pin in document["pins"] if pin["id"] == card_id), None)
                    if payload["pinned"] and previous_pin is None and _durable_count(document) >= MAX_CARDS:
                        raise CardError("The saved board is full. Delete a saved card or stopped snapshot, or unpin an automatic card, to free a slot.")
                    document["pins"] = [pin for pin in document["pins"] if pin["id"] != card_id]
                    if payload["pinned"]:
                        scope = {key: selected["source"][key] for key in ("kind", "id")}
                        document["pins"].append(_snapshot(selected, scope, prefix="auto"))
            elif op == "snooze":
                until = _stamp(payload["until"], "Snooze time")
                if not now < until <= now + 366 * 86400:
                    raise CardError("Choose a snooze time in the next year.")
                pref["snoozed_until"] = until
            elif op == "dismiss":
                pref["dismissed"] = True
            elif op == "restore":
                pref.update(dismissed=False, snoozed_until=None, restored_at=now)
            if not pref.get("pinned") and not pref.get("dismissed") and not (pref.get("snoozed_until") or 0) > now:
                expiry = all_cards[card_id].get("expires_at")
                if expiry is None or expiry > now:
                    document["preferences"].pop(card_id, None)
        _check_capacity(document, retained_before)
        _commit(document, generation)
        from agent_friday.core import _SETTINGS_WRITE_LOCK
        from agent_friday.services import off_record
        with _SETTINGS_WRITE_LOCK:
            private = off_record.active() or off_record.generation() != generation
            result = _board(document, _paused_sources(now) if private else observations, now, private=private)
    _changed()
    return result
