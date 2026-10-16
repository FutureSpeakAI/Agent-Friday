"""KnowledgeGraphStore — derived-index artifacts + ingest manifest.

Artifacts under ~/.friday/knowledge-graph/ follow the graphrag-workbench
JSON contract (entities / relationships / communities / community_reports),
extended with a Friday `provenance` block per record (see spec §5.2).

Encryption at rest: records whose provenance marks them TIER_2/TIER_3 (or
derived from an encrypted wiki section) are split into a sibling
`<artifact>.sensitive.json` written through vault_crypto with the same
AES-256-GCM key as the wiki/finance/health vaults. When no vault key is
available, sensitive records are NOT persisted (fail closed on disk) — the
graph simply omits them until a key exists and the index is rebuilt.

The manifest (`.manifest.json`) tracks every ingested source under a single
canonical absolute-path key (obsidian-wiki's rule — prevents the same file
being tracked twice under ~ and expanded forms) with a content fingerprint,
so reindexing is an incremental delta rather than a full pass.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from . import KG_DIR

ARTIFACT_NAMES = ("entities", "relationships", "communities", "community_reports")
_SENSITIVE_SUFFIX = ".sensitive.json"


# ── vault key (lazy, mirrors wiki_engine) ─────────────────────

def _vault_key() -> Optional[bytes]:
    try:
        from agent_friday.services.agent import _get_vault_key  # upper layer — lazy
        return _get_vault_key()
    except Exception:
        return None


_KEYS_CACHE: dict = {}


def _key_generation():
    """vault_passphrase's key generation: it moves on every store, rotation
    and repair (vault_passphrase.reset_cache). None when it cannot be read."""
    try:
        from agent_friday.services import vault_passphrase as _vp
        return _vp.key_generation()
    except Exception:
        return None


def _known_keys() -> list:
    """(label, key) for every key this machine could have sealed a file with:
    the current vault key first, then every passphrase any home holds
    (credential_store._legacy_keys: environment, launcher, keychain, DPAPI
    file, start.bat).

    Derived once per key state, because Argon2id is deliberately slow. The
    cache is valid only for the vault_passphrase key generation and the
    current key it was built under: a stored, rotated or repaired passphrase
    rebuilds it, so "no key opens this file" is never a stale answer."""
    cur = _vault_key()
    stamp = (_key_generation(), cur)
    if _KEYS_CACHE.get("stamp") == stamp and "keys" in _KEYS_CACHE:
        return _KEYS_CACHE["keys"]
    out, seen = [], set()
    if cur:
        out.append(("current", cur))
        seen.add(cur)
    try:
        from agent_friday.services import credential_store as _cs
        for label, k in _cs._legacy_keys():
            if k not in seen:
                seen.add(k)
                out.append((label, k))
    except Exception:
        pass
    _KEYS_CACHE["keys"] = out
    _KEYS_CACHE["stamp"] = stamp
    return out


def _open_sealed(raw: bytes):
    """(records, label of the key that opened it) or (None, None)."""
    import agent_friday.privacy.vault_crypto as _vc
    for label, key in _known_keys():
        try:
            return json.loads(_vc.decrypt(raw, key).decode("utf-8")) or [], label
        except Exception:
            continue
    return None, None


#: What the last look at each sensitive file found, keyed by its path:
#: {"state": "ok" | "older_key" | "cannot_decrypt", "via", "records", "bytes",
#:  "sig"}. Read by the integrity probe; never holds a record.
SENSITIVE_STATE: dict = {}
_SAID: set = set()

#: What the last load() IN THIS PROCESS saw for each sealed file, keyed by its
#: path: {"verdict": "ok" | "older_key" | "cannot_decrypt"}.
#: Written only by load(). A caller's records come from its last load, so a
#: sealed save is refused while that load could not read the file: even if
#: the file opens now, the caller's list lacks the records it never saw, and
#: saving it would replace them. Only a fresh load lifts the refusal.
LOAD_VERDICT: dict = {}


def _note_state(path: Path, state: str, via, records, raw_len: int) -> None:
    try:
        st = path.stat()
        sig = (st.st_size, st.st_mtime_ns)
    except OSError:
        sig = None
    SENSITIVE_STATE[str(path)] = {"state": state, "via": via, "records": records,
                                  "bytes": raw_len, "sig": sig}
    if state != "ok" and (str(path), state) not in _SAID:
        _SAID.add((str(path), state))
        import logging
        log = logging.getLogger("friday.knowledge_graph")
        if state == "cannot_decrypt":
            log.error("DATA INTEGRITY: %s (%d bytes) does not open with any key Friday "
                      "knows; its records are missing from the graph. The file is kept.",
                      path.name, raw_len)
        else:
            log.warning("knowledge graph: %s opens only with an older vault key (%s); "
                        "%s records read", path.name, via, records)


def _note_load(path: Path, verdict: str) -> None:
    LOAD_VERDICT[str(path)] = {"verdict": verdict}


def _stale_load(spath: Path) -> str:
    """"stale_load" while the last load of ``spath`` in this process reported
    cannot_decrypt, else "". Lifted only by a fresh load."""
    lv = LOAD_VERDICT.get(str(spath))
    return "stale_load" if lv and lv.get("verdict") == "cannot_decrypt" else ""


def _sensitivity_of(record: dict) -> int:
    """Resolve a record's sensitivity tier from its provenance block (1/2/3)."""
    prov = record.get("provenance") or {}
    raw = prov.get("sensitivity", 1)
    if isinstance(raw, str):
        raw = raw.strip().upper().replace("TIER_", "")
    try:
        tier = int(raw)
    except (TypeError, ValueError):
        tier = 1
    return min(max(tier, 1), 3)


