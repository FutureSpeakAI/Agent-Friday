"""The full text of what left the PC, kept with its receipt (north star §29.6
as amended by A20, burn-down BD-15 slice P2).

A20, the owner's ruling of 2026-10-21: by default each send's receipt keeps
the FULL content that left (text, and any images inside the payload),
encrypted at rest, included in "Export all my data" and removed by every
delete that covers it. The owner can switch to FINGERPRINT-ONLY in Settings >
Privacy & Data > Privacy map; that deletes what is kept and from then on each
receipt keeps only a keyed fingerprint of what left.

SEALING. With a vault passphrase set, each entry is sealed with the vault key
(privacy/vault_crypto, AES-256-GCM): "under the vault". Without one there is
no vault key; the entry is then sealed with Friday's key store instead
(credential_store.protect) and the privacy map says so, plainly. It is never
written readable: if neither can seal it, nothing is written and the receipt
says the text was not kept.

THE MODE lives in its own file, and only FINGERPRINT-ONLY is ever written
there, signed with the governance key, by `set_mode` (the privacy-map route,
a click in Friday's own window). A missing, edited or unsigned file reads as
the default, full text: nothing but the owner's signed choice can make
Friday keep less, and deleting the file can only make it keep more.

Off the record nothing is kept (the ledger writes no fingerprint then
either). Entries are kept until deleted; past MAX_TOTAL_BYTES the oldest
content goes first and its receipt keeps the fingerprint.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional
from agent_friday.user_errors import UserFacingError

_log = logging.getLogger("friday.sent_text")

FULL, FINGERPRINT = "full", "fingerprint"
#: The most one kept send may hold; a larger send keeps its fingerprint only.
MAX_KEEP_BYTES = 8 * 1024 * 1024
#: The most everything kept may hold; past it the oldest goes first.
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
_FP = re.compile(r"[0-9a-f]{64}")
_LOCK = threading.Lock()
_last_prune = 0.0
_running_total: Optional[int] = None

EXPLANATION_FULL = (
    "Friday keeps the full text of each send to the cloud (and any images in it) "
    "with its receipt, so you can open exactly what left this PC. It is encrypted on "
    "this PC, included when you export your data, and removed when you delete it. "
    "Nothing is kept while you're off the record, and a single send over 8 MB keeps "
    "only its fingerprint. You can switch to fingerprints "
    "only: then each receipt keeps a short code that identifies what left, without "
    "the words, and the text already kept is deleted.")
EXPLANATION_FINGERPRINT = (
    "Only fingerprints are kept: each receipt has a short code that identifies "
    "exactly what left this PC, without the words. Switch back to keep the full "
    "text of each new send with its receipt, encrypted on this PC.")


def _dir() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "vault" / "sent-text"


def _choice_path() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "vault" / "sent-text-choice.json"


def _gov_key() -> Optional[bytes]:
    try:
        from agent_friday.governance.proof_of_integrity import get_governance_key
        return get_governance_key()
    except Exception:
        return None


def _sign(body: Dict[str, Any], key: bytes) -> str:
    raw = json.dumps(body, sort_keys=True).encode("utf-8")
    return hmac.new(key, b"sent-text-choice\x00" + raw, hashlib.sha256).hexdigest()


def mode() -> str:
    """FULL unless the owner's signed choice says FINGERPRINT."""
    try:
        data = json.loads(_choice_path().read_text(encoding="utf-8"))
    except Exception:
        return FULL
    if not isinstance(data, dict):
        return FULL
    key = _gov_key()
    body = {k: data.get(k) for k in ("mode", "at")}
    if key is None or body.get("mode") != FINGERPRINT \
            or not hmac.compare_digest(str(data.get("sig") or ""), _sign(body, key)):
        return FULL
    return FINGERPRINT


def keeping() -> bool:
    return mode() == FULL


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record
        return bool(off_record.active())
    except Exception:
        return True                       # can't tell: keep nothing


