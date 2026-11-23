"""Pairing a TV (D2): commit, reveal, six digits on both screens, a press on this PC.

The TV connects over TLS and records the certificate it met. Then:

  1. TV -> PC   its device public key (P-256 SPKI, made in its Keystore)
     PC -> TV   commit = H(pin || device key || Nd)      Nd stays secret
  2. TV -> PC   Nt
     PC -> TV   Nd                                       TV checks the commitment
  3. both       SAS = H(pin || device key || Nt || Nd) mod 10^6

"pin" is the SHA-256 of the gateway certificate's SPKI: the PC uses its own,
the TV the one it met. A machine in the middle meets each side with its own
key, so the two screens show different digits; because the PC committed to Nd
before seeing Nt, the middle gets one guess at matching them, not a million.

The PC then raises a ``device_pair`` card naming the six digits. It is a
screen-only kind: only a verified press on this PC's own screen approves it
(approvals.SCREEN_ONLY_KINDS). Voice, chat, the gateway and the TV can decline
it at most. The device is recorded only from the card's decision hook, with
the least rights ("chat_read"). Each wider right then has its own screen-only
``device_right`` card, raised at once beside it and named in plain words
(services/device_pairing.RIGHTS): one press per right, nothing pre-selected,
and declining or ignoring one leaves the TV at chat-and-read.

Limits: pairing is only possible inside a window the owner opened at this PC
(device_pairing.open_pairing_window; at most 15 minutes, closes by itself). A
pairing lives five minutes, each step happens once, one pairing at a time, a start
that never reveals frees its place after 30 seconds, at most three started per hour
from one address and twelve from all. The card names the address and the name that
asked, so the owner can tell their own phone from someone else's.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import deque
from typing import Optional

from agent_friday.services import device_pairing

KIND = "device_pair"
RIGHT_KIND = device_pairing.RIGHT_CARD_KIND

# The words on each right's card. Fixed here, never from the device or a model.
RIGHT_CARDS = {
    "tv_brain": (
        "Let the paired TV use Friday as its brain?",
        "The TV may chat with Friday, use her memory, knowledge graph and models, and "
        "propose actions on the TV, which the TV still asks about on its own screen. "
        "Anything Friday would do on this PC still needs a press here."),
    "studio": (
        "Let the paired TV start Studio jobs that spend money?",
        "A press on the TV may start a Studio job on this PC's paid providers "
        "(Higgsfield, kie.ai, Gemini and others). Your daily spend stop does not apply "
        "to these jobs. Each one shows its cost first and leaves a receipt."),
    "tv_channels": (
        "Let the paired TV share channels with this PC?",
        "The TV may send a copy of its channels (small web pages) to this PC and fetch the ones "
        "kept here. The PC only stores them and never runs them. Nothing is added on the TV "
        "without a yes on the TV, and no shared channel gets website access from here."),
    "pc_read": (
        "Let this phone or tablet read what is on this PC?",
        "The device may read your chat list and chats, workspaces, jobs and their journals, and the media "
        "index on this PC, from home or over your private link. It can only read: it cannot change anything, "
        "run an action or approve a card. Each kind of reading is listed in the PC's receipts, and removing "
        "the device here ends it."),
    "pc_act": (
        "Let this phone or tablet control jobs on this PC?",
        "The device may ask this PC to stop a job after its current step, to pick up an interrupted job, or to run a "
        "finished one again. Each goes through the same checks as the button on this PC, and anything that needs your "
        "yes still raises a card here. It cannot approve a card, change a setting or open anything else. Every use is "
        "listed in the PC's receipts, and removing the device here ends it."),
    "pc_approve": (
        "Let this phone or tablet approve cards for this PC?",
        "The device may approve one card at a time, from home or over your private link. Each approval is "
        "signed on the device, which must be unlocked. Every approval from a device is listed in the PC's "
        "receipts and shown on this screen. Removing the device here ends it."),
}
PAIR_TTL_S = 300
MAX_STARTS_PER_HOUR = 3             # per source address
MAX_STARTS_PER_HOUR_ALL = 12        # from every address together
PAIRING_CLOSED = ("Pairing is closed. On the PC, say or type \"Friday, add a device\" "
                  "(or open Add a device at /pairing), then try again.")
REVEAL_WITHIN_S = 30                # a start that has not revealed by then frees its place
SAS_CONTEXT = b"friday-tv-pair-v1|"
COMMIT_CONTEXT = b"friday-tv-pair-commit-v1|"
FIRST_RIGHTS = "chat_read"


def owner_grant(kind: str, member: str) -> bool:
    """True for the owner's own personal device (phone, tablet, watch signed in as the owner).
    Owner decision, 2026-11-13: the six-digit press at the PC IS the grant of every right, so such a
    device is paired with all of them and no second press is asked. The family member's devices (the TV)
    keep the tiered rights: chat_read first, each wider right its own screen-only card."""
    from agent_friday.tv_gateway import household
    return member == household.OWNER_ID and kind in household.PERSONAL_KINDS

_LOCK = threading.RLock()
_PENDING: dict = {}
_STARTS: deque = deque()


class PairingRefused(Exception):
    """A pairing step was refused; the message is safe to show the TV. ``code`` is for apps that branch on it."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


