"""One read-only view behind both model surfaces.

The top-bar quick switch and Settings → Intelligence used to assemble
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

intelligence_bp = Blueprint("intelligence", __name__)


# ── Capability model ─────────────────────────────────────────────────────────
#
# A job is described by the MODALITY it requires, never by a list of approved
# models. That is the whole fix for the orchestrator picker offering Whisper:
# running an agentic loop requires "tools", and an ASR model does not declare
# it, so it is shown as unsuitable-for-this-job with the reason stated rather
# than being silently absent.
#
# The labels are the words Stephen uses for these jobs, not the internal keys.

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
    ("heavy_hitter",   "Heavy thinking",        "orchestrator", "tools",
     "Long, hard problems where you will wait for a better answer."),
    ("local",          "Quick reflexes",        "orchestrator", "tools",
     "The small local model that stays awake for fast replies."),
    ("subagent",       "Research & background", "subagent",     "tools",
     "Runs commissions and background work while you do other things."),
    ("creative_image", "Images",                "creative",     "image",
     "Generates pictures."),
    ("creative_video", "Video",                 "creative",     "video",
     "Generates moving images."),
    ("creative_music", "Music",                 "creative",     "music",
     "Generates audio compositions."),
    ("voice",          "Live voice",            "voice",        "live",
     "Real-time spoken conversation."),
    ("asr",            "Voice in",              None,           "audio",
     "Turns what you say into text."),
    ("tts",            "Voice out",             None,           "audio",
     "Speaks Friday's replies aloud."),
    ("embedding",      "Memory",                None,           "text",
     "Turns text into vectors so Friday can recall it later."),

    # THE WORKING ROLES (roles contract 1). Rule R11: these are chosen by
    # Stephen, never inferred, and an unassigned one is an empty seat awaiting
    # a choice -- not an error. They were unassignable until seat_binding got
    # capability keys for them, so "chosen by the user" described a choice the
    # UI offered no way to make.
    ("orchestrator",     "Routing your work",   "orchestrator", "tools",
     "Decides which model handles what. Empty until you pick one."),
    ("sidekick_fast",    "Fast sidekick",       "orchestrator", "tools",
     "The quickest local model, for work that should not make you wait."),
    ("function_manager", "Tool calling",        "orchestrator", "tools",
     "Turns your request into the right tool call."),
    ("memory_manager",   "Memory keeper",       "orchestrator", "tools",
     "Reads the day and decides what is worth keeping."),
    ("researcher",       "Deep research",       "subagent",     "tools",
     "Runs long commissions end to end."),
]

# capability key -> residency class, from the contract's 3.
#
# THIS IS THE POINT OF SHOWING IT. Thirteen roles without their class reads as
# thirteen models resident at once, which on a 12 GB card looks impossible and
# is not: only the conversational tier stays warm, and seven roles routinely
# fit in three or four models. Sent to the client so the page can group by it
# rather than listing thirteen equal-looking seats.
_RESIDENCY_FOR_ROLE = {
    "reasoning": "resident", "local": "resident", "orchestrator": "resident",
    "sidekick_fast": "resident", "function_manager": "resident",
    "embedding": "resident",
    "heavy_hitter": "leased", "subagent": "leased", "researcher": "leased",
    "creative_image": "leased", "creative_video": "leased",
    "creative_music": "leased",
    "memory_manager": "on-demand", "asr": "on-demand", "tts": "on-demand",
    "voice": "on-demand",
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
    "embedding": "embedder",
    "asr": "stt",
    "tts": "tts",
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
        return {
            "title": "Image generation cannot run on this machine",
            "why": "There is no GPU available to hold an image model.",
            "action": "Images will be generated in the cloud instead.",
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


def _costs_rollup():
    """What has actually been served, per provider and per model.

    Read-only against the existing cost ledger. This is the column nobody
    builds: a provider that is configured but has never served anything says
    so, instead of looking identical to one doing all the work.
    """
    path = os.path.expanduser("~/.friday/costs.db")
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
#: How long a refusal is remembered. MEASURED on Windows 2026-08-28: connecting
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


@intelligence_bp.route("/api/intelligence")
def api_intelligence():
    from agent_friday.services.model_catalog import build_catalog
    from agent_friday.core import _load_settings

    cat = build_catalog()
    settings = _load_settings()
    routing = settings.get("capability_routing") or {}

    # ── Residency: what is actually loaded, and what a change would cost ──
    seats, budgets, refusals, resident = {}, {}, [], set()
    pinned = {}
    _planned_at = None
    try:
        # Reuse the residency route's own view rather than re-deriving it, so
        # this surface and /api/residency/status can never disagree.
        from agent_friday.routes.residency import status as _residency_status
        resp = _residency_status()
        st = resp.get_json() if hasattr(resp, "get_json") else (resp[0].get_json())
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
                        or "smart")).lower()

    return jsonify({
        "status": "ok",
        "serving": costs["serving"],
        "residency_help": RESIDENCY_HELP,
        "routing_mode": routing_mode,
        "routing_modes": [
            {"id": "local_only", "label": "Local only",
             "help": "Never leaves the machine. If a local model cannot answer, "
                     "I say so rather than using the cloud."},
            {"id": "local_preferred", "label": "Local preferred",
             "help": "Try local first, fall back to the cloud when local is "
                     "busy or unavailable."},
            {"id": "smart", "label": "Smart",
             "help": "Choose per task: local for routine work, cloud when it "
                     "will clearly be better."},
            {"id": "cloud_only", "label": "Cloud only",
             "help": "Always use a cloud model. Fastest, costs money, and every "
                     "turn leaves the machine."},
        ],
        "roles": roles,
        "models": models,
        "machine": machine,
        "providers": providers,
        "catalog_meta": cat.get("catalog_meta") or {},
        "now": time.time(),
    })
