"""The Friday Bundle: one encrypted, versioned backup of Friday's durable self
that restores onto a clean machine (north star §27.1, §27.2, §27.4-§27.6;
burn-down BD-17).

What it holds, by category (each can be left out):

* memory: the memory card, memory stores, user model, knowledge graph, wiki,
  Library, people and trust graphs, learning, persona and instructions;
* vault: the sealed vault folder, the keystore, Friday's own signing key for
  file grants and traces, and the vault passphrase (so the vault opens on the
  new machine);
* settings: settings and privacy-shield settings, the MCP server list;
* skills: the owner's and learned skills with their signed ledgers;
* workspaces: built workspaces, codebases, Workspace Studio;
* receipts: the action ledger, task journals, setup record, reasoning
  traces, provenance, governance records, approvals, the organize-undo store,
  learning records, costs; and the governance key that signs them, so every
  signature still checks on the new machine;
* conversations;
* other: anything else in the data folder that is not excluded below.

Never in it: third-party credentials (provider API keys, Google and MCP OAuth
tokens, platform credentials, the phone line's secrets; connectors are
re-connected after a restore, §27.2), files only this machine can open (DPAPI
blobs), this device's identity (attestation key, TLS and local-address keys,
the web session secret), downloads and caches (models, runtime, voice
environments: fetched again), logs, and process files.

Format (``.fridaybundle``)::

    MAGIC "FRIDAYBUNDLE\\x02" | u32 header length | header JSON |
    chunks: u32 length | AES-256-GCM(chunk)  ...

The key is Argon2id(passphrase, salt). Each chunk's nonce is an 8-byte random
prefix and a 4-byte counter; its additional data is SHA-256(header), the
counter and a last-chunk flag, so chunks cannot be reordered, dropped, cut off
or moved between bundles, and the header cannot be changed. The plaintext is a
tar stream: ``home/<path>`` for each file, ``secrets/...`` and, last,
``manifest.json`` with every file's size, SHA-256 and category.

Verify (``verify``) decrypts every chunk and checks every file against the
manifest without writing anything: the "test-read" of §27.4. Restore
(``restore``) does the same into a staging folder beside the target, then
moves it into place, installs the keys, re-checks every file, holds outward
actions until the owner resumes them (§27.6: re-established on this device),
and writes a restore receipt into the action ledger.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import sqlite3
import struct
import tarfile
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional

MAGIC = b"FRIDAYBUNDLE\x02"
FORMAT = 2
CHUNK = 4 * 1024 * 1024
SUFFIX = ".fridaybundle"
MIN_PASSPHRASE = 12

CATEGORIES = ("memory", "vault", "settings", "skills", "workspaces", "receipts",
              "conversations", "other")

_TOP_CATEGORY = {
    "core_memory": "memory", "memory": "memory", "knowledge-graph": "memory",
    "wiki": "memory", "library": "memory", "instructions": "memory",
    "learning_records": "receipts",
    "vault": "vault",
    "skills": "skills", "skillopt": "skills",
    "workspaces": "workspaces", "codebases": "workspaces", "workspace_studio": "workspaces",
    "browser-workspaces": "workspaces",
    "ledger": "receipts", "tasks": "receipts", "setup": "receipts", "traces": "receipts",
    "provenance": "receipts", "governance": "receipts", "actions": "receipts",
    "trash": "receipts", "trust": "receipts", "inbox_cleanup": "receipts",
    "conversations": "conversations",
}
_FILE_CATEGORY = {
    "user_model.db": "memory", "user_model.json": "memory", "wiki-pending.json": "memory",
    "people_graph.json": "memory", "trust_graph.json": "memory", "learning.db": "memory",
    "personality.json": "memory", "SOUL.md": "memory", "agent-personality.txt": "memory",
    "trajectories.jsonl": "memory",
    "settings.json": "settings", "privacy_shield.json": "settings",
    "mcp_servers.json": "settings",
    "approvals.json": "receipts", "decisions.jsonl": "receipts", "gate_events.jsonl": "receipts",
    "costs.db": "receipts", "work_log.db": "receipts", "sent_mail.jsonl": "receipts",
    "chat_history.json": "conversations",
}

#: Top-level folders never carried: downloads, caches, logs, state of this run.
_EXCLUDED_TOPS = {"runtime", "local_voice", "models", "cache", "backups", "logs", "tmp",
                  "audio-cache", "vibe-code-logs", "tool-output", "browser-profile",
                  "doctor", "safe_mode", "boot_guard", "restore", "tls", "local-address",
                  "providers", "google_accounts", "mcp_oauth", "platforms"}
#: Exact paths never carried (relative, POSIX).
_EXCLUDED_PATHS = {"vault/.governance-key", "vault/.attestation-key-ed25519",
                   "vault/.attestation-key-ed25519.pub", "vault/.action-ledger-anchor",
                   "secret_key", "friday_server.lock", "friday_server.pid",
                   "friday_server.port", "startup-report.json", "friday.log",
                   "server_stderr.log", "ledger/.lock", "phone/secrets"}
_EXCLUDED_SUFFIXES = (".dpapi", ".cred", ".oauth.enc", ".token.enc", ".lock", ".pid",
                      ".tmp", "-key.pem", "key.pem")
#: Inside security/, only the keystore travels (it wraps Friday's own keys;
#: the third-party credentials it also wraps are not carried).
_SECURITY_KEEP = {"security/keystore.json"}
#: Friday's own key material outside security/ that travels with the vault.
_KEY_FILES = {"privacy/ledger_signing.key": "vault"}


class BundleError(RuntimeError):
    """The bundle could not be made, read, verified or restored. The message
    is for the owner."""


# ── Choosing the files ──────────────────────────────────────────────────────

def category_of(rel: PurePosixPath) -> Optional[str]:
    """The category a file belongs to, or None when it never travels."""
    s = rel.as_posix()
    parts = rel.parts
    if not parts:
        return None
    if s in _KEY_FILES:
        return _KEY_FILES[s]
    if s in _SECURITY_KEEP:
        return "vault"
    if parts[0] == "security":
        return None
    if parts[0] in _EXCLUDED_TOPS or s in _EXCLUDED_PATHS:
        return None
    if any(s.startswith(p + "/") for p in _EXCLUDED_PATHS):
        return None
    if rel.name.endswith(_EXCLUDED_SUFFIXES) or rel.name.endswith(".key"):
        return None
    if "__pycache__" in parts:
        return None
    if parts[0] == "ledger" and len(parts) > 1 and parts[1] in ("quarantine",):
        return "receipts"
    if len(parts) == 1:
        return _FILE_CATEGORY.get(rel.name, "other")
    return _TOP_CATEGORY.get(parts[0], "other")


def plan(home: Path, categories: Iterable[str] = CATEGORIES) -> dict:
    """What a bundle of `home` would hold: files by category, counts left out."""
    want = set(categories)
    files, excluded, left_out = [], {}, {}
    for p in sorted(home.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        rel = PurePosixPath(p.relative_to(home).as_posix())
        cat = category_of(rel)
        if cat is None:
            top = rel.parts[0]
            excluded[top] = excluded.get(top, 0) + 1
            continue
        if cat not in want:
            left_out[cat] = left_out.get(cat, 0) + 1
            continue
        files.append((rel, cat))
    return {"files": files, "excluded": excluded, "left_out": left_out}


# ── The encrypted stream ────────────────────────────────────────────────────

def _derive(passphrase: str, salt: bytes, kdf: dict) -> bytes:
    from agent_friday.privacy import vault_crypto as vc
    prof = vc.Argon2Profile(time_cost=int(kdf["t"]), memory_cost=int(kdf["m"]),
                            lanes=int(kdf["p"]))
    return vc.derive_key(passphrase, salt, prof)


def _aad(header_hash: bytes, counter: int, last: bool) -> bytes:
    return header_hash + struct.pack(">I", counter) + (b"\x01" if last else b"\x00")


class _EncryptingWriter(io.RawIOBase):
    """A write-only file object: buffers to CHUNK, seals each chunk."""

    def __init__(self, out, key: bytes, header_hash: bytes, prefix: bytes):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self.out, self.aes, self.hh, self.prefix = out, AESGCM(key), header_hash, prefix
        self.buf = bytearray()
        self.counter = 0
        self.closed_stream = False

    def writable(self):
        return True

    def write(self, b):
        self.buf += b
        while len(self.buf) > CHUNK:
            self._seal(bytes(self.buf[:CHUNK]), last=False)
            del self.buf[:CHUNK]
        return len(b)

    def _seal(self, data: bytes, last: bool):
        if self.counter >= 2 ** 32 - 1:
            raise BundleError("the bundle is too large")
        nonce = self.prefix + struct.pack(">I", self.counter)
        ct = self.aes.encrypt(nonce, data, _aad(self.hh, self.counter, last))
        self.out.write(struct.pack(">I", len(ct)) + ct)
        self.counter += 1

    def finish(self):
        if not self.closed_stream:
            self._seal(bytes(self.buf), last=True)
            self.buf = bytearray()
            self.closed_stream = True


def _read_header(f) -> tuple[dict, bytes]:
    magic = f.read(len(MAGIC))
    if magic != MAGIC:
        raise BundleError("This is not a Friday Bundle (or it is from a format this "
                          "version does not read).")
    (n,) = struct.unpack(">I", f.read(4))
    if n > 1 << 20:
        raise BundleError("The bundle's header is damaged.")
    raw = f.read(n)
    try:
        header = json.loads(raw)
    except ValueError:
        raise BundleError("The bundle's header is damaged.")
    if header.get("format") != FORMAT:
        raise BundleError("This bundle uses format %s; this Friday reads format %d."
                          % (header.get("format"), FORMAT))
    return header, hashlib.sha256(magic + struct.pack(">I", n) + raw).digest()


class _DecryptingReader(io.RawIOBase):
    """Read-only file object over the chunks; raises BundleError on any tag
    failure, a missing last chunk, or bytes after the last chunk."""

    def __init__(self, f, key: bytes, header_hash: bytes, prefix: bytes):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self.f, self.aes, self.hh, self.prefix = f, AESGCM(key), header_hash, prefix
        self.counter = 0
        self.buf = b""
        self.done = False

    def readable(self):
        return True

    def _next(self):
        from cryptography.exceptions import InvalidTag
        lenb = self.f.read(4)
        if len(lenb) < 4:
            raise BundleError("The bundle ends early: it was cut off or damaged.")
        (n,) = struct.unpack(">I", lenb)
        if n > CHUNK + 64:
            raise BundleError("The bundle is damaged.")
        ct = self.f.read(n)
        if len(ct) != n:
            raise BundleError("The bundle ends early: it was cut off or damaged.")
        nonce = self.prefix + struct.pack(">I", self.counter)
        for last in (False, True):
            try:
                data = self.aes.decrypt(nonce, ct, _aad(self.hh, self.counter, last))
                break
            except InvalidTag:
                data = None
        if data is None:
            if self.counter == 0:
                raise BundleError("Wrong passphrase, or the bundle was changed.")
            raise BundleError("The bundle was changed or damaged (part %d)." % self.counter)
        self.counter += 1
        if last:
            if self.f.read(1):
                raise BundleError("The bundle has extra bytes after its end: it was changed.")
            self.done = True
        return data

    def readinto(self, b):
        while not self.buf and not self.done:
            self.buf = self._next()
        if not self.buf:
            return 0
        n = min(len(b), len(self.buf))
        b[:n] = self.buf[:n]
        self.buf = self.buf[n:]
        return n


# ── Making a bundle ─────────────────────────────────────────────────────────

def _sqlite_snapshot(p: Path) -> Optional[bytes]:
    """A consistent copy of a live SQLite database, or None (not SQLite, or
    encrypted): the backup API, never a byte copy of a file being written."""
    try:
        with open(p, "rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                return None
    except OSError:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="fb-sqlite-")) / "copy.db"
    try:
        src = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=10)
        dst = sqlite3.connect(str(tmp))
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        return tmp.read_bytes()
    except sqlite3.Error:
        return None
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


def _vault_passphrase() -> str:
    try:
        from agent_friday.services import vault_passphrase
        return vault_passphrase.resolve(use_cache=False)[0] or ""
    except Exception:
        return ""


def _governance_key_hex(home: Path) -> str:
    """The governance key, read without minting one (none: an empty string)."""
    try:
        import keyring
        try:
            v = keyring.get_password("agent-friday", "governance-key")
            if v:
                return v
        except Exception:
            pass
    except ImportError:
        pass
    f = Path(home) / "vault" / ".governance-key"
    if f.exists():
        raw = f.read_bytes()
        return raw.hex() if len(raw) == 32 else raw.decode("ascii").strip()
    return ""


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes, mtime: float) -> None:
    ti = tarfile.TarInfo(name)
    ti.size = len(data)
    ti.mtime = int(mtime)
    ti.mode = 0o600
    tar.addfile(ti, io.BytesIO(data))


def create(out: Path, passphrase: str, *, home: Optional[Path] = None,
           categories: Iterable[str] = CATEGORIES, kdf_profile=None,
           progress=None) -> dict:
    """Write a bundle of `home` (default: this Friday's data folder) to `out`.
    Returns the manifest. The file is written beside `out` and renamed into
    place only when complete; nothing is left behind on failure."""
    from agent_friday.paths import friday_home
    from agent_friday.privacy import vault_crypto as vc
    home = Path(home or friday_home())
    if len(passphrase or "") < MIN_PASSPHRASE:
        raise BundleError("The bundle passphrase must be at least %d characters." % MIN_PASSPHRASE)
    cats = [c for c in categories if c in CATEGORIES]
    if not cats:
        raise BundleError("Choose at least one category.")
    out = Path(out)
    try:
        out.resolve().relative_to(home.resolve())
        raise BundleError("A bundle cannot be written inside Friday's own data folder.")
    except ValueError:
        pass
    prof = kdf_profile or vc.DEFAULT_PROFILE
    salt = os.urandom(16)
    kdf = {"name": "argon2id", "t": prof.time_cost, "m": prof.memory_cost, "p": prof.lanes,
           "salt": base64.b64encode(salt).decode("ascii")}
    bundle_id = "fb-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
    prefix = os.urandom(8)
    header = {"format": FORMAT, "bundle_id": bundle_id, "kind": "friday-bundle",
              "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "kdf": kdf, "cipher": "aes-256-gcm", "chunk": CHUNK,
              "nonce_prefix": base64.b64encode(prefix).decode("ascii"),
              "categories": cats, "app": _app_info()}
    raw_header = json.dumps(header, sort_keys=True).encode("utf-8")
    head_bytes = MAGIC + struct.pack(">I", len(raw_header)) + raw_header
    key = _derive(passphrase, salt, kdf)
    p = plan(home, cats)
    tmp = out.with_name(out.name + ".partial-" + uuid.uuid4().hex[:6])
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest_files = []
    ledger_state = None
    try:
        with open(tmp, "wb") as f:
            f.write(head_bytes)
            w = _EncryptingWriter(f, key, hashlib.sha256(head_bytes).digest(), prefix)
            with tarfile.open(fileobj=w, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                ledger_files = [(r, c) for r, c in p["files"] if r.parts[0] == "ledger"]
                other_files = [(r, c) for r, c in p["files"] if r.parts[0] != "ledger"]
                for rel, cat in other_files:
                    src = home / Path(*rel.parts)
                    data = _sqlite_snapshot(src) if src.suffix in (".db", ".sqlite", ".sqlite3") else None
                    try:
                        if data is None:
                            data = src.read_bytes()
                        mtime = src.stat().st_mtime
                    except OSError:
                        continue                     # gone while the bundle was made
                    _add_bytes(tar, "home/" + rel.as_posix(), data, mtime)
                    manifest_files.append({"path": rel.as_posix(), "size": len(data),
                                           "sha256": hashlib.sha256(data).hexdigest(),
                                           "category": cat})
                    if progress:
                        progress(len(manifest_files), len(p["files"]))
                if ledger_files:
                    ledger_state = _add_ledger(tar, home, ledger_files, manifest_files)
                secrets = {}
                if "vault" in cats:
                    vp = _vault_passphrase()
                    if vp:
                        secrets["vault-passphrase"] = vp.encode("utf-8")
                if "receipts" in cats or "skills" in cats or "vault" in cats:
                    gk = _governance_key_hex(home)
                    if gk:
                        secrets["governance-key"] = gk.encode("ascii")
                for name, data in secrets.items():
                    _add_bytes(tar, "secrets/" + name, data, time.time())
                manifest = {
                    "bundle_id": bundle_id, "format": FORMAT, "created_at": header["created_at"],
                    "categories": cats, "app": header["app"], "files": manifest_files,
                    "secrets": sorted(secrets), "excluded_by_folder": p["excluded"],
                    "left_out_by_category": p["left_out"],
                    "credentials": "not carried: reconnect services after a restore",
                    "ledger": ledger_state,
                    "stores": _inventory(home),
                }
                _add_bytes(tar, "manifest.json",
                           json.dumps(manifest, sort_keys=True, indent=1).encode("utf-8"), time.time())
            w.finish()
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, out)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    _remember(home, out, bundle_id, verified=False)
    return manifest


def _add_ledger(tar, home: Path, ledger_files, manifest_files) -> Optional[dict]:
    """The action ledger is read under its write lock, so no entry is half in
    the bundle; its state goes into the manifest."""
    from agent_friday.services import action_ledger
    state = None
    lock = action_ledger._FileLock(home / "ledger" / action_ledger.LOCK_NAME)
    with lock:
        for rel, cat in ledger_files:
            src = home / Path(*rel.parts)
            try:
                data = src.read_bytes()
                mtime = src.stat().st_mtime
            except OSError:
                continue
            _add_bytes(tar, "home/" + rel.as_posix(), data, mtime)
            manifest_files.append({"path": rel.as_posix(), "size": len(data),
                                   "sha256": hashlib.sha256(data).hexdigest(), "category": cat})
        try:
            live = home / "ledger" / action_ledger.LIVE_NAME
            last = None
            if live.exists():
                for raw in action_ledger._reverse_lines(live):
                    e = action_ledger._safe_json(raw)
                    if isinstance(e, dict) and isinstance(e.get("seq"), int):
                        last = e
                        break
            if last:
                state = {"last_seq": last["seq"], "tip": last.get("hash")}
        except Exception:
            state = None
    return state


def _inventory(home: Path) -> list:
    """Which stores are in the folder and their declared versions (§37.3-1)."""
    out = []
    for name, rel in (("settings", "settings.json"), ("vault", "vault/.vault_config.json"),
                      ("action_ledger", "ledger/actions.jsonl"), ("keystore", "security/keystore.json"),
                      ("instructions", "instructions/manifest.json")):
        p = home / rel
        if not p.exists():
            continue
        ver = None
        try:
            if p.suffix == ".json":
                d = json.loads(p.read_text(encoding="utf-8"))
                ver = d.get("version") or d.get("schema_version") or d.get("v") if isinstance(d, dict) else None
            elif name == "action_ledger":
                with open(p, "rb") as f:
                    first = f.readline()
                ver = (json.loads(first) or {}).get("v") if first.strip() else None
        except Exception:
            ver = "unreadable"
        out.append({"store": name, "version": ver})
    return out


def _app_info() -> dict:
    out = {}
    try:
        from agent_friday import release
        out.update(release=release.RELEASE_TAG, build_sequence=release.BUILD_SEQUENCE)
    except Exception:
        pass
    return out


def _remember(home: Path, out: Path, bundle_id: str, *, verified: bool) -> None:
    """The Doctor's "backup" check reads this: when, and whether it was verified.
    No path is kept (the location may name the owner)."""
    try:
        d = home / "backups"
        d.mkdir(parents=True, exist_ok=True)
        (d / "last_bundle.json").write_text(json.dumps({
            "bundle_id": bundle_id, "made_at": time.time(), "verified": bool(verified)}),
            encoding="utf-8")
    except OSError:
        pass


# ── Reading a bundle ────────────────────────────────────────────────────────

@dataclass
class _Opened:
    header: dict
    manifest: dict
    members: dict = field(default_factory=dict)    # name -> sha256 of what was read
    secrets: dict = field(default_factory=dict)


def _safe_member(name: str) -> bool:
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name:
        return False
    if not p.parts:
        return False
    if p.parts[0] == "home":
        if len(p.parts) < 2:
            return False
        for part in p.parts[1:]:
            stem = part.split(".")[0].upper()
            if stem in ("CON", "PRN", "AUX", "NUL") or (stem[:3] in ("COM", "LPT") and stem[3:].isdigit()):
                return False
            if part.endswith((" ", ".")):
                return False
        return True
    return name in ("manifest.json", "secrets/vault-passphrase", "secrets/governance-key")


def _walk_bundle(path: Path, passphrase: str, sink=None) -> _Opened:
    """Decrypt and read every member. `sink(name, data)` receives each file
    under home/ (None: read only). Raises BundleError on anything wrong."""
    path = Path(path)
    with open(path, "rb") as f:
        header, hh = _read_header(f)
        try:
            salt = base64.b64decode(header["kdf"]["salt"])
            prefix = base64.b64decode(header["nonce_prefix"])
        except Exception:
            raise BundleError("The bundle's header is damaged.")
        key = _derive(passphrase, salt, header["kdf"])
        r = _DecryptingReader(f, key, hh, prefix)
        opened = _Opened(header=header, manifest={})
        try:
            with tarfile.open(fileobj=io.BufferedReader(r, CHUNK), mode="r|") as tar:
                for ti in tar:
                    if not ti.isfile() or not _safe_member(ti.name):
                        raise BundleError("The bundle holds an entry it should not (%s); "
                                          "it was not made by Friday." % ti.name[:80])
                    if ti.name in opened.members:
                        raise BundleError("The bundle holds the same entry twice.")
                    data = tar.extractfile(ti).read()
                    opened.members[ti.name] = (len(data), hashlib.sha256(data).hexdigest())
                    if ti.name == "manifest.json":
                        opened.manifest = json.loads(data)
                    elif ti.name.startswith("secrets/"):
                        opened.secrets[ti.name[len("secrets/"):]] = data
                    elif sink is not None:
                        sink(ti.name[len("home/"):], data)
        except tarfile.TarError:
            raise BundleError("The bundle's contents are damaged.")
        if not r.done:
            # drain: the last chunk must be present and nothing may follow it
            while not r.done:
                r._next()
    _check_manifest(opened)
    return opened


def _check_manifest(o: _Opened) -> None:
    m = o.manifest
    if not m:
        raise BundleError("The bundle has no manifest: it is incomplete.")
    if m.get("bundle_id") != o.header.get("bundle_id"):
        raise BundleError("The bundle's manifest belongs to another bundle.")
    listed = {}
    for f in m.get("files") or []:
        listed["home/" + f["path"]] = (f["size"], f["sha256"])
    got = {k: v for k, v in o.members.items() if k.startswith("home/")}
    if set(listed) != set(got):
        raise BundleError("The bundle's files do not match its manifest (%d listed, %d present)."
                          % (len(listed), len(got)))
    for k, (size, digest) in listed.items():
        if got[k] != (size, digest):
            raise BundleError("A file in the bundle does not match its manifest: %s" % k[5:][:80])


def verify(path: Path, passphrase: str) -> dict:
    """Read the whole bundle and check every file against its manifest,
    writing nothing. Returns a summary."""
    o = _walk_bundle(path, passphrase)
    m = o.manifest
    by_cat: dict = {}
    for f in m.get("files") or []:
        by_cat[f["category"]] = by_cat.get(f["category"], 0) + 1
    return {"ok": True, "bundle_id": m["bundle_id"], "created_at": m.get("created_at"),
            "files": len(m.get("files") or []), "by_category": by_cat,
            "secrets": sorted(o.secrets), "app": m.get("app"), "ledger": m.get("ledger"),
            "stores": m.get("stores"), "bytes": sum(f["size"] for f in m.get("files") or [])}


# ── Restoring ───────────────────────────────────────────────────────────────

def _in_use(home: Path) -> bool:
    """Whether a data folder holds a Friday someone has used. A folder that
    a fresh install or a first start left behind (seeded settings, bundled
    skills, empty folders) does not count: restoring moves it aside whole."""
    if not home.exists():
        return False
    markers = (".setup_complete", "vault/.vault_config.json", "core_memory/ME.md",
               "core_memory/FRIDAY.md", "user_model.db")
    if any((home / m).exists() for m in markers):
        return True
    conv = home / "conversations"
    if conv.is_dir() and any(conv.iterdir()):
        return True
    return False


def _is_empty_home(home: Path) -> bool:
    return not _in_use(home)


def _server_running(home: Path) -> bool:
    lock = home / "friday_server.lock"
    if not lock.exists():
        return False
    try:
        fh = open(lock, "a+b")
    except OSError:
        return True
    try:
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        fh.close()


def hold_path(home: Optional[Path] = None) -> Path:
    from agent_friday.paths import friday_home
    return Path(home or friday_home()) / "restore" / "hold.json"


def restore_hold() -> Optional[str]:
    """The sentence outward actions wait on after a restore, or None."""
    p = hold_path()
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return ("Friday was restored from a Friday Bundle (%s); outward actions wait until "
            "you review your connections and press Resume on the Doctor page"
            % d.get("bundle_id", "a bundle"))


def resume_after_restore(by: str = "owner:ui") -> bool:
    """The owner's re-attestation on this device: outward actions may go."""
    p = hold_path()
    if not p.exists():
        return False
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    from agent_friday.services import action_ledger
    action_ledger.append("restore_resumed", {"bundle_id": d.get("bundle_id"), "by": by},
                         durable=True)
    p.unlink()
    return True


def restore(path: Path, passphrase: str, *, home: Optional[Path] = None,
            only: Optional[Iterable[str]] = None, replace: bool = False,
            dry_run: bool = False, allow_newer: bool = False) -> dict:
    """Restore a bundle into `home` (default: this Friday's data folder).

    The §27.6 steps: (1) every chunk and file checked against the manifest
    before anything is written, (2) the passphrase, (3) the format and the
    version that made it, (4) the stores it holds, (5) the categories chosen,
    (6) a target that is not empty is refused unless `replace` (which moves
    the existing folder aside, whole, never deleting it), (7) `dry_run` stops
    here with the summary, (8) the files are written to a staging folder and
    moved into place, (9) derived indexes are marked for rebuilding,
    (10) the governance key and vault passphrase are installed for this device
    and outward actions are held until the owner resumes them, (11) services
    to reconnect are listed, (12) downloads are left to the next start,
    (13) every file is checked again in place and the action ledger is
    verified, (14) a restore receipt is written."""
    from agent_friday.paths import friday_home
    home = Path(home or friday_home())
    chosen = set(only) if only else None
    if _server_running(home):
        raise BundleError("Friday is running on this data folder. Quit Friday first, then restore.")
    summary = verify(path, passphrase)                                 # steps 1-2
    man_header = summary
    app = man_header.get("app") or {}
    try:
        from agent_friday import release
        mine = int(getattr(release, "BUILD_SEQUENCE", 0) or 0)
    except Exception:
        mine = 0
    theirs = int(app.get("build_sequence") or 0)
    if theirs and mine and theirs > mine and not allow_newer:                # step 3
        raise BundleError("This bundle was made by a newer Friday (%s). Update Friday first."
                          % app.get("release"))
    if not replace and not _is_empty_home(home):                       # step 6
        raise BundleError("This data folder already has a Friday in it. Restore onto a "
                          "clean machine, or choose to replace it (the current folder is "
                          "moved aside, not deleted).")
    plan_ = {"bundle": summary, "target_empty": _is_empty_home(home),
             "categories": sorted(chosen) if chosen else summary["by_category"],
             "will_move_aside": home.exists()}
    if dry_run:                                                        # step 7
        return {"dry_run": True, **plan_}
    stage = home.parent / (home.name + ".friday-restore-" + uuid.uuid4().hex[:8])
    stage.mkdir(parents=True)
    written = []

    def sink(rel: str, data: bytes):
        cat = category_of(PurePosixPath(rel))
        if cat is None or (chosen is not None and cat not in chosen):
            return
        dst = stage.joinpath(*PurePosixPath(rel).parts)
        try:
            dst.resolve().relative_to(stage.resolve())
        except ValueError:
            raise BundleError("The bundle tried to write outside the data folder.")
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(dst, "xb") as f:
            f.write(data)
        written.append(rel)

    try:
        opened = _walk_bundle(path, passphrase, sink=sink)             # step 8
        listed = {f["path"]: f for f in opened.manifest["files"]}
        for rel in written:                                             # step 13 (staged)
            got = hashlib.sha256(stage.joinpath(*PurePosixPath(rel).parts).read_bytes()).hexdigest()
            if got != listed[rel]["sha256"]:
                raise BundleError("A restored file did not match its manifest: %s" % rel[:80])
        moved_aside = None
        if home.exists():
            # replace was chosen, or the folder holds only what a fresh install
            # left: either way it is moved aside whole, never deleted
            moved_aside = home.parent / (home.name + ".pre-restore-" + time.strftime("%Y%m%d-%H%M%S"))
            os.replace(home, moved_aside)
        os.replace(stage, home)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    keys = _install_keys(home, opened.secrets, moved_aside)            # step 10
    _mark_reindex(home)                                                 # step 9
    hold = hold_path(home)
    hold.parent.mkdir(parents=True, exist_ok=True)
    hold.write_text(json.dumps({"bundle_id": opened.manifest["bundle_id"],
                                "restored_at": time.time()}), encoding="utf-8")
    from agent_friday.services import action_ledger
    with _as_home(home):
        # a new device (or a replaced folder at the same path): the anchor kept
        # outside the data folder now names the restored chain
        action_ledger.reset_anchor_to_tip()
    after = _verify_in_place(home, opened.manifest, written)             # step 13
    reconnect = _to_reconnect(opened.manifest)                          # step 11
    receipt = {"bundle_id": opened.manifest["bundle_id"], "made_at": opened.manifest.get("created_at"),
               "restored_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "files": len(written), "categories": sorted({listed[r]["category"] for r in written}),
               "keys": keys, "checked_in_place": after, "reconnect": reconnect,
               "moved_aside": moved_aside.name if moved_aside else None,
               "downloads": "models and voice files are fetched again at the next start",
               "outward_actions": "held until you press Resume on the Doctor page"}
    _write_receipt(home, receipt)                                       # step 14
    return receipt


def _install_keys(home: Path, secrets: dict, moved_aside: Optional[Path]) -> dict:
    """Install the governance key and the vault passphrase for this device.
    A key the target machine already held is kept, as a file, inside the
    folder that was moved aside, so that folder still opens."""
    out = {"governance_key": "not in bundle", "vault_passphrase": "not in bundle"}
    gk = secrets.get("governance-key")
    if gk:
        gk = gk.decode("ascii").strip()
        bytes.fromhex(gk)                                               # validates
        try:
            import keyring
            try:
                old = keyring.get_password("agent-friday", "governance-key")
            except Exception:
                old = None
            if old and old != gk and moved_aside is not None:
                (moved_aside / "vault").mkdir(parents=True, exist_ok=True)
                (moved_aside / "vault" / ".governance-key").write_bytes(bytes.fromhex(old))
            keyring.set_password("agent-friday", "governance-key", gk)
            out["governance_key"] = "installed in this device's keychain"
        except Exception:
            f = home / "vault" / ".governance-key"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(bytes.fromhex(gk))
            try:
                f.chmod(0o600)
            except OSError:
                pass
            out["governance_key"] = "installed in the vault folder (no keychain here)"
    vp = secrets.get("vault-passphrase")
    if vp:
        try:
            from agent_friday.services import vault_passphrase
            homes = vault_passphrase.store(vp.decode("utf-8"))
            vault_passphrase.reset_cache()
            out["vault_passphrase"] = ("stored: " + ", ".join(homes)) if homes else \
                "not stored on this device: Friday will ask for it"
        except Exception as e:  # noqa: BLE001
            out["vault_passphrase"] = "not stored (%s): Friday will ask for it" % type(e).__name__
    return out


def _mark_reindex(home: Path) -> None:
    d = home / "restore"
    d.mkdir(parents=True, exist_ok=True)
    (d / "needs_reindex.json").write_text(json.dumps({
        "at": time.time(), "indexes": ["knowledge_graph", "conversation_memory", "library"]}),
        encoding="utf-8")


class _as_home:
    """Point Friday's path helpers at `home` for a moment (the restored folder
    may not be the one this process started with)."""

    def __init__(self, home: Path):
        self.home = home
        self.prev = {}

    def __enter__(self):
        for k, v in (("FRIDAY_HOME", str(self.home)),
                     ("FRIDAY_LEDGER_DIR", str(self.home / "ledger"))):
            if k == "FRIDAY_LEDGER_DIR" and k not in os.environ:
                continue                       # only a test moves the ledger
            self.prev[k] = os.environ.get(k)
            os.environ[k] = v
        from agent_friday.services import action_ledger
        action_ledger.reset_caches()
        return self

    def __exit__(self, *exc):
        for k, v in self.prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        from agent_friday.services import action_ledger
        action_ledger.reset_caches()


def _verify_in_place(home: Path, manifest: dict, written: list) -> dict:
    listed = {f["path"]: f for f in manifest["files"]}
    bad = [r for r in written
           if hashlib.sha256(home.joinpath(*PurePosixPath(r).parts).read_bytes()).hexdigest()
           != listed[r]["sha256"]]
    out = {"files_match": not bad, "mismatched": len(bad)}
    try:
        from agent_friday.services import action_ledger
        with _as_home(home):
            rep = action_ledger.verify(force=True, migrate=False)
            out["ledger"] = {"ok": rep.get("ok"), "records": rep.get("records"),
                             "problems": rep.get("open_problem_count"),
                             "first_problem": next((p.get("reason") for p in rep.get("problems") or []), None)}
    except Exception as e:  # noqa: BLE001
        out["ledger"] = {"ok": False, "error": type(e).__name__}
    return out


def _to_reconnect(manifest: dict) -> dict:
    ex = manifest.get("excluded_by_folder") or {}
    return {"google_accounts": ex.get("google_accounts", 0), "mcp_sign_ins": ex.get("mcp_oauth", 0),
            "provider_keys": ex.get("providers", 0), "platforms": ex.get("platforms", 0)}


def _write_receipt(home: Path, receipt: dict) -> None:
    d = home / "restore"
    d.mkdir(parents=True, exist_ok=True)
    (d / ("receipt-%s.json" % receipt["bundle_id"])).write_text(
        json.dumps(receipt, indent=1, sort_keys=True), encoding="utf-8")
    from agent_friday.services import action_ledger
    with _as_home(home):
        action_ledger.append("restore", receipt, durable=True)