def commitment(pin_hex: str, device_spki: bytes, nd: bytes) -> str:
    return hashlib.sha256(COMMIT_CONTEXT + bytes.fromhex(pin_hex) + device_spki + nd).hexdigest()


def sas(pin_hex: str, device_spki: bytes, nt: bytes, nd: bytes) -> str:
    h = hashlib.sha256(SAS_CONTEXT + bytes.fromhex(pin_hex) + device_spki + nt + nd).digest()
    return "%06d" % (int.from_bytes(h[:8], "big") % 1_000_000)


def spoken(code: str) -> str:
    return code[:3] + " " + code[3:]


def _pin() -> str:
    from agent_friday.tv_gateway import tls
    return tls.spki_sha256()


def _expire(now: float) -> None:
    for pid, p in list(_PENDING.items()):
        if p["state"] in ("committed", "awaiting_press") and p["expires"] < now:
            p["state"] = "expired"
        if p["state"] == "committed" and p.get("started", now) + REVEAL_WITHIN_S < now:
            p["state"] = "expired"                         # never revealed: a stranger must not hold the place
        if p["expires"] + 600 < now:
            _PENDING.pop(pid, None)


def _norm_name(name: str) -> str:
    """A device's name with the app's own prefix ("Agent Friday on Pixel 10" and "Friday on Pixel 10" are one phone)."""
    n = " ".join(str(name or "").lower().split())
    return n[len("agent "):] if n.startswith("agent ") else n


def stale_rows(spki: bytes, name: str, kind: str, member: str) -> list:
    """Rows this pairing replaces, by id: the same key already in the store, or another row of the same kind,
    for the same household member, with the same device name (the same phone paired again after its key
    changed). Never a row of another kind, member or name."""
    from agent_friday.tv_gateway import household
    me = device_pairing.spki_id(spki)
    out = []
    for d in device_pairing.list_devices():
        if d.get("id") == me:
            out.append(d)
        elif (d.get("kind") == kind and household.member_for_device(str(d.get("id"))) == member
              and _norm_name(d.get("name")) == _norm_name(name)):
            out.append(d)
    return out


_NAME_DROPPED = frozenset(['"', "<", ">", "`", chr(92)])


def clean_name(name: str) -> str:
    """A device's name as data: printable characters only, no quotes or angle brackets, one line, 60 long.
    It is shown on the owner's approval card, and a stranger chooses it."""
    kept = "".join(c if c.isprintable() and c not in _NAME_DROPPED else " " for c in str(name or ""))
    return " ".join(kept.split())[:60] or "TV"


def end_in_flight() -> None:
    """The owner closed pairing: a pairing not yet pressed ends with it (a card for it does nothing)."""
    with _LOCK:
        for p in _PENDING.values():
            if p["state"] in ("committed", "awaiting_press"):
                p["state"] = "expired"


def reset_budget() -> None:
    """The owner opened pairing: whatever strangers used up before is forgotten."""
    with _LOCK:
        _STARTS.clear()


