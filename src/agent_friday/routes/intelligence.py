"""One read-only view behind both model surfaces.

The top-bar quick switch and Settings → Models used to assemble
themselves from whatever each happened to fetch, which is how the same model
ended up listed twice with the selected dot on both copies, and how an
automatic-speech-recognition model ended up offered as the orchestrator.

This composes the four live sources — the catalogue, the residency plan, the
machine, and what has actually been billed — into one payload, deduplicated
once, so the two surfaces cannot disagree with each other.

Nothing here is curated. Every list is derived from what the machine reports
right now; there are no hardcoded model names and no fallback lists.
"""
from __future__ import annotations

import os
import sqlite3
import time

from flask import Blueprint, jsonify
from agent_friday.paths import friday_home

intelligence_bp = Blueprint("intelligence", __name__)


# ── Capability model ─────────────────────────────────────────────────────────
#
# A job is described by the MODALITY it requires, never by a list of approved
# models. That is the whole fix for the orchestrator picker offering Whisper:
# running an agentic loop requires "tools", and an ASR model does not declare
# it, so it is shown as unsuitable-for-this-job with the reason stated rather
# than being silently absent.
#
# The labels are the words a user uses for these jobs, not the internal keys.

ROLE_SPEC = [
    # (key, label, catalogue role, required modality, help)
    #
    # TWO signals, because neither alone is sufficient on this machine:
    #
    #   - `modalities` is authoritative for media work (image / video / audio),
    #     but every local Ollama model declares only ["text"] — none advertise
    #     "tools" even though they demonstrably run tool loops. Filtering the
    #     orchestrator on modality alone would hide EVERY local model, which is
    #     worse than the bug being fixed.
    #   - `roles` is what the catalogue says a model is FOR, and it is correct
    #     where it matters here: Whisper and Piper carry roles [], Z-Image
    #     carries ["creative"], the chat models carry ["orchestrator"].
    #
    # A model qualifies if EITHER signal says so. That admits every model that
    # can really do the job and still excludes speech and image models from the
    # seat that runs your conversation.
    ("reasoning",      "Everyday conversation", "orchestrator", "tools",
     "The model that answers you in chat and can use tools."),
    ("heavy_hitter",   "Deep thinking (on demand)", "orchestrator", "tools",
     "Long, hard problems where you will wait for a better answer. Loaded "
     "only while it works, and it replaces the everyday model on this card."),
    ("local",          "Quick reflexes",        "orchestrator", "tools",
     "The small local model that stays awake for fast replies."),
    ("subagent",       "Background & research", "subagent",     "tools",
     "Runs commissions and background work while you do other things."),
    ("memory_manager", "Memory keeper",         "orchestrator", "tools",
     "Reads the day and decides what is worth keeping. Local only."),
    ("creative_image", "Images",                "creative",     "image",
     "Generates pictures."),
    ("creative_video", "Video",                 "creative",     "video",
     "Generates moving images."),
    ("creative_music", "Music",                 "creative",     "music",
     "Generates audio compositions."),
    ("voice",          "Live voice",            "voice",        "live",
     "Real-time spoken conversation. Chosen on the Voice tab."),
    # REMOVED 2026-09-17 (docs/design/active/model-soup.md §11.2 C):
    # orchestrator, sidekick_fast, function_manager, researcher, asr, tts and
    # embedding. `services/role_consumers.py` finds no reader for the first
    # six, and `embedding` is read only for a badge: the embedder is the
    # module constant all-MiniLM-L6-v2, in-process on CPU. A picker with no
    # reader is a lie about control (docs/decisions/2026-09-04-five-dead-
    # settings.md); the card renders embeddings as one fixed line instead.
]

#: The seats that stay resident on the card. Everything else is leased or
#: wakes on demand.
DEAD_ROLE_KEYS = ("orchestrator", "sidekick_fast", "function_manager",
                  "researcher", "asr", "tts", "embedding")

# capability key -> residency class, from the contract's 3.
#
# THIS IS THE POINT OF SHOWING IT. Thirteen roles without their class reads as
# thirteen models resident at once, which on a 12 GB card looks impossible and
# is not: only the conversational tier stays warm, and seven roles routinely
# fit in three or four models. Sent to the client so the page can group by it
# rather than listing thirteen equal-looking seats.
_RESIDENCY_FOR_ROLE = {
    "reasoning": "resident", "local": "resident",
    "heavy_hitter": "leased", "subagent": "leased",
    "creative_image": "leased", "creative_video": "leased",
    "creative_music": "leased",
    "memory_manager": "on-demand", "voice": "on-demand",
}

RESIDENCY_HELP = {
    "resident": "Warm all day. These hold VRAM continuously.",
    "leased": "Loaded only while the work runs, then released.",
    "on-demand": "Wakes when needed, sleeps again. Costs nothing idle.",
}

# Seat names in the residency plan do not match capability keys one-for-one.
_SEAT_FOR_ROLE = {
    "reasoning": "interactive_brain",
    "heavy_hitter": "heavy_hitter",
    "local": "sidekick",
    "subagent": "sidekick_heavy",
    "creative_image": "image",
}


def _gb(mib):
    try:
        v = float(mib) / 1024.0
    except Exception:
        return None
    return ("%.1f" % v).rstrip("0").rstrip(".") + " GB"


def _pretty_model(mid: str) -> str:
    """`hf.co/HauhauCS/Gemma-4-E4B-...:Q4_K_M` -> `Gemma 4 E4B`.

    The server-side twin of index.html's prettyModel, and it exists because
    these strings now appear inside SENTENCES rather than in a monospace list.
    A raw tag is tolerable as a row in a table and is not tolerable in
    "X does not fit the card for Memory keeper".
    """
    import re as _re
    if not mid:
        return ""
    s = str(mid)
    s = _re.sub(r"^hf\.co/", "", s, flags=_re.I)
    s = _re.sub(r"^[^/]+/", "", s)
    s = _re.sub(r":(Q\d[^:]*|f?p?\d{1,2}|latest)$", "", s, flags=_re.I)
    s = _re.sub(r"-(GGUF|QAT|AWQ|GPTQ)$", "", s, flags=_re.I)
    s = _re.sub(r"-(Uncensored|HauhauCS|Balanced|Aggressive|Instruct|Chat)\b", "",
                s, flags=_re.I)
    s = _re.sub(r"[-_]+", " ", s)
    s = _re.sub(r"\s+", " ", s).strip()
    return s[:44] + "…" if len(s) > 46 else s


