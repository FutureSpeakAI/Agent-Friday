"""What each store Friday keeps declares about itself (north star §11.7-3):
encryption, owner scope, migration, backup, retention and corruption
detection, for every entry of store_inventory.STORES.

The declarations are Friday's own fixed words, worked out from what the code
does, never from the folder's contents:

* backup is read from the Friday Bundle's own category rules
  (friday_bundle.category_of), so it cannot drift from what a bundle carries;
* encryption follows the store's kind (the vault, the key store, files Friday
  encrypts, credential files the inventory never opens, or plain files the
  Windows account protects);
* migration says the store moves with the data folder's schema
  (services/migrations) or is recreated;
* retention says how long it is kept and what trims it;
* corruption detection says what notices damage (the receipt chain's hashes
  and signatures, SQLite's header, a JSON parse) or that nothing does.

One person owns a data folder today, so the owner scope is the owner (or this
PC, for state that belongs to the machine). ``policy(store)`` returns the six
declarations; ``all_policies()`` every store's. The Doctor's store list shows
them and the inventory in every report, bundle and export carries them.
"""
from __future__ import annotations

from pathlib import PurePosixPath

KEYS = ("encryption", "owner_scope", "migration", "backup", "retention", "corruption_check")

ENC_VAULT = "sealed in the vault (the vault key)"
ENC_KEYSTORE = "Friday's key store"
ENC_FRIDAY = "encrypted by Friday"
ENC_CREDENTIAL = "credential files: sealed where Friday's credential store writes them; never opened here"
ENC_PLAIN = "not encrypted by Friday (the Windows account protects this PC's disk)"

#: Retention words for stores that are trimmed or cleared on a rule.
RETENTION = {
    "diagnostic_reports": "the newest five diagnostic reports",
    "doctor_reports": "the newest saved Doctor reports",
    "upgrade_records": "upgrade receipts kept; rebuild results the newest 20; the owner's list until marked sorted",
    "trash": "until the owner empties the trash",
    "backups_schedule": "the newest bundles, as many as the owner set",
    "action_ledger": "kept whole: receipts are never edited; old segments are archived, not deleted",
    "conversations": "kept until the owner deletes or forgets them",
}

#: Corruption detection for stores with a check of their own.
CORRUPTION = {
    "action_ledger": "hash chain and signatures (the receipt audit and the Doctor's receipt_chain check)",
    "vault": "the vault's own seal: a damaged item does not open",
    "keystore": "the key store's version and seal",
    "data_schema": "parsed at every start; a schema that is not a number counts as none",
}


def _backup(s) -> str:
    from agent_friday.services import friday_bundle
    loc = s.location.rstrip("/")
    rel = PurePosixPath(loc + "/x") if s.is_folder else PurePosixPath(loc)
    try:
        cat = friday_bundle.category_of(rel)
    except Exception:  # noqa: BLE001
        return "unknown"
    if loc.lower() == getattr(friday_bundle, "SCHEMA_FILE", "schema.json"):
        return "always in a Friday Bundle (with any categories chosen)"
    return ("in a Friday Bundle, as %s" % cat) if cat else "never in a Friday Bundle (stays on this PC)"


def _encryption(s) -> str:
    loc = s.location
    if loc.startswith("vault/"):
        return ENC_VAULT
    if loc.startswith("security/"):
        return ENC_KEYSTORE if loc.endswith(".json") else ENC_CREDENTIAL
    if "encrypted" in s.fmt:
        return ENC_FRIDAY
    if s.version and s.version[0] == "secret":
        return ENC_CREDENTIAL
    return ENC_PLAIN


def _corruption(s) -> str:
    if s.name in CORRUPTION:
        return CORRUPTION[s.name]
    kind = s.version[0] if s.version else "none"
    if kind == "secret":
        return "not opened by the inventory; the service that reads it reports damage"
    if kind in ("sqlite", "sqlite_meta") or s.fmt == "sqlite":
        return "SQLite's header is read for its version; a damaged file reads as unreadable"
    if kind == "jsonl":
        return "its first line is read for its version; a damaged line reads as unreadable"
    if kind == "json":
        return "parsed for its version; a file that does not parse reads as unreadable"
    if kind == "in":
        return "a file inside is read for its version; a damaged one reads as unreadable"
    return "none beyond the inventory's walk (sizes and counts)"


def policy(s) -> dict:
    """The six declarations for one store_inventory.Store."""
    durable = bool(s.durable)
    return {
        "encryption": _encryption(s),
        "owner_scope": "the owner" if durable else "this PC",
        "migration": ("moves with the data folder's schema (versioned migrations at start)" if durable
                      else "none: recreated or fetched again when needed"),
        "backup": _backup(s),
        "retention": RETENTION.get(s.name) or ("kept until the owner deletes it, or Forget or Erase removes it"
                                               if durable else "this PC's working state: may be cleared or "
                                                               "rebuilt at any time"),
        "corruption_check": _corruption(s),
    }


def all_policies() -> dict:
    from agent_friday.services import store_inventory
    return {s.name: policy(s) for s in store_inventory.STORES}
