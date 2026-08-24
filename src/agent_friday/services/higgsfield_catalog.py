"""
services/higgsfield_catalog.py — live Higgsfield model catalog.

Enumerates Higgsfield's generation models from the provider itself and writes
them into the SAME on-disk discovery cache that services/model_discovery
(and therefore the model catalog / picker) already reads. There is no
hardcoded Higgsfield model list anywhere: what the picker offers is what the
account actually carried at the last successful refresh.

Why this module exists at all
-----------------------------
Higgsfield was wired into Friday as an MCP *tool* surface only
(~/.friday/mcp_servers.json -> servers.higgsfield). The creative picker is
built from provider descriptors (services/provider_registry), which Higgsfield
had no entry in, so ~60 image/video/3D models were reachable by the chat loop
and invisible in Settings.

Why the MCP and not the CLI
---------------------------
Both surfaces enumerate, and they DISAGREE. Measured 2026-08-24:
`higgsfield model list` (CLI @1.1.23) reports 67 job types and has no 3D type
at all; `models_explore` over MCP reports those plus 17 3D models. The MCP is
the superset, it is already connected inside Friday, and it is the same
surface generation dispatches through — one source for both listing and
calling, so the picker cannot drift from what is callable.

Design notes (mirrors services/hosted_catalog.py deliberately):
  * The single network seam is _explore(), which goes through the running
    MCPManager. Tests monkeypatch that one function; nothing here touches the
    wire directly.
  * Failure policy is stale-while-revalidate: a failed or empty enumeration
    NEVER clobbers a previously working cache.
  * Generation models only. Higgsfield's catalog mixes generators with
    post-processors (upscale, background removal, outpaint, remesh, rigging).
    Offering "Image Background Remover" as your image *generation* model is
    its own small lie, so _is_edit_model() keeps them out of the role lists.
  * Audio is split, not lumped. Of the audio models exactly ONE generates
    music (`sonilo_music`); the rest are speech/TTS. They carry different
    modalities and different roles so the Music panel and the voice surface
    each get the right set and neither implies a depth that is not there.
"""
from __future__ import annotations

import json
import logging
import time

_log = logging.getLogger("friday.higgsfield_catalog")

#: Provider name — the key under which the catalog is cached and the
#: descriptor in provider_registry that reads it back.
PROVIDER = "higgsfield"

#: MCP server name in ~/.friday/mcp_servers.json, and the enumeration tool.
MCP_SERVER = "higgsfield"
EXPLORE_TOOL = "models_explore"

#: Output types Higgsfield's `models_explore` accepts for its `type` filter.
MODALITIES = ("image", "video", "audio", "3d")

#: A cache older than this renders as "catalog stale, showing cached".
#: Same 24h as hosted_catalog.STALE_AFTER_S — one convention, not two.
STALE_AFTER_S = 24 * 3600

#: Pagination hard stop. The catalog is ~120 models across four types; 20
#: pages of 100 is far beyond any plausible size and bounds a buggy
#: has_more/next_page_token loop.
_MAX_PAGES = 20
_PAGE_SIZE = 100

#: Tokens that mark a POST-PROCESSOR rather than a generator. Matched against
#: the model id and its tags. These stay in the flat catalog (the Model
#: Browser can show them) but never enter a creative role list.
_EDIT_MARKERS = (
    "upscale", "background_remover", "remove_background", "outpaint",
    "deflicker", "remesh", "retexture", "rigging", "restyle", "reframe",
    "relight", "skin_enhancer", "clipify", "decompose", "dubbing",
    "voice_change", "topaz", "sam_3_video",
)

#: Ids whose output_type is misreported upstream. `llm_text` is typed "video"
#: by the API but generates text; it is not a creative pick.
_TYPE_OVERRIDES = {"llm_text": "text", "brain_activity": "text"}


# ── The single network seam ──────────────────────────────────────────────────

def _explore(params: dict, timeout: float = 60.0) -> dict:
    """Call `models_explore` on the Higgsfield MCP server and return the parsed
    JSON envelope. Raises on transport failure or unparseable output — callers
    convert that into a status, never a crash.

    This is the ONE place that talks to Higgsfield. Monkeypatch it in tests.
    """
    from agent_friday.services import agent as _agent
    mgr = getattr(_agent, "_MCP_MANAGER", None)
    if mgr is None:
        raise RuntimeError("MCP manager not initialized — Higgsfield "
                           "enumeration needs the connector running")
    raw = mgr.call(MCP_SERVER, EXPLORE_TOOL, dict(params), timeout=timeout)
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        raise ValueError("models_explore returned an empty response")
    if text.startswith("[mcp error]") or text.startswith("[blocked]"):
        # The manager flattens transport/gate failures into a text marker.
        raise RuntimeError(text[:300])
    try:
        return json.loads(text)
    except (TypeError, ValueError) as e:
        raise ValueError(f"models_explore returned non-JSON: {e}") from e