def record_is_sensitive(record: dict) -> bool:
    return _sensitivity_of(record) >= 2


class KnowledgeGraphStore:
    """Read/write/rebuild the JSON graph artifacts.

    All artifacts are derived and rebuildable; ``clear()`` is always safe.
    """

    def __init__(self, base_dir: Optional[Path] = None):
        self.base = Path(base_dir) if base_dir else KG_DIR
        self.dropped_sensitive = 0  # records withheld because no vault key
        self.refused: list = []     # saves/clears refused to protect a sealed file

    # ── paths ────────────────────────────────────────────────
    def _plain_path(self, name: str) -> Path:
        return self.base / f"{name}.json"

    def _sensitive_path(self, name: str) -> Path:
        return self.base / f"{name}{_SENSITIVE_SUFFIX}"

    # ── save / load ──────────────────────────────────────────
    def save(self, name: str, records: Iterable[dict]) -> dict:
        """Persist one artifact, splitting sensitive records to encrypted disk.

        Returns {"public": n, "sensitive": n, "dropped": n}, plus
        ``"refused": reason`` (and nothing written) when the existing sealed
        file must not be replaced; see ``_guard_sealed``.
        """
        if name not in ARTIFACT_NAMES:
            raise ValueError(f"unknown artifact: {name!r}")
        self.base.mkdir(parents=True, exist_ok=True)

        public, sensitive = [], []
        for rec in records:
            (sensitive if record_is_sensitive(rec) else public).append(rec)

        # The sealed sibling is checked BEFORE anything is written. A sealed
        # file no known key opens is neither replaced nor removed, and neither
        # is its plain half (the two stay one artifact): the save is refused.
        # A sealed file only an older key opens is kept aside first, and if
        # that copy cannot be made the save is refused too.
        spath = self._sensitive_path(name)
        verdict = _stale_load(spath) or self._guard_sealed(spath)
        if verdict != "ok":
            self.refused.append({"artifact": name, "reason": verdict})
            import logging
            logging.getLogger("friday.knowledge_graph").error(
                "knowledge graph: save of %s refused (%s); nothing was written",
                name, verdict)
            return {"public": 0, "sensitive": 0, "dropped": 0, "refused": verdict}

        self._atomic_write(self._plain_path(name),
                           json.dumps(public, ensure_ascii=False, indent=1).encode("utf-8"))

        dropped = 0
        if sensitive:
            key = _vault_key()
            if key:
                import agent_friday.privacy.vault_crypto as _vc
                blob = _vc.encrypt(
                    json.dumps(sensitive, ensure_ascii=False).encode("utf-8"), key)
                self._atomic_write(spath, blob)
            else:
                dropped = len(sensitive)
                self.dropped_sensitive += dropped
                if spath.exists():
                    spath.unlink()
        elif spath.exists():
            spath.unlink()

        return {"public": len(public), "sensitive": len(sensitive) - dropped,
                "dropped": dropped}

    def load(self, name: str) -> list[dict]:
        """Load one artifact, transparently merging the encrypted sibling."""
        if name not in ARTIFACT_NAMES:
            raise ValueError(f"unknown artifact: {name!r}")
        out: list[dict] = []
        p = self._plain_path(name)
        if p.exists():
            try:
                out.extend(json.loads(p.read_text(encoding="utf-8")) or [])
            except (OSError, ValueError):
                pass
        sp = self._sensitive_path(name)
        LOAD_VERDICT.pop(str(sp), None)
        if sp.exists():
            # A sealed file is never dropped silently: every key Friday knows
            # is tried, and what happened is recorded (SENSITIVE_STATE for the
            # integrity report, LOAD_VERDICT for save) and said
            # (integrity_report).
            try:
                import agent_friday.privacy.vault_crypto as _vc
                raw = sp.read_bytes()
                if _vc.is_encrypted(raw):
                    recs, via = _open_sealed(raw)
                    if recs is None:
                        _note_state(sp, "cannot_decrypt", None, None, len(raw))
                        _note_load(sp, "cannot_decrypt")
                    else:
                        out.extend(recs)
                        v = "ok" if via == "current" else "older_key"
                        _note_state(sp, v, via, len(recs), len(raw))
                        _note_load(sp, v)
            except OSError as e:
                _note_state(sp, "cannot_decrypt", type(e).__name__, None, 0)
                _note_load(sp, "cannot_decrypt")
        return out

    def _guard_sealed(self, spath: Path) -> str:
        """May ``spath`` be replaced or removed? ``"ok"`` or the reason not.

        * absent, plaintext, or opened by the current key: ``"ok"``;
        * opened by NO key Friday knows (what load reports as
          ``cannot_decrypt``): ``"cannot_decrypt"``. Replacing or removing it
          would lose records no key here can read, so it is left alone;
        * opened only by an older key: kept aside first
          (``_keep_if_unopenable``), then ``"ok"``;
        * the file cannot be read, or the copy cannot be made:
          ``"keep_failed"``. Nothing is overwritten or removed on a guess.

        This checks the file against the key state NOW. It is not the only
        gate: save() first refuses while the last load of this path in this
        process could not read it (``LOAD_VERDICT``, ``"stale_load"``),
        because the caller's records come from that load."""
        if not spath.exists():
            return "ok"
        try:
            import agent_friday.privacy.vault_crypto as _vc
            raw = spath.read_bytes()
        except OSError:
            return "keep_failed"
        if not _vc.is_encrypted(raw):
            return "ok"
        cur = _vault_key()
        if cur:
            try:
                _vc.decrypt(raw, cur)
                return "ok"
            except Exception:
                pass
        recs, via = _open_sealed(raw)
        if recs is None:
            _note_state(spath, "cannot_decrypt", None, None, len(raw))
            return "cannot_decrypt"
        try:
            self._keep_if_unopenable(spath, raw)
        except OSError:
            return "keep_failed"
        return "ok"

    def _keep_if_unopenable(self, spath: Path, raw: Optional[bytes] = None) -> Optional[Path]:
        """Copy a sealed file the CURRENT key does not open aside as
        ``<name>.sensitive.kept-<stamp>.json`` before it is replaced or removed.

        Raises OSError when the copy cannot be made or read back intact: the
        caller then aborts, because replacing a file whose only copy could not
        be kept is how records an older key still opens are lost. The kept
        copy is still ciphertext; ``scripts/vault_key_doctor.py --recover-kg``
        re-seals it under the current key."""
        if not spath.exists():
            return None
        import agent_friday.privacy.vault_crypto as _vc
        if raw is None:
            raw = spath.read_bytes()
        if not _vc.is_encrypted(raw):
            return None
        cur = _vault_key()
        if cur:
            try:
                _vc.decrypt(raw, cur)
                return None            # the current key reads it: nothing to keep
            except Exception:
                pass
        stamp = time.strftime("%Y%m%dT%H%M%S")
        dest = spath.with_name(spath.name.replace(_SENSITIVE_SUFFIX, "")
                               + ".sensitive.kept-%s.json" % stamp)
        n = 1
        while dest.exists():
            dest = spath.with_name(spath.name.replace(_SENSITIVE_SUFFIX, "")
                                   + ".sensitive.kept-%s-%d.json" % (stamp, n))
            n += 1
        from agent_friday.services import vault_passphrase as _vp
        _vp._durable_write(dest, raw)
        if dest.read_bytes() != raw:
            raise OSError("the kept copy of %s did not read back intact" % spath.name)
        import logging
        logging.getLogger("friday.knowledge_graph").error(
            "knowledge graph: kept %s aside as %s before replacing it (the current "
            "vault key does not open it)", spath.name, dest.name)
        return dest

    def save_layout(self, meta: dict) -> None:
        """Layout metadata (seed, algorithm, bounds); positions live on entities."""
        self.base.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self.base / "layout.json",
                           json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8"))

    def load_layout(self) -> dict:
        p = self.base / "layout.json"
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except (OSError, ValueError):
            return {}

    def clear(self) -> None:
        """Delete every derived artifact (safe: sources are never touched).

        Every sealed file is checked first (``_guard_sealed``). If any check or
        kept copy FAILS, nothing at all is deleted. An artifact whose sealed
        file no known key opens is left in place, plain half included; the
        rest are removed."""
        if not self.base.exists():
            return
        verdicts = {name: (_stale_load(self._sensitive_path(name))
                           or self._guard_sealed(self._sensitive_path(name)))
                    for name in ARTIFACT_NAMES}
        import logging
        _log = logging.getLogger("friday.knowledge_graph")
        if any(v == "keep_failed" for v in verdicts.values()):
            self.refused.append({"artifact": "*", "reason": "keep_failed"})
            _log.error("knowledge graph: clear refused; a sealed file could not be "
                       "checked or kept aside, so nothing was deleted")
            return
        for name in ARTIFACT_NAMES:
            if verdicts[name] != "ok":
                self.refused.append({"artifact": name, "reason": verdicts[name]})
                _log.error("knowledge graph: %s left in place on clear (%s)",
                           name, verdicts[name])
                continue
            for p in (self._plain_path(name), self._sensitive_path(name)):
                if p.exists():
                    p.unlink()
        p = self.base / "layout.json"
        if p.exists():
            p.unlink()

    def summary(self) -> dict:
        counts = {}
        newest = 0.0
        for name in ARTIFACT_NAMES:
            counts[name] = len(self.load(name))
            for p in (self._plain_path(name), self._sensitive_path(name)):
                if p.exists():
                    newest = max(newest, p.stat().st_mtime)
        return {"counts": counts, "last_indexed": newest or None,
                "dropped_sensitive": self.dropped_sensitive}

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)