def start(device_spki: bytes, name: str, kind: str = "tv", remote: str = "") -> dict:
    now = time.time()
    if not device_pairing.pairing_window()["open"]:
        raise PairingRefused(PAIRING_CLOSED, code="pairing_closed")
    remote = str(remote or "")[:64]
    try:
        device_pairing._load_pub(device_spki)
    except Exception:
        raise PairingRefused("the TV's key must be P-256")
    from agent_friday.tv_gateway import household
    try:
        member = household.member_for_kind(kind)       # the PC picks the member; a device declares only its kind
    except ValueError:
        raise PairingRefused("unknown device kind")
    # A key that is already in the store is NOT a dead end (2026-11-13: a TV with no valid token was told
    # "already paired; remove it on the PC" and could never recover). It pairs again like any device: the
    # owner's screen press is the confirmation, and the new pairing replaces the stale row (see _on_decision).
    with _LOCK:
        _expire(now)
        while _STARTS and now - _STARTS[0][0] > 3600:
            _STARTS.popleft()
        if (sum(1 for e in _STARTS if e[2]) >= MAX_STARTS_PER_HOUR_ALL      # only starts that got as far as a card
                or sum(1 for e in _STARTS if e[1] == remote) >= MAX_STARTS_PER_HOUR):
            raise PairingRefused("too many pairing attempts this hour; try again later")
        if any(p["state"] in ("committed", "awaiting_press") for p in _PENDING.values()):
            raise PairingRefused("another device is pairing right now")
        entry = [now, remote, False]
        _STARTS.append(entry)
        pid = secrets.token_urlsafe(16)
        nd = secrets.token_bytes(32)
        pin = _pin()
        _PENDING[pid] = {"state": "committed", "spki": device_spki, "name": clean_name(name),
                         "nd": nd, "pin": pin, "expires": now + PAIR_TTL_S, "started": now, "entry": entry, "card_id": None,
                         "remote": remote,
                         "kind": kind, "member": member,
                         "device_id": device_pairing.spki_id(device_spki)}
        return {"pairing_id": pid, "commit": commitment(pin, device_spki, nd), "ttl_s": PAIR_TTL_S}


def reveal(pairing_id: str, nt: bytes) -> bytes:
    if not isinstance(nt, (bytes, bytearray)) or len(nt) != 32:
        raise PairingRefused("Nt must be 32 bytes")
    with _LOCK:
        _expire(time.time())
        p = _PENDING.get(str(pairing_id))
        if p is None or p["state"] != "committed":
            raise PairingRefused("no pairing waiting for this step")
        p["state"] = "awaiting_press"
        p["entry"][2] = True
        code = sas(p["pin"], p["spki"], bytes(nt), p["nd"])
        nd = p["nd"]
    from agent_friday.services import approvals
    from agent_friday.tv_gateway import household
    who = household.member_name(p["member"])
    what = "TV" if p["kind"] == "tv" else p["kind"]
    if owner_grant(p["kind"], p["member"]):
        grant = ("Approving pairs it as YOUR device with all of your rights: Friday's memory, knowledge graph "
                 "and models, her tools (each side effect still needs a press here), Studio jobs that spend "
                 "money, and channels. There is no second press. ")
    else:
        grant = ("Paired, it may chat with Friday and read what she shares; "
                 "what else it may do is your choice afterwards, under Paired devices. ")
    again = stale_rows(p["spki"], p["name"], p["kind"], p["member"])
    with _LOCK:
        p["replaces"] = [d["id"] for d in again]
    if again:
        replaced = "; ".join("%s (paired %s, last seen %s)" % (
            d.get("name") or "device", time.strftime("%H:%M", time.localtime(d.get("paired_at") or 0)),
            time.strftime("%H:%M", time.localtime(d["last_seen"])) if d.get("last_seen") else "never")
            for d in again)
        grant += "This replaces the old pairing of the same %s: %s. " % (what, replaced)
    asked_by = ("Asked by \"%s\" from %s. If you did not ask for this, press Decline. "
                % (p["name"], p.get("remote") or "an unknown address"))
    card = approvals.create_approval(
        kind=KIND, subject_type="device", subject_id=str(pairing_id),
        title="Pair a %s: is it showing %s?" % (what, spoken(code)),
        description=("Press Approve only if the %s in front of you shows %s. "
                     % (what, spoken(code))) + asked_by + grant +
                    ("It will be signed in as %s (%s) and will receive %s's settings, model choices, "
                     "chats, arcade and creations, plus the shared household ones. No keys are sent."
                     % (who, p["member"], who)),
        action_description="Pair a %s with Friday" % what,
        payload={"pairing_id": str(pairing_id), "device_id": p["device_id"], "sas": code,
                 "origin": "tv_gateway", "kind": p["kind"], "member": p["member"],
                 "remote": p.get("remote", "")},
        requested_by="tv_gateway", force_gate=True)
    with _LOCK:
        p["card_id"] = card.get("approval_id")
    return nd


