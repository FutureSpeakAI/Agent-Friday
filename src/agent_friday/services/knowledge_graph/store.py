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

    # ── paths ────────────────────────────────────────────────
    def _plain_path(self, name: str) -> Path:
        return self.base / f"{name}.json"

    def _sensitive_path(self, name: str) -> Path:
        return self.base / f"{name}{_SENSITIVE_SUFFIX}"

    # ── save / load ──────────────────────────────────────────
    def save(self, name: str, records: Iterable[dict]) -> dict:
        """Persist one artifact, splitting sensitive records to encrypted disk.

        Returns {"public": n, "sensitive": n, "dropped": n}.
        """
        if name not in ARTIFACT_NAMES:
            raise ValueError(f"unknown artifact: {name!r}")
        self.base.mkdir(parents=True, exist_ok=True)

        public, sensitive = [], []
        for rec in records:
            (sensitive if record_is_sensitive(rec) else public).append(rec)

        self._atomic_write(self._plain_path(name),
                           json.dumps(public, ensure_ascii=False, indent=1).encode("utf-8"))

        dropped = 0
        spath = self._sensitive_path(name)
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
        if sp.exists():
            key = _vault_key()
            if key:
                try:
                    import agent_friday.privacy.vault_crypto as _vc
                    raw = sp.read_bytes()
                    if _vc.is_encrypted(raw):
                        out.extend(json.loads(_vc.decrypt(raw, key).decode("utf-8")) or [])
                except Exception:
                    pass  # wrong key / tamper — omit sensitive records
        return out

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
        """Delete every derived artifact (safe: sources are never touched)."""
        if not self.base.exists():
            return
        for name in ARTIFACT_NAMES:
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
