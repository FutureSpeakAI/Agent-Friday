"""The models the installer's picker chose, fetched on first start.

The Windows installer never downloads model weights. Its model page records
what the person chose, and whether they agreed to the download, in
``first-run.json`` beside the install (``<install root>\\first-run.json``). On
first start Friday reads it here and fetches the weights through the same
downloader the Models screen uses, so a first-run download gets, unchanged:

  * progress (a process orb and ``/api/models/first-run``),
  * resume (an interrupted fetch continues from its ``.part`` file),
  * the publisher's sha256 (a file that does not match is deleted and the
    model is never registered), and
  * the disk floor (residency rule R8).

Two seats are filled, both required unless the person chose a cloud model:

  fast_responder   the local VOICE FRONT from ``voice_front.FRONT_MODELS``:
                   Ternary Bonsai 1.7B by default, or a Qwen3 build (the 1.7B
                   or the 4B) as the alternative. It arrives through the voice
                   installer's pinned, sha256-verified, resumable artifact
                   path, together with the speech ear (``voice-ear-streaming``)
                   and ``sherpa-onnx``, and is seated by setting
                   ``voice_front_model``, which ``voice_front.selected_model``
                   reads. Ternary Bonsai 1.7B also fills Quick reflexes
                   (``capability_routing.local``); a Qwen3 front fills nothing
                   else.
  deep_thinker     a Bonsai model from the shortlist. Seated as
                   ``capability_routing.reasoning`` (and
                   ``model_routing.local_model``), "Everyday conversation".

A seat is set only after its weights are on disk, verified and registered, so
a failed or cancelled download leaves the person's existing choice alone. The
request file stays until every chosen model is installed, so a restart
resumes; then it is renamed ``first-run.done.json``.

Nothing here reaches the network except the downloader's plain GET of a
public file, and nothing about the machine is sent anywhere.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from agent_friday.services import model_ids

log = logging.getLogger("friday.first_run_models")

REQUEST_NAME = "first-run.json"
DONE_NAME = "first-run.done.json"
STATUS_NAME = "first-run-status.json"

#: seat -> (label shown to the person, settings capability it fills; the fast
#: seat fills none, it is the ``voice_front_model`` setting)
SEATS = {
    "fast_responder": ("Fast responder", None),
    "deep_thinker": ("Deep thinker", "reasoning"),
}

#: Which shortlist roles may fill the deep seat. Bonsai 2 27B is the brain; the
#: earlier Ternary Bonsai generation may stand in as a lesser brain on a
#: machine below the 27B's floor.
SEAT_ROLES = {
    "deep_thinker": {"brain", "lesser_brain"},
}

#: The voice front models (``voice_front.FRONT_MODELS`` keys) and the voice
#: artifact that carries each one's pinned file.
FRONT_ARTIFACT = {
    "ternary-bonsai:1.7b": "voice-front-bonsai-1.7b",
    "qwen3-4b-instruct-2507": "voice-front-4b",
    "qwen3-1.7b": "voice-front-1.7b",
}

#: Local voice needs these beside any front: the ear and its runtime, and the
#: pinned whisper recognisers push-to-transcribe / the CPU ear ("whisper-base")
#: and media transcripts ("whisper-base.en") load by path and never fetch
#: themselves (services/feature_models). The installer page's size includes
#: them (resources/voice_front_options.json), so the consent covers them.
FRONT_COMPANIONS = ("voice-ear-streaming", "sherpa-onnx", "whisper-base", "whisper-base.en")


def front_artifacts(model_id: str) -> list:
    """Every artifact a fast-responder choice downloads, front first."""
    return [FRONT_ARTIFACT[model_id], *FRONT_COMPANIONS]

#: Fast first: it is small. The PrismML runtime a Bonsai front is served on
#: comes with the deep thinker, which is always a Bonsai model.
ORDER = ("fast_responder", "deep_thinker")

_LOCK = threading.RLock()
_WORKER: Optional[threading.Thread] = None


# ── where the files live ────────────────────────────────────────────────────

def install_root() -> Optional[Path]:
    """The folder the installer made (the app's parent), or None for a source
    checkout, which has no installer and no first-run request."""
    env = os.environ.get("FRIDAY_INSTALL_ROOT")
    if env:
        return Path(env)
    from agent_friday.services.app_version import APP_ROOT
    parent = Path(APP_ROOT).parent
    if (parent / "install-manifest.json").is_file() or (parent / "python").is_dir():
        return parent
    return None


def request_path() -> Optional[Path]:
    env = os.environ.get("FRIDAY_FIRST_RUN_FILE")
    if env:
        return Path(env)
    root = install_root()
    return (root / REQUEST_NAME) if root else None


def _sibling(name: str) -> Optional[Path]:
    p = request_path()
    return p.with_name(name) if p else None


# ── reading and checking the request ────────────────────────────────────────

def _read_json(p: Optional[Path]) -> Optional[dict]:
    if not p:
        return None
    try:
        # utf-8-sig: the installer writes through Pascal and PowerShell.
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def validate_choice(seat: str, model_id: str, packing: Optional[str] = None) -> Optional[str]:
    """None when ``model_id`` may fill ``seat``, else the reason it may not.

    The request file is data on disk, so every name in it is checked against
    the shortlist before it reaches the downloader.
    """
    from agent_friday.services import model_shortlist as sl

    if seat not in SEATS:
        return "unknown seat %r" % seat
    if seat == "fast_responder":
        from agent_friday.services import voice_artifacts as va
        from agent_friday.services import voice_front as vf
        if model_id not in FRONT_ARTIFACT or model_id not in vf.FRONT_MODELS:
            return "%r is not a voice front model" % (model_id,)
        for aid in front_artifacts(model_id):
            ok, why = va.pinned(aid)
            if not ok:
                return why
        return None
    m = sl.get(str(model_id))
    if not m:
        return "%r is not on the shortlist" % model_id
    if not (set(m.get("roles") or []) & SEAT_ROLES[seat]):
        return "%s cannot fill the %s seat" % (m.get("label", model_id), SEATS[seat][0].lower())
    f = sl.file_entry(str(model_id), packing)
    if not f:
        return "no file %r for %s" % (packing, model_id)
    if not f.get("sha256") or not f.get("bytes"):
        return "%s has no published checksum, so it will not be installed" % model_id
    return None


def _bounded_int(value, low: int, high: int) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if low <= n <= high else None


def serve_for_pick(pick) -> Optional[dict]:
    """The serving numbers for the installer's hardware pick, or None.

    ``pick`` is the output of the matching rule in bonsai2-tiers.json
    (context, n_gpu_layers, kv, slots, batch). It becomes the model record's
    ``serve_num_ctx`` and ``serve_args``, which the Arbiter honours over its own
    defaults, so a CPU-tier computer is not asked to hold a 131K-token context.
    Every field is range-checked: the file is data on disk.
    """
    if not isinstance(pick, dict):
        return None
    ctx = _bounded_int(pick.get("context"), 2048, 262144)
    ngl = _bounded_int(pick.get("n_gpu_layers"), 0, 99)
    slots = _bounded_int(pick.get("slots"), 1, 4)
    kv = pick.get("kv")
    if ctx is None or ngl is None or slots is None or kv not in ("f16", "q8_0", "q4_0"):
        return None
    args = ["-ngl", str(ngl), "-np", str(slots)]
    m = re.fullmatch(r"-b (\d{2,5}) -ub (\d{2,5})", str(pick.get("batch") or ""))
    if m:
        args += ["-b", m.group(1), "-ub", m.group(2)]
    # Always explicit: the Arbiter keeps a model's declared KV type over its own default.
    args += ["--cache-type-k", kv, "--cache-type-v", kv]
    return {"serve_num_ctx": ctx, "serve_args": args}


def load_request() -> Optional[dict]:
    """The pending request, normalised, or None when there is nothing to do.

    ``{"cloud": bool, "consent": bool, "seats": {seat: {"model_id", "packing"}},
    "problems": [str]}``. A request whose consent box was not ticked, or that
    chose a cloud model, carries no seats: nothing will be downloaded.
    """
    raw = _read_json(request_path())
    if raw is None:
        return None
    cloud = bool(raw.get("cloud"))
    consent = bool(raw.get("consent_download"))
    seats: dict = {}
    problems: list = []
    if not cloud and consent:
        for seat in ORDER:
            want = (raw.get("seats") or {}).get(seat) or {}
            model_id = str(want.get("model_id") or "")
            if not model_id:
                problems.append("no model was chosen for the %s seat" % SEATS[seat][0].lower())
                continue
            why = validate_choice(seat, model_id, want.get("packing"))
            if why:
                problems.append(why)
                continue
            seats[seat] = {"model_id": model_id, "packing": want.get("packing"),
                           "with_companions": want.get("with_companions", True) is not False,
                           "serve": serve_for_pick(want.get("pick"))}
    return {"cloud": cloud, "consent": consent, "seats": seats, "problems": problems,
            "release": raw.get("release")}


# ── seating ─────────────────────────────────────────────────────────────────

def apply_seat(seat: str, model_id: str) -> None:
    """Seat an installed model. Called only once its weights are verified."""
    from agent_friday import core

    if seat == "fast_responder":
        # voice_front.selected_model(settings) reads this key.
        delta = {"voice_front_model": model_id}
        reflex = _register_reflex(model_id)
        if reflex:
            # The same file is the Quick reflexes model (capability "local"),
            # served by the Arbiter on the engine the front declares.
            delta["capability_routing"] = {"local": {"provider": "llama-cpp-local",
                                                     "model": reflex}}
            delta["model_routing"] = {"local_model": reflex}
        core._save_settings(delta)
        log.info("first run: voice front set to %s%s", model_id,
                 " (and Quick reflexes)" if reflex else "")
        return
    _label, capability = SEATS[seat]
    delta: dict = {"capability_routing": {capability: {"provider": "ollama-local",
                                                       "model": model_id}},
                   "model_routing": {"local_model": model_id}}
    core._save_settings(delta)
    log.info("first run: %s seat set to %s", seat, model_id)


def _register_reflex(model_id: str) -> Optional[str]:
    """Record a voice front that is also a shortlist model (Ternary Bonsai
    1.7B) in the model store, so the Arbiter can serve it in the Quick
    reflexes seat; its model id, or None for a front that is only a front
    (the Qwen3 builds). The engine is the runtime the front declares, named
    by path: the deep thinker's download installs it after this seat."""
    from agent_friday.services import model_shortlist as sl
    from agent_friday.services import voice_front as vf
    spec = vf.FRONT_MODELS.get(model_id) or {}
    if not spec.get("engine") or not sl.get(model_id):
        return None
    from agent_friday.services import model_download as md
    from agent_friday.services import model_store
    binary = (sl.runtime_spec(spec["engine"]).get("server_binary") or {}).get(
        sl.os_family(), "llama-server")
    model_store.register(
        model_id, vf.model_path(model_id), source=model_store.SOURCE_DOWNLOAD,
        sha256=spec["sha256"], label=spec["label"],
        engine=str(md.runtime_dir_for(spec["engine"]) / binary),
        origin={"repo": spec["repo"], "file": spec["file"], "licence": spec["licence"]})
    return model_id


# ── is the seat already served? ─────────────────────────────────────────────

#: Setting: a first-run seat counts as done when a working local model already
#: serves it (the requested model under another spelling, or the model the
#: seat's capability names), rather than only when the literal requested id
#: finished downloading. On by default; off restores the literal-id check.
SERVED_SETTING = "first_run_seat_satisfied_by_serving"

#: (seat, requested, served) triples already written to friday.log, so a card
#: polled every few seconds does not repeat the same line.
_NOTED: set = set()


def _note_once(seat: str, wanted: str, got: str, how: str) -> None:
    sig = (seat, wanted, got, how)
    if sig in _NOTED:
        return
    _NOTED.add(sig)
    log.info("first run: the %s seat asked for %s and is satisfied by %s (%s)",
             SEATS.get(seat, (seat,))[0].lower(), wanted, got, how)


def _served_setting_on() -> bool:
    try:
        from agent_friday import core
        return (core._load_settings() or {}).get(SERVED_SETTING, True) is not False
    except Exception:
        return True


def installed_equivalent(model_id: str) -> Optional[str]:
    """The model-store key of an installed model that is ``model_id`` under
    any spelling (``bonsai2-27b`` for ``bonsai2:27b``), or None."""
    from agent_friday.services import model_store
    from agent_friday.services.model_ids import find_equivalent
    try:
        return find_equivalent(model_id, model_store.available())
    except Exception as e:  # noqa: BLE001 - an unreadable registry is "not installed"
        log.debug("first run: model store unreadable: %s", e)
        return None


def _served_ids() -> list:
    """Model ids a working local seat is serving now, from the Arbiter's own
    view. Read-only: it adopts nothing and starts nothing, and a model merely
    present in a daemon's list is not counted as serving.

      * a seat this process's Arbiter spawned counts: that is the Arbiter's
        own record of what it put on that port;
      * a seat another Friday process published counts only when the server
        on that port says it holds that model (``residency_arbiter.
        _model_on_port``, its ``/v1/models``; the Arbiter starts every seat
        with ``--alias <model id>``). A port reused by something else that
        answers ``/health`` is not the published model.

    The published ports are asked at once, so a stale port costs one timeout
    for the whole call, not one per entry. Call it once per question and pass
    the list on (``seat_served_by(..., served=...)``).
    """
    out: list = []
    try:
        from agent_friday.services import residency_arbiter as ra
        arb = ra.get_arbiter()
        procs = dict(getattr(getattr(arb, "llama", None), "procs", {}) or {}) if arb is not None else {}
        out.extend(str(m) for m in procs)
        our_ports = set()
        for entry in procs.values():
            try:
                our_ports.add(int(entry[1]))
            except Exception:
                continue
        foreign = []
        for mid, base in (ra._read_published() or {}).items():
            port = ra._endpoint_port(base)
            if str(mid) in out or not port or port in our_ports:
                continue
            foreign.append((str(mid), port))
        if foreign:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(8, len(foreign))) as pool:
                said = list(pool.map(lambda mp: ra._model_on_port(mp[1]), foreign))
            for (mid, port), held in zip(foreign, said):
                if held and model_ids.same(held, mid):
                    out.append(mid)
                else:
                    log.debug("first run: port %s was published for %s but serves %s; not counted",
                              port, mid, held or "no model id")
    except Exception as e:  # noqa: BLE001 - no answer is "not served"
        log.debug("first run: serving check unavailable: %s", e)
    return [m for m in out if not m.startswith("voice-front:")]


