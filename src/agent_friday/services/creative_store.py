"""Pull-to-disk for cloud creative providers - the step that makes "done" true.

The seam every async creative provider shares: **submit -> poll -> pull to disk
-> verify -> record**. This module owns the last four, provider-agnostically,
so adding a provider means writing a status adapter rather than a second
downloader. Higgsfield is the first caller; AtlasCloud is the second.

The motivating fact is retention. Higgsfield keeps output for *at least* seven
days and may then delete it, so a job is not finished when the vendor says
``completed`` - it is finished when verified bytes are on this machine. Every
provider gets that treatment by default, because the cost of assuming a
provider keeps files forever is losing work the user believed was saved.

Design notes that are load-bearing rather than decorative:

* **A zero-byte or wrong-magic download is a failure, not a creation.** A file
  that exists but is empty is exactly the "ran and produced nothing, exited
  zero" shape this codebase keeps getting bitten by.
* **The sidecar is written after the bytes land**, never before, so a sidecar
  can never advertise a file that is not there.
* **Retries are counted and surfaced.** After ``WARN_AFTER`` attempts the
  caller is handed a warning payload carrying the still-live source URL and
  the estimated expiry date, so the work can be saved by hand.
* **The provider that actually served is recorded**, not the one that was
  asked - so a fallback from one provider to another can never be reported as
  though the first had succeeded.
* Provenance and the gallery manifest are best-effort: a provenance failure
  must never destroy a download that otherwise succeeded.

Per-provider status parsing lives in ``parse_*_status`` adapters below: each
vendor nests status and output URLs differently, and that difference is the
only thing a new provider must supply.
"""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_friday.core import CREATIONS_DIR, FRIDAY_DIR

META_DIR = FRIDAY_DIR / "creations_meta"
RETENTION_DAYS = 7          # vendor floor; treat as the deadline, not a promise
WARN_AFTER = 5              # failed attempts before we escalate to the user
_TIMEOUT = 180