def _humanise_refusal(r: dict, role_label) -> dict:
    """Turn one planner refusal into something a person can act on.

    Every surviving warning owes the reader three things: what is wrong, why
    that matters in plain words, and what to do about it. The panel used to
    render `prettyModel(model) + " for " + role + " — " + explanation`, which
    put the planner's own arithmetic on screen: "override needs 7814 MiB but
    only 0 MiB is available on the largest GPU after the other pinned seats".
    Every word true, and it tells someone who is not the author of the
    residency policy nothing they can do.

    If a rule cannot say what to do, it says so rather than inventing advice.
    That is deliberate: a fabricated remedy is worse than an admitted gap,
    because the reader spends their evening on it.
    """
    rid = (r.get("rule_id") or "").upper()
    who = role_label or (r.get("role") or "a seat")
    model = _pretty_model(r.get("model") or "")
    need = _gb(r.get("need_mib") or r.get("vram_mib"))
    have = _gb(r.get("headroom_mib"))
    raw = r.get("explanation") or ""

    if rid == "R3":
        return {
            "title": "%s does not fit the card for %s" % (model or "That model", who),
            "why": ("There is not enough free video memory for it beside the "
                    "models already loaded."
                    + (" It needs about %s and about %s is free." % (need, have)
                       if need and have else "")),
            "action": ("Choose a smaller model for %s, or give a larger seat "
                       "a smaller model to free the card." % who),
            "severity": "problem",
        }
    if rid == "R6" and "not installed" in raw:
        return {
            "title": "%s is not available to this seat" % (model or "That model"),
            "why": ("%s is filled by a model running on this machine, and this "
                    "one is not one of them." % who),
            "action": ("Pick a local model for %s, or leave it empty." % who),
            "severity": "problem",
        }
    if rid == "R6":
        return {
            "title": "%s cannot do the job %s needs" % (model or "That model", who),
            "why": raw or "The model does not have the right capabilities.",
            "action": "Choose a different model for this seat.",
            "severity": "problem",
        }
    if rid == "R5":
        # R5 is the exclusive-GPU-lease rule, and BOTH the image seat and the
        # video seat refuse under it. This branch used to render the image
        # sentence for either one, so the video refusal -- which
        # residency_policy emits unconditionally on every profile, because no
        # local video backend exists anywhere in the tree -- displayed as
        # "Image generation cannot run on this machine" on a machine with a
        # perfectly good 12GB card. Stephen reported exactly that on
        # 2026-09-10. Read the role before writing the sentence.
        role = str(r.get("role") or "").lower()
        if role == "video":
            return {
                "title": "Video generation runs in the cloud",
                "why": ("Friday has no local video generator — there is no "
                        "such backend in the build, on any machine. This is "
                        "not about your hardware."),
                "action": "Nothing to do. Video requests go to a cloud provider.",
                "severity": "info",
            }
        if role == "image":
            return {
                "title": "Image generation cannot be placed on the GPU right now",
                "why": ("No GPU budget was free for an exclusive image lease. "
                        "Image models need the card to themselves, so a seat "
                        "already holding video memory blocks them."
                        + (" It needs about %s and about %s is free."
                           % (need, have) if need and have else "")),
                "action": ("Free the card — unload the model holding it — or "
                           "let this image render in the cloud."),
                "severity": "info",
            }
        return {
            "title": "%s needs the GPU to itself" % (who[:1].upper() + who[1:]),
            "why": raw or ("This job takes an exclusive GPU lease and the card "
                           "is not free."),
            "action": "Free the card, or run this job in the cloud.",
            "severity": "info",
        }
    if rid == "R2":
        return {
            "title": "%s would use too much system memory" % (model or "That model"),
            "why": raw or "It would push system memory past its safe ceiling.",
            "action": "Choose a smaller model, or close other applications.",
            "severity": "problem",
        }
    if rid == "R8":
        return {
            "title": "Not enough free disk to load %s" % (model or "that model"),
            "why": raw or "Loading it would take free disk below the floor.",
            "action": "Free some disk space, then try again.",
            "severity": "problem",
        }
    if rid == "R11":
        return {
            "title": "%s has no model yet" % who,
            "why": "This seat is yours to choose. Friday will not pick one for you.",
            "action": "Pick a model for it, or leave it empty — it is optional.",
            "severity": "choice",
        }
    return {
        "title": "%s could not be seated" % who,
        "why": raw or "The planner refused this placement.",
        "action": "",
        "severity": "problem",
    }


# ── Monitor verdicts, humanised (headroom.md §8.3, §12 Phase 4 item 3) ──────
#
# `_humanise_refusal` above turns a PLANNER refusal into something a person
# can act on. This is the same job for a MACHINE_MONITOR verdict — a live
# reading, not a placement decision — so THE MACHINE's three groups can
# carry both kinds of row without the page inventing a third vocabulary.
# Only `disk_system` and `thrash` are wired here (H-DISK-SYS, H-THRASH):
# `vram_slack` and `ram_available` stay unrendered because their own verdict
# is `basis: "unknown"` on every machine today (D1 is not decided) and a row
# built from an unknown basis is exactly the phantom control HR1 exists to
# refuse — "wire them once D1 lands, don't fake a threshold now" per this
# phase's own instructions.
def _humanise_monitor_verdict(resource: str, v: dict) -> dict | None:
    status = (v or {}).get("status")
    if status not in ("at_risk", "breached"):
        return None
    why = v.get("explanation") or ""
    if resource == "disk_system":
        return {
            "rule_id": "H-DISK-SYS",
            "title": "Free space on the system drive is critically low",
            "why": why,
            "action": "Free up space on the drive Windows is installed on "
                      "(the pagefile and Friday's own data live there "
                      "whatever the model store points at). Friday will "
                      "refuse new loads and fetches until this clears.",
            "severity": "problem",
        }
    if resource == "thrash":
        if status == "breached":
            return {
                "rule_id": "H-THRASH",
                "title": "Something is thrashing the graphics card, not "
                         "just using it",
                "why": why,
                "action": "Close whatever else is drawing on the GPU (a "
                          "game, a render, another AI tool) or wait for it "
                          "to finish. Friday will not start a new local "
                          "load until this clears.",
                "severity": "problem",
            }
        return {
            "rule_id": "H-THRASH",
            "title": "A local model is answering slower than usual",
            "why": why,
            "action": "Nothing to do yet — this is early notice, not a "
                      "refusal. If it gets worse the affected model will "
                      "be marked degraded here.",
            "severity": "info",
        }
    return None


#: Ledger provider names that mean "ran on this machine". The ledger's
#: provider column is coarse ("local", "arbiter-local", "fridayweaver-seat")
#: and does not line up with catalogue names, so the test is by shape.
def _ledger_provider_is_local(prov) -> bool:
    p = str(prov or "").lower()
    return (p in ("local", "arbiter-local", "ollama-local", "ollama",
                  "local-voice-lite", "local-voice")
            or "local" in p or p.endswith("-seat"))


def _costs_rollup():
    """What has actually been served, per provider and per model.

    Read-only against the existing cost ledger. This is the column nobody
    builds: a provider that is configured but has never served anything says
    so, instead of looking identical to one doing all the work.
    """
    path = str(friday_home() / "costs.db")
    out = {"providers": {}, "models": {}, "serving": None}
    if not os.path.exists(path):
        return out
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    except Exception:
        return out
    try:
        cutoff = time.time() - 7 * 86400
        for prov, calls, last_ts, cost in con.execute(
                "SELECT provider, COUNT(*), MAX(ts), COALESCE(SUM(cost_usd),0) "
                "FROM cost_calls GROUP BY provider"):
            out["providers"][prov or "?"] = {
                "calls": calls, "last_ts": last_ts, "cost_usd": round(cost or 0.0, 4)}
        for prov, calls, cost in con.execute(
                "SELECT provider, COUNT(*), COALESCE(SUM(cost_usd),0) FROM cost_calls "
                "WHERE ts > ? GROUP BY provider", (cutoff,)):
            p = out["providers"].setdefault(prov or "?", {})
            p["calls_7d"] = calls
            p["cost_7d"] = round(cost or 0.0, 4)
        for model, calls, last_ts in con.execute(
                "SELECT model, COUNT(*), MAX(ts) FROM cost_calls GROUP BY model"):
            if model:
                out["models"][model] = {"calls": calls, "last_ts": last_ts}
        # The pill must name the model that served the LAST ACTUAL TURN, not
        # the one configured in settings — those drift apart, and the whole
        # point of the pill is answering "who is answering me".
        row = con.execute(
            "SELECT model, provider, ts FROM cost_calls WHERE kind='chat' "
            "ORDER BY ts DESC LIMIT 1").fetchone()
        if row:
            out["serving"] = {"model": row[0], "provider": row[1], "at": row[2]}
        # The Model Soup card's ledger row (model-soup.md §11.2 A): how many
        # model calls ran here and how many left, over the last day and the
        # last month, and what the cloud share cost. This is the acceptance
        # test for the whole local lineup, so it is computed from the ledger
        # every time rather than estimated.
        out["ledger"] = {}
        now = time.time()
        for label, span in (("24h", 86400), ("30d", 30 * 86400)):
            acc = {"calls": 0, "local": 0, "cloud": 0, "cost_usd": 0.0}
            for prov, calls, cost in con.execute(
                    "SELECT provider, COUNT(*), COALESCE(SUM(cost_usd),0) "
                    "FROM cost_calls WHERE ts > ? AND kind IN "
                    "('chat','text','scheduled','voice','creative') "
                    "GROUP BY provider", (now - span,)):
                acc["calls"] += calls
                if _ledger_provider_is_local(prov):
                    acc["local"] += calls
                else:
                    acc["cloud"] += calls
                    acc["cost_usd"] += float(cost or 0.0)
            acc["cost_usd"] = round(acc["cost_usd"], 2)
            out["ledger"][label] = acc
        # Typical price of one cloud chat turn: the 30-day median, which is
        # what "Think in the cloud" quotes before the click.
        costs_30d = [float(r[0] or 0.0) for r in con.execute(
            "SELECT cost_usd FROM cost_calls WHERE ts > ? AND kind='chat' "
            "AND COALESCE(cost_usd,0) > 0 ORDER BY cost_usd",
            (now - 30 * 86400,))]
        if costs_30d:
            mid = len(costs_30d) // 2
            med = (costs_30d[mid] if len(costs_30d) % 2 else
                   (costs_30d[mid - 1] + costs_30d[mid]) / 2.0)
            out["ledger"]["cloud_turn_median_usd"] = round(med, 4)
    except Exception:
        pass
    finally:
        try:
            con.close()
        except Exception:
            pass
    return out