def seat_served_by(seat: str, model_id: str, served: Optional[list] = None) -> Optional[str]:
    """The id of a working local model that serves ``seat`` now, or None.

    "Serves" is the Arbiter's verified view (``_served_ids``); pass
    ``served`` to reuse one survey for several seats. A served model
    satisfies the seat when it is the requested model under any spelling, or
    the local model the seat's capability already names (the owner's own
    choice; ``model_ids.same`` tells the two apart). The fast seat is the
    voice front, which is not an Arbiter seat, so it is judged by its own
    download only.
    """
    capability = SEATS.get(seat, (None, None))[1]
    if not capability or not _served_setting_on():
        return None
    from agent_friday.services.model_ids import find_equivalent
    if served is None:
        served = _served_ids()
    if not served:
        return None
    hit = find_equivalent(model_id, served)
    if hit:
        return hit
    try:
        from agent_friday import core
        cr = (core._load_settings() or {}).get("capability_routing") or {}
        configured = (cr.get(capability) or {}).get("model")
    except Exception:
        configured = None
    if configured:
        from agent_friday.services.local_seats import _names_a_cloud_model
        if not _names_a_cloud_model(configured):
            return find_equivalent(configured, served)
    return None


# ── the status the UI reads ─────────────────────────────────────────────────