# ── Classification ───────────────────────────────────────────────────────────

def _is_edit_model(item: dict) -> bool:
    """True when the model post-processes existing media rather than
    generating new media."""
    haystack = " ".join([
        str(item.get("id") or ""),
        " ".join(str(t) for t in (item.get("tags") or [])),
    ]).lower()
    return any(marker in haystack for marker in _EDIT_MARKERS)


def _is_music(item: dict) -> bool:
    """True for music generation, as opposed to speech/TTS or sound effects.

    Measured 2026-08-24: `sonilo_music` is the only model tagged `music` /
    `text-to-music`. Every other audio model is speech. This is a tag test
    rather than an id allowlist so a second music model surfaces the day
    Higgsfield adds one.
    """
    tags = {str(t).lower() for t in (item.get("tags") or [])}
    if tags & {"music", "text-to-music"}:
        return True
    blob = (str(item.get("name") or "") + " "
            + str(item.get("description") or "")).lower()
    return "music" in blob and "speech" not in blob


def _is_speech(item: dict) -> bool:
    """True for speech/TTS models — they belong to the voice surface, not the
    creative picker."""
    tags = {str(t).lower() for t in (item.get("tags") or [])}
    if tags & {"tts", "text-to-speech", "speech", "voice"}:
        return True
    blob = (str(item.get("name") or "") + " "
            + str(item.get("description") or "")).lower()
    return "text to speech" in blob or "text-to-speech" in blob or "tts" in blob


def _classify(item: dict) -> tuple:
    """Return (modalities, roles, kind) for one catalog item.

    roles=[] means "in the catalog, pickable nowhere" — the same treatment
    Lyria gets (provider_registry: music is chosen in the Studio Music panel
    via `music_model`, never via the creative_model picker).
    """
    from agent_friday.services.provider_registry import ROLE_CREATIVE, ROLE_VOICE

    mid = str(item.get("id") or "")
    otype = _TYPE_OVERRIDES.get(mid, str(item.get("output_type") or "").lower())

    if _is_edit_model(item):
        # Post-processors: catalogued and browsable, never a generation pick.
        base = {"image": ["image"], "video": ["video"],
                "3d": ["3d"], "audio": ["audio"]}.get(otype, ["text"])
        return base, [], "edit"

    if otype == "image":
        return ["image"], [ROLE_CREATIVE], "generate"
    if otype == "video":
        return ["video"], [ROLE_CREATIVE], "generate"
    if otype == "3d":
        return ["3d"], [ROLE_CREATIVE], "generate"
    if otype == "audio":
        if _is_music(item):
            # Music panel picks this via `music_model`, not creative_model.
            return ["audio", "music"], [], "generate"
        if _is_speech(item):
            return ["audio", "speech"], [ROLE_VOICE], "generate"
        # Sound effects / text-to-audio: real generation, but neither a
        # creative image/video pick nor a voice. Catalogued, unpicked.
        return ["audio"], [], "generate"
    # text and anything unrecognised: visible in the Model Browser only.
    return ["text"], [], "generate"


def _constraints(item: dict) -> dict:
    """Pull the picker-relevant constraints off a catalog item.

    A picker needs to know that Seedance takes a duration and Nano Banana Pro
    does not, and which aspect ratios each accepts. Higgsfield publishes this
    per model, so it is read rather than tabulated.
    """
    params = item.get("parameters") or []
    by_name = {str(p.get("name")): p for p in params if isinstance(p, dict)}
    out = {}
    ratios = item.get("aspect_ratios") or []
    if ratios:
        out["aspect_ratios"] = [str(r) for r in ratios]
    ar = by_name.get("aspect_ratio") or {}
    if not ratios and ar.get("options"):
        out["aspect_ratios"] = [str(o) for o in ar["options"]]
    if "duration" in by_name:
        d = by_name["duration"]
        out["duration"] = {"required": d.get("required") == "required",
                           "default": d.get("default"),
                           "min": d.get("min"), "max": d.get("max")}
        if d.get("options"):
            out["duration"]["options"] = [str(o) for o in d["options"]]
    res = by_name.get("resolution") or {}
    if res.get("options"):
        out["resolutions"] = [str(o) for o in res["options"]]
    required = sorted(n for n, p in by_name.items()
                      if p.get("required") == "required")
    if required:
        out["required_params"] = required
    medias = [m for m in (item.get("medias") or []) if isinstance(m, dict)]
    needs_media = [m for m in medias if m.get("required")]
    if needs_media:
        out["requires_input_media"] = True
        out["input_media"] = [
            {"type": m.get("type"), "max": m.get("max"),
             "roles": [str(r) for r in (m.get("roles") or [])]}
            for m in needs_media
        ]
    return out