#: How long a successful model list is reused. The set of PULLED models changes
#: when somebody pulls one, not between two renders of a panel that polls on a
#: timer, so this is short enough to feel live and long enough to stop the poll
#: paying for the same answer.
_OLLAMA_OK_TTL_S = 25.0
#: How long a refusal is remembered. Measured on Windows: connecting
#: to a closed localhost port costs ~2,005 ms (the stack retries the SYN before
#: giving up) and a black-holed address costs the full 3,000 ms timeout. Not the
#: microseconds loopback suggests. With the panel polling and no memory of the
#: failure, a machine with Ollama switched off paid that on every render against
#: a client abort of twelve seconds.
#:
#: Backoff rather than removal: the daemon can be started at any moment, so the
#: probe must keep trying — it must simply not re-learn the same "no" at full
#: price several times a minute.
_OLLAMA_DOWN_BACKOFF_S = 20.0

#: (checked_at, sizes_or_None). None means the last probe failed.
_OLLAMA_CACHE = (0.0, None)


def reset_ollama_probe_state_for_tests():
    global _OLLAMA_CACHE
    _OLLAMA_CACHE = (0.0, None)


def _ollama_sizes():
    """On-disk size per local model — the basis for the wake estimate.

    Cached both ways. A success is reused briefly; a failure is remembered for
    longer, because a failure is the expensive one.
    """
    global _OLLAMA_CACHE
    now = time.time()
    stamp, hit = _OLLAMA_CACHE
    if hit is not None and (now - stamp) < _OLLAMA_OK_TTL_S:
        return hit
    if hit is None and stamp and (now - stamp) < _OLLAMA_DOWN_BACKOFF_S:
        # Known down, recently. Answer without touching the socket.
        return {}
    try:
        from agent_friday.routing.ollama_manager import OLLAMA_HOST  # type: ignore
        host = OLLAMA_HOST
    except Exception:
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    sizes = {}
    try:
        import requests
        r = requests.get(f"{host.rstrip('/')}/api/tags", timeout=3)
        for m in (r.json() or {}).get("models", []):
            if m.get("name"):
                sizes[m["name"]] = int(m.get("size") or 0)
    except Exception:
        # Record the failure, not an empty success: an empty dict cached as a
        # hit would look like "Ollama is running and has no models", which is a
        # different and wronger thing to display.
        _OLLAMA_CACHE = (now, None)
        return {}
    _OLLAMA_CACHE = (now, sizes)
    return sizes


# Measured on this class of machine: an NVMe-resident weight file reaches VRAM
# at roughly 1.1 GB/s once page cache is cold. Reported as an estimate, and
# labelled as one in the UI, because it is one.
_GB_PER_SEC = 1.1


def _wake_estimate_s(size_bytes: int) -> int | None:
    if not size_bytes:
        return None
    return max(1, int(round((size_bytes / 1e9) / _GB_PER_SEC)))


#: What every routing mode does and does not govern. Surfaced next to the mode
#: picker so "Cloud only" cannot be read as a claim about embeddings, which it
#: never was: `conversation_memory.EMBED_MODEL` is a module constant pinned to
#: all-MiniLM-L6-v2 and runs in-process on CPU, consulting no routing setting.
def _vault_policy_status():
    """Resolved vault posture for the Intelligence panel. Never raises."""
    try:
        from agent_friday.privacy import vault_policy
        return vault_policy.status()
    except Exception as exc:
        return {"gated": None, "degraded": None,
                "summary": "Vault posture could not be resolved: %s" % exc}


_MODE_SCOPE_NOTE = (
    "Governs chat, voice and agent turns. Embeddings are always computed "
    "locally on this machine (all-MiniLM-L6-v2, on CPU) and never leave it in "
    "any mode. Vault gating is a separate setting."
)


# ── LOCAL MODELS ON THIS MACHINE (headroom.md §8.2, §12 Phase 4 item 1) ─────
#
# Three axes, never a combined "compatible" (HR2). Every row's fits/
# runs_well/worth_it comes straight from `residency_policy.verdicts()`,
# which itself refuses to invent a basis (HR1) -- this function's only job
# is deciding WHICH rows exist and whether each is installed; it never
# grades one itself.

_VERDICT_RANK = {"refused": 4, "unknown": 3, "degraded": 2,
                 "ready-but": 1, "ready": 0}
_VERDICT_WORD = {"refused": "Refused", "degraded": "Degraded",
                 "ready-but": "Ready, with caveats", "unknown": "Not measured",
                 "ready": "Ready"}
_AXIS_LABEL = {"fits": "Fits", "runs_well": "Runs well", "worth_it": "Worth it"}


def _verdict_summary(v: dict) -> dict:
    """The one permitted one-word reading of three axes: the WORST of them,
    with the axis named (§5.2, "Degraded -- RAM", never a bare 'compatible')."""
    worst_axis, worst_rank = "fits", -1
    for axis in ("fits", "runs_well", "worth_it"):
        status = ((v or {}).get(axis) or {}).get("status")
        rank = _VERDICT_RANK.get(status, 3)
        if rank > worst_rank:
            worst_axis, worst_rank = axis, rank
    if worst_rank <= 0:
        return {"word": "Ready", "axis": None}
    status = (v.get(worst_axis) or {}).get("status")
    return {"word": _VERDICT_WORD.get(status, "Not measured"),
           "axis": _AXIS_LABEL[worst_axis]}


def _embedder_installed() -> bool | None:
    """Best-effort: has `sentence-transformers` already cached
    all-MiniLM-L6-v2, or will first use still pay a 90 MB download
    (`model_plan.EMBEDDER`)? `None` (unknown) rather than a guess when the
    cache cannot be located -- this is an Installed hint, not one of the
    three governed axes, so an honest unknown here costs nothing HR1 cares
    about."""
    try:
        import os as _os
        from pathlib import Path as _Path
        home = _os.environ.get("SENTENCE_TRANSFORMERS_HOME") \
            or _os.environ.get("HF_HOME") \
            or str(_Path.home() / ".cache" / "huggingface")
        base = _Path(home)
        if not base.is_dir():
            return False
        return any("minilm" in p.name.lower() for p in base.rglob("*")
                  if p.is_dir())
    except Exception:
        return None


