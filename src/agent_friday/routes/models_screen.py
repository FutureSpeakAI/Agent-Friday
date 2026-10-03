"""Settings → Models: the shortlist, and one-click downloads with resume.

Read-only routes sample the machine and the registry; the download route
starts a background job that reports through a process orb and through
`/api/models/downloads`. Nothing here sends anything about the machine
anywhere: the shortlist is a file in the package, and a download is a plain
GET of a public file.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent_friday.core import login_required
from agent_friday.routes._errors import api_error, public_result

models_screen_bp = Blueprint("models_screen", __name__)


def _wire_after_install() -> None:
    """A download counts as installed once it is measured: the bench runs on
    the download's own thread after registration."""
    try:
        from agent_friday.services import model_bench, model_download
        if model_bench.after_install not in model_download.AFTER_INSTALL:
            model_download.AFTER_INSTALL.append(model_bench.after_install)
    except Exception:
        pass


_wire_after_install()


def _installed_ids() -> set:
    try:
        from agent_friday.services import model_store
        return set(model_store.available().keys())
    except Exception:
        return set()


def shortlist_payload() -> dict:
    from agent_friday.services import model_download as md
    from agent_friday.services import model_shortlist as sl
    installed = _installed_ids()
    rows = []
    for m in sl.entries():
        files = []
        for f in m.get("files") or []:
            files.append({"file": f["file"], "packing": f.get("packing"),
                          "bytes": f.get("bytes"), "gib": round((f.get("bytes") or 0) / 2 ** 30, 2),
                          "default": bool(f.get("default")), "note": f.get("note"),
                          "runtime": f.get("runtime") or m.get("runtime"),
                          "time": md.estimate_seconds(int(f.get("bytes") or 0))})
        rows.append({
            "id": m["id"], "label": m.get("label"), "publisher": m.get("publisher"),
            "friday_standard": bool(m.get("friday_standard")), "roles": m.get("roles") or [],
            "licence": m.get("licence"), "licence_class": m.get("licence_class"),
            "telemetry": m.get("telemetry"), "repo_url": m.get("repo_url"),
            "runtime": m.get("runtime"), "generation_note": m.get("generation_note"),
            "installed": m["id"] in installed, "files": files,
            "companions": [{"file": c["file"], "kind": c.get("kind"),
                            "gib": round((c.get("bytes") or 0) / 2 ** 30, 2)}
                           for c in m.get("companions") or []],
        })
    return {"status": "ok", "models": rows,
            "runtime_installed": md.runtime_binary() is not None,
            "download_rate": md.estimate_seconds(2 ** 30)}


@models_screen_bp.route("/api/models/shortlist", methods=["GET"])
@login_required
def shortlist():
    try:
        return jsonify(public_result(shortlist_payload(), "Couldn't read the model shortlist"))
    except Exception as e:
        return api_error(e, "Couldn't read the model shortlist", shape="bare")


@models_screen_bp.route("/api/models/download", methods=["POST"])
@login_required
def download():
    """Start one download: `{model_id, packing?}`. Returns the job, or a
    refusal with its reason (the disk floor, an unknown model)."""
    try:
        from agent_friday.services import model_download as md
        data = request.get_json(silent=True) or {}
        model_id = (data.get("model_id") or data.get("model") or "").strip()
        if not model_id and not (data.get("repo") and data.get("file")):
            return jsonify({"status": "error", "error": "model_id, or repo and file, is required"}), 400
        try:
            from agent_friday.services import machine_monitor as mm
            s = mm.last_sample() or mm.sample()
            v = mm.disk_system_verdict(s) or {}
            if v.get("status") == "breached":
                return jsonify({"status": "refused", "rule_id": "R-DISK-SYSTEM",
                                "error": "Free space on the system drive is critically low (%s); "
                                         "Friday will not start a download until this clears."
                                         % v.get("explanation", "")}), 409
        except Exception:
            pass
        if data.get("repo") and data.get("file"):
            job = md.start_pasted(data["repo"], data["file"], bytes_=int(data.get("bytes") or 0),
                                  sha256=data.get("sha256"), label=data.get("label"),
                                  licence=data.get("licence"))
        else:
            job = md.start_model(model_id, data.get("packing"))
        code = 409 if job.get("status") == "refused" else 200
        return jsonify(public_result(job, "Couldn't start the download")), code
    except Exception as e:
        return api_error(e, "Couldn't start the download", shape="bare")


@models_screen_bp.route("/api/models/downloads", methods=["GET"])
@login_required
def downloads():
    try:
        from agent_friday.services import model_download as md
        return jsonify({"status": "ok", "downloads": md.jobs()})
    except Exception as e:
        return api_error(e, "Couldn't list downloads", shape="bare")


