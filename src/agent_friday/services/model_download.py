"""Download model weights and the runtime that serves them, safely.

The one way a model file gets onto this machine from the Models screen.
What "safely" means here, each a rule a test holds:

  * a download writes to `<dest>.part` and resumes from its length with an
    HTTP byte range, so an interrupted 6 GB fetch does not start over;
  * the file is accepted only when its sha256 matches the publisher's, read
    from the shortlist or the Hugging Face API; a mismatch deletes the part
    and reports the failure, never a half-installed model;
  * the rename to its final name is atomic, so a file either exists whole or
    not at all;
  * the registry (`model_store.register`) records it with the engine that
    can serve it, so the Arbiter never spawns stock llama.cpp on a Bonsai
    file;
  * nothing is fetched below the disk floor (residency rule R8), and the
    request carries nothing identifying: no token, no query parameter, no
    custom User-Agent, no cookie (the same test the update check passes);
  * progress is a process orb the user can see, and pause, resume and
    cancel are each one call.

The queue is in memory with a JSON mirror under `runtime/models/`, so a
restart shows what was in flight and its part files resume on request.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import threading
import time
import uuid
import zipfile
from pathlib import Path

import requests

from agent_friday.core import runtime_dir
from agent_friday.services import model_shortlist as sl

CHUNK = 1 << 20
CONNECT_TIMEOUT_S = 20
READ_TIMEOUT_S = 120

_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}
_CTRL: dict[str, dict] = {}

#: Called with the finished job after registration (the on-device bench
#: hooks in here). Never raises into the download.
AFTER_INSTALL: list = []

# The single network seam. Tests replace it; the identity test reads what it
# was called with.
_http_get = requests.get


class DownloadError(RuntimeError):
    pass


# ── paths ───────────────────────────────────────────────────────────────────

def state_path() -> Path:
    return runtime_dir() / "models" / "downloads.json"


def rate_path() -> Path:
    return runtime_dir() / "models" / "download_rate.json"


def runtime_dir_for(name: str = "prism-fork") -> Path:
    return runtime_dir() / ("llama.cpp-bonsai" if name == "prism-fork" else name)


def destination(kind: str, filename: str) -> Path:
    from agent_friday.services import model_store
    if kind == "gguf":
        return model_store.store_dir() / filename
    if kind == "runtime":
        return runtime_dir() / "downloads" / filename
    if kind.startswith("comfy:"):
        from agent_friday.services.local_image import comfy_root
        return comfy_root() / "models" / kind.split(":", 1)[1] / filename
    raise ValueError("unknown destination kind %r" % kind)


# ── the measured download rate ──────────────────────────────────────────────

def measured_rate_mib_s() -> float | None:
    try:
        d = json.loads(rate_path().read_text(encoding="utf-8"))
        return float(d.get("mib_s")) if d.get("mib_s") else None
    except Exception:
        return None


def _record_rate(mib_s: float) -> None:
    try:
        prev = measured_rate_mib_s()
        ema = mib_s if prev is None else 0.7 * mib_s + 0.3 * prev
        rate_path().parent.mkdir(parents=True, exist_ok=True)
        rate_path().write_text(json.dumps({"mib_s": round(ema, 2),
                                           "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S")}),
                               encoding="utf-8")
    except Exception:
        pass


def estimate_seconds(nbytes: int) -> dict:
    """How long `nbytes` would take at the last measured rate, or an honest
    'unknown' before any download has been measured."""
    rate = measured_rate_mib_s()
    if not rate:
        return {"basis": "unknown", "seconds": None,
                "note": "depends on your connection; Friday measures the first download"}
    secs = int(nbytes / (rate * 1048576))
    return {"basis": "measured", "seconds": secs, "rate_mib_s": rate}


# ── the queue ───────────────────────────────────────────────────────────────

def _save_state() -> None:
    try:
        state_path().parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps({"jobs": list(_JOBS.values())}, indent=1, default=str),
                       encoding="utf-8")
        os.replace(tmp, state_path())
    except Exception:
        pass


def jobs() -> list[dict]:
    with _LOCK:
        return [dict(j) for j in _JOBS.values()]


def get_job(job_id: str) -> dict | None:
    with _LOCK:
        j = _JOBS.get(job_id)
        return dict(j) if j else None


def _set(job: dict, **kw) -> None:
    with _LOCK:
        job.update(kw)
        job["updated_at"] = time.time()
    _save_state()


def _orb(job: dict, **kw) -> None:
    pid = job.get("orb_pid")
    if not pid:
        return
    try:
        from agent_friday.core import process_update
        process_update(pid, **kw)
    except Exception:
        pass


# ── one file ────────────────────────────────────────────────────────────────

def _sha256_file(path: Path, progress=None) -> str:
    h = hashlib.sha256()
    total = path.stat().st_size
    done = 0
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
            done += len(block)
            if progress:
                progress(done, total)
    return h.hexdigest()


def fetch_file(url: str, dest: Path, *, expected_bytes: int | None,
               expected_sha256: str | None, ctrl: dict, on_progress=None) -> dict:
    """Fetch `url` into `dest` with resume, verify, and rename atomically.

    Returns `{"status": "done"|"paused"|"cancelled", ...}`; raises
    DownloadError on a checksum mismatch or a server that will not serve
    the file.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    offset = part.stat().st_size if part.exists() else 0
    if expected_bytes and offset > expected_bytes:
        part.unlink()
        offset = 0
    headers = {}
    if offset:
        headers["Range"] = "bytes=%d-" % offset
    t0 = time.time()
    got = 0
    if not (expected_bytes and offset == expected_bytes):
        resp = _http_get(url, stream=True, headers=headers, allow_redirects=True,
                         timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S))
        try:
            status = int(getattr(resp, "status_code", 0) or 0)
            if status == 416 and expected_bytes and offset >= expected_bytes:
                pass            # already complete
            elif status == 206 and offset:
                mode = "ab"
            elif status == 200:
                # The server ignored the range (or there was none): start over.
                mode = "wb"
                offset = 0
            else:
                raise DownloadError("HTTP %s fetching %s" % (status, url))
            if status in (200, 206):
                with open(part, mode) as f:
                    for block in resp.iter_content(chunk_size=CHUNK):
                        if ctrl["cancel"].is_set():
                            f.close()
                            try:
                                part.unlink()
                            except OSError:
                                pass
                            return {"status": "cancelled"}
                        if ctrl["pause"].is_set():
                            return {"status": "paused", "bytes": offset + got}
                        if not block:
                            continue
                        f.write(block)
                        got += len(block)
                        if on_progress:
                            on_progress(offset + got, expected_bytes)
        finally:
            try:
                resp.close()
            except Exception:
                pass
    elapsed = time.time() - t0
    if got and elapsed > 0.5:
        _record_rate(got / 1048576 / elapsed)
    size = part.stat().st_size if part.exists() else 0
    if expected_bytes and size != expected_bytes:
        raise DownloadError("size mismatch: got %d bytes, expected %d" % (size, expected_bytes))
    if expected_sha256:
        if on_progress:
            on_progress(size, expected_bytes, phase="verifying")
        actual = _sha256_file(part)
        if actual.lower() != expected_sha256.lower():
            try:
                part.unlink()
            except OSError:
                pass
            raise DownloadError("checksum mismatch for %s: the file is not the one the "
                                "publisher signed (expected %s…, got %s…)"
                                % (dest.name, expected_sha256[:12], actual[:12]))
        checked = "sha256"
    else:
        checked = "size only"
    os.replace(part, dest)
    return {"status": "done", "bytes": size, "checked": checked,
            "elapsed_s": round(elapsed, 1)}


