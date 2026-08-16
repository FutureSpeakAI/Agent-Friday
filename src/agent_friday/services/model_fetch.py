"""
Agent Friday — acquire a model without Ollama.

The last thing `ollama pull` was needed for. This fetches a GGUF straight from
where it lives — Hugging Face by default, any HTTPS URL otherwise — verifies
it, and hands it to `model_store`, which reads its facts from the file.

Three things this does that a plain download does not:

**Verifies against the publisher's own hash.** Hugging Face serves LFS objects
with an `X-Linked-Etag` / `ETag` carrying the sha256 of the content. We compare
against it rather than against nothing, so a truncated transfer or a corrupted
mirror fails here — at download time, with an explanation — instead of at load
time as `wrong number of tensors`, which reads exactly like an unsupported
architecture. This codebase has already spent a day misreading that error once.

**Resumes.** A 9 GB artifact over a domestic connection is a long time to be
one dropped packet away from starting again. Partial files are kept as `.part`
and continued with a Range request.

**Refuses before it starts, not after.** Disk is checked against R8's floor up
front. Filling a disk and then discovering it is the failure mode that takes
the whole machine down with it, not just the download.

No API token is required for public repositories. `HF_TOKEN` is used when set,
because gated repositories exist and failing with "401" rather than "this model
requires you to accept its licence" would be unkind.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HF_HOST = "https://huggingface.co"
DISK_FLOOR_BYTES = 10 * 1024 ** 3        # R8
CHUNK = 8 << 20
USER_AGENT = "agent-friday/1.0 (+local model store)"


class FetchError(RuntimeError):
    pass


def _headers() -> dict:
    h = {"User-Agent": USER_AGENT}
    tok = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if tok:
        h["Authorization"] = "Bearer %s" % tok
    return h


def hf_url(repo: str, filename: str, revision: str = "main") -> str:
    return "%s/%s/resolve/%s/%s" % (HF_HOST, repo.strip("/"), revision,
                                    filename.lstrip("/"))


def _clean_etag(v: str | None) -> str | None:
    """HF returns the sha256 as a quoted ETag; some proxies add W/."""
    if not v:
        return None
    v = v.strip().strip("W/").strip('"')
    return v if re.fullmatch(r"[0-9a-f]{64}", v or "") else None


def probe(url: str) -> dict:
    """Size, hash and resumability, before committing to the download."""
    req = urllib.request.Request(url, method="HEAD", headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            info = r.headers
            status = r.status
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise FetchError(
                "%d from the host. If this is a gated repository you need to "
                "accept its licence on the website and set HF_TOKEN." % e.code)
        raise FetchError("HTTP %d fetching headers for %s" % (e.code, url))
    except Exception as e:
        raise FetchError("could not reach %s: %s" % (url, e))

    size = int(info.get("Content-Length") or 0)
    # ONLY X-Linked-Etag. The plain ETag on this endpoint is a git blob id or a
    # CDN tag, not a hash of the content — and it is 64 hex characters, so it
    # passes a shape check and then fails the comparison against a file that is
    # perfectly good. Measured 2026-08-15: ETag c07418bf... against a correct
    # file whose sha256 is 06507c7b..., which is the LFS oid the tree API
    # reports. A verifier that rejects valid downloads gets turned off, so
    # being wrong here is worse than not checking.
    sha = _clean_etag(info.get("X-Linked-Etag"))
    return {
        "url": url, "status": status, "size_bytes": size, "sha256": sha,
        "resumable": (info.get("Accept-Ranges") or "").lower() == "bytes",
        "filename": url.rsplit("/", 1)[-1],
    }


def _check_disk(dest: Path, need_bytes: int) -> None:
    free = shutil.disk_usage(str(dest.anchor or dest.parent)).free
    after = free - need_bytes
    if after < DISK_FLOOR_BYTES:
        raise FetchError(
            "refusing: %.1f GB free after a %.1f GB download is below the "
            "%.0f GB floor (R8). Free some space or choose a smaller "
            "quantization."
            % (after / 1024 ** 3, need_bytes / 1024 ** 3,
               DISK_FLOOR_BYTES / 1024 ** 3))


def download(url: str, dest, *, expected_sha256: str | None = None,
             progress=None, resume: bool = True) -> dict:
    """Fetch one file to `dest`, resuming and verifying. Returns a report."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")

    meta = probe(url)
    total = meta["size_bytes"]
    sha_expected = expected_sha256 or meta["sha256"]

    if dest.exists() and total and dest.stat().st_size == total:
        return {"ok": True, "path": str(dest), "reused": True,
                "size_bytes": total, "sha256": sha_expected,
                "verified": "size (already present)"}

    have = part.stat().st_size if (resume and part.exists()) else 0
    if have and meta["resumable"] and have < total:
        mode, headers = "ab", dict(_headers(), Range="bytes=%d-" % have)
    else:
        have, mode, headers = 0, "wb", _headers()
    _check_disk(dest, max(0, total - have))

    t0 = time.time()
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r, \
                open(part, mode) as f:
            done = have
            last = 0.0
            while True:
                buf = r.read(CHUNK)
                if not buf:
                    break
                f.write(buf)
                done += len(buf)
                if progress and (time.time() - last) > 2.0:
                    last = time.time()
                    progress(done, total, time.time() - t0)
    except Exception as e:
        raise FetchError("transfer failed after %d bytes: %s" % (have, e))
    if progress:
        progress(done, total, time.time() - t0)

    if total and part.stat().st_size != total:
        raise FetchError("short read: %d of %d bytes — the partial file is "
                         "kept at %s and the next attempt will resume it"
                         % (part.stat().st_size, total, part))

    verified = "size only — the host published no checksum"
    if sha_expected:
        actual = _sha256(part, progress=progress)
        if actual != sha_expected:
            part.unlink(missing_ok=True)
            raise FetchError(
                "checksum mismatch: expected %s, got %s. The partial file has "
                "been deleted; a corrupted GGUF fails at load time as 'wrong "
                "number of tensors', which reads like an unsupported "
                "architecture and is very hard to diagnose from there."
                % (sha_expected[:16], actual[:16]))
        verified = "sha256 against the publisher's checksum"

    # GGUF magic, cheaply, so an HTML error page saved as .gguf is caught here.
    with open(part, "rb") as f:
        if f.read(4) != b"GGUF":
            part.unlink(missing_ok=True)
            raise FetchError("the downloaded file is not a GGUF (bad magic) — "
                             "the URL probably returned an error page")

    os.replace(part, dest)
    return {"ok": True, "path": str(dest), "reused": False,
            "size_bytes": dest.stat().st_size, "sha256": sha_expected,
            "verified": verified, "seconds": round(time.time() - t0, 1)}


