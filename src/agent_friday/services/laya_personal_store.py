"""Where Laya keeps what she learns about you: the vault, and a buffer for when it is locked.

Spec: ftv/program/reference/laya_swot_and_stack_spec_2026-10-10.md section d.3.
Labelled text is the source of truth; every personal weight is rebuilt from it
(services/laya_personal). This module only stores, ages out and deletes rows.

WHERE THINGS LIVE
  <runtime>/laya2/personal/<profile>/
      labels.enc         one sealed row per line, sealed with the vault key
      staging.enc        rows that arrived while the vault was locked, sealed to a
                         key the vault key wraps (below); one "<epoch>\\t<blob>" per line
      staging_key.json   the staging public key, and the private key wrapped by the vault key
      artifacts/         the last three rebuilt personal layers, sealed with the vault key
      state.enc          the live pointer, a pending refit, the notice flag

THE VAULT IS "OPEN" WHEN ITS KEY IS AVAILABLE (services/agent._get_vault_key, the
same key the Library's vault shelf and the sealed credential blobs use). With no
vault key nothing is kept at all: the words are not written in the clear.

THE STAGING BUFFER (Decision 10). While the vault is locked a new row cannot be
sealed with the vault key, and it must not wait in the clear. Each profile has an
X25519 key pair made while the vault was open: the public half sits in
staging_key.json, the private half only wrapped by the vault key. A row that
arrives locked is sealed to the public half (a fresh ephemeral key, HKDF, AES-256-GCM).
Nothing can read it, this process included, until the vault next opens and
unwraps the private half; training cannot read it either (training runs only
while the vault is open, and drains the buffer first). A profile that never saw
the vault open has no staging key, so a locked row is dropped and the report
card says so.

FORGETTING REALLY FORGETS. ``delete_row`` rewrites the file without the row;
``wipe`` removes the whole folder, including rollback artifacts. Retention
(Decision 11, 180 days) is ``purge_expired``. Callers (services/laya_personal)
rebuild what was derived from a deleted row; nothing here keeps a copy.

Nothing here makes a network call, loads a model or imports anything heavy.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

_log = logging.getLogger("friday.laya_personal_store")

LABELS_NAME = "labels.enc"
STAGING_NAME = "staging.enc"
STAGING_KEY_NAME = "staging_key.json"
STATE_NAME = "state.enc"
GEN_NAME = "gen.enc"
ARTIFACT_DIR = "artifacts"
#: Past this many rows the oldest go (a tidy bound, not a quota anyone meets).
MAX_ROWS = 4000
MAX_STAGED = 500
_INFO = b"agent-friday-laya-staging-v1"

_lock = threading.RLock()
_root_override: Optional[Path] = None
_key_provider: Optional[Callable[[], Optional[bytes]]] = None
now_fn: Callable[[], float] = time.time


# ---------------------------------------------------------------------------
#  the folder, and the vault key
# ---------------------------------------------------------------------------

def set_root(path: Optional[Path]) -> None:
    """Tests: keep everything under ``path`` instead of the runtime folder."""
    global _root_override
    _root_override = Path(path) if path is not None else None


def set_key_provider(fn: Optional[Callable[[], Optional[bytes]]]) -> None:
    """Tests: stand in for the vault key. None goes back to the real vault."""
    global _key_provider
    _key_provider = fn


def root() -> Path:
    if _root_override is not None:
        return _root_override
    from agent_friday.core import FRIDAY_DIR
    return Path(FRIDAY_DIR) / "runtime" / "laya2" / "personal"


def _safe(profile: str) -> str:
    p = "".join(c for c in str(profile or "") if c.isalnum() or c in "-_.")[:64]
    if not p or p.strip(".") == "":
        raise ValueError("no profile")
    return p


def profile_dir(profile: str) -> Path:
    return root() / _safe(profile)


def vault_key() -> Optional[bytes]:
    """The 32-byte vault key, or None when the vault is locked or has none."""
    if _key_provider is not None:
        try:
            return _key_provider()
        except Exception:
            return None
    try:
        from agent_friday.services import agent
        k = agent._get_vault_key()
        return k if isinstance(k, (bytes, bytearray)) and len(k) == 32 else None
    except Exception:
        return None


def vault_open() -> bool:
    return vault_key() is not None


# ---------------------------------------------------------------------------
#  sealing
# ---------------------------------------------------------------------------

def _crypto():
    from agent_friday.privacy import vault_crypto
    return vault_crypto


def seal(obj, key: bytes) -> str:
    """JSON -> AES-256-GCM (the vault's own container) -> one base64 line."""
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(_crypto().encrypt(raw, key)).decode("ascii")


def unseal(blob: str, key: bytes):
    return json.loads(_crypto().decrypt(base64.b64decode(blob), key).decode("utf-8"))


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
#  the staging key pair
# ---------------------------------------------------------------------------

def notice_seen(profile: str) -> bool:
    return (profile_dir(profile) / "notice_seen").exists()


def mark_notice_seen(profile: str) -> None:
    p = profile_dir(profile) / "notice_seen"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")


def has_staging_key(profile: str) -> bool:
    return (profile_dir(profile) / STAGING_KEY_NAME).exists()


def ensure_staging_key(profile: str) -> bool:
    """Make the profile's staging key pair, while the vault is open. True when
    one exists afterwards."""
    with _lock:
        p = profile_dir(profile) / STAGING_KEY_NAME
        if p.exists():
            return True
        key = vault_key()
        if key is None:
            return False
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        priv = X25519PrivateKey.generate()
        raw_priv = priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                      serialization.NoEncryption())
        raw_pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        wrapped = base64.b64encode(_crypto().encrypt(raw_priv, key)).decode("ascii")
        _atomic_write(p, json.dumps({"v": 1, "public": raw_pub.hex(), "wrapped_private": wrapped,
                                     "mac": _pub_mac(key, raw_pub)}, sort_keys=True).encode("utf-8"))
        return True