def local_models_catalog(profile: dict, sizes: dict) -> dict:
    """One row per model Friday knows how to run locally, whether or not it
    is installed yet -- text from `model_plan.BRAIN_MODELS`, image from
    `local_image.MODELS`, video from `local_video.MODELS`, voice from the two
    engines, embed from the catalog (§8.2). `sizes` is the caller's own
    `_ollama_sizes()` result, so this does not re-probe the daemon.
    """
    from agent_friday.services import residency_policy as rp
    from agent_friday.services import local_image as li
    from agent_friday.services import local_video as lvi
    from agent_friday.services import model_plan as mp
    from agent_friday.services import local_voice as lv
    from agent_friday.services import nemo_voice as nv
    from agent_friday.services import footprint_measure as fm

    def row(model_id, modality, label, installed, *, download_gib=None,
           licence=None):
        v = rp.verdicts({"model_id": model_id}, profile)
        return {
            "model_id": model_id, "modality": modality,
            "label": label or _pretty_model(model_id) or model_id,
            "installed": installed,
            "download_gib": download_gib,
            "licence": licence,
            "verdicts": v,
            "summary": _verdict_summary(v),
        }

    text = [row(m["id"], "text", _pretty_model(m["id"]),
               m["id"] in sizes, download_gib=m.get("gib"))
           for m in mp.BRAIN_MODELS]

    image = [row(mid, "image", spec.get("label") or spec.get("short"),
                li.is_installed(mid), licence=spec.get("licence"))
             for mid, spec in li.MODELS.items()]

    # D8's "one sentence, no rows" resolution (video_note below) predates
    # local_video.py -- with nothing able to serve a video job, a row would
    # have been the exact seat-that-serves-nothing defect the comment names.
    # Local video generation now exists (earned-availability Wan/CogVideoX
    # models, same is_installed discipline as image), so it earns the same
    # row treatment image gets rather than staying lumped into one static
    # sentence.
    video = [row(mid, "video", spec.get("label") or spec.get("short"),
                lvi.is_installed(mid), licence=spec.get("licence"))
            for mid, spec in lvi.MODELS.items()]

    voice = [
        row(rp.DEFAULT_STT_MODEL, "stt", "Whisper (small, CPU speech-to-text)",
            fm._whisper_installed(lv.DEFAULT_WHISPER_MODEL)),
        row(rp.DEFAULT_TTS_MODEL, "tts", "Piper (CPU text-to-speech)",
            fm._piper_installed(lv.DEFAULT_PIPER_VOICE)),
        row(nv.NEMO_ASR_MODEL, "stt", "NeMo streaming ASR (GPU speech-to-text)",
            nv.nemo_deps_installed()),
    ]

    embed = [
        row("qwen3-embedding:0.6b", "embed", "Qwen3 Embedding 0.6B",
            "qwen3-embedding:0.6b" in sizes),
        row(mp.EMBEDDER["id"], "embed",
            "all-MiniLM-L6-v2 (bundled sentence-transformers dependency)",
            _embedder_installed()),
    ]

    return {
        "text": text, "image": image, "video": video, "voice": voice, "embed": embed,
    }


# ── The onboarding starting set (headroom.md §9, §12 Phase 6) ───────────────
#
# One call chain -- hardware_profile.get() (the caller's `profile`) ->
# model_plan.plan() for the brain -> plan_chain() for the interview's own
# chain [stt, interactive_brain, tts] and, separately, [..., image] -- per
# section 9's shape verbatim. Reuses `local_models_catalog()`'s row shape
# (Phase 4) for brain/voice/image rather than inventing a second one, so the
# wizard's Hardware Check renders through the SAME `LocalModelRow` component
# Settings does (HR2: no second rendering path, no combined "compatible").
#
# D1 is not decided (`headroom_contract.py`'s own docstring). `contract`
# below reports only what Phase 0 built honestly -- the display reserve --
# never a fabricated working/away/yield level or a VRAM-slack/RAM-available
# floor this document did not build.

def build_starter_set(profile: dict) -> dict:
    from agent_friday.services import model_plan as mp
    from agent_friday.services import residency_policy as rp
    from agent_friday.services import headroom_contract as hc
    from agent_friday.services import local_image as li
    from agent_friday.routing.ollama_manager import get_manager

    sizes = _ollama_sizes()
    lm = local_models_catalog(profile, sizes)

    # What is already on the machine, and what of that can actually hold a
    # conversation -- the same two lookups `cli.py`'s own planning call makes,
    # so a starter set proposed at onboarding cannot disagree with `friday
    # models` run right after it.
    installed = None
    try:
        mgr = get_manager()
        installed = ([m.get("name") for m in (mgr.list_models() or [])]
                    if mgr.is_available() else None)
    except Exception:
        installed = None
    conversational = None
    try:
        from agent_friday.services import local_seats
        conversational = [n for n, _ in local_seats.installed()]
    except Exception:
        pass

    mp_plan = mp.plan(profile, installed=installed, conversational=conversational)
    brain_tier = next((t for t in mp_plan.get("tiers", [])
                       if t.get("id") == "brain"), None)
    brain_id = None
    if brain_tier and brain_tier.get("models"):
        brain_id = brain_tier["models"][0].get("id")
    elif brain_tier and brain_tier.get("alternatives"):
        # Already installed and picked -- `models` is empty because there is
        # nothing left to download, but `alternatives` still names the pick.
        default_alt = next((a for a in brain_tier["alternatives"]
                            if a.get("default")), None)
        brain_id = (default_alt or {}).get("id")
    brain_row = (next((r for r in lm["text"] if r["model_id"] == brain_id),
                      None) if brain_id else None)
    # The FULL LocalModelRow-shaped row when there is one (label, installed,
    # licence, summary -- everything `LocalModelRow` in index.html already
    # knows how to draw), plus the two fields that are §9's own, not that
    # component's: `download_gib` (the WHOLE plan's download, not per-row --
    # `model_plan.plan()`'s tiers never populate a size on the brain row
    # itself) and `why` (the planner's own human sentence for the pick, kept
    # separate from `verdicts` because it explains the CHOICE among
    # alternatives, not any one axis).
    brain = dict(brain_row) if brain_row else {"model_id": brain_id,
                                                "verdicts": None}
    brain["download_gib"] = mp_plan.get("download_gib")
    brain["why"] = (brain_tier or {}).get("reason")

    stt_row = next((r for r in lm["voice"]
                    if r["model_id"] == rp.DEFAULT_STT_MODEL), None)
    tts_row = next((r for r in lm["voice"]
                    if r["model_id"] == rp.DEFAULT_TTS_MODEL), None)
    voice = {"stt": stt_row, "tts": tts_row, "where": "cpu"}

    image_row = next((r for r in lm["image"] if r["model_id"] == li.MODEL_ID),
                     None)
    image_fits = ((image_row or {}).get("verdicts") or {}).get("fits") or {}
    # Full row again (label/installed/licence/summary), same reasoning as
    # `brain` above -- one shape, rendered by one component.
    image = dict(image_row) if image_row else {"model_id": li.MODEL_ID,
                                                "verdicts": None}
    # D8-shaped: no row for a candidate nothing can serve, but a REFUSED or
    # UNMEASURED local row still gets its one honest alternative named
    # (§6.4), the same "never a refusal with no next step" rule the chain
    # planner itself follows.
    image["alternative"] = ("cloud" if image_fits.get("status")
                            in (None, "refused", "unknown") else None)

    try:
        from agent_friday import core
        cloud_ok = bool(getattr(core, "ANTHROPIC_API_KEY", None) or
                        getattr(core, "GEMINI_API_KEY", None))
    except Exception:
        cloud_ok = False

    # Nothing is resident yet at onboarding -- `resident={}` is the honest
    # starting point for "what would running this chain cost from here",
    # distinct from `/api/work/forecast`'s own use of the LIVE arbiter plan
    # for a chain mid-session (`routes/work_plan.py`). `plan_chain` has no
    # notion of "the default brain" the way it does for stt/tts/image
    # (`_chain_default_model`) -- an `interactive_brain` stage with no
    # `model_id` and nothing resident goes straight to cloud (or refuses),
    # per its own rules for `ASSIGNED_ROLES`-shaped roles. Naming `brain_id`
    # explicitly is what makes the interview's chain actually price the
    # model `model_plan.plan()` just picked, rather than silently reporting
    # "no local model available" for a machine that plainly has one.
    brain_stage = {"role": "interactive_brain"}
    if brain_id:
        brain_stage["model_id"] = brain_id
    voice_stages = [{"role": "stt"}, brain_stage, {"role": "tts"}]
    image_stages = [{"role": "stt"}, brain_stage,
                    {"role": "image", "units": 1}, {"role": "tts"}]
    try:
        chain_voice = rp.plan_chain(profile, [], voice_stages, resident={},
                                    cloud_ok=cloud_ok)
    except Exception as e:
        chain_voice = {"error": str(e)}
    try:
        chain_with_image = rp.plan_chain(profile, [], image_stages,
                                         resident={}, cloud_ok=cloud_ok)
    except Exception as e:
        chain_with_image = {"error": str(e)}

    reserve = hc.resolve_display_reserve(profile)

    return {
        "brain": brain,
        "voice": voice,
        "image": image,
        # D8: one sentence, no candidate row (§8.2, §14.2).
        "video": "cloud",
        "chain": {"voice_only": chain_voice, "with_image": chain_with_image},
        # D1 not decided -- the honest subset: the display reserve alone,
        # never a fabricated working/away/yield level (headroom_contract.py).
        "contract": {
            "display_reserve_mib": reserve.get("mib"),
            "basis": reserve.get("basis"),
            "sources": reserve.get("sources"),
            "note": "the full Headroom Contract (working/away/yield levels, "
                    "VRAM-slack and RAM-available floors) is not decided "
                    "(D1); this is the one piece built without it -- the "
                    "live display reserve.",
        },
        "floor_model": mp.FLOOR_MODEL,
    }