def _write_status(status: dict) -> None:
    p = _sibling(STATUS_NAME)
    if not p:
        return
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(status, indent=1), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def _entry(seat: str, model_id: str, packing: Optional[str]) -> dict:
    if seat == "fast_responder":
        from agent_friday.services import voice_artifacts as va
        from agent_friday.services import voice_front as vf
        total = sum(va.size_bytes(a) for a in front_artifacts(model_id))
        return {"seat": seat, "label": SEATS[seat][0], "model_id": model_id,
                "model_label": vf.FRONT_MODELS[model_id]["label"] + " + speech ear",
                "packing": vf.FRONT_MODELS[model_id].get("packing", "Q4_K_M"),
                "bytes_total": int(total), "bytes_done": 0,
                "state": "waiting", "error": None, "job_id": None, "artifacts_done": 0}
    from agent_friday.services import model_shortlist as sl
    m = sl.get(model_id) or {}
    f = sl.file_entry(model_id, packing) or {}
    return {"seat": seat, "label": SEATS[seat][0], "model_id": model_id,
            "model_label": m.get("label", model_id), "packing": f.get("packing"),
            "bytes_total": int(f.get("bytes") or 0), "bytes_done": 0,
            "state": "waiting", "error": None, "job_id": None}


def status() -> dict:
    """What the first-run download is doing, for the progress card.

    ``state`` is one of ``none`` (nothing was asked for), ``cloud`` (the person
    chose a cloud model), ``declined`` (models were chosen but the download
    was not agreed to), ``running``, ``failed`` (something stopped and will
    resume), or ``done``. Live byte counts come from the downloader's own job.
    """
    req = load_request()
    if req is None:
        done = _read_json(_sibling(DONE_NAME))
        if done is not None:
            saved = _read_json(_sibling(STATUS_NAME)) or {}
            return {"state": "done", "seats": saved.get("seats", []), "problems": []}
        return {"state": "none", "seats": [], "problems": []}
    if req["cloud"]:
        return {"state": "cloud", "seats": [], "problems": []}
    if not req["consent"]:
        return {"state": "declined", "seats": [], "problems": []}
    saved = _read_json(_sibling(STATUS_NAME)) or {}
    rows = {r["seat"]: dict(r) for r in saved.get("seats", []) if isinstance(r, dict)}
    out = []
    try:
        from agent_friday.services import model_download as md
    except Exception:
        md = None
    for seat in ORDER:
        want = req["seats"].get(seat)
        if not want:
            continue
        row = rows.get(seat) or _entry(seat, want["model_id"], want.get("packing"))
        job = md.get_job(row["job_id"]) if (md and row.get("job_id")) else None
        if seat == "fast_responder" and row["state"] == "downloading":
            # The voice installer reports the file it is on; earlier artifacts
            # of this choice are already counted in artifacts_done.
            try:
                from agent_friday.services import voice_installer as vi
                prog = (vi.status().get("progress") or {})
                row["bytes_done"] = int(row.get("artifacts_done") or 0) + int(prog.get("done") or 0)
            except Exception:
                pass
        if job and row["state"] not in ("installed", "failed"):
            row["bytes_done"] = int(job.get("bytes_done") or 0)
            row["bytes_total"] = int(job.get("bytes_total") or row["bytes_total"])
            row["rate_mib_s"] = job.get("rate_mib_s")
            row["eta_s"] = job.get("eta_s")
            row["phase"] = job.get("phase")
        out.append(row)
    with _LOCK:
        alive = _WORKER is not None and _WORKER.is_alive()
    # The card asks "is this seat working", not "did this exact id download".
    # A download still in flight (or queued behind one) is left to finish,
    # which also keeps the 4 s poll off the survey; the survey itself runs at
    # most once per call, and only when a row needs it.
    pending = [r for r in out if r.get("state") != "installed"
               and not (alive and r.get("state") in ("downloading", "waiting"))
               and SEATS.get(r["seat"], (None, None))[1]]
    served = _served_ids() if (pending and _served_setting_on()) else []
    satisfied = False
    for row in pending:
        got = seat_served_by(row["seat"], row["model_id"], served=served)
        if not got:
            continue
        row["served_by"] = got
        if model_ids.same(got, row["model_id"]):
            # The requested model itself, under another spelling: the
            # download is done in every sense that matters.
            _note_once(row["seat"], row["model_id"], got, "the same model serves the seat")
            row.update(state="installed", error=None, bytes_done=row.get("bytes_total") or 0)
            satisfied = True
        else:
            # The owner's own, different model serves the seat. The card says
            # so, but the requested download is not marked done: whether to
            # drop it stays the owner's call.
            _note_once(row["seat"], row["model_id"], got, "the owner's configured model serves the seat")
    # A seat whose runtime cannot serve is not done, whatever its weights say,
    # and the card names the reason instead of "did not finish downloading".
    for row in out:
        if row.get("state") == "installed" and not row.get("served_by"):
            why = _runtime_problem_for(row["model_id"])
            if why:
                row.update(state="failed", error=why)
    states = {r["state"] for r in out}
    if satisfied and not alive and out and states == {"installed"}:
        # Persist it: the next boot must not start a download for a seat the
        # same model already fills (run_at_boot reads the request file).
        _write_status({"seats": out})
        _retire_request()
    if out and states == {"installed"}:
        state = "done"
    elif "failed" in states and not alive:
        state = "failed"
    else:
        state = "running"
    return {"state": state, "seats": out, "problems": req["problems"]}