def normalize(items: list) -> list:
    """Turn `models_explore` items into discovery-cache entries.

    The entry shape matches services/model_discovery.parse_openrouter so the
    existing catalog/browser code needs no special case: id, label,
    modalities, source, plus the Higgsfield-specific extras the picker uses.
    """
    out, seen = [], set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("id") or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        modalities, roles, kind = _classify(item)
        label = str(item.get("name") or mid).strip() or mid
        vendor = str(item.get("provider_name") or "").strip()
        entry = {
            "id": mid,
            # Two models are both called "Nano Banana Pro" upstream; the
            # vendor prefix is what makes the rows distinguishable in a list.
            "label": f"{label} ({vendor})" if vendor and vendor.lower() not in
                     label.lower() else label,
            "modalities": modalities,
            "roles": roles,
            "kind": kind,
            "source": "hosted",
            "supports_tools": False,
            "context_window": None,
            "max_output": None,
            "price_in": None,
            "price_out": None,
            "free": False,
            # Per-generation cost in Higgsfield credits. Higgsfield does not
            # price per token, so price_in/price_out stay None and this is the
            # only meaningful cost figure. None means UNKNOWN and must render
            # as exactly that — the spread across this catalog is ~150x
            # (z_image 0.15, seedance_2_0 22.5), so a guessed number attached
            # to someone's balance is worse than no number. Filled by
            # enrich_credits() below; the field exists either way so the
            # picker never has to distinguish "absent" from "unknown".
            "credits": None,
        }
        if item.get("description"):
            entry["note"] = str(item["description"])[:240]
        constraints = _constraints(item)
        if constraints:
            entry["constraints"] = constraints
        out.append(entry)
    return out


# ── Cost ─────────────────────────────────────────────────────────────────────

#: Per-model credit estimates, so a refresh does not re-preflight a catalog
#: whose prices rarely move. Sits beside the discovery cache.
_CREDITS_CACHE = "higgsfield_credits.json"

#: A refresh must not turn into a two-minute stall. Enumeration is ~120 models;
#: preflighting every one serially at the 60 s call timeout is unbounded, so
#: cost enrichment runs against a wall-clock budget and keeps whatever it got.
_CREDITS_BUDGET_S = 25.0
_CREDITS_WORKERS = 6


def _credits_cache_path():
    # Same directory the discovery cache uses, so the two age and clear
    # together rather than leaving priced-but-unlisted models behind.
    from agent_friday.services.model_discovery import CACHE_DIR
    return CACHE_DIR / _CREDITS_CACHE


def _load_credits() -> dict:
    try:
        with open(_credits_cache_path(), "r", encoding="utf-8") as f:
            blob = json.load(f)
        got = blob.get("credits")
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_credits(table: dict) -> None:
    try:
        path = _credits_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": time.time(), "credits": table}, f)
    except OSError as e:
        _log.info("higgsfield credit cache not written: %s", e)