# ── The Model Soup card ───────────────────────────────────────────────────────
#
# One object that answers the question the Intelligence tab is for: what is
# about to answer me, where does it run, what will it cost. Every state here
# is derived, never asserted: "proven" means a live endpoint answered just
# now, "cold" means the files are here and nothing serves them, "refused"
# carries the reason the machine gave. docs/design/active/model-soup.md §11.2.

_LOCAL_PROVIDER_HINTS = ("local", "arbiter", "ollama", "seat", "llama")


def _provider_is_local(provider) -> bool:
    p = str(provider or "").lower()
    return any(h in p for h in _LOCAL_PROVIDER_HINTS)


def _seat_state(model_id: str, serving: dict, seat: dict, store_avail: dict,
                store_missing: dict, costs: dict) -> dict:
    """The manifest vocabulary for one local seat: proven / loading / cold /
    refused, with the reason and the evidence."""
    if not model_id:
        return {"state": "refused", "reason": "no model is bound to this seat"}
    base = serving.get(model_id)
    used = (costs.get("models") or {}).get(model_id) or {}
    if base:
        port = None
        try:
            port = int(base.rsplit(":", 1)[1].split("/")[0])
        except Exception:
            pass
        return {"state": "proven", "base": base, "port": port,
                "proven_at": time.time(), "last_served": used.get("last_ts"),
                "reason": ""}
    if seat and (seat.get("absent") or seat.get("pin_unenforced")):
        return {"state": "refused",
                "reason": seat.get("pin_unenforced") or "seat is absent"}
    if model_id in store_avail:
        return {"state": "cold",
                "reason": "weights are on this machine; nothing is serving "
                          "them right now"}
    gone = store_missing.get(model_id)
    if gone:
        return {"state": "refused", "reason": gone.get("why") or "not reachable"}
    return {"state": "refused",
            "reason": "not in Friday's model store and not served by the "
                      "daemon"}


def _weights_rows(store_all: dict, store_avail: dict) -> list:
    """Every local seat's files: where they are, whether that is a network
    share, and whether they are reachable right now (§11.2 D)."""
    from agent_friday.services import model_store as ms
    from agent_friday.services import path_probe
    rows = []
    for mid, rec in sorted(store_all.items()):
        if not isinstance(rec, dict) or rec.get("is_embedding"):
            continue
        files = ms.seat_files(rec)
        recorded = rec.get("path") or ""
        remote = path_probe.is_remote(recorded)
        served_from = files.get("gguf")
        rows.append({
            "model_id": mid,
            "label": rec.get("label") or mid,
            "path": recorded,
            "served_from": served_from,
            "remote": bool(remote and served_from and
                           path_probe.is_remote(served_from)),
            "recorded_remote": bool(remote),
            "present": bool(served_from),
            "available": mid in store_avail,
            "retired": rec.get("retired") or None,
            "size_bytes": rec.get("size_bytes"),
            "sha256_short": (rec.get("sha256") or "")[:12] or None,
            "lora": rec.get("lora"),
            "lora_present": bool(files.get("lora")),
            "mmproj": rec.get("mmproj"),
            "mmproj_present": bool(files.get("mmproj")),
            "probe": path_probe.probe_state(recorded) if remote else None,
            "local_dir": str(ms.store_dir()),
        })
    return rows