# ── the runtime ─────────────────────────────────────────────────────────────

def _release_json(api_url: str) -> dict:
    resp = _http_get(api_url, timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S))
    if int(getattr(resp, "status_code", 0) or 0) != 200:
        raise DownloadError("could not read the runtime release list (HTTP %s)"
                            % getattr(resp, "status_code", "?"))
    return resp.json()


def _unpack(archive: Path, into: Path) -> Path:
    """Unpack a release archive; a single top-level folder is flattened.
    Returns the folder that holds the binaries."""
    tmp = into.with_name(into.name + ".unpack")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    name = archive.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for member in z.namelist():
                if member.startswith("/") or ".." in Path(member).parts:
                    raise DownloadError("archive member escapes its folder: %r" % member)
            z.extractall(tmp)
    elif name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive, "r:gz") as t:
            for member in t.getmembers():
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise DownloadError("archive member escapes its folder: %r" % member.name)
            t.extractall(tmp)
    else:
        raise DownloadError("unknown archive type: %s" % archive.name)
    entries = [p for p in tmp.iterdir()]
    src = entries[0] if len(entries) == 1 and entries[0].is_dir() else tmp
    into.mkdir(parents=True, exist_ok=True)
    for p in src.iterdir():
        target = into / p.name
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink()
        shutil.move(str(p), str(target))
    shutil.rmtree(tmp, ignore_errors=True)
    return into


