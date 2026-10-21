"""The exact text of what was sent to the cloud, kept only if the owner turns
it on (north star §29.6, burn-down BD-15 slice P2).

By default the egress ledger keeps a FINGERPRINT of each send (a keyed hash
of the content the gate released), never the words: §29.6 says the ledger
"does not store the full sensitive payload by default". The owner can choose
to keep the exact content as well, so a receipt can show precisely what left.

THE CHOICE lives in its own file, signed with the governance key, and only
`set_keep` writes it (the privacy-map route, which needs a click in Friday's
own window). It is deliberately not a key in settings.json: the generic
settings route, a local program or a tool that can write files cannot turn
keeping on. A missing, edited or unsigned choice reads as OFF.

WHILE ON:
* each send is written encrypted through Friday's key store
  (credential_store.protect); if that can't encrypt, nothing is kept;
* nothing is kept while off the record (the ledger has no fingerprint then);
* a send over MAX_KEEP_BYTES keeps its fingerprint only; images inside a
  smaller send are kept with it;
* everything is pruned after `days` (1 to 365, default 30), and the oldest is
  dropped first once the total passes MAX_TOTAL_BYTES.
Turning it off deletes everything already kept.
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

_log = logging.getLogger("friday.sent_text")

DEFAULT_DAYS = 30
MAX_DAYS = 365
#: The most one kept send may hold; a larger send keeps its fingerprint only.
MAX_KEEP_BYTES = 4 * 1024 * 1024
#: The most everything kept may hold; past it the oldest goes first.
MAX_TOTAL_BYTES = 256 * 1024 * 1024
_FP = re.compile(r"[0-9a-f]{64}")
_LOCK = threading.Lock()
_last_prune = 0.0

EXPLANATION = (
    "By default Friday keeps only a fingerprint of each send to the cloud: a short "
    "code that identifies exactly what the privacy gate let out, without keeping the "
    "words. If you turn this on, Friday also keeps the content of each send (text, and "
    "any images in it) for the number of days you choose, encrypted with Friday's key "
    "store on this PC, so a receipt can show you precisely what left. Unless you've set "
    "a passphrase for that key store, a program running as you on this PC could open "
    "it. Nothing is kept while you're off the record. Turning it off deletes everything "
    "already kept.")


def _dir() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "vault" / "sent-text"


def _choice_path() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "vault" / "sent-text-choice.json"


def _key() -> Optional[bytes]:
    try:
        from agent_friday.governance.proof_of_integrity import get_governance_key
        return get_governance_key()
    except Exception:
        return None


def _sign(body: Dict[str, Any], key: bytes) -> str:
    raw = json.dumps(body, sort_keys=True).encode("utf-8")
    return hmac.new(key, b"sent-text-choice\x00" + raw, hashlib.sha256).hexdigest()


def _choice() -> Dict[str, Any]:
    """The owner's signed choice; anything missing, edited or unsigned is off."""
    off = {"keep": False, "days": DEFAULT_DAYS}
    try:
        data = json.loads(_choice_path().read_text(encoding="utf-8"))
    except Exception:
        return off
    if not isinstance(data, dict):
        return off
    key = _key()
    body = {k: data.get(k) for k in ("keep", "days", "at")}
    if key is None or not hmac.compare_digest(str(data.get("sig") or ""), _sign(body, key)):
        return off
    try:
        days_ = max(1, min(MAX_DAYS, int(body["days"])))
    except Exception:
        days_ = DEFAULT_DAYS
    return {"keep": body.get("keep") is True, "days": days_}


def enabled() -> bool:
    return _choice()["keep"]


def days() -> int:
    return _choice()["days"]


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record
        return bool(off_record.active())
    except Exception:
        return True                       # can't tell: keep nothing


def _path(fp: str) -> Optional[Path]:
    if not isinstance(fp, str) or not _FP.fullmatch(fp):
        return None
    return _dir() / (fp + ".bin")


def keep(fp: str, text: str, *, provider: str = "", model: str = "",
         purpose: str = "") -> bool:
    """Keep the content of one send under its fingerprint. Returns True only
    when it was written encrypted. Never raises."""
    try:
        if not enabled() or _off_record():
            return False
        p = _path(fp)
        if p is None:
            return False
        raw = json.dumps({"ts": time.time(), "provider": provider, "model": model,
                          "purpose": purpose, "text": text}, ensure_ascii=False).encode("utf-8")
        if len(raw) > MAX_KEEP_BYTES:
            return False
        from agent_friday.services import credential_store as cs
        blob, method = cs.protect(raw)
        if method == "plaintext" or blob == raw:
            return False                  # never keep it readable
        with _LOCK:
            # Checked again under the lock: a switch-off that ran meanwhile wins.
            if not enabled() or _off_record():
                return False
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            with open(tmp, "wb") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, p)
        _prune_sometimes()
        return True
    except Exception as e:
        _log.warning("sent text could not be kept: %s", type(e).__name__)
        return False


def read(fp: str) -> Optional[Dict[str, Any]]:
    p = _path(fp)
    if p is None or not p.is_file():
        return None
    try:
        from agent_friday.services import credential_store as cs
        data = json.loads(cs.unprotect(p.read_bytes()).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if time.time() - float(data.get("ts") or 0) > days() * 86400:
        return None                       # past its time; pruned on the next pass
    return data


def prune(now: Optional[float] = None) -> int:
    """Drop what is past its days, then the oldest until under MAX_TOTAL_BYTES."""
    now = now or time.time()
    cutoff = now - days() * 86400
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
            if st.st_mtime < cutoff:
                try:
                    f.unlink()
                    n += 1
                except OSError:
                    pass
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


def _prune_sometimes() -> None:
    global _last_prune
    if time.time() - _last_prune > 3600:
        _last_prune = time.time()
        prune()


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


class ChoiceRefused(Exception):
    pass


def set_keep(keep_text: bool, keep_days: Optional[int] = None) -> Dict[str, Any]:
    """The owner's choice, signed. Off deletes everything already kept, and
    always works; on needs the governance key to sign the choice."""
    if keep_text is not True:
        try:
            _choice_path().unlink()
        except FileNotFoundError:
            pass
        deleted = purge_all()
        return dict(status(), deleted=deleted)
    key = _key()
    if key is None:
        raise ChoiceRefused("Keeping the text needs Friday's integrity key, which isn't available.")
    body = {"keep": True, "days": max(1, min(MAX_DAYS, int(keep_days or DEFAULT_DAYS))),
            "at": round(time.time(), 3)}
    data = dict(body, sig=_sign(body, key))
    p = _choice_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, p)
    return dict(status(), deleted=0)


def status() -> Dict[str, Any]:
    d = _dir()
    files = list(d.glob("*.bin")) if d.is_dir() else []
    c = _choice()
    return {"keep": c["keep"], "days": c["days"], "kept": len(files),
            "bytes": sum(f.stat().st_size for f in files if f.is_file()),
            "explanation": EXPLANATION}