@models_screen_bp.route("/api/models/downloads/<job_id>/<action>", methods=["POST"])
@login_required
def download_control(job_id, action):
    try:
        from agent_friday.services import model_download as md
        fn = {"pause": md.pause, "resume": md.resume, "cancel": md.cancel}.get(action)
        if fn is None:
            return jsonify({"ok": False, "error": "unknown action %r" % action}), 400
        out = fn(job_id)
        return jsonify(out), (200 if out.get("ok") else 404)
    except Exception as e:
        return api_error(e, "Couldn't change the download", shape="bare")


# ── the catalogue, what-if, and any model ───────────────────────────────────

@models_screen_bp.route("/api/models/catalog", methods=["GET"])
@login_required
def catalog():
    """Every model Friday can offer, one row per file, with the verdict for
    this machine and the arithmetic behind it. `?beside=1` places each
    candidate beside the seats that are resident now."""
    try:
        from agent_friday.services import model_catalog_rows as rows
        beside = (request.args.get("beside") or "") in ("1", "true", "yes")
        return jsonify(public_result(rows.catalog_payload(beside_resident=beside),
                                     "Couldn't build the model catalogue"))
    except Exception as e:
        return api_error(e, "Couldn't build the model catalogue", shape="bare")


@models_screen_bp.route("/api/models/whatif", methods=["POST"])
@login_required
def whatif():
    """The same catalogue on a pretend machine: `{vram_gb?, ram_gb?, gpu_name?}`.
    Local arithmetic only; every row carries `simulated`."""
    try:
        from agent_friday.services import hardware_profile as hwp
        from agent_friday.services import model_catalog_rows as rows
        from agent_friday.services import model_fit as mf
        data = request.get_json(silent=True) or {}
        vram = data.get("vram_gb")
        ram = data.get("ram_gb")
        sim = mf.what_if(hwp.get(), vram_total_mib=int(float(vram) * 1024) if vram else None,
                         ram_total_mib=int(float(ram) * 1024) if ram else None,
                         gpu_name=data.get("gpu_name"))
        return jsonify(public_result(rows.catalog_payload(sim), "Couldn't compute the what-if"))
    except Exception as e:
        return api_error(e, "Couldn't compute the what-if", shape="bare")


@models_screen_bp.route("/api/models/check", methods=["POST"])
@login_required
def check():
    """Any model: `{repo}` is a Hugging Face id. One anonymous metadata read;
    the verdict for each GGUF file in it."""
    try:
        from agent_friday.services import model_catalog_rows as rows
        data = request.get_json(silent=True) or {}
        repo = (data.get("repo") or data.get("id") or "").strip()
        if not repo:
            return jsonify({"status": "error", "error": "repo is required"}), 400
        out = rows.check_hf_repo(repo)
        return jsonify(public_result(out, "Couldn't check that model")), (200 if out.get("status") == "ok" else 400)
    except Exception as e:
        return api_error(e, "Couldn't check that model", shape="bare")


# ── the stack preview ───────────────────────────────────────────────────────

def _pick_from_catalog(spec: dict, profile: dict) -> dict | None:
    """Resolve `{id, packing?, kind?}` to a stack pick with real bytes."""
    from agent_friday.services import model_catalog_rows as rows
    from agent_friday.services import model_shortlist as sl
    mid = str(spec.get("id") or "")
    kind = spec.get("kind") or "text"
    if kind in ("image", "video", "music"):
        # A footprint measured on this machine (footprint_measure records the
        # peak under the lease) beats the shipped peak table, which predates
        # ComfyUI being told the display reserve.
        peak = None
        try:
            from agent_friday.services import residency_catalog as rc
            fp = rc.footprint(mid, profile) or {}
            if fp.get("basis") == "measured" and fp.get("vram_mib"):
                peak = int(fp["vram_mib"])
        except Exception:
            pass
        if peak is None:
            try:
                from agent_friday.services import local_image as li
                peak = li.MEASURED_PEAK_MIB.get(mid)
            except Exception:
                pass
        return {"id": mid, "label": spec.get("label") or mid, "kind": kind,
                "peak_vram_mib": peak or spec.get("peak_vram_mib"), "file_bytes": spec.get("bytes") or 0,
                "measured_load_s": spec.get("load_s")}
    if kind == "cpu":
        return {"id": mid, "label": spec.get("label") or mid, "kind": "cpu",
                "host_ram_mib": spec.get("host_ram_mib") or 0}
    m = sl.get(mid)
    if m is None:
        m = next((x for x in rows.store_models() if x["id"] == mid), None)
    if m is None:
        return None
    f = next((x for x in m.get("files") or [] if x.get("packing") == spec.get("packing")), None) \
        or next((x for x in m.get("files") or [] if x.get("default")), None) \
        or ((m.get("files") or [None])[0])
    if not f:
        return None
    lay = rows._layout_for(m)
    mm = next((c for c in m.get("companions") or [] if c.get("kind") == "mmproj"), None)
    return {"id": mid, "label": m.get("label") or mid, "kind": "text",
            "role": spec.get("role") or ("interactive_brain" if m.get("friday_standard") else "heavy_hitter"),
            "file_bytes": int(f.get("bytes") or 0), "layout": lay,
            "context_cap": (m.get("serve") or {}).get("serve_num_ctx") or m.get("context_training"),
            "mmproj_bytes": int((mm or {}).get("bytes") or 0),
            "measured_load_s": rows._measured_load_s(mid, profile)}