def _model_soup(settings: dict, routing: dict, costs: dict, seats: dict,
                providers: list, labels: dict) -> dict:
    from agent_friday.services import local_seats
    mr = settings.get("model_routing") or {}
    mode = str(mr.get("mode") or "local_preferred").lower()
    if mode == "smart":
        # `smart` is gone from the picker (§11.2 B); an existing value reads
        # as local_preferred until the router's branches are deleted.
        mode = "local_preferred"

    try:
        serving = local_seats.serving()
    except Exception:
        serving = {}
    try:
        from agent_friday.services import model_store as ms
        store_all = ms.all_models()
        store_avail = ms.available()
        store_missing = ms.missing()
    except Exception:
        store_all, store_avail, store_missing = {}, {}, {}

    def _binding(key):
        b = routing.get(key) or {}
        return str(b.get("model") or ""), str(b.get("provider") or "")

    def _label(model_id):
        # A local seat's own registry label first ("FridayWeaver-1.0"), then
        # the catalogue's, then the id. The catalogue humanises ids it did
        # not get from the store, which mangles a name chosen on purpose.
        rec = store_all.get(model_id) if isinstance(store_all, dict) else None
        if isinstance(rec, dict) and rec.get("label"):
            return rec["label"]
        return labels.get(model_id) or model_id

    reasoning_model, reasoning_prov = _binding("reasoning")
    heavy_model, heavy_prov = _binding("heavy_hitter")
    local_default = str(mr.get("local_model") or "")
    cloud_default = str(mr.get("default_cloud_model") or "")

    # The local seat that answers everyday turns: the reasoning binding when
    # it names something local, else `model_routing.local_model`.
    if reasoning_model and _provider_is_local(reasoning_prov):
        local_model = reasoning_model
    else:
        local_model = local_default
    # The cloud model: the reasoning binding when it is cloud, else default.
    if reasoning_model and not _provider_is_local(reasoning_prov):
        cloud_model, cloud_prov = reasoning_model, reasoning_prov
    else:
        cloud_model, cloud_prov = cloud_default, str(
            mr.get("cloud_provider") or "anthropic")

    seat_local = _seat_state(local_model, serving,
                             (seats.get("interactive_brain") or {})
                             if isinstance(seats, dict) else {},
                             store_avail, store_missing, costs)
    seat_local.update({"model": local_model,
                       "label": _label(local_model),
                       "where": "local",
                       "num_ctx": ((seats.get("interactive_brain") or {})
                                   .get("num_ctx")
                                   if isinstance(seats, dict) else None)})

    key_present = None
    for p in providers or []:
        if p.get("name") == cloud_prov:
            key_present = p.get("key_present")
            break
    cloud = {"model": cloud_model, "label": labels.get(cloud_model) or
             cloud_model, "provider": cloud_prov, "where": "cloud",
             "key_present": key_present,
             "state": "ready" if key_present in (True, None) else "refused",
             "reason": "" if key_present in (True, None)
             else "no API key for %s" % cloud_prov,
             "cost_per_turn_usd": (costs.get("ledger") or {}).get(
                 "cloud_turn_median_usd")}

    if heavy_model and _provider_is_local(heavy_prov):
        deep = _seat_state(heavy_model, serving,
                           (seats.get("heavy_hitter") or {})
                           if isinstance(seats, dict) else {},
                           store_avail, store_missing, costs)
        deep.update({"model": heavy_model,
                     "label": _label(heavy_model),
                     "where": "local", "on_demand": True,
                     "displaces": "replaces the everyday model while it runs"})
    elif heavy_model:
        deep = {"model": heavy_model, "label": labels.get(heavy_model) or
                heavy_model, "where": "cloud", "provider": heavy_prov,
                "state": "cloud", "reason": ""}
    else:
        deep = {"model": "", "label": "", "state": "absent",
                "reason": "no deep model is bound; the everyday model takes "
                          "hard turns too"}

    # What takes the NEXT turn, given the mode and the states above.
    if mode == "cloud_only":
        now = dict(cloud)
        now["why"] = "routing mode is Cloud only"
    elif mode == "local_only":
        now = dict(seat_local)
        now["why"] = ("routing mode is Local only"
                      if now.get("state") == "proven"
                      else "routing mode is Local only and the local seat is "
                           "not serving: the next turn will be refused with "
                           "an offer to use the cloud")
    else:
        if seat_local.get("state") == "proven":
            now = dict(seat_local)
            now["why"] = "local first; the local seat is up"
        else:
            now = dict(cloud)
            now["why"] = ("local first, but the local seat is %s (%s), so the "
                          "next turn goes to the cloud and the reply will say "
                          "so" % (seat_local.get("state"),
                                  seat_local.get("reason") or "no reason given"))

    # Posture: the three settings that decide what leaves the machine, read
    # from where the code reads them, never from the flag the UI used to show.
    try:
        from agent_friday.privacy import cloud_consent as cc
        cs = cc.resolve(mr)
        snap = cs.capability_snapshot or {}
        consent = {"answered": cs.answered, "choice": cs.choice, "at": cs.at,
                   "source": cs.source, "unrestricted": cs.unrestricted,
                   "snapshot_capable": snap.get("capable"),
                   "snapshot_why": snap.get("why") or
                   (((snap.get("roles") or {}).get("text") or {}).get("why")
                    if isinstance(snap.get("roles"), dict) else None)
                   or snap.get("chain_why")}
    except Exception as e:
        consent = {"answered": None, "error": str(e)}
    kg = str(((settings.get("knowledge_graph") or {}).get("indexing_mode"))
             or "local").lower()
    posture = {
        "mode": mode,
        "vault_local_only": bool(mr.get("vault_local_only", False)),
        "kg_indexing_mode": kg,
        "kg_local_possible": bool(serving),
        "egress_mode": str(settings.get("egress_mode") or "audit"),
        "consent": consent,
        "deep_seat_keep_warm_s": int(mr.get("deep_seat_keep_warm_s") or 300),
    }

    return {
        "now": now,
        "local": seat_local,
        "deep": deep,
        "cloud": cloud,
        "posture": posture,
        "ledger": costs.get("ledger") or {},
        "serving": serving,
        "embeddings": "all-MiniLM-L6-v2 on this CPU, always",
        "weights": _weights_rows(store_all, store_avail),
    }