def _sha256(path, progress=None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
#  Acquire straight into the store
# ─────────────────────────────────────────────────────────────────────────────

def fetch_gguf(model_id: str, *, repo: str | None = None,
               filename: str | None = None, url: str | None = None,
               revision: str = "main", mmproj_filename: str | None = None,
               borrow_template_from: str | None = None,
               progress=None) -> dict:
    """Download a GGUF and register it in Friday's store. No Ollama involved.

    Either `url`, or `repo` + `filename`. `model_id` is the name Friday will
    know it by and can be anything — the seat picker, the residency plan and
    the arbiter all key off it.
    """
    from agent_friday.services import gguf_extract as gx
    from agent_friday.services import model_store as ms

    if not url:
        if not (repo and filename):
            raise FetchError("need either url=, or repo= and filename=")
        url = hf_url(repo, filename, revision)

    # The tree API's LFS `oid` is the authoritative content hash — it is what
    # the file actually hashes to, and it is what a mirror would have to match.
    # Asked for first, because the download headers are a weaker source.
    expected = None
    if repo and filename:
        try:
            for f in list_repo_ggufs(repo, revision):
                if f["filename"] == filename and f.get("sha256"):
                    expected = f["sha256"]
                    break
        except FetchError:
            pass

    dest = ms.store_dir() / ms_safe_name(model_id)
    rep = download(url, dest, expected_sha256=expected, progress=progress)

    mm_path = None
    if mmproj_filename and repo:
        mm_dest = ms.store_dir() / (ms_safe_name(model_id)[:-5] +
                                    ".mmproj.gguf")
        try:
            mm = download(hf_url(repo, mmproj_filename, revision), mm_dest,
                          progress=progress)
            mm_path = mm["path"]
        except FetchError:
            # A missing vision tower is not a failed model — text still works,
            # and saying so beats refusing the whole download.
            mm_path = None

    tmpl = None
    facts = ms.describe(dest)
    if facts.get("has_chat_template"):
        tmpl = gx.chat_template_path(model_id)
        tmpl.parent.mkdir(parents=True, exist_ok=True)
        tmpl.write_text(
            gx.gguf_metadata(dest, keys=("tokenizer.chat_template",))
            .get("tokenizer.chat_template") or "", encoding="utf-8")
    elif borrow_template_from:
        res = gx.ensure_chat_template(model_id,
                                      family_source=borrow_template_from)
        tmpl = res.get("path") if res.get("ok") else None

    entry = ms.register(model_id, dest, source=ms.SOURCE_DOWNLOAD,
                        mmproj=mm_path, chat_template=tmpl,
                        sha256=rep.get("sha256"),
                        origin={"url": url, "repo": repo,
                                "filename": filename, "revision": revision})
    return {"ok": True, "entry": entry, "download": rep,
            "mmproj": mm_path, "chat_template": str(tmpl) if tmpl else None}


def ms_safe_name(model_id: str) -> str:
    return model_id.replace(":", "-").replace("/", "-") + ".gguf"


def search_hf(query: str, *, limit: int = 10) -> list:
    """Find GGUF repositories by name. Read-only, unauthenticated, best effort.

    Deliberately thin. Ollama's real value was curation, and this does not
    pretend to replace that — it answers "what is this called on the Hub"
    so a download does not require leaving the app to look it up.
    """
    url = ("%s/api/models?search=%s&filter=gguf&limit=%d&sort=downloads"
           % (HF_HOST, urllib.parse.quote(query), limit))
    try:
        req = urllib.request.Request(url, headers=_headers())
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        return [{"error": "search unavailable: %s" % e}]
    return [{"repo": m.get("modelId") or m.get("id"),
             "downloads": m.get("downloads"),
             "likes": m.get("likes")} for m in data]


def list_repo_ggufs(repo: str, revision: str = "main") -> list:
    """The GGUF files in a repo, so a quantization can be chosen by name."""
    url = "%s/api/models/%s/tree/%s" % (HF_HOST, repo.strip("/"), revision)
    try:
        req = urllib.request.Request(url, headers=_headers())
        with urllib.request.urlopen(req, timeout=30) as r:
            tree = json.loads(r.read().decode())
    except Exception as e:
        raise FetchError("could not list %s: %s" % (repo, e))
    return [{"filename": t["path"], "size_bytes": t.get("size"),
             "sha256": (t.get("lfs") or {}).get("oid")}
            for t in tree if str(t.get("path", "")).lower().endswith(".gguf")]