def _vault_key(*, derive: bool = True) -> Optional[bytes]:
    """The vault key. `derive=False` (the map's read path) never derives it,
    which can write the vault config: it reads what start-up already set."""
    try:
        import sys
        if not derive:
            agent = sys.modules.get("agent_friday.services.agent")
            if agent is None or not getattr(agent, "_VAULT_KEY_READY", False):
                return None
            return getattr(agent, "_VAULT_KEY", None)
        from agent_friday.services import agent as _agent
        return _agent._get_vault_key()
    except Exception:
        return None


def _vault_magic() -> bytes:
    try:
        from agent_friday.privacy import vault_crypto as vc
        return vc.MAGIC
    except Exception:
        return b"\x00no-vault\x00"


def _passphrase_set() -> bool:
    try:
        from agent_friday.services import vault_passphrase as vp
        return bool(vp.resolve()[0])
    except Exception:
        return False


def _vault_state() -> str:
    """"ready" (the key is in memory), "pending" (a passphrase is set and the
    key not derived yet: the first send derives it), "failed" (a passphrase
    is set and the key could not be derived), or "none" (no passphrase).
    Changes nothing: no derivation, no key created."""
    if _vault_key(derive=False) is not None:
        return "ready"
    if not _passphrase_set():
        return "none"
    import sys
    agent = sys.modules.get("agent_friday.services.agent")
    if agent is not None and getattr(agent, "_VAULT_KEY_READY", False):
        return "failed"
    return "pending"


def sealed_with() -> Optional[str]:
    """What new entries are sealed with: "vault", "keystore", or None (nothing
    can seal them, so nothing is kept). Reads state only; it never derives a
    key or creates a key store."""
    state = _vault_state()
    if state in ("ready", "pending"):
        return "vault"
    if state == "failed":
        return None                       # never a weaker seal than the owner set up
    return "keystore"


def _seal(raw: bytes) -> Optional[bytes]:
    """The vault key when a passphrase is set (and nothing else then: an entry
    is never sealed more weakly than the owner set up); Friday's key store
    when none is. None when it can't be sealed: nothing is kept."""
    if _passphrase_set() or _vault_key(derive=False) is not None:
        key = _vault_key()
        if key is None:
            return None
        try:
            from agent_friday.privacy import vault_crypto as vc
            blob = vc.encrypt(raw, key)
            return blob if blob and blob != raw else None
        except Exception:
            return None
    try:
        from agent_friday.services import credential_store as cs
        blob, method = cs.protect(raw)
        if method != "plaintext" and blob != raw and cs.looks_protected(blob):
            return blob
    except Exception:
        pass
    return None                           # never keep it readable


def _open(blob: bytes) -> Optional[bytes]:
    """Open a kept entry. Reads never derive the vault key, and anything not
    in a known sealed envelope is refused: a file planted in the folder is
    not shown as what was sent."""
    if blob.startswith(_vault_magic()):
        key = _vault_key(derive=False)
        if key is None:
            return None
        try:
            from agent_friday.privacy import vault_crypto as vc
            return vc.decrypt(blob, key)
        except Exception:
            return None
    try:
        from agent_friday.services import credential_store as cs
        if cs.looks_protected(blob) not in ("keystore", "dpapi"):
            return None
        return cs.unprotect(blob)
    except Exception:
        return None


def has(fp: str) -> bool:
    p = _path(fp)
    return bool(p is not None and p.is_file())


def _path(fp: str) -> Optional[Path]:
    if not isinstance(fp, str) or not _FP.fullmatch(fp):
        return None
    from agent_friday.paths import contained
    try:
        return contained(_dir(), fp + ".bin")
    except ValueError:
        return None


def keep(fp: str, text: str, *, provider: str = "", model: str = "",
         purpose: str = "") -> bool:
    """Keep the content of one send under its fingerprint. True only when it
    was written sealed. Never raises."""
    try:
        if not keeping() or _off_record():
            return False
        p = _path(fp)
        if p is None:
            return False
        raw = json.dumps({"ts": time.time(), "provider": provider, "model": model,
                          "purpose": purpose, "text": text}, ensure_ascii=False).encode("utf-8")
        if len(raw) > MAX_KEEP_BYTES:
            return False
        blob = _seal(raw)
        if blob is None:
            return False
        with _LOCK:
            # Checked again under the lock: a switch to fingerprints that ran
            # meanwhile wins.
            if not keeping() or _off_record():
                return False
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            with open(tmp, "wb") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, p)
        _prune_sometimes(len(blob))
        return True
    except Exception as e:
        _log.warning("sent text could not be kept: %s", type(e).__name__)
        return False