def runtime_binary(name: str = "prism-fork") -> Path | None:
    spec = sl.runtime_spec(name)
    binary = (spec.get("server_binary") or {}).get(sl.os_family(), "llama-server")
    p = runtime_dir_for(name) / binary
    return p if p.exists() else None


# ── the job ─────────────────────────────────────────────────────────────────

def start_model(model_id: str, packing: str | None = None, *, profile: dict | None = None,
                with_companions: bool = True, with_runtime: bool = True,
                engine_override: str | None = None, start_thread: bool = True,
                serve_override: dict | None = None) -> dict:
    """Queue a shortlist model: its weights, its companions, and the runtime
    it needs if that is not installed. Returns the job (status `refused`
    with a reason when it may not start).

    `serve_override` replaces the shortlist's serving numbers (`serve_num_ctx`,
    `serve_args`) in the model's record: the installer's hardware pick uses it
    so a 16 GB computer is not registered with a context only a large card holds."""
    m = sl.get(model_id)
    if not m:
        return {"status": "refused", "error": "%r is not on the shortlist" % model_id}
    f = sl.file_entry(model_id, packing)
    if not f:
        return {"status": "refused", "error": "no such file for %s: %r" % (model_id, packing)}
    items = [{"kind": "gguf", "name": f["file"], "url": sl.file_url(model_id, f),
              "bytes": int(f.get("bytes") or 0), "sha256": f.get("sha256"),
              "role": "weights", "packing": f.get("packing")}]
    if with_companions:
        for c in sl.companions(model_id):
            items.append({"kind": "gguf", "name": c["file"], "url": sl.file_url(model_id, c),
                          "bytes": int(c.get("bytes") or 0), "sha256": c.get("sha256"),
                          "role": c.get("kind", "companion")})
    needs_runtime = (m.get("runtime") == "prism-fork" and f.get("runtime") != "llama.cpp"
                     and with_runtime and not engine_override and runtime_binary() is None)
    total = sum(i["bytes"] for i in items)
    refusal = _disk_refusal(profile, total)
    if refusal:
        return {"status": "refused", "error": refusal, "rule_id": "R8"}
    job = {
        "id": "dl-%s" % uuid.uuid4().hex[:8], "model_id": model_id, "label": m.get("label", model_id),
        "packing": f.get("packing"), "status": "queued", "items": items,
        "bytes_total": total, "bytes_done": 0, "rate_mib_s": None, "eta_s": None,
        "needs_runtime": needs_runtime, "engine": engine_override, "error": None,
        "started_at": time.time(), "finished_at": None, "orb_pid": None,
        "serve": (serve_override if serve_override is not None else (m.get("serve") or {})),
        "licence": m.get("licence"),
    }
    try:
        from agent_friday.core import process_register
        job["orb_pid"] = "download-%s" % job["id"][3:]
        process_register(job["orb_pid"], name="Download", label="Downloading %s" % job["label"],
                         category="monitoring", icon="⬇", model=model_id)
    except Exception:
        job["orb_pid"] = None
    with _LOCK:
        _JOBS[job["id"]] = job
        _CTRL[job["id"]] = {"pause": threading.Event(), "cancel": threading.Event()}
    _save_state()
    if start_thread:
        threading.Thread(target=run_job, args=(job["id"],), daemon=True,
                         name="download-%s" % job["id"]).start()
    return dict(job)


