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
import threading
from datetime import datetime, timezone

_log = logging.getLogger("friday.keystore_offer")

FILE_NAME = "keystore_offer.json"
_LOCK = threading.Lock()   # the start-up card thread and request handlers both write the record

CARD_TITLE = "Lock your saved keys with a passphrase?"
CARD_BODY = ("Friday keeps your AI provider keys and account sign-ins encrypted on this computer. "
             "The key that unlocks them is protected only by this computer account's file "
             "permissions. A passphrase (your vault passphrase) can lock it too, so the file alone "
             "is useless to anyone who copies it. Start-up stays automatic. If you ever lose the "
             "passphrase, you would enter those keys and sign-ins again; locking them loses nothing "
             "else. This is optional, and you can do it any time in Settings > Privacy & Data (set a "
             "vault passphrase there first if you have not).")
CARD_ACTIONS = [{"label": "Open Privacy & Data to lock them", "workspace": "settings", "tab": "privacy"}]


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
    with _LOCK:
        d = dict(state(), **fields, at=datetime.now(timezone.utc).isoformat())
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(FILE_NAME + ".tmp")
        tmp.write_text(json.dumps(d, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(p)
        return d


#: Decisions that end the offer. "failed" does not: the owner wanted the lock and did not get it,
#: so the one-time card still comes, with the reason.
SETTLED = ("protected", "skipped", "already")


def record(decided: str, via: str, why: str = "") -> dict:
    """Record the owner's decision (setup's choice, or a skip). After a settled one no card is
    shown. `why` is the plain reason a lock did not happen (never a secret)."""
    return _write(decided=decided, via=via, why=str(why or "")[:300],
                  card=state().get("card") or "not_needed")


def record_skip_during_setup(via: str = "setup") -> bool:
    """A skip counts only while setup is still asking, and never over an earlier decision: on a
    finished install the one-time card is the place to decide."""
    if _setup_finished() or state().get("decided") in SETTLED:
        return False
    record("skipped", via)
    return True


def passphrase_is_kept(passphrase: str) -> bool:
    """The passphrase is in a home the next start reads by itself (the OS keychain or Friday's
    DPAPI file), not only in this process's environment."""
    try:
        import hmac
        from agent_friday.services import vault_passphrase as vp
        kept = vp.durable_value()[0] or ""
        return bool(kept) and hmac.compare_digest(kept.encode("utf-8"), str(passphrase or "").encode("utf-8"))
    except Exception:
        return False


def protect_keystore(passphrase: str) -> tuple:
    """Wrap the key store's root key with `passphrase` and prove the next start can unwrap it.
    (True, "") on success; (False, reason in plain words) otherwise, with the key store left as
    it was (protected by file permissions). Never logs the passphrase."""
    from agent_friday.services import keystore as ks
    if not passphrase_is_kept(passphrase):
        # Locking with a passphrase that lives only in this process would lock the owner out at
        # the next start: every saved key unreadable. Never.
        return False, ("the passphrase could not be saved on this computer (credential manager or "
                       "Friday's protected file), so the saved keys were not locked with it; they "
                       "stay protected by this account's file permissions")
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
        return bool(core._is_existing_install())
    except Exception:
        return False


def push_card_if_pending() -> str:
    """For an install that finished setup before A21: one card, once. Returns what happened:
    'decided' | 'setup_pending' | 'already' | 'shown' | 'failed'."""
    st = state()
    if st.get("decided") in SETTLED or st.get("card") == "shown":
        return "decided"
    if not _setup_finished():
        return "setup_pending"                  # setup will ask
    try:
        from agent_friday.services import keystore as ks
        d = ks.describe() or {}
        if d.get("wrap") == "passphrase":
            record("already", "boot")
            return "already"
        if not d.get("exists"):
            return "setup_pending"               # no saved keys yet: nothing to offer
    except Exception:
        pass
    try:
        import agent_friday.notifications_engine as _ne
        body = CARD_BODY
        if st.get("decided") == "failed" and st.get("why"):
            body = ("Setup tried to lock your saved keys and could not: " + st["why"] + ". " + body)
        notice = _ne.push(title=CARD_TITLE, body=body, priority="high", source="security",
                          kind="keystore_offer", emitter="KS1", dedupe_key="keystore-passphrase-offer",
                          tier="needs_you", actions=list(CARD_ACTIONS),
                          target={"workspace": "settings", "tab": "privacy"})
    except Exception as e:
        _log.info("key store offer card not shown: %s", type(e).__name__)
        return "failed"
    if not notice or not notice.get("id") or notice.get("logged") or notice.get("pull_only"):
        return "failed"                          # not on screen: tried again at the next start
    _write(card="shown")
    return "shown"