def status(pairing_id: str) -> dict:
    with _LOCK:
        _expire(time.time())
        p = _PENDING.get(str(pairing_id))
        if p is None:
            return {"state": "unknown"}
        out = {"state": p["state"]}
        if p["state"] == "paired":
            out["device_id"] = p["device_id"]
        return out


def _on_decision(rec: dict) -> None:
    payload = rec.get("payload") or {}
    with _LOCK:
        p = _PENDING.get(str(payload.get("pairing_id")))
        if p is None or p["card_id"] != rec.get("approval_id") or p["state"] != "awaiting_press":
            return
        if time.time() > p["expires"]:
            p["state"] = "expired"
            return
        from agent_friday.services.approvals import SCREEN_LABEL
        if rec.get("status") != "approved" or rec.get("decided_by") != SCREEN_LABEL:
            p["state"] = "refused"
            return
        full = owner_grant(p["kind"], p["member"])
        granted = list(device_pairing.RIGHTS) if full else FIRST_RIGHTS
        dev = device_pairing.add_device(p["spki"], name=p["name"], rights=granted, kind=p["kind"])
        from agent_friday.tv_gateway import household
        household.ensure_default_members()
        household.bind_device(dev["id"], p["member"])
        for old_id in p.get("replaces") or []:
            if old_id != dev["id"] and device_pairing.get_device(old_id) is not None:
                device_pairing.remove_device(old_id)           # sessions of the old row die with it
        p["state"] = "paired"
    device_pairing._receipt("device_paired", device_pairing.get_device(dev["id"]) or dev,
                            approval_id=rec.get("approval_id"), rights=granted)
    if not full:                         # the owner's own device already holds every right
        _offer_rights(dev["id"])


def _offer_rights(device_id: str) -> list:
    """One screen-only card per wider right, for the owner to press or leave."""
    from agent_friday.services import approvals
    out = []
    for right, (title, description) in RIGHT_CARDS.items():
        if right not in device_pairing.RIGHTS:
            continue                       # the PC rights (pc_read ...) are asked for by the device that needs them
        card = approvals.create_approval(
            kind=RIGHT_KIND, subject_type="device_right", subject_id="%s:%s" % (device_id, right),
            title=title, description=description, action_description=title,
            payload={"device_id": device_id, "right": right, "origin": "tv_gateway"},
            requested_by="tv_gateway", force_gate=True)
        out.append(card.get("approval_id"))
    return out


_OFFERED: dict = {}                    # "device:right" -> when its card was last raised
OFFER_COOLDOWN_S = 3600


def offer_right(device_id: str, right: str, *, key_id: str = "", biometric: Optional[bool] = None,
                detail: str = "") -> Optional[str]:
    """Raise the screen-only card for one wider right on an already-paired device. Returns the card's id, or None
    when nothing new is needed or the right has no card.

    Plain rights: at most one card an hour per device and right (the cooldown tests in test_tv_pc_read.py).
    The approval right is raised for the device's staged key: ``key_id`` and ``biometric`` are bound into the card,
    a card is raised only if that key and choice are still the staged ones and none is pending for them, and a new
    card replaces the device's older one (test_a_new_key_replaces_the_pending_card,
    test_the_same_key_twice_before_the_press_raises_one_card, test_an_older_key_registering_late_raises_no_card)."""
    from agent_friday.services import approvals
    if right not in RIGHT_CARDS:
        return None
    if key_id:
        if not isinstance(biometric, bool):
            raise ValueError("a key card names the fingerprint choice")
        return _raise_key_card(device_id, right, key_id, biometric, detail)
    key = ":".join((device_id, right))
    with _LOCK:
        now = time.time()
        if now - _OFFERED.get(key, 0.0) < OFFER_COOLDOWN_S:
            return None
        _OFFERED[key] = now
    title, description = RIGHT_CARDS[right]
    card = approvals.create_approval(
        kind=RIGHT_KIND, subject_type="device_right", subject_id=key,
        title=title, description=description, action_description=title,
        payload={"device_id": device_id, "right": right, "origin": "tv_gateway"},
        requested_by="tv_gateway", force_gate=True)
    return card.get("approval_id")


_KEY_CARD_LOCK = threading.RLock()