# First bytes we accept per extension. Anything else means we downloaded an
# error page, an HTML redirect, or a truncated file - none of which are art.
_MAGIC = {
    ".png":  (b"\x89PNG\r\n\x1a\n",),
    ".jpg":  (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",),
    ".gif":  (b"GIF8",),
    ".mp4":  None,           # checked via the ftyp box below
    ".mov":  None,
    ".svg":  (b"<", b"\xef\xbb\xbf<"),
    ".wav":  (b"RIFF",),
    ".mp3":  (b"ID3", b"\xff\xfb"),
    ".glb":  (b"glTF",),
}


# An error page, a login wall, or an anti-bot interstitial is HTML, arrives
# with HTTP 200, and is comfortably larger than any size floor. Saving one as
# a creation is the same class of bug as a search tool returning a CAPTCHA
# page as its results, so HTML is rejected outright whatever the filename says.
_TEXTUAL_PREFIXES = (b"<!doctype", b"<html", b"<?xml", b"{", b"[")


def _looks_real(data: bytes, suffix: str) -> bool:
    """True when the bytes plausibly are the media type the filename claims."""
    if len(data) < 512:                      # nothing real is this small
        return False

    head = data[:64].lstrip().lower()
    if suffix.lower() != ".svg" and head.startswith(_TEXTUAL_PREFIXES):
        return False                         # HTML/JSON masquerading as media

    suffix = suffix.lower()
    if suffix in (".mp4", ".mov"):
        return b"ftyp" in data[:32]
    expected = _MAGIC.get(suffix)
    if expected is None:
        # Unknown or absent extension: we cannot name what this is, so we
        # refuse to file it rather than guess. An output URL Friday cannot
        # identify is reported, not silently accepted.
        return False
    return any(data.startswith(sig) for sig in expected)


#: Terminal job states. Anything else means "still working".
TERMINAL = ("completed", "failed", "nsfw", "canceled")


def parse_higgsfield_status(structured):
    """Read a Higgsfield ``job_status`` reply into (status, output_urls).

    Written down because guessing this shape cost a paid video generation.
    The reply nests everything one level deep under ``generation`` - there is
    no top-level ``status`` and no ``results`` *list*; ``results`` is an object
    keyed ``rawUrl`` / ``minUrl`` / ``thumbnailUrl``. A poller that looks for
    ``reply["status"]`` sees None forever, polls until its own timeout, and
    exits zero having produced nothing while the vendor has already finished
    and started the seven-day deletion clock.

    Always prefers ``rawUrl``: the thumbnail and ``minUrl`` are downscaled
    previews, and filing one of those as the creation would be a quiet
    substitution of something worse for what was actually paid for.
    """
    gen = (structured or {}).get("generation") or {}
    if not gen and isinstance(structured, dict):
        gen = structured                       # tolerate an unwrapped reply
    status = gen.get("status") or ""
    res = gen.get("results")
    urls = []
    if isinstance(res, dict):
        if res.get("rawUrl"):
            urls.append(res["rawUrl"])
    elif isinstance(res, list):
        for item in res:
            if isinstance(item, dict) and (item.get("rawUrl") or item.get("url")):
                urls.append(item.get("rawUrl") or item["url"])
    return status, urls


def expiry_estimate(created_at=None):
    """When the vendor may delete this output. A floor, deliberately not a
    guarantee - the docs say 'at least' seven days."""
    return (created_at or datetime.now(timezone.utc)) + timedelta(days=RETENTION_DAYS)


def download_output(url, *, job, dest_dir=None, attempt=1):
    """Fetch one output URL to disk and verify it.

    Returns a result dict. ``ok`` is True only when verified bytes are on disk.
    On failure the caller gets ``retryable``, the attempt count, and - once
    attempts reach WARN_AFTER - a ``warning`` string naming the live URL and
    the expiry date so the user can rescue the file manually.
    """
    dest_dir = Path(dest_dir or CREATIONS_DIR)
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = url.rsplit("/", 1)[-1].split("?")[0] or "%s-%d" % (job.get("provider") or "job", int(time.time()))
    dest = dest_dir / filename

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "agent-friday"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = resp.read()
            declared = resp.headers.get("Content-Length")
    except Exception as e:
        return _failure(url, job, filename, attempt, "download failed: %s" % e)

    if not data:
        return _failure(url, job, filename, attempt, "server returned zero bytes")
    if declared and declared.isdigit() and int(declared) != len(data):
        return _failure(url, job, filename, attempt,
                        "truncated: expected %s bytes, got %d" % (declared, len(data)))
    if not _looks_real(data, dest.suffix):
        return _failure(url, job, filename, attempt,
                        "content is not a valid %s file (%d bytes) - refusing to "
                        "file it as a creation" % (dest.suffix or "media", len(data)))

    # Bytes are good. Only now does anything else get written.
    dest.write_bytes(data)
    sidecar = _write_sidecar(filename, url, job, len(data))
    _append_manifest(dest_dir, filename, dest, job)
    _write_provenance(dest, job)

    return {"ok": True, "filename": filename, "path": str(dest),
            "bytes": len(data), "sidecar": str(sidecar) if sidecar else None,
            "attempt": attempt,
            "file_record": {                     # the shape routes must return
                "filename": filename,
                "kind": job.get("kind") or "image",
                "url": "/api/creations/%s" % filename,
                "framed_url": "/creation/%s" % filename,
                "path": str(dest),
            }}


def _failure(url, job, filename, attempt, detail):
    exp = expiry_estimate(job.get("created_at"))
    out = {"ok": False, "filename": filename, "error": detail,
           "attempt": attempt, "retryable": True, "source_url": url,
           "expires_after": exp.date().isoformat()}
    if attempt >= WARN_AFTER:
        out["warning"] = (
            "I generated this but cannot pull it to your disk after %d attempts "
            "- the error is: %s. The file is still on Higgsfield at %s and they "
            "may delete it after about %s. Save it by hand if you want to keep "
            "it; I will keep retrying until then."
            % (attempt, detail, url, exp.date().isoformat())
        )
    return out


def _write_sidecar(filename, url, job, size):
    try:
        META_DIR.mkdir(parents=True, exist_ok=True)
        p = META_DIR / ("%s.json" % filename)
        p.write_text(json.dumps({
            "filename": filename,
            "kind": job.get("kind"),
            "prompt": job.get("prompt"),
            "model": job.get("model_friendly") or job.get("model"),
            "api_model": job.get("model"),
            "provider": job.get("provider") or "unknown",
            "request_id": job.get("request_id"),
            "mode": job.get("mode"),
            "seed_image": job.get("seed_image"),
            "source_url": url,
            "bytes": size,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "vendor_expiry_estimate": expiry_estimate(job.get("created_at")).isoformat(),
        }, indent=1), encoding="utf-8")
        return p
    except Exception:
        return None      # a sidecar failure must not un-download the file


def _append_manifest(dest_dir, filename, dest, job):
    try:
        with (dest_dir / "creations-manifest.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "filename": filename, "path": str(dest),
                "prompt": job.get("prompt"), "provider": job.get("provider") or "unknown",
                "model": job.get("model"), "kind": job.get("kind"),
                "request_id": job.get("request_id"),
                "created": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _write_provenance(dest, job):
    try:
        from agent_friday.services import provenance
        provenance.write(str(dest),
                         tool_chain=["%s.generate_%s" % (job.get("provider") or "cloud",
                                                     job.get("kind") or "image")],
                         media_type=job.get("kind"))
    except Exception:
        pass