# ═══════════════════════════════════════════════════════════════
#  Manifest — canonical-path source tracking + delta
# ═══════════════════════════════════════════════════════════════

def canonical(path: str | Path) -> str:
    """Single canonical key form: expand ~ and env vars, make absolute.

    Virtual sources (e.g. "conversation:abc123") pass through unchanged —
    only real filesystem paths are canonicalized. Windows drive prefixes
    ("C:\\...") are single letters and don't match the scheme test.
    """
    s = str(path)
    if len(s.split(":", 1)[0]) > 1 and s.split(":", 1)[0].isalpha():
        return s
    return os.path.abspath(os.path.expanduser(os.path.expandvars(s)))


def fingerprint(path: str | Path) -> Optional[dict]:
    """Content fingerprint for change detection (sha1 of on-disk bytes)."""
    p = Path(canonical(path))
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    return {"size": len(raw), "sha1": hashlib.sha1(raw).hexdigest(),
            "mtime": p.stat().st_mtime}


class KnowledgeGraphManifest:
    """Tracks ingested sources by canonical absolute path.

    Entry shape:
      sources[canonical_path] = {kind, size, sha1, mtime, produced: [ids],
                                 ingested_at}
    """

    VERSION = 1

    def __init__(self, base_dir: Optional[Path] = None):
        self.base = Path(base_dir) if base_dir else KG_DIR
        self.path = self.base / ".manifest.json"
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(d, dict) and isinstance(d.get("sources"), dict):
                    d.setdefault("version", self.VERSION)
                    return d
            except (OSError, ValueError):
                pass
        return {"version": self.VERSION, "sources": {}, "stats": {}}

    def save(self) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    @property
    def sources(self) -> dict:
        return self._data["sources"]

    def record(self, source: str | Path, kind: str,
               produced: Optional[list[str]] = None) -> None:
        """Mark a source as ingested with its current fingerprint."""
        key = canonical(source)
        fp = fingerprint(key) or {}
        self.sources[key] = {
            "kind": kind,
            "produced": list(produced or []),
            "ingested_at": time.time(),
            **fp,
        }

    def forget(self, source: str | Path) -> None:
        self.sources.pop(canonical(source), None)

    def delta(self, paths: Iterable[str | Path]) -> dict:
        """Classify current sources against the manifest.

        Returns {"new": [...], "changed": [...], "unchanged": [...],
                 "removed": [...]} — all canonical paths. "removed" lists
        manifest entries whose file no longer appears in `paths`.
        """
        seen = set()
        new, changed, unchanged = [], [], []
        for p in paths:
            key = canonical(p)
            seen.add(key)
            entry = self.sources.get(key)
            if entry is None:
                new.append(key)
                continue
            fp = fingerprint(key)
            if fp is None or fp["sha1"] != entry.get("sha1"):
                changed.append(key)
            else:
                unchanged.append(key)
        removed = [k for k in self.sources if k not in seen]
        return {"new": new, "changed": changed,
                "unchanged": unchanged, "removed": removed}