def _staged_key_card(device_id: str, key_id: str, biometric: bool) -> Optional[dict]:
    from agent_friday.services import approvals
    for card in approvals.list_approvals(status="pending"):
        payload = card.get("payload") or {}
        if (card.get("kind") == RIGHT_KIND and payload.get("device_id") == device_id
                and payload.get("right") == device_pairing.APPROVAL_RIGHT and payload.get("key_id") == key_id
                and payload.get("biometric") is biometric):
            return card
    return None


def key_card_in_step(device_id: str) -> bool:
    """True when the device has no staged key, or a pending card names the staged key and its fingerprint choice.
    Guarded by test_a_registered_key_always_has_a_card_on_the_screen."""
    staged = device_pairing.pending_approval_key(device_id)
    return staged is None or _staged_key_card(device_id, staged.key_id, staged.biometric) is not None


class CardNotRaised(Exception):
    """The PC could not put the card for a staged key on its screen."""


def register_approval_key(device_id: str, spki_der: bytes, biometric: bool, detail: str):
    """Stage a device's approval key and raise its card as ONE step, so two registrations from one device cannot
    interleave and leave a card nobody can use. Returns (KeyStage, key_id); raises device_pairing.ApprovalKeyRefused
    or CardNotRaised (the key is then not staged). Guarded by test_two_registrations_at_once_leave_one_live_card."""
    with _KEY_CARD_LOCK:
        stage, key_id = device_pairing.stage_approval_key(device_id, spki_der, biometric)
        if stage is device_pairing.KeyStage.ALREADY_ACTIVE:
            device_pairing.expire_pending_key_cards(device_id, "the active key was registered again")
            return stage, key_id
        try:
            offer_right(device_id, device_pairing.APPROVAL_RIGHT, key_id=key_id, biometric=biometric, detail=detail)
            raised = key_card_in_step(device_id)
        except Exception as error:
            device_pairing.clear_pending_key(device_id)
            raise CardNotRaised() from error
        if not raised:
            device_pairing.clear_pending_key(device_id)
            raise CardNotRaised()
        return stage, key_id


def _raise_key_card(device_id: str, right: str, key_id: str, biometric: bool, detail: str) -> Optional[str]:
    from agent_friday.services import approvals
    with _KEY_CARD_LOCK:
        staged = device_pairing.pending_approval_key(device_id)
        if staged is None or staged.key_id != key_id or staged.biometric is not biometric:
            return None                                       # not the staged key and choice (any more): no card
        if _staged_key_card(device_id, key_id, biometric) is not None:
            return None
        device_pairing.expire_pending_key_cards(device_id, "a newer key was registered")
        title, description = RIGHT_CARDS[right]
        card = approvals.create_approval(
            kind=RIGHT_KIND, subject_type="device_right",
            subject_id="%s:%s:%s" % (device_id, right, secrets.token_hex(6)),
            title=title, description=" ".join(part for part in (description, detail) if part),
            action_description=title,
            payload={"device_id": device_id, "right": right, "origin": "tv_gateway", "key_id": key_id,
                     "biometric": biometric},
            requested_by="tv_gateway", force_gate=True)
        return card.get("approval_id")


def _on_right_decision(rec: dict) -> None:
    from agent_friday.services.approvals import SCREEN_LABEL
    payload = rec.get("payload") or {}
    right, device_id = payload.get("right"), payload.get("device_id")
    if right not in RIGHT_CARDS or rec.get("status") != "approved" or rec.get("decided_by") != SCREEN_LABEL:
        return
    if right == device_pairing.APPROVAL_RIGHT and not device_pairing.activate_approval_key(
            str(device_id), str(payload.get("key_id") or ""), bool(payload.get("biometric"))):
        return                          # the key this card named is gone or was replaced: grant nothing
    device_pairing.set_right(str(device_id), right, True)


def _register() -> None:
    from agent_friday.services import approvals
    approvals.register_decision_hook(KIND, _on_decision)
    approvals.register_decision_hook(RIGHT_KIND, _on_right_decision)
    device_pairing.on_pairing_window(opened=reset_budget, closed=end_in_flight)


_register()


def _reset_for_tests() -> None:
    with _LOCK:
        _PENDING.clear()
        _STARTS.clear()
        _OFFERED.clear()


def pending_card(pairing_id: str) -> Optional[str]:
    with _LOCK:
        p = _PENDING.get(str(pairing_id))
        return p["card_id"] if p else None