# ── the work ────────────────────────────────────────────────────────────────

def _run_front(row: dict, rows: list) -> None:
    """Fetch the chosen voice front, the speech ear and sherpa-onnx through
    the voice installer's artifact path (pinned revision, sha256 checked, a
    stopped transfer resumes), then set ``voice_front_model``."""
    from agent_friday.services import voice_artifacts as va
    from agent_friday.services import voice_installer as vi

    row["state"] = "downloading"
    row["artifacts_done"] = 0
    for aid in front_artifacts(row["model_id"]):
        if vi.artifact_installed(aid):
            row["artifacts_done"] += va.size_bytes(aid)
            continue
        vi._install_artifact(aid)
        row["artifacts_done"] += va.size_bytes(aid)
        _write_status({"seats": rows})
    row["state"] = "installed"
    row["bytes_done"] = row["bytes_total"]
    apply_seat("fast_responder", row["model_id"])


def _runtime_problem_for(model_id: str) -> Optional[str]:
    """Why the runtime this shortlist model needs cannot serve, or None."""
    try:
        from agent_friday.services import model_download as md
        from agent_friday.services import model_shortlist as sl
        if (sl.get(model_id) or {}).get("runtime") != "prism-fork":
            return None
        return md.installed_runtime_problem("prism-fork")
    except Exception as e:  # noqa: BLE001 - a check that cannot run is not a finding
        log.debug("first run: runtime check skipped: %s", e)
        return None