@intelligence_bp.route("/api/intelligence")
def api_intelligence():
    from agent_friday.services.model_catalog import build_catalog
    from agent_friday.core import _load_settings

    cat = build_catalog()
    settings = _load_settings()
    routing = settings.get("capability_routing") or {}

    # ── Residency: what is actually loaded, and what a change would cost ──
    seats, budgets, refusals, resident = {}, {}, [], set()
    _res_state, _res_at = "unknown", 0.0
    pinned = {}
    _planned_at = None
    try:
        # Reuse the residency route's own view rather than re-deriving it, so
        # this surface and /api/residency/status can never disagree.
        # THROUGH A SNAPSHOT, because it reads the machine. Profiled 2026-09-23:
        # this route cost 32.7s cold and 12.6s warm -- and the cold figure is
        # OVER the 30s AbortController the panel itself sets, so Settings >
        # Intelligence aborted its own fetch and reported a timeout. The cost is
        # here: socket.create_connection 5.3s over 7 calls,
        # residency_arbiter.resident() 4.1s, _ours_resident_mib() 4.0s --
        # loopback probes against seats that are not running.
        #
        # This also caused what looked like a separate bug: "nothing changed
        # when I picked Opus 5.5". The save persisted correctly every time
        # (verified end to end, POST 200 and settings.json updated), but the
        # panel confirms by RE-FETCHING this route -- so the new seat never
        # arrived on screen and the pick looked like it did nothing.
        from agent_friday.services import machine_probe as _mp
        from agent_friday.routes.residency import status as _residency_status

        def _residency_payload():
            resp = _residency_status()
            return (resp.get_json() if hasattr(resp, "get_json")
                    else resp[0].get_json())

        st, _res_at, _res_state = _mp.snapshot(
            "intelligence:residency", _residency_payload,
            fresh_for=15.0, budget=1.0, default=None)
        if st is None:
            # Not read yet: omit the seats block rather than guess at it. Every
            # other section still renders, and the payload says why below.
            st = {}
        seats = st.get("seats") or {}
        budgets = st.get("budgets") or {}
        refusals = st.get("refusals") or []
        pinned = st.get("pinned_vram_mib") or {}
        try:
            from agent_friday.services.residency_arbiter import get_arbiter as _ga
            _planned_at = getattr(_ga(), "planned_at", None)
        except Exception:
            pass
        for k in ("resident_ollama", "resident_llama_server"):
            v = st.get(k)
            if isinstance(v, dict):
                resident.update(v.keys())
            elif isinstance(v, list):
                resident.update(v)
    except Exception:
        pass

    seat_by_model = {}
    for seat_name, seat in (seats.items() if isinstance(seats, dict) else []):
        mid = (seat or {}).get("model_id")
        if mid:
            seat_by_model[mid] = dict(seat, seat=seat_name)
            if (seat or {}).get("status") in ("resident", "pinned", "leased"):
                resident.add(mid)

    costs = _costs_rollup()
    sizes = _ollama_sizes()

    # ── Deduplicate the catalogue by model id ────────────────────────────────
    # The same local model is published by more than one provider entry
    # (arbiter-local and ollama-local are the same daemon), which is why three
    # models appeared twice in the picker and why selection state rendered on
    # both copies of e4b. One row per model, provenance kept as a list.
    merged: dict[str, dict] = {}
    for m in cat.get("models", []):
        mid = m.get("id")
        if not mid:
            continue
        cur = merged.get(mid)
        if cur is None:
            cur = dict(m)
            cur["providers"] = []
            cur["roles"] = list(m.get("roles") or [])
            cur["modalities"] = list(m.get("modalities") or [])
            merged[mid] = cur
        else:
            cur["roles"] = sorted(set(cur["roles"]) | set(m.get("roles") or []))
            cur["modalities"] = sorted(set(cur["modalities"]) | set(m.get("modalities") or []))
            cur["available"] = bool(cur.get("available")) or bool(m.get("available"))
        prov = m.get("provider")
        if prov and prov not in cur["providers"]:
            cur["providers"].append(prov)

    models = []
    for mid, m in merged.items():
        size = sizes.get(mid) or 0
        seat = seat_by_model.get(mid)
        is_local = bool(m.get("local")) or m.get("classification") == "local"
        state = "cloud"
        if is_local:
            state = "resident" if (mid in resident or (seat and seat.get("status") in
                                                       ("resident", "pinned", "leased"))) else "cold"
        used = costs["models"].get(mid) or {}
        models.append({
            "id": mid,
            "label": m.get("label") or mid,
            "providers": m["providers"],
            "provider_label": m.get("provider_label"),
            "local": is_local,
            "state": state,
            "seat": (seat or {}).get("seat"),
            "seat_status": (seat or {}).get("status"),
            "vram_mib": (seat or {}).get("vram_mib"),
            "size_bytes": size or None,
            "wake_s": None if state == "resident" else _wake_estimate_s(size),
            "modalities": m.get("modalities") or [],
            "roles": m.get("roles") or [],
            "available": bool(m.get("available", True)),
            "needs_key": m.get("needs_key"),
            "cost_per_1k": m.get("cost_per_1k"),
            "free": bool(m.get("free")),
            "context_window": m.get("context_window"),
            "last_used": used.get("last_ts"),
            "calls": used.get("calls") or 0,
        })
    models.sort(key=lambda x: (not x["local"], x["label"].lower()))

    # ── Roles, in his language, each with what it requires ───────────────────
    roles = []
    # Refusals, so an empty seat can say WHICH kind of empty it is. R11 means
    # nothing was asked for; R1-R10 mean something could not be done. Rendering
    # both as "unset" loses the only distinction that matters to the person
    # looking at it.
    #
    # Prefer the ACTIONABLE refusal when a role has more than one. First-wins
    # picked whichever the planner happened to append first, which for a role
    # with a failed assignment was sometimes the R11 -- so the row said "this
    # seat is yours to choose" about a seat that already had a model and a
    # concrete reason it would not fit. R11 now only fires for genuinely
    # unassigned roles, so this is belt and braces rather than the fix, but a
    # role can still carry both an R3 and an R6 and the R11 is never the more
    # useful of any pair.
    refusal_for = {}
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        _arb = get_arbiter()
        for _r in ((_arb.plan_fresh() if _arb else None) or {}).get("refusals") or []:
            _role = _r.get("role")
            if not _role:
                continue
            _prev = refusal_for.get(_role)
            if _prev is None or (_prev.get("rule_id") == "R11"
                                 and _r.get("rule_id") != "R11"):
                refusal_for[_role] = _r
    except Exception:
        pass

    for key, label, need_role, need_mod, help_text in ROLE_SPEC:
        bound = routing.get(key) or {}
        mid = bound.get("model") or ""
        seat = seat_by_model.get(mid) or {}
        # The working roles deliberately use their residency-role name AS the
        # capability key, so no third mapping exists to drift out of sync.
        seat_name = _SEAT_FOR_ROLE.get(key, key)
        if not seat and seat_name:
            seat = (seats.get(seat_name) or {}) if isinstance(seats, dict) else {}
        used = costs["models"].get(mid) or {}
        roles.append({
            "key": key, "label": label, "help": help_text,
            "requires": need_mod,
            "requires_role": need_role,
            "model": mid,
            "provider": bound.get("provider"),
            "seat": seat_name,
            "where": seat.get("device") or ("local" if mid in sizes else None),
            "status": seat.get("status"),
            "backend": seat.get("backend"),
            "num_ctx": seat.get("num_ctx"),
            # "Proven" means this exact model has actually served this machine,
            # not that it appears in a config file.
            "proven": bool(used.get("calls")),
            "last_used": used.get("last_ts"),
            # Grouping, so thirteen roles do not read as thirteen models.
            "residency": _RESIDENCY_FOR_ROLE.get(key),
            # WHY this seat is empty, when it is. R11 is the normal state of a
            # role nobody has assigned; every other rule means something could
            # not be done. The page must not render the first as a failure.
            "awaiting_choice": (not mid) and (
                (refusal_for.get(seat_name) or {}).get("rule_id") == "R11"),
            "blocked_because": (
                (refusal_for.get(seat_name) or {}).get("explanation")
                if (not mid) and (refusal_for.get(seat_name) or {}
                                  ).get("rule_id") not in (None, "R11")
                else None),
        })

    # ── The machine ──────────────────────────────────────────────────────────
    # Humanised, and grouped by severity so the page can render a fault as a
    # fault and a choice as a choice. `raw` is kept for anyone debugging the
    # planner; nothing renders it.
    _label_for_role = {}
    for _k, _l, _a, _b, _c in ROLE_SPEC:
        _label_for_role[_SEAT_FOR_ROLE.get(_k, _k)] = _l
    problems, choices, infos = [], [], []
    for _r in (refusals or []):
        _h = _humanise_refusal(_r, _label_for_role.get(_r.get("role")))
        _h["rule_id"] = _r.get("rule_id")
        _h["role"] = _r.get("role")
        _h["raw"] = _r.get("explanation")
        {"problem": problems, "choice": choices, "info": infos}[
            _h["severity"]].append(_h)

    machine = {"vram": None, "ram": None, "refusals": refusals,
               "problems": problems, "choices": choices, "notes": infos,
               "planned_at": _planned_at,
               "resident": sorted(resident), "pinned_vram_mib": pinned}
    try:
        from agent_friday.services import gpu_headroom
        risk = gpu_headroom.display_at_risk()
        machine["vram"] = {
            "total_mib": risk.get("total_mib"), "free_mib": risk.get("free_mib"),
            "reserve_mib": risk.get("threshold_mib"), "at_risk": risk.get("at_risk"),
            "gpu": risk.get("gpu"),
        }
    except Exception:
        pass
    ram = (budgets or {}).get("ram") or {}
    if ram:
        machine["ram"] = {
            "total_mib": ram.get("total_mib"),
            "available_mib": ram.get("available_hard_mib"),
            "reserve_mib": ram.get("os_reserve_mib"),
        }

    # The headroom monitor's own reading (docs/design/implemented/headroom.md §4.3,
    # §12 Phase 1) -- so THE MACHINE reads one source for utilisation/power/
    # disk-system/thrash instead of a page-specific probe. `vram`/`ram`
    # above are left exactly as they were (§8.3's correction is Phase 4's
    # job, not this one's); this is purely additive.
    try:
        from agent_friday.services import machine_monitor as mm
        _mon_sample = mm.last_sample() or mm.sample()
        # _track_history=False: this route can be polled far faster than the
        # loop's own 60s/5s cadence, and re-appending the SAME cached sample
        # on every poll would pollute the thrash window with duplicates
        # instead of the three distinct, cadence-spaced readings the
        # signature is defined against. The loop's own tick() is the only
        # writer of that shared history; this call only reads it.
        _mon_verdict = mm.verdict(_mon_sample, _track_history=False)
        machine["monitor"] = {"sample": _mon_sample, "verdict": _mon_verdict}

        # §8.3's third bar: system disk, against the EXISTING DISK_FLOOR_MIB
        # (R8, residency_policy.py:129) -- not a new number, and not
        # D1-gated, so this is drawn unconditionally.
        from agent_friday.services.residency_policy import DISK_FLOOR_MIB
        machine["disk_system"] = {
            "free_mib": _mon_sample.get("disk_system_free_mib"),
            "total_mib": mm.disk_system_total_mib(),
            "floor_mib": DISK_FLOOR_MIB,
        }

        # H-DISK-SYS / H-THRASH (§12 Phase 4 item 3): the monitor's own
        # verdicts, humanised into the SAME problems/choices/notes groups a
        # planner refusal renders into, so THE MACHINE does not grow a
        # fourth vocabulary. vram_slack/ram_available are skipped -- their
        # verdict is `basis: "unknown"` on every machine until D1 lands.
        for _resource in ("disk_system", "thrash"):
            _mh = _humanise_monitor_verdict(_resource, _mon_verdict.get(_resource))
            if _mh is None:
                continue
            {"problem": problems, "choice": choices, "info": infos}[
                _mh["severity"]].append(_mh)
    except Exception:
        pass

    # ── Contract level (§8.3 item 4) ──────────────────────────────────────────
    # D1 was ANSWERED 2026-09-24 (see docs/design/implemented/headroom.md §13):
    # "I need my machine" releases the machine. `services/stand_down.py` unloads
    # the local models, pauses every scheduled job through the one gate in
    # `scheduler._run_task`, and persists across a restart. So `levels_enforced`
    # is now true, and the UI no longer carries a "(not yet enforced)" caveat.
    try:
        from agent_friday.services import work_queue as wq
        from agent_friday.services import stand_down as _sd
        _down = _sd.is_stood_down()
        machine["contract"] = {
            "level": "yield" if _down else "working",
            "levels_enforced": True,
            "stood_down": _down,
            "stand_down_reason": _sd.reason() if _down else "",
            "idle_s": wq.idle_seconds(),
        }
    except Exception:
        machine["contract"] = {"level": "working", "levels_enforced": True,
                               "stood_down": False, "stand_down_reason": "",
                               "idle_s": None}

    # ── LOCAL MODELS ON THIS MACHINE (§8.2, §12 Phase 4 item 1) ──────────────
    try:
        from agent_friday.services import hardware_profile as hwp
        local_models = local_models_catalog(hwp.get(), sizes)
    except Exception as exc:
        local_models = {"text": [], "image": [], "video": [], "voice": [], "embed": [],
                        "error": "%s: %s" % (type(exc).__name__, exc)}

    # ── Providers, including whether they have ever actually served ──────────
    #
    # Attribution is by MODEL, not by matching provider-name strings. The cost
    # ledger records coarse provider names ("local") that do not line up with
    # catalogue names ("ollama-local", "local-voice-lite"), and stem-matching
    # them put 657 local chat calls under "Local Voice (CPU)" while reporting
    # "Local (Ollama) — never served anything", which was false in both
    # directions. Every cost row names a model, and the catalogue says which
    # provider owns that model, so we count through the model.
    prov_of_model = {}
    for m in models:
        for pname in m["providers"]:
            prov_of_model.setdefault(m["id"], set()).add(pname)

    by_provider = {}
    for mid, used in costs["models"].items():
        owners = prov_of_model.get(mid)
        if not owners:
            continue
        # A model published by two provider entries (the same local daemon seen
        # twice) credits both; they are the same hardware either way.
        for owner in owners:
            acc = by_provider.setdefault(owner, {"calls": 0, "last_ts": None, "cost_usd": 0.0})
            acc["calls"] += used.get("calls") or 0
            ts = used.get("last_ts")
            if ts and (acc["last_ts"] is None or ts > acc["last_ts"]):
                acc["last_ts"] = ts

    # Cost is only meaningful per provider for paid ones, and the ledger's own
    # provider column is right about money even when it is coarse about which
    # local backend served a turn.
    for pname, stat in list(costs["providers"].items()):
        for cand in (pname, pname + "-local"):
            if cand in by_provider:
                by_provider[cand]["cost_usd"] = stat.get("cost_usd") or 0.0

    providers = []
    for p_ in cat.get("providers", []):
        name = p_.get("name")
        stat = by_provider.get(name) or {}
        if not stat and name in costs["providers"]:
            stat = costs["providers"][name]
        key_env = p_.get("needs_key")
        providers.append({
            "name": name,
            "label": p_.get("label") or name,
            "type": p_.get("type"),
            "connected": bool(p_.get("available")),
            "key_env": key_env,
            "key_present": bool(os.environ.get(key_env)) if key_env else None,
            "models": sum(1 for m in models if name in m["providers"]),
            "calls": stat.get("calls") or 0,
            "cost_usd": round(stat.get("cost_usd") or 0.0, 2),
            "last_used": stat.get("last_ts"),
        })
    providers.sort(key=lambda x: (x["last_used"] is None, -(x["last_used"] or 0)))

    # Routing mode. Present in the payload because it belongs on the same page
    # as the seats: it decides whether a seat may be substituted at all, and
    # dropping it from the rebuilt picker was a regression.
    routing_mode = str(((settings.get("model_routing") or {}).get("mode")
                        or "local_preferred")).lower()
    if routing_mode == "smart":
        # Removed from the picker 2026-09-17 (model-soup.md §11.2 B). The
        # router's `smart` branches stay for one release; the UI reads the
        # value as local_preferred, which is what it does for tool turns.
        routing_mode = "local_preferred"

    labels = {m["id"]: (m.get("label") or m["id"]) for m in models
              if m.get("id")}
    try:
        soup = _model_soup(settings, routing, costs, seats, providers, labels)
    except Exception as exc:
        soup = {"error": "%s: %s" % (type(exc).__name__, exc)}

    return jsonify({
        "status": "ok",
        "serving": costs["serving"],
        "residency_help": RESIDENCY_HELP,
        "routing_mode": routing_mode,
        # The vault posture, stated where a user can see it. A privacy control
        # that disables itself silently is worse than one that is absent; this
        # is the surface that makes `vault_local_only: false` visible without
        # reading the logs. `degraded` is true when gating is off.
        "vault_policy": _vault_policy_status(),
        "routing_modes": [
            # Three modes, and each sentence describes what the code does.
            # `smart` was removed: its help promised a quality split and the
            # router split by tool presence (model-soup.md §4.1 U4).
            {"id": "local_only", "label": "Local only",
             "help": "Every turn runs on this machine. If the local model "
                     "cannot answer, Friday says so and offers the cloud for "
                     "that turn. Nothing leaves without that click.",
             "covers": _MODE_SCOPE_NOTE},
            {"id": "local_preferred", "label": "Local preferred",
             "help": "Local first. When the local model fails or is busy, the "
                     "turn goes to the cloud model on the card and the reply "
                     "is marked cloud. Vault content still stays local.",
             "covers": _MODE_SCOPE_NOTE},
            {"id": "cloud_only", "label": "Cloud only",
             "help": "Every chat, voice and background turn goes to the cloud "
                     "model. Embeddings still run here.",
             "covers": _MODE_SCOPE_NOTE},
        ],
        "soup": soup,
        "roles": roles,
        "models": models,
        "machine": machine,
        "local_models": local_models,
        "providers": providers,
        "catalog_meta": cat.get("catalog_meta") or {},
        # So the panel can say "as of 20s ago" or "couldn't read the seats yet"
        # instead of presenting a stale or absent residency read as live.
        "residency_reading": _res_state,
        "residency_age": _mp.age_note(_res_at, _res_state),
        "now": time.time(),
    })