def start_pasted(repo: str, filename: str, *, bytes_: int, sha256: str | None,
                 label: str | None = None, licence: str | None = None,
                 profile: dict | None = None, start_thread: bool = True) -> dict:
    """Queue one GGUF the user named by Hugging Face repo and file name. It
    is served by stock llama.cpp (no engine recorded); the verdict screen
    said what it will do here before this was pressed."""
    repo = repo.strip().strip("/")
    if repo.count("/") != 1 or not filename.lower().endswith(".gguf") or "/" in filename or ".." in filename:
        return {"status": "refused", "error": "a pasted model is publisher/model plus one .gguf file name"}
    model_id = "hf:" + repo.split("/")[1].lower()
    items = [{"kind": "gguf", "name": filename, "url": sl.HF_RESOLVE.format(repo=repo, file=filename),
              "bytes": int(bytes_ or 0), "sha256": sha256, "role": "weights",
              "packing": filename.rsplit(".", 1)[0].split("-")[-1]}]
    refusal = _disk_refusal(profile, items[0]["bytes"])
    if refusal:
        return {"status": "refused", "error": refusal, "rule_id": "R8"}
    job = {
        "id": "dl-%s" % uuid.uuid4().hex[:8], "model_id": model_id, "label": label or repo.split("/")[1],
        "packing": items[0]["packing"], "status": "queued", "items": items,
        "bytes_total": items[0]["bytes"], "bytes_done": 0, "rate_mib_s": None, "eta_s": None,
        "needs_runtime": False, "engine": None, "error": None, "started_at": time.time(),
        "finished_at": None, "orb_pid": None, "serve": {}, "licence": licence,
        "pasted": {"repo": repo, "file": filename},
    }
    try:
        from agent_friday.core import process_register
        job["orb_pid"] = "download-%s" % job["id"][3:]
        process_register(job["orb_pid"], name="Download", label="Downloading %s" % job["label"],
                         category="monitoring", icon="⬇", model=model_id)
    except Exception:
        job["orb_pid"] = None
    with _LOCK:
        _JOBS[job["id"]] = job
        _CTRL[job["id"]] = {"pause": threading.Event(), "cancel": threading.Event()}
    _save_state()
    if start_thread:
        threading.Thread(target=run_job, args=(job["id"],), daemon=True,
                         name="download-%s" % job["id"]).start()
    return dict(job)


def _disk_refusal(profile: dict | None, total_bytes: int) -> str | None:
    try:
        from agent_friday.services import residency_policy as rp
        from agent_friday.services import hardware_profile as hwp
        prof = profile or hwp.get()
        v = rp.check_disk_headroom(prof, max(1, int(total_bytes / 1048576)))
        if isinstance(v, dict) and v.get("ok") is False:
            return v.get("explanation") or "not enough free disk for this download"
    except Exception:
        return None
    return None


def run_job(job_id: str) -> dict:
    with _LOCK:
        job = _JOBS.get(job_id)
        ctrl = _CTRL.get(job_id)
    if not job or not ctrl:
        return {"status": "unknown"}
    ctrl["pause"].clear()
    _set(job, status="downloading", error=None)
    done_before = 0
    kept = None
    if not job.get("kept_previous"):
        # A download that replaces a model keeps the old file and record in
        # the previous-version slot; a failure puts them straight back.
        try:
            from agent_friday.services import model_remove
            kept = model_remove.keep_previous(job["model_id"])
            if kept:
                _set(job, kept_previous=True)
        except Exception:
            kept = None
    try:
        for item in job["items"]:
            if item.get("status") == "done":
                done_before += item["bytes"]
                continue
            dest = destination(item["kind"], item["name"])
            item["dest"] = str(dest)
            if dest.exists() and dest.stat().st_size == item["bytes"] and item.get("sha256"):
                # Already on disk at the right size: verify rather than refetch.
                if _sha256_file(dest).lower() == item["sha256"].lower():
                    item["status"] = "done"
                    done_before += item["bytes"]
                    continue
            t_item = time.time()

            def _progress(done, total, phase="downloading", _item=item, _base=done_before, _t=t_item):
                el = max(0.001, time.time() - _t)
                rate = done / 1048576 / el
                job_done = _base + done
                _set(job, bytes_done=job_done, rate_mib_s=round(rate, 2),
                     eta_s=int((job["bytes_total"] - job_done) / (rate * 1048576)) if rate > 0 else None,
                     phase=phase)
                frac = job_done / job["bytes_total"] if job["bytes_total"] else 0
                _orb(job, progress=min(0.98, frac),
                     label="%s %s: %d%%" % (phase.capitalize(), job["label"], int(frac * 100)))

            out = fetch_file(item["url"], dest, expected_bytes=item["bytes"],
                             expected_sha256=item.get("sha256"), ctrl=ctrl,
                             on_progress=_progress)
            if out["status"] == "paused":
                _set(job, status="paused")
                _orb(job, label="Paused: %s" % job["label"])
                return get_job(job_id)
            if out["status"] == "cancelled":
                _set(job, status="cancelled", finished_at=time.time())
                _orb(job, status="cancelled", label="Cancelled: %s" % job["label"])
                return get_job(job_id)
            item["status"] = "done"
            item["checked"] = out.get("checked")
            done_before += item["bytes"]
            _set(job, bytes_done=done_before)
        if job.get("needs_runtime"):
            _set(job, status="installing-runtime")
            _orb(job, label="Installing the runtime for %s" % job["label"])
            job["engine"] = str(_install_runtime(ctrl))
        _set(job, status="registering")
        rec = _register(job)
        _set(job, status="installed", finished_at=time.time(), record=rec)
        _orb(job, status="completed", progress=1.0, label="%s installed" % job["label"])
        for hook in list(AFTER_INSTALL):
            try:
                hook(dict(job))
            except Exception:
                pass
    except Exception as e:
        _set(job, status="failed", error="%s: %s" % (type(e).__name__, e),
             finished_at=time.time())
        _orb(job, status="error", label="Failed: %s" % job["label"])
        if kept:
            try:
                from agent_friday.services import model_remove
                model_remove.rollback(job["model_id"])
                _set(job, rolled_back=True)
            except Exception:
                pass
    return get_job(job_id)