# ── Said out loud (services/integrity_report) ────────────────

_CHECKING = __import__("threading").Event()


def _check_in_background(paths: list) -> None:
    """Open the sealed files whose state is unknown or stale, off the caller's
    thread, and record what was found (SENSITIVE_STATE)."""
    try:
        import agent_friday.privacy.vault_crypto as _vc
        for sp in paths:
            try:
                raw = sp.read_bytes()
            except OSError:
                continue
            if not _vc.is_encrypted(raw):
                continue
            recs, via = _open_sealed(raw)
            if recs is None:
                _note_state(sp, "cannot_decrypt", None, None, len(raw))
            else:
                _note_state(sp, "ok" if via == "current" else "older_key", via,
                            len(recs), len(raw))
    finally:
        _CHECKING.clear()


def _integrity_probe() -> list:
    """Sealed knowledge-graph files Friday cannot read with its current key.

    Runs inside /api/health, so it never derives a key or decrypts on the
    caller's thread: it reports what load() or an earlier background check
    recorded, and a file whose state is unknown or whose size/mtime changed is
    checked in the background and reported once known."""
    out = []
    base = Path(KG_DIR)
    stale = []
    for name in ARTIFACT_NAMES:
        sp = base / f"{name}{_SENSITIVE_SUFFIX}"
        try:
            st = sp.stat()
        except OSError:
            continue
        known = SENSITIVE_STATE.get(str(sp))
        if not known or known.get("sig") != (st.st_size, st.st_mtime_ns):
            stale.append(sp)
            continue
        if known["state"] == "cannot_decrypt":
            out.append({"store": "knowledge graph", "state": "cannot_decrypt",
                        "file": sp.name, "records": None,
                        "message": ("The knowledge graph's %s file (%d MB, encrypted) does not "
                                    "open with any vault key Friday knows, so its private "
                                    "records are missing from the graph. The file is kept "
                                    "untouched." % (name, max(1, known["bytes"] // 1_000_000)))})
        elif known["state"] == "older_key":
            out.append({"store": "knowledge graph", "state": "sealed_under_other_key",
                        "file": sp.name, "records": known["records"],
                        "message": ("The knowledge graph's %s file (%d private records) opens "
                                    "only with a vault passphrase that is not the one Friday "
                                    "is using now (%s). Friday reads it, but the vault's "
                                    "passphrase homes disagree." % (name, known["records"],
                                                                    known["via"]))})
    if stale and not _CHECKING.is_set():
        _CHECKING.set()
        __import__("threading").Thread(target=_check_in_background, args=(stale,),
                                       name="kg-seal-check", daemon=True).start()
    return out


try:
    from agent_friday.services import integrity_report as _integrity_report
    _integrity_report.register("knowledge graph", _integrity_probe)
except Exception:
    pass