def enrich_credits(entries: list, use_cache: bool = True) -> int:
    """Fill each entry's `credits` from the vendor's own cost preflight.

    Returns how many entries ended up with a number. Never raises and never
    invents a figure: a model whose estimate fails, times out, or falls outside
    the budget keeps `credits: None`, which the picker renders as "cost
    unknown". Cached estimates are reused so the cost of knowing the cost is
    paid roughly once.
    """
    if not entries:
        return 0
    table = _load_credits() if use_cache else {}
    todo = []
    for e in entries:
        hit = table.get(e.get("id"))
        if isinstance(hit, (int, float)):
            e["credits"] = float(hit)
        elif e.get("kind"):
            todo.append(e)

    if todo:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from agent_friday.services import higgsfield_generate as _gen
        deadline = time.monotonic() + _CREDITS_BUDGET_S
        with ThreadPoolExecutor(max_workers=_CREDITS_WORKERS) as pool:
            futures = {pool.submit(_gen.estimate_credits, e["kind"], e["id"]): e
                       for e in todo}
            for fut in as_completed(futures):
                entry = futures[fut]
                try:
                    val = fut.result(timeout=max(0.0, deadline - time.monotonic()))
                except Exception:
                    val = None
                if isinstance(val, (int, float)):
                    entry["credits"] = float(val)
                    table[entry["id"]] = float(val)
                if time.monotonic() > deadline:
                    # Stop collecting; outstanding models stay unknown rather
                    # than holding the refresh open.
                    break
        if table:
            _save_credits(table)

    known = sum(1 for e in entries if isinstance(e.get("credits"), (int, float)))
    if known < len(entries):
        _log.info("higgsfield cost: %d/%d models priced, rest render unknown",
                  known, len(entries))
    return known


# ── Enumeration ──────────────────────────────────────────────────────────────

def _enumerate_type(mtype: str) -> list:
    """Every model of one output type, following next_page_token."""
    items, cursor, pages = [], None, 0
    while pages < _MAX_PAGES:
        pages += 1
        params = {"action": "list", "type": mtype, "limit": _PAGE_SIZE}
        if cursor:
            params["after"] = cursor
        env = _explore(params)
        batch = env.get("items") or []
        items.extend(b for b in batch if isinstance(b, dict))
        cursor = env.get("next_page_token")
        if not env.get("has_more") or not cursor or not batch:
            break
    else:
        _log.warning("higgsfield %s enumeration hit the %d-page stop",
                     mtype, _MAX_PAGES)
    return items


def refresh() -> dict:
    """Enumerate the live Higgsfield catalog into the discovery cache.

    Returns {status: "refreshed"|"unavailable"|"error", provider, count,
    by_type?, fetched_at?, error?}. Never raises. A failed or empty
    enumeration leaves any existing cache untouched.
    """
    raw, by_type, errors = [], {}, []
    for mtype in MODALITIES:
        try:
            found = _enumerate_type(mtype)
        except Exception as e:
            errors.append(f"{mtype}: {type(e).__name__}: {e}"[:200])
            continue
        by_type[mtype] = len(found)
        raw.extend(found)

    if not raw:
        detail = "; ".join(errors) or "provider returned no models"
        _log.warning("higgsfield catalog refresh failed: %s", detail)
        # Distinguish "connector down" from "provider answered with nothing" —
        # the picker says different things about each.
        status = "unavailable" if errors else "error"
        return {"status": status, "provider": PROVIDER, "count": 0,
                "error": f"{detail} — keeping the previous cache"[:300]}

    normalized = normalize(raw)
    if not normalized:
        return {"status": "error", "provider": PROVIDER, "count": 0,
                "error": "no usable models after normalisation — "
                         "keeping the previous cache"}

    # Price the catalog before caching it, so the picker can show cost at the
    # point of choice. Best-effort and time-boxed: unpriced models cache with
    # credits None and render as "cost unknown".
    try:
        priced = enrich_credits(normalized)
    except Exception as e:
        _log.info("higgsfield cost enrichment skipped: %s", e)
        priced = 0

    from agent_friday.services.model_discovery import read_cache, write_cache
    write_cache(PROVIDER, normalized)
    blob = read_cache(PROVIDER) or {}
    result = {"status": "refreshed", "provider": PROVIDER,
              "count": len(normalized), "by_type": by_type,
              "priced": priced,
              "fetched_at": blob.get("fetched_at") or time.time()}
    if errors:
        # Partial success is reported as such rather than as a clean sweep.
        result["partial"] = errors
    return result


def cache_age():
    """Seconds since the Higgsfield catalog was enumerated, or None."""
    from agent_friday.services.model_discovery import read_cache
    fetched_at = (read_cache(PROVIDER) or {}).get("fetched_at")
    if not fetched_at:
        return None
    try:
        return max(0.0, time.time() - float(fetched_at))
    except (TypeError, ValueError):
        return None


def is_stale() -> bool:
    """True when the cache is older than STALE_AFTER_S or was never fetched.

    The picker uses this to say "showing cached list" rather than presenting a
    stale lineup as though it were live.
    """
    age = cache_age()
    return age is None or age > STALE_AFTER_S