def _install_runtime(ctrl: dict) -> Path:
    spec = sl.runtime_spec()
    from agent_friday.services import hardware_profile as hwp
    backend = sl.backend_for(hwp.get())
    release = _release_json(spec["releases_api"])
    assets = sl.pick_release_assets(release, backend)
    if not assets:
        raise DownloadError("no runtime build for this OS and %s backend" % backend)
    into = runtime_dir_for()
    for a in assets:
        dest = destination("runtime", a["name"])
        out = fetch_file(a["url"], dest, expected_bytes=a.get("size"),
                         expected_sha256=a.get("sha256"), ctrl=ctrl)
        if out["status"] != "done":
            raise DownloadError("runtime download %s" % out["status"])
        _unpack(dest, into)
    binary = runtime_binary()
    if binary is None:
        raise DownloadError("the runtime unpacked but no server binary was found")
    return binary


def _register(job: dict) -> dict:
    from agent_friday.services import model_store
    weights = next(i for i in job["items"] if i["role"] == "weights")
    mmproj = next((i for i in job["items"] if i["role"] == "mmproj"), None)
    m = sl.get(job["model_id"]) or {}
    if job.get("pasted"):
        m = {"repo": job["pasted"]["repo"], "label": job.get("label"), "licence": job.get("licence")}
    serve = job.get("serve") or {}
    engine = job.get("engine")
    if not engine and m.get("runtime") == "prism-fork":
        b = runtime_binary()
        engine = str(b) if b else None
    return model_store.register(
        job["model_id"], weights["dest"], source=model_store.SOURCE_DOWNLOAD,
        mmproj=mmproj["dest"] if mmproj else None,
        sha256=weights.get("sha256"),
        origin={"repo": m.get("repo"), "file": weights["name"], "url": weights["url"],
                "licence": m.get("licence"), "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
        label="%s (%s)" % (m.get("label", job["model_id"]), weights.get("packing") or "gguf"),
        engine=engine, serve_args=serve.get("serve_args"),
        serve_num_ctx=serve.get("serve_num_ctx"))


# ── controls ────────────────────────────────────────────────────────────────

def pause(job_id: str) -> dict:
    with _LOCK:
        c = _CTRL.get(job_id)
    if not c:
        return {"ok": False, "error": "no such download"}
    c["pause"].set()
    return {"ok": True}


def resume(job_id: str) -> dict:
    with _LOCK:
        j = _JOBS.get(job_id)
        c = _CTRL.get(job_id)
    if not j or not c:
        return {"ok": False, "error": "no such download"}
    if j.get("status") not in ("paused", "failed"):
        return {"ok": False, "error": "download is %s" % j.get("status")}
    c["pause"].clear()
    c["cancel"].clear()
    threading.Thread(target=run_job, args=(job_id,), daemon=True).start()
    return {"ok": True}


def cancel(job_id: str) -> dict:
    with _LOCK:
        j = _JOBS.get(job_id)
        c = _CTRL.get(job_id)
    if not j or not c:
        return {"ok": False, "error": "no such download"}
    c["cancel"].set()
    c["pause"].clear()
    if j.get("status") in ("paused", "queued", "failed"):
        for item in j.get("items") or []:
            d = item.get("dest")
            if d:
                part = Path(d).with_name(Path(d).name + ".part")
                try:
                    part.unlink()
                except OSError:
                    pass
        _set(j, status="cancelled", finished_at=time.time())
        _orb(j, status="cancelled", label="Cancelled: %s" % j["label"])
    return {"ok": True}