@models_screen_bp.route("/api/models/stack/preview", methods=["POST"])
@login_required
def stack_preview():
    """`{picks: [{id, packing?, kind?, role?}], vram_gb?, ram_gb?}` → the bars,
    the swap times and the sentence. Nothing is downloaded or loaded."""
    try:
        from agent_friday.services import hardware_profile as hwp
        from agent_friday.services import model_fit as mf
        from agent_friday.services import model_stack as ms
        data = request.get_json(silent=True) or {}
        prof = hwp.get()
        if data.get("vram_gb") or data.get("ram_gb"):
            prof = mf.what_if(prof, vram_total_mib=int(float(data["vram_gb"]) * 1024) if data.get("vram_gb") else None,
                              ram_total_mib=int(float(data["ram_gb"]) * 1024) if data.get("ram_gb") else None,
                              gpu_name=data.get("gpu_name"))
        picks = [p for p in (_pick_from_catalog(s, prof) for s in data.get("picks") or []) if p]
        out = ms.preview(prof, picks)
        out["status"] = "ok"
        out["picks"] = [{"id": p["id"], "label": p["label"], "kind": p["kind"]} for p in picks]
        return jsonify(public_result(out, "Couldn't preview the stack"))
    except Exception as e:
        return api_error(e, "Couldn't preview the stack", shape="bare")


# ── measure, remove, roll back ──────────────────────────────────────────────

@models_screen_bp.route("/api/models/<path:model_id>/bench", methods=["POST", "GET"])
@login_required
def bench_route(model_id):
    """GET: the last bench receipt. POST: measure the model on this machine
    now, through the arbiter's bench lease, on a background thread."""
    try:
        from agent_friday.services import model_bench
        if request.method == "GET":
            r = model_bench.last_receipt(model_id)
            return jsonify({"status": "ok", "receipt": r}) if r else (jsonify({"status": "none"}), 404)
        import threading
        from agent_friday.core import process_register
        pid = "bench-%s" % model_id.replace(":", "-")
        try:
            process_register(pid, name="Benchmark", label="Measuring %s on this computer" % model_id,
                             category="monitoring", icon="\u23f1", model=model_id)
        except Exception:
            pass

        def _run():
            out = model_bench.bench(model_id)
            try:
                from agent_friday.core import process_update
                ok = out.get("status") == "measured"
                process_update(pid, status="completed" if ok else "error", progress=1.0,
                               label=("%s: %s tok/s measured" % (model_id, (out.get("result") or {}).get("decode_tok_s")))
                               if ok else "Measuring %s failed" % model_id)
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"status": "measuring", "task_id": pid, "model_id": model_id})
    except Exception as e:
        return api_error(e, "Couldn't measure the model", shape="bare")


@models_screen_bp.route("/api/models/<path:model_id>", methods=["DELETE"])
@login_required
def remove_route(model_id):
    """Remove a model and free its file. `?replacement=<id>` names what takes
    its roles; without one, a model that holds a role is refused with the
    roles named."""
    try:
        from agent_friday.services import model_remove
        out = model_remove.remove(model_id, replacement=request.args.get("replacement") or None,
                                  force=(request.args.get("force") or "") in ("1", "true"))
        return jsonify(public_result(out, "Couldn't remove the model")), (200 if out.get("ok") else 409)
    except Exception as e:
        return api_error(e, "Couldn't remove the model", shape="bare")


@models_screen_bp.route("/api/models/<path:model_id>/rollback", methods=["POST"])
@login_required
def rollback_route(model_id):
    try:
        from agent_friday.services import model_remove
        out = model_remove.rollback(model_id)
        return jsonify(public_result(out, "Couldn't go back")), (200 if out.get("ok") else 404)
    except Exception as e:
        return api_error(e, "Couldn't go back", shape="bare")


@models_screen_bp.route("/api/models/<path:model_id>/previous", methods=["GET"])
@login_required
def previous_route(model_id):
    try:
        from agent_friday.services import model_remove
        p = model_remove.previous_version(model_id)
        return jsonify({"status": "ok", "previous": p}) if p else (jsonify({"status": "none"}), 404)
    except Exception as e:
        return api_error(e, "Couldn't read the previous version", shape="bare")