def _pub_mac(key: bytes, public: bytes) -> str:
    """The vault key vouches for the staging public key, so a local writer who swaps it is caught
    when the buffer is drained (a locked process cannot check it, but cannot be fooled for long)."""
    return hmac.new(key, b"laya-staging-pub\0" + public, hashlib.sha256).hexdigest()


def _staging_key_ok(profile: str, key: bytes) -> bool:
    try:
        d = json.loads((profile_dir(profile) / STAGING_KEY_NAME).read_text(encoding="utf-8"))
        return hmac.compare_digest(str(d.get("mac") or ""), _pub_mac(key, bytes.fromhex(d["public"])))
    except Exception:
        return False


def _staging_public(profile: str) -> Optional[bytes]:
    try:
        d = json.loads((profile_dir(profile) / STAGING_KEY_NAME).read_text(encoding="utf-8"))
        return bytes.fromhex(d["public"])
    except Exception:
        return None


def _staging_private(profile: str, key: bytes):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    d = json.loads((profile_dir(profile) / STAGING_KEY_NAME).read_text(encoding="utf-8"))
    raw = _crypto().decrypt(base64.b64decode(d["wrapped_private"]), key)
    return X25519PrivateKey.from_private_bytes(raw)


def _derive(shared: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(shared)


def seal_to_staging(obj, public: bytes) -> str:
    """Seal ``obj`` so only the holder of the staging private key can open it."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    eph = X25519PrivateKey.generate()
    shared = eph.exchange(X25519PublicKey.from_public_bytes(public))
    eph_pub = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    nonce = os.urandom(12)
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ct = AESGCM(_derive(shared)).encrypt(nonce, raw, eph_pub)
    return base64.b64encode(eph_pub + nonce + ct).decode("ascii")


def unseal_staged(blob: str, private) -> dict:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = base64.b64decode(blob)
    eph_pub, nonce, ct = raw[:32], raw[32:44], raw[44:]
    shared = private.exchange(X25519PublicKey.from_public_bytes(eph_pub))
    return json.loads(AESGCM(_derive(shared)).decrypt(nonce, ct, eph_pub).decode("utf-8"))


# ---------------------------------------------------------------------------
#  rows
# ---------------------------------------------------------------------------

def _staging_lines(profile: str) -> List[Tuple[float, str]]:
    p = profile_dir(profile) / STAGING_NAME
    out: List[Tuple[float, str]] = []
    try:
        for line in p.read_text(encoding="ascii").splitlines():
            at, _, blob = line.partition("\t")
            if blob:
                try:
                    out.append((float(at), blob))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def _write_staging(profile: str, lines: List[Tuple[float, str]]) -> None:
    p = profile_dir(profile) / STAGING_NAME
    if not lines:
        try:
            p.unlink()
        except OSError:
            pass
        return
    _atomic_write(p, ("\n".join("%.3f\t%s" % (at, blob) for at, blob in lines) + "\n").encode("ascii"))


def staged_count(profile: str) -> int:
    with _lock:
        return len(_staging_lines(profile))


def clear_staging(profile: str) -> int:
    """Drop every buffered row without opening it (a forget while the vault is locked: the sealed rows
    cannot be read, so nothing waiting is kept). Returns how many were dropped."""
    with _lock:
        n = len(_staging_lines(profile))
        _write_staging(profile, [])
        return n


def _gen_path(profile: str) -> Path:
    """The anti-restore counter lives BESIDE the profile folder, not in it, so putting an older copy of the
    folder back (File History, OneDrive, a shadow copy of the folder) leaves the counter where it was."""
    return root() / (_safe(profile) + "." + GEN_NAME)


def _gen_of(profile: str, key: bytes) -> int:
    try:
        return int(unseal(_gen_path(profile).read_text(encoding="ascii").strip(), key).get("gen") or 0)
    except Exception:
        return 0


def _set_gen(profile: str, gen: int, key: bytes) -> None:
    _atomic_write(_gen_path(profile), seal({"gen": int(gen)}, key).encode("ascii"))


def _header_gen(profile: str, key: bytes) -> int:
    try:
        with open(profile_dir(profile) / LABELS_NAME, "r", encoding="ascii") as f:
            first = f.readline().strip()
        obj = unseal(first, key)
        return int(obj.get("gen") or 0) if isinstance(obj, dict) and obj.get("_hdr") else 0
    except Exception:
        return 0


def _read_labels(profile: str, key: bytes) -> List[dict]:
    p = profile_dir(profile) / LABELS_NAME
    rows: List[dict] = []
    try:
        lines = p.read_text(encoding="ascii").splitlines()
    except OSError:
        return rows
    header_gen = None
    for line in lines:
        if not line.strip():
            continue
        try:
            obj = unseal(line, key)
        except Exception:
            _log.warning("a sealed label could not be opened and was skipped")
            continue
        if isinstance(obj, dict) and obj.get("_hdr"):
            header_gen = int(obj.get("gen") or 0)
            continue
        rows.append(obj)
    if header_gen is not None and header_gen < _gen_of(profile, key):
        # an older copy of the file came back (a backup restore): rows forgotten since would return with it
        _log.warning("the label file is older than the last forget; it was ignored")
        return []
    return rows


def _write_labels(profile: str, rows: List[dict], key: bytes) -> None:
    """Rewrite the whole file. Every rewrite (a forget, an undo, retention) bumps the generation kept
    beside it, so restoring an older copy of the file is noticed and ignored."""
    p = profile_dir(profile) / LABELS_NAME
    gen = max(_gen_of(profile, key), _header_gen(profile, key)) + 1
    if rows:
        body = [seal({"_hdr": 1, "gen": gen}, key)] + [seal(r, key) for r in rows]
        _atomic_write(p, ("\n".join(body) + "\n").encode("ascii"))
    else:
        try:
            p.unlink()
        except OSError:
            pass
    _set_gen(profile, gen, key)


def _append_label(profile: str, row: dict, key: bytes) -> None:
    """Add one sealed line; nothing already stored is rewritten. A file made by appends alone still has a
    generation (and its counter) from its first line."""
    p = profile_dir(profile) / LABELS_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    fresh = not p.exists()
    with open(p, "ab") as f:
        if fresh:
            gen = max(1, _gen_of(profile, key))
            f.write((seal({"_hdr": 1, "gen": gen}, key) + "\n").encode("ascii"))
            if _gen_of(profile, key) < gen:
                _set_gen(profile, gen, key)
        f.write((seal(row, key) + "\n").encode("ascii"))


def over_cap(profile: str, cap: int) -> bool:
    """More stored lines than ``cap``? (A cheap line count; the writer thread asks after an append.)"""
    try:
        with open(profile_dir(profile) / LABELS_NAME, "rb") as f:
            return sum(1 for _ in f) > cap + 1
    except OSError:
        return False


def excess_ids(profile: str, cap: int) -> List[str]:
    """Ids of the oldest rows beyond ``cap`` (the vault must be open)."""
    with _lock:
        key = vault_key()
        if key is None:
            return []
        rows = _read_labels(profile, key)
        if len(rows) <= cap:
            return []
        return [r["id"] for r in sorted(rows, key=lambda r: float(r.get("at") or 0))[:len(rows) - cap]]


def delete_ids(profile: str, ids) -> int:
    """Delete rows by id (forget semantics: the caller drops the artifacts built on them)."""
    ids = set(ids)
    with _lock:
        key = vault_key()
        if key is None or not ids:
            return 0
        rows = _read_labels(profile, key)
        kept = [r for r in rows if r.get("id") not in ids]
        if len(kept) != len(rows):
            _write_labels(profile, kept, key)
        return len(rows) - len(kept)


def drain_staging(profile: str) -> int:
    """Move buffered rows into the vault store. Needs the vault open; a no-op
    otherwise. Rows that cannot be opened are dropped. Returns rows moved."""
    with _lock:
        key = vault_key()
        if key is None:
            return 0
        lines = _staging_lines(profile)
        if not lines:
            return 0
        moved: List[dict] = []
        if not _staging_key_ok(profile, key):
            # the public key was changed (or never vouched for): what is in the buffer may be sealed to someone else
            _log.warning("the staging key does not check out; the buffer was cleared and the key replaced")
            _write_staging(profile, [])
            try:
                (profile_dir(profile) / STAGING_KEY_NAME).unlink()
            except OSError:
                pass
            ensure_staging_key(profile)
            return 0
        try:
            priv = _staging_private(profile, key)
        except Exception:
            _log.warning("the staging key could not be opened; the buffer was cleared")
            _write_staging(profile, [])
            return 0
        for _at, blob in lines:
            try:
                moved.append(unseal_staged(blob, priv))
            except Exception:
                continue
        rows = _read_labels(profile, key)
        seen = {r.get("id") for r in rows}
        rows += [r for r in moved if r.get("id") not in seen]
        _write_labels(profile, rows, key)
        _write_staging(profile, [])
        return len(moved)


def load_rows(profile: str) -> Optional[List[dict]]:
    """Every row, oldest first, or None when the vault is locked (nothing can
    be read then, which is the point). Drains the staging buffer first."""
    with _lock:
        key = vault_key()
        if key is None:
            return None
        drain_staging(profile)
        return _read_labels(profile, key)


def append(profile: str, row: dict) -> str:
    """Keep one row. Returns "stored" (sealed with the vault key), "staged"
    (sealed to the staging key, vault locked) or "dropped:<why>"."""
    with _lock:
        key = vault_key()
        if key is not None:
            ensure_staging_key(profile)
            drain_staging(profile)
            _append_label(profile, row, key)
            return "stored"
        public = _staging_public(profile)
        if public is None:
            return "dropped:the vault is locked and has never been opened for this profile"
        lines = _staging_lines(profile)
        if len(lines) >= MAX_STAGED:
            return "dropped:the locked-vault buffer is full"
        lines.append((now_fn(), seal_to_staging(row, public)))
        _write_staging(profile, lines)
        return "staged"


def replace_rows(profile: str, rows: List[dict]) -> bool:
    """Write back rows a caller changed (an item set aside, a status set). It MERGES: under the store lock the
    file is read again, and any row stored since the caller took its snapshot (the writer thread appends
    while a rebuild works for seconds) is kept after the caller's rows. Every deleter holds the build lock the
    rewriters hold, so a stored row the caller does not have is always one appended since; no owner row is
    lost. Rows are never trimmed here: the cap is enforced with forget semantics (excess_ids). Needs the
    vault open."""
    with _lock:
        key = vault_key()
        if key is None:
            return False
        have = {r.get("id") for r in rows}
        extra = [r for r in _read_labels(profile, key) if r.get("id") not in have]
        _write_labels(profile, list(rows) + extra, key)
        return True


def delete_row(profile: str, row_id: str) -> bool:
    """Remove one row for good. True when a row was removed. Needs the vault open."""
    with _lock:
        key = vault_key()
        if key is None:
            return False
        drain_staging(profile)
        rows = _read_labels(profile, key)
        kept = [r for r in rows if r.get("id") != row_id]
        if len(kept) == len(rows):
            return False
        _write_labels(profile, kept, key)
        return True


def drop_last_staged(profile: str) -> bool:
    """Forget the newest row that is still in the locked-vault buffer, without
    opening it (the buffer is in arrival order)."""
    with _lock:
        lines = _staging_lines(profile)
        if not lines:
            return False
        _write_staging(profile, lines[:-1])
        return True


def expired_ids(profile: str, days: float) -> List[str]:
    """Ids of the stored rows older than ``days`` (the vault must be open; the buffer
    holds no readable ids). Used to drop the artifacts trained on them."""
    cutoff = now_fn() - float(days) * 86400.0
    with _lock:
        key = vault_key()
        if key is None:
            return []
        return [r["id"] for r in _read_labels(profile, key) if float(r.get("at") or 0) < cutoff]


def purge_expired(profile: str, days: float) -> int:
    """Delete rows (and buffered rows) older than ``days``. Returns how many."""
    cutoff = now_fn() - float(days) * 86400.0
    n = 0
    with _lock:
        lines = _staging_lines(profile)
        keep = [(at, b) for at, b in lines if at >= cutoff]
        if len(keep) != len(lines):
            n += len(lines) - len(keep)
            _write_staging(profile, keep)
        key = vault_key()
        if key is not None:
            rows = _read_labels(profile, key)
            kept = [r for r in rows if float(r.get("at") or 0) >= cutoff]
            if len(kept) != len(rows):
                n += len(rows) - len(kept)
                _write_labels(profile, kept, key)
    return n


def wipe(profile: str) -> dict:
    """Delete everything kept for this profile: rows, buffer, key pair, state and
    every rollback artifact. Works with the vault locked."""
    with _lock:
        d = profile_dir(profile)
        n_stage = len(_staging_lines(profile))
        n_rows = 0
        key = vault_key()
        if key is not None:
            n_rows = len(_read_labels(profile, key))
        n_art = len(list((d / ARTIFACT_DIR).glob("*.enc"))) if (d / ARTIFACT_DIR).is_dir() else 0
        shutil.rmtree(d, ignore_errors=True)
        if d.exists():                                      # a file held by an indexer or a scanner: try once more
            time.sleep(0.2)
            shutil.rmtree(d, ignore_errors=True)
        return {"rows": n_rows, "staged": n_stage, "artifacts": n_art, "remaining": d.exists()}


# ---------------------------------------------------------------------------
#  artifacts and state (sealed with the vault key; need it open)
# ---------------------------------------------------------------------------

def write_blob(profile: str, name: str, obj, *, sub: str = "") -> bool:
    with _lock:
        key = vault_key()
        if key is None:
            return False
        # A blob lives inside its profile folder: the resolved path is checked before the write.
        base = os.path.realpath(profile_dir(profile))
        full = os.path.realpath(os.path.join(base, sub, name) if sub else os.path.join(base, name))
        if not full.startswith(base + os.sep):
            return False
        _atomic_write(Path(full), seal(obj, key).encode("ascii"))
        return True


def read_blob(profile: str, name: str, *, sub: str = ""):
    with _lock:
        key = vault_key()
        if key is None:
            return None
        # A blob is read only from inside its profile folder (the resolved path is checked).
        base = os.path.realpath(profile_dir(profile))
        full = os.path.realpath(os.path.join(base, sub, name) if sub else os.path.join(base, name))
        if not full.startswith(base + os.sep):
            return None
        try:
            return unseal(Path(full).read_text(encoding="ascii").strip(), key)
        except Exception:
            return None


def list_blobs(profile: str, sub: str) -> List[str]:
    d = profile_dir(profile) / sub
    try:
        return sorted(p.name for p in d.glob("*.enc"))
    except OSError:
        return []


def delete_blob(profile: str, name: str, *, sub: str = "") -> bool:
    with _lock:
        path = profile_dir(profile) / sub / name if sub else profile_dir(profile) / name
        try:
            path.unlink()
            return True
        except OSError:
            return False
