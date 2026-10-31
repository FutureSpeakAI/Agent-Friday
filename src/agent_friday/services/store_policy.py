"""What each store Friday keeps declares about itself (north star §11.7-3):
encryption, owner scope, migration, backup, retention and corruption
detection, for every entry of store_inventory.STORES. (Its schema version is
the inventory's own version column.)

The declarations are Friday's own fixed words about what the code does, never
read from the folder. Most follow from the store's kind; the stores that
differ are named below, so no store is described by a rule that does not fit
it:

* backup is read from the Friday Bundle's own category rules
  (friday_bundle.category_of), so it cannot drift from what a bundle carries;
* encryption names who protects the store: the credential store, Friday's own
  encryption, the vault (partly), the browser, or only this Windows account's
  file permissions;
* migration says what the data folder's schema does for it today;
* retention says what trims it, or that nothing does;
* corruption detection says what notices damage, and what it does not.

One person owns a data folder today, so the owner scope is the owner (or this
PC, for the machine's own state). ``policy(store)`` returns the six;
``all_policies()`` every store's. The Doctor's store list shows them
(GET /api/doctor/stores carries them as one map).
"""
from __future__ import annotations

from pathlib import PurePosixPath

KEYS = ("encryption", "owner_scope", "migration", "backup", "retention", "corruption_check")

ENC_PLAIN = ("not encrypted by Friday; protected by this Windows account's file permissions "
             "(and BitLocker if it is on)")
ENC_CREDENTIAL_STORE = "sealed by Friday's credential store; never opened by the inventory"
ENC_PLAIN_SECRET = ("not encrypted by Friday: a secret in a plain file, protected only by this Windows "
                    "account's file permissions; never opened by the inventory")
ENC_BROWSER = "the browser's own storage (its protection, not Friday's); never opened by the inventory"
ENC_CONFIG = "plain settings file; its secret values are encrypted separately by Friday's credential store"
ENC_FRIDAY = "encrypted by Friday (no plain copy is kept)"
ENC_FRIDAY_LINES = "encrypted by Friday line by line (on by default)"
ENC_LIBRARY = "the Library's index is encrypted by Friday when its key exists"
ENC_VAULT = ("partly encrypted: items written with the vault key are sealed; the vault's logs and many "
             "older items are plain files")
ENC_KEYSTORE = ("holds Friday's root key: wrapped by the passphrase when one is set, otherwise protected only "
                "by file permissions")

CREDENTIAL_STORE = {"provider_keys", "google_accounts", "mcp_oauth", "platforms", "signing"}
BROWSER = {"browser_profile", "browser_sessions"}
CONFIG_WITH_SEPARATE_SECRETS = {"mcp_servers", "channels", "platforms_config"}
FRIDAY_ENCRYPTED = {"work_orders", "meetings"}
FRIDAY_ENCRYPTED_LINES = {"task_journals", "traces"}

NONE_MIGRATED = "none: not migrated; downloads are fetched again, caches, logs and reports start fresh"
SCHEMA_MIGRATED = ("its version is recorded in the data folder's schema at start (migration 1); no versioned "
                   "migration changes it yet")

KEPT = "kept; nothing trims it on a rule; friday erase removes it with the whole data folder"
OWNER_PERIOD = ("kept unless the owner sets a retention period in Settings; then older entries are deleted")

RETENTION = {
    "diagnostic_reports": "the newest five diagnostic reports",
    "upgrade_records": ("upgrade receipts kept; rebuild results the newest 20; the owner's list until marked "
                        "sorted (at most 200)"),
    "trash": "kept until undone or removed by hand; Friday never empties it",
    "backups_schedule": ("the schedule's settings and its list of bundles made (the newest 120 entries); the "
                         "bundles themselves are in the owner's chosen folder, the newest as many as the owner set"),
    "action_ledger": "kept whole: receipts are never edited; old segments are archived, not deleted (friday erase "
                     "excepted)",
    "conversations": "kept until the owner deletes or forgets them; friday erase removes them",
    "task_journals": OWNER_PERIOD,
    "traces": OWNER_PERIOD,
    "meetings": "kept unless the owner sets how long meeting text is kept (Settings)",
}

CORRUPTION = {
    "action_ledger": "hash chain and signatures (the receipt audit and the Doctor's receipt_chain check)",
    "vault": "an encrypted item that is damaged fails its seal and does not open; plain files there are not checked",
    "keystore": "parsed for its version; a damaged file reads as unreadable",
    "data_schema": "read at every start; a damaged or non-numeric schema counts as 0 and the migrations run again",
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
    line = ("in a Friday Bundle, as %s" % cat) if cat else "never in a Friday Bundle (stays on this PC)"
    if s.name == "vault":
        line += " (Friday's own signing keys inside stay on this PC)"
    elif s.name == "security":
        line += " (the key store excepted)"
    return line


def _encryption(s) -> str:
    name, loc = s.name, s.location
    if name in CREDENTIAL_STORE:
        return ENC_CREDENTIAL_STORE
    if name in BROWSER:
        return ENC_BROWSER
    if name in CONFIG_WITH_SEPARATE_SECRETS:
        return ENC_CONFIG
    if name in FRIDAY_ENCRYPTED:
        return ENC_FRIDAY
    if name in FRIDAY_ENCRYPTED_LINES:
        return ENC_FRIDAY_LINES
    if name == "library":
        return ENC_LIBRARY
    if name == "vault":
        return ENC_VAULT
    if name == "keystore":
        return ENC_KEYSTORE
    if loc.startswith("security/") or (s.version and s.version[0] == "secret"):
        return ENC_PLAIN_SECRET
    return ENC_PLAIN


def _corruption(s) -> str:
    if s.name in CORRUPTION:
        return CORRUPTION[s.name]
    kind = s.version[0] if s.version else "none"
    if kind == "secret":
        return "not opened by the inventory; the service that reads it reports damage"
    if kind in ("sqlite", "sqlite_meta") or s.fmt == "sqlite":
        return ("only SQLite's 100-byte header is read: a short header reads as unreadable, a foreign one as "
                "encrypted_or_not_sqlite; damage past the header is not noticed")
    if kind == "jsonl":
        return ("its first line is read for its version; a damaged first line reads as unreadable; later lines "
                "are not checked")
    if kind == "json":
        return "parsed for its version; a file that does not parse reads as unreadable (files over 8 MB are not read)"
    if kind == "in":
        return "a file inside is read for its version; a damaged one reads as unreadable"
    return "none beyond the inventory's walk (sizes and counts)"


def _retention(s) -> str:
    if s.name == "doctor_reports":
        try:
            from agent_friday.services import doctor
            return "the newest %d saved Doctor reports" % int(doctor.KEEP_REPORTS)
        except Exception:  # noqa: BLE001
            return "the newest saved Doctor reports"
    if s.name in RETENTION:
        return RETENTION[s.name]
    return KEPT if s.durable else "this PC's working state: may be cleared or rebuilt at any time"


def policy(s) -> dict:
    """The six declarations for one store_inventory.Store."""
    durable = bool(s.durable)
    return {
        "encryption": _encryption(s),
        "owner_scope": "the owner" if durable else "this PC",
        "migration": SCHEMA_MIGRATED if durable else NONE_MIGRATED,
        "backup": _backup(s),
        "retention": _retention(s),
        "corruption_check": _corruption(s),
    }


def all_policies() -> dict:
    from agent_friday.services import store_inventory
    return {s.name: policy(s) for s in store_inventory.STORES}
