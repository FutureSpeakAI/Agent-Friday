"""A21: Friday's key store is protected by a passphrase by default.

The owner's ruling (2026-10-21, docs/design/north-star/AMENDMENTS.md A21):

* New installs: setup asks for a passphrase that also protects the key store's root key (the
  key that encrypts every saved API key and sign-in). It is on by default; the owner can skip it
  explicitly. The words say plainly what it protects and that losing it means entering the keys
  again.
* Existing installs: one one-time card offers it. Nothing is forced.

The passphrase is the vault passphrase (the key store is wrapped with the passphrase Friday
resolves at start-up: services/keystore, routes/owner_security), so start-up stays unattended.

State is one small record, ``security/keystore_offer.json``:
    {"decided": "protected" | "skipped" | "failed" | "already" , "via": "setup" | "card" | "boot",
     "card": "shown" | "not_needed", "at": iso}
Written once a decision exists (setup's, or the card having been shown), so the card is shown at
most once and never to an install whose setup already decided.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

_log = logging.getLogger("friday.keystore_offer")

FILE_NAME = "keystore_offer.json"

CARD_TITLE = "Lock your saved keys with a passphrase?"
CARD_BODY = ("Friday keeps your AI provider keys and account sign-ins encrypted on this computer. "
             "The key that unlocks them is protected only by this Windows account's file "
             "permissions. A passphrase (your vault passphrase) can lock it too, so the file alone "
             "is useless to anyone who copies it. Start-up stays automatic. If you ever lose the "
             "passphrase, you would enter those keys and sign-ins again; nothing else is lost. "
             "This is optional, and you can do it any time in Settings > Privacy & Data.")
CARD_ACTIONS = [{"label": "Lock my saved keys", "workspace": "settings", "tab": "privacy"}]


def _path():
    from agent_friday import core
    return core.FRIDAY_DIR / "security" / FILE_NAME


def state() -> dict:
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write(**fields) -> dict:
    d = dict(state(), **fields, at=datetime.now(timezone.utc).isoformat())
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(FILE_NAME + ".tmp")
    tmp.write_text(json.dumps(d, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(p)
    return d


def record(decided: str, via: str) -> dict:
    """Record the owner's decision (setup's choice, or a skip). After this no card is shown."""
    return _write(decided=decided, via=via, card=state().get("card") or "not_needed")


def protect_keystore(passphrase: str) -> tuple:
    """Wrap the key store's root key with `passphrase` and prove the next start can unwrap it.
    (True, "") on success; (False, reason in plain words) otherwise, with the key store left as
    it was (protected by file permissions). Never logs the passphrase."""
    from agent_friday.services import keystore as ks
    try:
        ks.root_key()                           # a fresh install has no key store file yet
        if (ks.describe() or {}).get("wrap") == "passphrase":
            return True, ""
        ks.set_wrap("passphrase", passphrase)
        ks.root_key(use_cache=False)            # the next start can unwrap it
        return True, ""
    except Exception as e:
        _log.warning("key store passphrase wrap failed: %s", type(e).__name__)
        try:
            ks.set_wrap("none")
        except Exception:
            pass
        return False, ("the saved keys stay protected by this account's file permissions; you can "
                       "lock them later in Settings > Privacy & Data")


def _setup_finished() -> bool:
    from agent_friday import core
    try:
        return bool(core._SETUP_MARKER.exists())
    except Exception:
        return False


def push_card_if_pending() -> str:
    """For an install that finished setup before A21: one card, once. Returns what happened:
    'decided' | 'setup_pending' | 'already' | 'shown' | 'failed'."""
    st = state()
    if st.get("decided") or st.get("card") == "shown":
        return "decided"
    if not _setup_finished():
        return "setup_pending"                  # setup will ask
    try:
        from agent_friday.services import keystore as ks
        if (ks.describe() or {}).get("wrap") == "passphrase":
            record("already", "boot")
            return "already"
    except Exception:
        pass
    try:
        import agent_friday.notifications_engine as _ne
        notice = _ne.push(title=CARD_TITLE, body=CARD_BODY, priority="high", source="security",
                          kind="keystore_offer", emitter="KS1", dedupe_key="keystore-passphrase-offer",
                          actions=list(CARD_ACTIONS), target={"workspace": "settings", "tab": "privacy"})
    except Exception as e:
        _log.info("key store offer card not shown: %s", type(e).__name__)
        return "failed"
    if not notice:
        return "failed"                          # tried again at the next start
    _write(card="shown")
    return "shown"
