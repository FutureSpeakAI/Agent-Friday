"""
footprint_measure — the reproducible measurement job (headroom.md §5.3,
§12 Phase 2 item 4e: "friday measure <model_id>").

Runs ONE real generation (image) or ONE real `load()` (voice), under the
SAME path every normal request already takes — `local_image.generate()`'s
Arbiter `image_job` lease for image, `local_voice.WhisperASR.load()` /
`PiperTTS.load()` for voice — samples what it actually cost, and records the
result as a `residency_catalog.Footprint` with `basis="measured"` and
`measured_at` set.

**HR6 — an idle reading is never written as a footprint.** Every function
below either (a) actually runs the model and records what it measured, or
(b) returns a `status: "blocked"` result and calls `record_footprint`
nowhere in that path. There is no code path here that writes a Footprint
without first producing a real sample; `test_footprint_measure.py` pins this
directly by monkeypatching the underlying call to prove it never ran and
asserting the store stays empty.

**No parallel GPU path.** Image measurement reuses `local_image.generate()`
exactly — the same Arbiter `image_job` lease every ordinary image request
already takes (headroom.md §12 Phase 2 item 3: "use the existing path, do
not build a parallel one"). This module adds only: a courtesy nvidia-smi
read before touching anything, a background VRAM sampler *around* the
existing call (never a second lease, never a second ComfyUI start), and the
bookkeeping to turn what came back into a Footprint.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

_log = logging.getLogger("friday.footprint_measure")


# ── image ────────────────────────────────────────────────────────────────

def _gpu_baseline():
    """A read-only nvidia-smi sample, before anything is touched. Never
    raises; `None` when nvidia-smi cannot answer (`machine_monitor`'s own
    rule: cannot verify, never plenty)."""
    from agent_friday.services import machine_monitor as mm
    rows = mm.gpu_rows(fresh=True)
    return rows[0] if rows else None


def _poll_gpu(samples: list, stop: threading.Event, interval_s: float):
    """Background sampler — the "sample nvidia-smi at the render's midpoint,
    not just before/after" requirement (§12 Phase 2 item 4b), done as a
    continuous poll for the whole job rather than one guessed midpoint."""
    from agent_friday.services import machine_monitor as mm
    while not stop.is_set():
        row = _gpu_baseline()
        if row is not None:
            samples.append({"ts": time.time(), **row})
        if stop.wait(interval_s):
            break


def _installed_bytes(model_id: str) -> int | None:
    """Sum of the on-disk weight files for `model_id`, from the same
    `MODELS[...]["files"]` list `is_installed()` checks — the artifact_bytes
    field, read rather than guessed."""
    from agent_friday.services import local_image as li
    spec = li.MODELS.get(model_id)
    if not spec:
        return None
    total = 0
    for sub, name in spec["files"]:
        p = li.comfy_root() / "models" / sub / name
        try:
            total += p.stat().st_size
        except OSError:
            return None
    return total


def _licence_record(model_id: str) -> dict | None:
    """`local_image.MODELS[...]["licence"]` generalised into the Footprint
    shape (§12 Phase 2 item 5). A model with no `licence` key returns
    `None` — "licence not recorded" (§5.2), not a guess. A trailing
    parenthesised URL (the citation a licence claim needs when it was
    looked up rather than declared by the codebase itself) is pulled out
    into its own field rather than left buried in the note text."""
    import re as _re
    from agent_friday.services import local_image as li
    text = (li.MODELS.get(model_id) or {}).get("licence")
    if not text:
        return None
    m = _re.search(r"\(?(https?://\S+?)\)?\s*$", text)
    url = m.group(1) if m else None
    note = text[:m.start()].rstrip(" (") if m else text
    name = note.split("—")[0].strip() if "—" in note else note
    return {"name": name, "note": note, "url": url}


def measure_image_model(model_id: str, *, prompt: str | None = None,
                        arbiter=None, poll_interval_s: float = 2.0) -> dict:
    """Run one real generation for `model_id` and record a measured
    Footprint. Returns `{"status": "measured", "footprint": {...}, ...}` on
    success, or `{"status": "blocked", "reason": "..."}` — and records
    nothing — when the weights are not installed or the run did not produce
    an image.
    """
    from agent_friday.services import local_image as li
    from agent_friday.services import residency_catalog as rc
    from agent_friday.services import hardware_profile as hwp

    if not li.is_installed(model_id):
        return {"status": "blocked", "model_id": model_id,
               "reason": "%s's weights are not installed under %s — this "
                        "job never downloads them; install first, then "
                        "measure" % (model_id, li.comfy_root())}

    profile = hwp.get()
    if arbiter is None:
        from agent_friday.services.residency_arbiter import Arbiter
        arbiter = Arbiter(profile=profile)
    arbiter.compute_plan()

    baseline = _gpu_baseline()
    resident_before = dict(arbiter.ollama.resident() or {})

    samples: list = []
    stop = threading.Event()
    poller = threading.Thread(target=_poll_gpu, args=(samples, stop,
                                                       poll_interval_s),
                              daemon=True)
    poller.start()
    t0 = time.time()
    try:
        result = li.generate(
            prompt=prompt or ("a friday measurement job: a simple still "
                              "life photograph on a wooden table"),
            system=True, model=model_id, arbiter=arbiter)
    finally:
        stop.set()
        poller.join(timeout=5.0)
    total_s = time.time() - t0

    # Cleanup — the courtesy the GPU-access unblock asks for: leave nothing
    # resident that this job loaded and the machine did not already have.
    # `generate()`'s own `release()` restores the ARBITER'S plan (interactive
    # brain + sidekick), which is correct for the LIVE server's arbiter but
    # not for a standalone measurement process with no plan to keep warm —
    # so anything resident now that was not resident before this call is
    # evicted straight back off, and the card is verified idle again by the
    # caller (`friday measure` prints before/after nvidia-smi for exactly
    # that check).
    try:
        resident_after = dict(arbiter.ollama.resident() or {})
        for name in resident_after:
            if name not in resident_before:
                arbiter.ollama.evict(name)
    except Exception as e:
        _log.warning("footprint_measure: cleanup eviction failed: %s", e)

    if result.get("status") != "ok" or not result.get("files"):
        return {"status": "blocked", "model_id": model_id,
               "reason": "generation did not complete: %s"
                        % (result.get("reason") or result.get("status")),
               "raw": result}

    load_s = None
    for t in reversed(arbiter.transitions):
        if t.get("action") == "start" and t.get("role") == "image":
            load_s = t.get("seconds")
            break
    elapsed_s = result.get("elapsed_s")
    render_s = (round(elapsed_s - load_s, 1)
               if elapsed_s is not None and load_s is not None else None)

    peak_used = max((s.get("used_mib") or 0) for s in samples) \
        if samples else None
    base_used = (baseline or {}).get("used_mib")
    vram_mib = (peak_used - base_used
               if peak_used is not None and base_used is not None else None)

    spec = li.model_spec(model_id)
    fp = rc.make_footprint(
        modality="image", device="gpu", basis="measured",
        vram_mib=vram_mib,
        artifact_bytes=_installed_bytes(model_id),
        load_s=load_s,
        unit="image",
        work_s_per_unit=render_s,
        licence=_licence_record(model_id),
        quality_note=spec.get("note"),
        measured_at=time.strftime("%Y-%m-%d"),
    )
    rc.record_footprint(model_id, rc.profile_fingerprint(profile), fp)
    return {"status": "measured", "model_id": model_id, "footprint": fp,
           "total_s": round(total_s, 1), "samples_taken": len(samples),
           "baseline_used_mib": base_used, "peak_used_mib": peak_used}


# ── voice (CPU host RAM, §12 Phase 2 item 2 / U6) ───────────────────────────

def _rss_mib() -> float:
    import psutil
    return psutil.Process().memory_info().rss / 1048576.0


def _whisper_installed(model_size: str) -> bool:
    """Best-effort: are `model_size`'s faster-whisper (CTranslate2) files
    already on disk under `WHISPER_DIR`? Never triggers a download to find
    out — an empty or absent directory answers `False` directly."""
    from agent_friday.services import local_voice as lv
    if not lv.WHISPER_DIR.is_dir():
        return False
    # faster-whisper caches under a HF-style `models--...` snapshot dir; a
    # directory whose name mentions the model size is good enough evidence
    # without importing faster_whisper (which would itself risk a network
    # probe on some versions).
    return any(model_size.lower() in p.name.lower()
              for p in lv.WHISPER_DIR.rglob("*") if p.is_dir())


def _piper_installed(voice: str) -> bool:
    from agent_friday.services import local_voice as lv
    tts = lv.PiperTTS(voice=voice)
    path = tts._voice_path()
    cfg = path.with_suffix(path.suffix + ".json")
    return path.exists() and cfg.exists()


def measure_voice_host_ram(*, whisper_model: str | None = None,
                           piper_voice: str | None = None) -> dict:
    """RSS delta of THIS process across `WhisperASR.load()` and
    `PiperTTS.load()` — U6. Measures only what is already installed; a
    component whose files are not present is reported `blocked` and nothing
    is recorded for it (HR6 — and disk is tight, so this never downloads
    just to get a number, per the Phase 2 instruction).
    """
    from agent_friday.services import local_voice as lv
    from agent_friday.services import residency_catalog as rc
    from agent_friday.services import hardware_profile as hwp

    whisper_model = whisper_model or lv.DEFAULT_WHISPER_MODEL
    piper_voice = piper_voice or lv.DEFAULT_PIPER_VOICE
    profile = hwp.get()
    fp_key = rc.profile_fingerprint(profile)
    today = time.strftime("%Y-%m-%d")
    out = {}

    if not _whisper_installed(whisper_model):
        out["stt"] = {"status": "blocked", "model": whisper_model,
                      "reason": "faster-whisper '%s' is not present under "
                               "%s — this job never downloads it"
                               % (whisper_model, lv.WHISPER_DIR)}
    else:
        rss0 = _rss_mib()
        asr = lv.WhisperASR(model_size=whisper_model)
        asr.load()
        delta = round(_rss_mib() - rss0, 1)
        stt_id = "faster-whisper-%s-int8" % whisper_model
        footprint = rc.make_footprint(
            modality="stt", device="cpu", basis="measured",
            vram_mib=0, host_ram_mib=delta, unit="second_audio",
            measured_at=today)
        rc.record_footprint(stt_id, fp_key, footprint)
        out["stt"] = {"status": "measured", "model_id": stt_id,
                      "host_ram_mib": delta, "footprint": footprint}

    if not _piper_installed(piper_voice):
        out["tts"] = {"status": "blocked", "voice": piper_voice,
                      "reason": "Piper voice '%s' is not present under %s "
                               "— this job never downloads it"
                               % (piper_voice, lv.PIPER_DIR)}
    else:
        rss0 = _rss_mib()
        tts = lv.PiperTTS(voice=piper_voice)
        tts.load()
        delta = round(_rss_mib() - rss0, 1)
        tts_id = "piper-%s" % piper_voice
        footprint = rc.make_footprint(
            modality="tts", device="cpu", basis="measured",
            vram_mib=0, host_ram_mib=delta, unit="second_audio",
            measured_at=today)
        rc.record_footprint(tts_id, fp_key, footprint)
        out["tts"] = {"status": "measured", "model_id": tts_id,
                      "host_ram_mib": delta, "footprint": footprint}

    return out


# ── dispatch, for the CLI ───────────────────────────────────────────────────

def measure(model_id: str) -> dict:
    """`friday measure <model_id>` — routes by what kind of id this is.

    Voice is not addressed by a single model_id the way image is (it is
    always the CPU pair), so the two voice sentinels `"stt"` / `"tts"` /
    `"voice"` measure both engines together; anything else is treated as an
    image model id and checked against `local_image.MODELS`.
    """
    from agent_friday.services import local_image as li
    if model_id in ("stt", "tts", "voice"):
        return measure_voice_host_ram()
    if model_id in li.MODELS:
        return measure_image_model(model_id)
    return {"status": "blocked", "model_id": model_id,
           "reason": "not a known image model id (%s) and not 'voice' — "
                    "nothing to measure" % ", ".join(sorted(li.MODELS))}