def _run(req: dict) -> None:
    from agent_friday.services import model_download as md
    from agent_friday.services import model_store

    rows: list = []
    for seat in ORDER:
        want = req["seats"].get(seat)
        if want:
            rows.append(_entry(seat, want["model_id"], want.get("packing")))
    _write_status({"seats": rows, "started_at": time.time()})

    for row in rows:
        want = req["seats"][row["seat"]]
        try:
            if row["seat"] == "fast_responder":
                _run_front(row, rows)
                _write_status({"seats": rows})
                continue
            have = (row["model_id"] if row["model_id"] in model_store.available()
                    else installed_equivalent(row["model_id"]))
            if have:
                # Weights on disk are not a working seat: a runtime from the
                # launch release unpacked without its server, so the seat could
                # never start and the card never cleared. Repair it first.
                _rt_problem = _runtime_problem_for(row["model_id"])
                if _rt_problem:
                    log.warning("first run: %s", _rt_problem)
                    row["state"], row["phase"] = "downloading", "runtime"
                    _write_status({"seats": rows})
                    md.repair_runtime()
                # Installed already, perhaps under another spelling
                # (bonsai2-27b for bonsai2:27b): seat the record that exists.
                if have != row["model_id"]:
                    _note_once(row["seat"], row["model_id"], have, "installed under an equivalent id")
                    row["served_by"] = have
                row["state"] = "installed"
                row["bytes_done"] = row["bytes_total"]
                apply_seat(row["seat"], have)
                _write_status({"seats": rows})
                continue
            served = seat_served_by(row["seat"], row["model_id"])
            if served and model_ids.same(served, row["model_id"]):
                # The requested model, under another name, already serves
                # this seat: nothing to fetch, and the seat is pointed at it.
                _note_once(row["seat"], row["model_id"], served, "the same model serves the seat")
                row.update(state="installed", served_by=served, bytes_done=row["bytes_total"])
                apply_seat(row["seat"], served)
                _write_status({"seats": rows})
                continue
            if served:
                # The owner's own, different model serves the seat. The card
                # names it, but the download that was asked for still runs:
                # cancelling it is the owner's choice, not ours.
                _note_once(row["seat"], row["model_id"], served,
                           "the owner's configured model serves the seat; the requested download still runs")
                row["served_by"] = served
            row["state"] = "downloading"
            # No thread of its own: the next model is queued after this one
            # has registered, so the runtime this one installs is already
            # there when the next is registered against it.
            job = md.start_model(row["model_id"], want.get("packing"), start_thread=False,
                                 with_companions=bool(want.get("with_companions", True)),
                                 serve_override=want.get("serve"))
            if job.get("status") == "refused":
                row["state"], row["error"] = "failed", job.get("error")
                _write_status({"seats": rows})
                continue
            row["job_id"] = job["id"]
            _write_status({"seats": rows})
            final = md.run_job(job["id"]) or {}
            if final.get("status") == "installed":
                row["state"] = "installed"
                row["bytes_done"] = row["bytes_total"]
                apply_seat(row["seat"], row["model_id"])
            else:
                row["state"] = "failed"
                row["error"] = final.get("error") or ("download %s" % final.get("status"))
        except Exception as e:  # noqa: BLE001 - one model failing must not stop the other
            log.warning("first run: %s failed: %s", row["model_id"], e)
            row["state"], row["error"] = "failed", "%s: %s" % (type(e).__name__, e)
        _write_status({"seats": rows})

    if rows and all(r["state"] == "installed" for r in rows):
        _retire_request()


def _retire_request() -> None:
    """Move the request to the done marker, so no later boot starts it again."""
    src, dst = request_path(), _sibling(DONE_NAME)
    try:
        if src and dst and src.exists():
            os.replace(src, dst)
            log.info("first run: every seat is filled; the request is retired to %s", dst.name)
    except OSError as e:
        log.warning("first run: could not retire the request file: %s", e)


def run_at_boot(*, blocking: bool = False) -> dict:
    """Start the pending first-run download, if any. Safe to call every boot:
    with no request file, or one already finished, it does nothing.

    Returns the same shape as ``status()``.
    """
    global _WORKER
    req = load_request()
    if req is None or req["cloud"] or not req["consent"] or not req["seats"]:
        return status()
    with _LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return status()
        t = threading.Thread(target=_run, args=(req,), daemon=True, name="first-run-models")
        _WORKER = t
        t.start()
    if blocking:
        t.join()
    return status()
