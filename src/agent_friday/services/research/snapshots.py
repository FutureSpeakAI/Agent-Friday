"""
Source snapshots — the sealed copy of every page a research finding cites.

A finding's quote has to be checkable later against the exact text it was
born from (RS12). The fetch cache (services/web_fetch) holds that text, but a
cache is shared, can be cleared, and says nothing about whether the bytes
changed after the fact. A snapshot is the per-commission record that can:

  * one file per cited source, under the commission's own directory
    (``<friday home>/research/<commission>/snapshots/<source_id>.snap``);
  * it holds the page text, the URL it came from, the time it was fetched and
    the SHA-256 of the text taken at snapshot time;
  * the whole record is written through ``credential_store.protect`` -- the
    same at-rest protection the task journal uses -- so the plaintext of a
    fetched page never lands on disk outside ``~/.friday`` and, when Friday's
    keystore is available, not in the clear inside it either. The method used
    is recorded, never assumed.

Reading a snapshot recomputes the hash. A record whose text no longer matches
its own hash is reported as not intact, and a quote checked against it does
not count as verified.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger("friday.research.snapshots")

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _dir(commission_id: str) -> Path:
    from agent_friday.services.research.objects import commission_dir
    return commission_dir(commission_id) / "snapshots"


def _path(commission_id: str, source_id: str) -> Path | None:
    if not _SAFE_ID.match(str(source_id or "")):
        return None
    return _dir(commission_id) / f"{source_id}.snap"


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _protect(raw: bytes) -> tuple[bytes, str]:
    from agent_friday.services import credential_store as cs
    blob, method = cs.protect(raw)
    return blob, method


def _unprotect(blob: bytes) -> bytes:
    from agent_friday.services import credential_store as cs
    return cs.unprotect(blob)


def exists(commission_id: str, source_id: str) -> bool:
    p = _path(commission_id, source_id)
    return bool(p and p.exists())


def snapshot(commission_id: str, source_id: str, *, url: str, text: str,
             fetched_at: float | None = None, title: str = "") -> dict | None:
    """Seal one cited page. Idempotent per (commission, source): the first
    snapshot is the one a finding was born from, so it is never overwritten.

    Returns the record's metadata (no text), or None if nothing could be
    written. Never raises into the research loop.
    """
    p = _path(commission_id, source_id)
    if p is None or not text:
        return None
    if p.exists():
        return meta(commission_id, source_id)
    rec = {
        "source_id": source_id,
        "url": url or "",
        "title": title or "",
        "fetched_at": float(fetched_at or time.time()),
        "snapshotted_at": time.time(),
        "sha256": sha256_text(text),
        "chars": len(text),
        "text": text,
    }
    try:
        blob, method = _protect(json.dumps(rec, ensure_ascii=False).encode("utf-8"))
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".snap.tmp")
        tmp.write_bytes(blob)
        tmp.replace(p)
    except Exception as e:
        _log.warning("could not snapshot source %s: %s", source_id, e)
        return None
    out = {k: v for k, v in rec.items() if k != "text"}
    out["protection"] = method
    return out


def load(commission_id: str, source_id: str) -> dict | None:
    """The sealed record with its text, and whether it is still intact."""
    p = _path(commission_id, source_id)
    if p is None or not p.exists():
        return None
    try:
        rec = json.loads(_unprotect(p.read_bytes()).decode("utf-8"))
    except Exception as e:
        return {"source_id": source_id, "intact": False,
                "error": f"the snapshot could not be read ({type(e).__name__})"}
    rec["intact"] = sha256_text(rec.get("text", "")) == rec.get("sha256")
    return rec


def meta(commission_id: str, source_id: str) -> dict | None:
    rec = load(commission_id, source_id)
    if rec is None:
        return None
    return {k: v for k, v in rec.items() if k != "text"}


def list_snapshots(commission_id: str) -> list[dict[str, Any]]:
    d = _dir(commission_id)
    out = []
    try:
        files = sorted(d.glob("*.snap"))
    except Exception:
        return []
    for f in files:
        m = meta(commission_id, f.stem)
        if m:
            out.append(m)
    return out


def quote_holds(commission_id: str, source_id: str, quote: str) -> bool:
    """True only when the snapshot is intact and holds the quote, under the
    same normalisation the harness's verification step uses."""
    from agent_friday.services.research.harness import _norm
    rec = load(commission_id, source_id)
    if not rec or not rec.get("intact"):
        return False
    q = _norm(quote)
    return bool(q) and q in _norm(rec.get("text", ""))