def read(fp: str) -> Optional[Dict[str, Any]]:
    p = _path(fp)
    if p is None or not p.is_file():
        return None
    try:
        raw = _open(p.read_bytes())
        data = json.loads(raw.decode("utf-8")) if raw else None
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def prune() -> int:
    """Drop the oldest until everything kept is under MAX_TOTAL_BYTES."""
    n = 0
    d = _dir()
    if not d.is_dir():
        return 0
    with _LOCK:
        files = []
        for f in d.glob("*.bin"):
            try:
                st = f.stat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, f))
        total = sum(s for _, s, _ in files)
        for _, size, f in sorted(files):
            if total <= MAX_TOTAL_BYTES:
                break
            try:
                f.unlink()
                total -= size
                n += 1
            except OSError:
                continue
    return n


def _total_now() -> int:
    d = _dir()
    total = 0
    for f in (d.glob("*.bin") if d.is_dir() else []):
        try:
            total += f.stat().st_size
        except OSError:
            continue
    return total


def _prune_sometimes(added: int = 0) -> None:
    """Keep a running total so the cap holds between full scans: a burst of
    large sends prunes as soon as it goes over, not up to ten minutes later."""
    global _last_prune, _running_total
    if _running_total is None or time.time() - _last_prune > 600:
        _last_prune = time.time()
        _running_total = _total_now()
    else:
        _running_total += int(added)
    if _running_total > MAX_TOTAL_BYTES:
        prune()
        _running_total = _total_now()


def purge_all() -> int:
    n = 0
    d = _dir()
    if not d.is_dir():
        return 0
    with _LOCK:
        for f in d.glob("*.bin*"):
            try:
                f.unlink()
                n += 1
            except OSError:
                continue
    return n


class ChoiceRefused(UserFacingError):
    pass


def set_mode(new_mode: str) -> Dict[str, Any]:
    """The owner's switch. FINGERPRINT is written signed and deletes what is
    kept; FULL removes the choice (back to the default)."""
    if new_mode == FULL:
        try:
            _choice_path().unlink()
        except FileNotFoundError:
            pass
        return dict(status(), deleted=0)
    if new_mode != FINGERPRINT:
        raise ChoiceRefused("say full or fingerprint")
    key = _gov_key()
    if key is None:
        raise ChoiceRefused("Switching to fingerprints needs Friday's integrity key, which isn't available.")
    body = {"mode": FINGERPRINT, "at": round(time.time(), 3)}
    p = _choice_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(dict(body, sig=_sign(body, key))), encoding="utf-8")
    with _LOCK:
        os.replace(tmp, p)
    deleted = purge_all()
    return dict(status(), deleted=deleted)


def delete_kept() -> Dict[str, Any]:
    """Delete everything kept now (the mode stays as it is)."""
    return dict(status(), deleted=purge_all())


def status() -> Dict[str, Any]:
    d = _dir()
    files = list(d.glob("*.bin")) if d.is_dir() else []
    m = mode()
    sealed = sealed_with() if m == FULL else None
    if m == FINGERPRINT:
        words = "Only fingerprints are kept."
    elif sealed == "vault":
        words = "The full text of each send is kept, encrypted with your vault passphrase."
    elif sealed == "keystore":
        words = ("The full text of each send is kept, encrypted with Friday's key store. No vault "
                 "passphrase is set, so it isn't under the vault yet: set one in Privacy & Data > Vault. "
                 "An export carries it still encrypted with this PC's key store.")
    elif _vault_state() == "failed":
        words = ("The full text is meant to be kept under your vault passphrase, but the vault key "
                 "couldn't be opened, so none is kept right now.")
    else:
        words = "The full text is meant to be kept, but nothing can encrypt it right now, so none is kept."
    return {"mode": m, "keep": m == FULL, "sealed_with": sealed, "words": words,
            "kept": len(files), "bytes": sum(f.stat().st_size for f in files if f.is_file()),
            "explanation": EXPLANATION_FULL if m == FULL else EXPLANATION_FINGERPRINT}
