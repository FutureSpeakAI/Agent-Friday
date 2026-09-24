"""Which installed model serves a role — asked once, in one place.

A hardcoded model name is a dangling pointer the moment someone runs
`ollama rm`. When the user removes a model that three separate modules still
name as a constant, every one of those modules fails differently and none of
them says why:

  * `judgment_gate.DEFAULT_JUDGE_MODEL` 404'd on every probe in the boot
    battery, which crawled startup and left the privacy gate quietly degraded
    to its deterministic verdict;
  * `research/harness.BRAIN` 404'd mid-commission and surfaced as "no usable
    JSON", which the pipeline is entitled to read as "the model would not
    comply" — so a missing model was reported as a research finding;
  * `EXTRACTOR` and `HEAVY` were the same bug waiting for the next stage.

The failure mode they share is worse than the outage: each one degrades into
something that looks like a legitimate negative result. A model that is not
installed is not a model that had nothing to say.

So the question "what should serve role X" is answered here, against what the
daemon actually reports, and a substitution is always announced. There is no
list of names to keep in sync — the roles map onto `capability_routing`, which
is the setting the user already edits, and the size-ordered fallback is only
reached when that points at something absent too.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request

_log = logging.getLogger("friday.local_seats")


def _names_a_cloud_model(model) -> bool:
    """True when `model` is a cloud id rather than a local seat name.

    This module picks among LOCAL seats, so a cloud id in
    `capability_routing.<cap>.model` is not a preference it can act on -- and
    emphatically not a local model that has gone missing. Treating one as
    missing produced, live on 2026-09-24:

        [seats] brain: 'claude-opus-5-5' is not installed - using 'bonsai2:27b'

    about a model that cannot be installed here and was never absent.

    The classification matches `_route_chosen_seat` in the router so the two
    cannot disagree about the same string: a named cloud family is cloud, a
    vendor-prefixed gateway id is cloud, and everything else -- including
    `hf.co/...` ids, which ARE local -- stays local, so its installation is
    still checked exactly as before.
    """
    mid = str(model or "").strip()
    if not mid:
        return False
    try:
        from agent_friday.routing.model_router import provider_family
        fam = provider_family(mid)
    except Exception:
        fam = None
    if fam == "local":
        return False
    if fam:
        return True
    return "/" in mid and not mid.startswith("hf.co/")

# role -> the capability_routing key that names its preferred seat
_ROLE_TO_CAPABILITY = {
    "brain": "reasoning",
    "judge": "reasoning",
    "sidekick": "subagent",
    "extractor": "subagent",
    "heavy": "heavy_hitter",
}

# Roles that want the biggest thing available rather than the cheapest.
_WANTS_LARGE = {"heavy"}

# Below this, a model is a toy for this purpose: `functiongemma:270m` is a
# 270M function-caller and will not extract passages or return a verdict.
_MIN_USEFUL_GB = 1.5

# Vision-language tags. These answer text, but they are not the seat anyone
# means by "reasoning", so they lose ties in the size-ordered fallback.
_VISION_HINTS = ("-vl", "vision", "llava", "-vl:", "minicpm-v")


def _looks_vision(name: str) -> bool:
    n = (name or "").lower()
    return any(h in n for h in _VISION_HINTS)


_CACHE: dict = {"at": 0.0, "rows": []}
_CACHE_TTL_S = 30.0
#: An empty inventory means "unknown", not "nothing installed" (see the
#: docstring), so it is re-checked sooner than a populated one — but it IS
#: cached, because producing it is the expensive case.
_EMPTY_TTL_S = 5.0

# So a substitution is logged once, not once per call in a grinding loop.
_ANNOUNCED: set = set()


def _friday_store() -> list[tuple[str, float]]:
    """(model_id, size_gb) from Friday's OWN registry.

    ~/.friday/runtime/models/models.json is the manifest the Arbiter serves
    from -- her runtime, not the daemon's. A file that is listed but missing
    from disk does not count.
    """
    out: list[tuple[str, float]] = []
    try:
        import pathlib
        from agent_friday.paths import friday_home
        # Honours FRIDAY_HOME. Still assumes the default "runtime"
        # segment rather than calling runtime_dir(): resolving that
        # imports core, a ~4s Flask bootstrap, on the CLI path. So
        # this still ignores FRIDAY_RUNTIME_DIR -- pre-existing, and
        # tracked separately from the FRIDAY_HOME fix.
        # `path_probe` rather than `Path.exists()`: a registered path on a
        # wedged `\\wsl.localhost` share held this function (and the chat
        # route above it) for the SMB timeout. Measured 2026-09-17.
        from agent_friday.services import path_probe
        store_dir = pathlib.Path(friday_home(), "runtime", "models", "gguf")
        raw = pathlib.Path(friday_home(), "runtime", "models",
                           "models.json").read_text("utf-8")
        for mid, rec in ((json.loads(raw) or {}).get("models") or {}).items():
            if not isinstance(rec, dict) or rec.get("is_embedding"):
                continue
            # Retired stays retired: the record is kept so the reason is
            # answerable, and the model is never a candidate again.
            if rec.get("retired"):
                continue
            # Same resolution order as model_store.seat_files, without the
            # core import: explicit local copy, then the store directory,
            # then the recorded path. A copy under runtime/models/gguf wins
            # the moment it lands.
            def _reach(key):
                p = rec.get(key)
                if not p:
                    return True
                local = (rec.get("local_files") or {}).get(key)
                cands = [c for c in (local, str(store_dir /
                                                 pathlib.Path(str(p)).name), p)
                         if c]
                return any(path_probe.exists(c) for c in cands)
            if not _reach("path"):
                continue
            # A fine-tune whose adapter is unreachable is not a seat; the
            # base under its name is the substitution DECISIONS.md refused.
            if rec.get("lora") and not _reach("lora"):
                continue
            out.append((mid, float(rec.get("size_bytes") or 0) / 1e9))
    except Exception as e:
        _log.debug("could not read Friday's model store: %s", e)
    return out


def _daemon_port_open(base: str, connect_timeout: float = 0.35) -> bool:
    """Is anything listening where the Ollama daemon would be?

    A cheap yes/no so callers can skip a four-second HTTP timeout when the
    answer is obviously no. Errors read as "maybe" and let the real request
    proceed: this is an optimisation, and an optimisation that swallows a
    working daemon because a URL parsed oddly would be worse than the wait it
    saves.
    """
    import socket as _socket
    from urllib.parse import urlparse as _urlparse
    try:
        u = _urlparse(base if "://" in base else "http://" + base)
        host = u.hostname or "127.0.0.1"
        port = int(u.port or 11434)
    except Exception:
        return True
    try:
        with _socket.create_connection((host, port), connect_timeout):
            return True
    except OSError:
        return False
    except Exception:
        return True


def installed(force: bool = False) -> list[tuple[str, float]]:
    """(name, size_gb) for every seat the daemon can serve, smallest first.

    Embedding models are excluded: they cannot answer, and offering one as a
    fallback would turn a missing-model problem into a baffling one.
    Returns [] when the daemon cannot be reached — callers must read that as
    "unknown", never as "nothing is installed".
    """
    now = time.time()
    # NEGATIVE RESULTS ARE CACHED TOO, on a shorter clock.
    #
    # This used to read `if _CACHE["rows"] and ...`, and the write below was
    # `if rows: _CACHE.update(...)`. Together those made an EMPTY answer
    # uncacheable — and empty is precisely the state that costs the most to
    # produce: no daemon means `urlopen(/api/tags)` runs to its 4-second
    # timeout, and every caller pays it again immediately. It is the same
    # defect as an import failure cached as `None` when `None` also means "not
    # yet attempted": one value carrying two meanings, so the expensive path
    # reruns forever. It bites hardest on a machine with nothing installed yet
    # — a fresh install, or the cloud-first 8 GB setup — which is the machine
    # least able to absorb it. `installed()` is on the chat path
    # (routes/chat.py, seat-still-exists check).
    #
    # The short TTL keeps the useful half of the old behaviour: "unknown"
    # should be re-asked sooner than a known-good inventory, just not on
    # every single call.
    ttl = _CACHE_TTL_S if _CACHE["rows"] else _EMPTY_TTL_S
    if not force and _CACHE["at"] and (now - _CACHE["at"]) < ttl:
        return list(_CACHE["rows"])

    # FRIDAY'S OWN STORE COUNTS AS INSTALLED.
    #
    # This module originally asked only the Ollama daemon, and that was wrong
    # in a way that inverted its purpose. `gemma4:e2b`, `gemma4:12b`, `e4b`
    # and `26b` are real entries in ~/.friday/runtime/models/models.json with
    # files on disk and extracted templates -- Friday's own runtime, which the
    # Arbiter serves as processes it owns. Asking the daemon about them
    # returns nothing, so a resolver that trusts the daemon alone concludes
    # her own models are missing and substitutes whatever happens to have been
    # `ollama pull`ed. That is not healing a dangling pointer; it is moving a
    # seat off the runtime the user chose -- which is exactly what happened
    # to the reasoning seat before this store was consulted.
    #
    # "Installed" means "Friday can serve it", from either store.
    rows = list(_friday_store())
    seen = {n for n, _ in rows}
    try:
        from agent_friday.services.local_call import ollama_url
        base = ollama_url().rstrip("/")
    except Exception:
        base = "http://localhost:11434"
    daemon_names: set = set()
    try:
        # DO NOT PAY FOR A DAEMON THAT IS NOT THERE.
        #
        # This probe sits on the chat path — `describe_for_model` builds the
        # system prompt for every turn and reaches here through `resolve()`.
        # Four seconds is cheap against a daemon that answers and ruinous
        # against one that is not installed, which is now the normal case:
        # Ollama was removed from this machine on 2026-09-18 and Friday serves
        # every local model through its own llama.cpp seats.
        #
        # A connect attempt on a closed loopback port is refused in about a
        # millisecond, so asking first costs nothing and skips the rest. The
        # negative is not remembered here on purpose — the surrounding cache
        # already holds it, and a daemon someone starts later should be found
        # on the next refresh rather than after a restart.
        if not _daemon_port_open(base):
            raise OSError("no Ollama daemon listening at %s" % base)
        with urllib.request.urlopen(f"{base}/api/tags", timeout=4) as r:
            raw = json.loads(r.read().decode()).get("models", [])
        for m in raw:
            n = m.get("name")
            if not n:
                continue
            # Recorded even when the store already listed it: the daemon having
            # the name is what makes it callable without a live llama.cpp seat.
            daemon_names.add(n)
            if n not in seen:
                seen.add(n)
                rows.append((n, float(m.get("size") or 0) / 1e9))
    except Exception as e:
        _log.debug("could not read the Ollama inventory: %s", e)

    rows = [t for t in rows if "embed" not in t[0].lower()]

    # A GGUF ON DISK IS NOT A SEAT A CALLER CAN REACH.
    #
    # The union above is right in principle -- Friday's own runtime models are
    # real and the Arbiter can serve them -- but "can be served" and "is being
    # served" are different claims, and this function's callers act on the
    # second one. `local_call` dispatches by NAME: it asks `seat_endpoint()`
    # for a live llama.cpp seat and, finding none, falls through to the Ollama
    # daemon. A model that exists only in Friday's store therefore resolves to
    # a name the daemon has never heard of.
    #
    # Measured on the reference machine. `resolve()` returned 'gemma4:e2b'
    # for judge, orchestrator, sidekick, interactive_brain, function_manager
    # and memory_manager. `seat_endpoint('gemma4:e2b')` was None and Ollama's
    # /api/tags did not list it, so every one of those calls produced
    #
    #     local_call HTTP 404 from gemma4:e2b: model 'gemma4:e2b' not found
    #
    # dozens of times in a burst -- and each 404 escalated to the cloud. A
    # trivial question ("what is two plus two") answered on claude-opus-5 in
    # 46-59s, while the seat that WAS up, gemma4:12b on port 8090, answered the
    # same thing in 1.45s. That is the "it took forever then kicked back to
    # the cloud" complaint, and the cause was never the router: it was this
    # list promising a model nothing could call.
    #
    # So a Friday-store model counts as installed only while it has a live
    # endpoint. Daemon models are unaffected -- being in /api/tags already means
    # the daemon will serve them on request. This deliberately does NOT try to
    # spawn a seat: that would evict whatever is resident, which is not a
    # decision a name-resolution helper gets to make.
    store_only = {n for n, _ in _friday_store()}
    try:
        from agent_friday.services.local_call import seat_endpoint
    except Exception:
        seat_endpoint = None
    if seat_endpoint is not None and store_only:
        keep, dropped = [], []
        for name, size in rows:
            if name in store_only and name not in daemon_names:
                try:
                    live = bool(seat_endpoint(name))
                except Exception:
                    live = False
                if not live:
                    dropped.append(name)
                    continue
            keep.append((name, size))
        if dropped and keep:
            # Only when it changes the answer, and only once per set.
            sig = ("unreachable", tuple(sorted(dropped)))
            if sig not in _ANNOUNCED:
                _ANNOUNCED.add(sig)
                _log.info("local_seats: %d model(s) on disk have no live seat "
                          "and no daemon entry, so they are not offered: %s",
                          len(dropped), ", ".join(sorted(dropped)))
        # Never strip the list to nothing: an empty result means "unknown" to
        # every caller, which is a worse lie than the one being fixed.
        if keep:
            rows = keep

    rows.sort(key=lambda t: t[1])
    _CACHE.update(at=now, rows=rows)
    return list(rows)


def _configured(role: str) -> str | None:
    key = _ROLE_TO_CAPABILITY.get(role)
    if not key:
        return None
    cr = {}
    try:
        from agent_friday.core import _load_settings
        cr = (_load_settings() or {}).get("capability_routing") or {}
    except Exception:
        pass
    if not cr:
        # Read the file directly. `_load_settings` is not always available this
        # early — at boot the judgment gate can ask before settings are warm,
        # get nothing, and fall through to the size-ordered guess, picking a
        # vision model over the reasoning seat the user actually configured,
        # purely because of import order.
        try:
            import os
            import pathlib
            home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
            raw = pathlib.Path(home, ".friday", "settings.json").read_text("utf-8")
            cr = (json.loads(raw) or {}).get("capability_routing") or {}
        except Exception:
            return None
    configured = ((cr.get(key) or {}).get("model")) or None
    # A cloud seat is a real choice, just not one this module can serve.
    # Returning it would make `resolve` hunt for it in the Ollama store,
    # fail, and announce a substitution for a model that was never here.
    if _names_a_cloud_model(configured):
        return None
    return configured


def _announce(role: str, wanted: str | None, got: str) -> None:
    sig = (role, wanted, got)
    if sig in _ANNOUNCED:
        return
    _ANNOUNCED.add(sig)
    if wanted:
        msg = (f"  [seats] {role}: {wanted!r} is not installed — using {got!r} "
               f"instead")
    else:
        msg = f"  [seats] {role}: using {got!r}"
    print(msg)
    # A substitution is a WARNING: the user asked for one model and is being
    # answered by another. At INFO this sat in friday.log unread on
    # 2026-09-18 while the UI said the seat change had succeeded.
    if wanted:
        _log.warning(msg.strip())
    else:
        _log.info(msg.strip())


def resolve(role: str, configured: str | None = None) -> str | None:
    """The best installed seat for `role`, or None if nothing can serve it.

    `configured` is the caller's own preference — a module constant or an
    explicit setting. It wins whenever it is actually installed, so this never
    overrides a working choice; it only replaces a name that has gone missing.

    When the daemon is unreachable the caller's preference is returned
    unchanged. Refusing to guess is right here: a transient daemon blip must
    not permanently rewrite which model the user asked for.
    """
    rows = installed()
    if not rows:
        return configured or _configured(role)

    names = {n for n, _ in rows}
    wanted = configured or _configured(role)

    for candidate in (configured, _configured(role)):
        if candidate and candidate in names:
            if candidate != wanted:
                _announce(role, wanted, candidate)
            return candidate

    useful = [t for t in rows if t[1] >= _MIN_USEFUL_GB] or rows
    # A vision-language model answers text fine, but it is not what anyone
    # means by "the reasoning seat", and picking one purely because it is a
    # few hundred MB smaller than the text model beside it is the kind of
    # silently-worse choice this module exists to prevent. Measured on the
    # reference machine: healing the reasoning seat chose qwen3-vl:8b (6.14 GB)
    # over the configured Gemma-4-E4B (6.33 GB). Prefer text; fall back to
    # vision only when there is nothing else.
    text_only = [t for t in useful if not _looks_vision(t[0])] or useful

    # A SEAT THAT IS ALREADY UP BEATS A SMALLER ONE THAT IS NOT.
    #
    # The size ordering answers "which is cheapest to run", but that is the
    # wrong question when one candidate is ALREADY RUNNING. Substituting for a
    # missing model is a repair, and a repair should land on the seat that
    # costs nothing to reach rather than the one that happens to be smallest.
    #
    # Measured on the reference machine, healing six roles that pointed at an absent
    # gemma4:e2b: size ordering chose SmolLM3-3B (1.9 GB, not loaded, would
    # need pulling into memory) while gemma4:12b was live on port 8090 and
    # answering in 1.45s. The smaller model is also markedly weaker for judging
    # and reasoning, so the cheap-looking choice was worse on both axes.
    #
    # Live seats first, then the existing rule inside each group, so nothing
    # about the vision/text or large/small preferences changes.
    try:
        from agent_friday.services.local_call import seat_endpoint

        def _live(name: str) -> bool:
            try:
                return bool(seat_endpoint(name))
            except Exception:
                return False

        live = [t for t in text_only if _live(t[0])]
        # WHEN LIVENESS IS UNKNOWABLE, DO NOT PRETEND IT IS A TIE.
        #
        # `live` being empty has two very different causes and this code used
        # to treat them identically: either nothing is running, or nothing
        # could be ASKED because `runtime/residency/endpoints.json` is empty.
        # The second is common — the file is written by whichever process
        # owns the seats, so after any restart it can hold `{}` while a seat
        # is demonstrably answering.
        #
        # Observed 2026-09-10 on Stephen's machine: endpoints.json held zero
        # entries while the FridayWeaver seat was serving on 8095. So `_live`
        # said False for everything, the pool fell back to size ordering, and
        # the SMALLEST candidate won — which was `gemma4:e2b-friday-v1`, a
        # retired alias for a model that no longer exists anywhere. Friday
        # then asked Ollama for it, Ollama has nothing installed, and the turn
        # died with "No model provider could run the agent". A seat that was
        # up and healthy the whole time.
        endpoints_known = bool(live)
    except Exception:
        live = []
        endpoints_known = False

    if not endpoints_known:
        # Probe the survey window directly rather than trusting a file that
        # may simply not have been written yet. This is the same 8090-8130
        # range the Arbiter adopts from, and asking costs one connect per
        # candidate against loopback.
        try:
            live = [t for t in text_only if _probe_seat(t[0])]
        except Exception:
            live = []

    pool = live or text_only
    pick = pool[-1][0] if role in _WANTS_LARGE else pool[0][0]
    _announce(role, wanted, pick)
    return pick


#: The Arbiter's adoption window. A seat outside it is invisible to Friday
#: regardless of health, which is its own documented hazard.
_SURVEY_PORTS = range(8090, 8131)


#: One survey answers for every candidate. Sweeping the window per model cost
#: 11.3s for two models on the reference machine — far too slow for the chat
#: path, and the reason this is a cached set rather than a per-name probe.
_SURVEY_CACHE: dict = {"at": 0.0, "served": frozenset()}
_SURVEY_TTL_S = 15.0


def _survey_seats(force: bool = False) -> frozenset:
    """Every model id actually advertised on the adoption window, measured.

    Ports that are closed refuse immediately; the timeout only bites on a port
    that accepts and then stalls, so it is kept short. A port that misbehaves
    costs this survey its timeout once per TTL, not once per lookup.
    """
    now = time.time()
    if not force and (now - _SURVEY_CACHE["at"]) < _SURVEY_TTL_S:
        return _SURVEY_CACHE["served"]
    import json as _json
    import socket as _s
    import urllib.request as _u
    from concurrent.futures import ThreadPoolExecutor

    # MEASURED, twice, because the obvious versions were both too slow for the
    # chat path: 41 sequential urlopen calls cost 6.05s, and adding a cheap
    # socket pre-check only got to 2.05s — this machine's closed loopback
    # ports do not refuse, they hang until the timeout, so every port paid it.
    # Sweeping in parallel costs one timeout for the whole window instead of
    # forty-one.
    def _ask(port):
        try:
            with _s.socket(_s.AF_INET, _s.SOCK_STREAM) as sk:
                sk.settimeout(0.15)
                if sk.connect_ex(("127.0.0.1", port)) != 0:
                    return ()
        except Exception:
            return ()
        try:
            with _u.urlopen("http://127.0.0.1:%d/v1/models" % port,
                            timeout=0.5) as r:
                body = _json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            return ()
        return tuple(str(row.get("id") or "")
                     for row in (body.get("data") or []) if row.get("id"))

    served = set()
    try:
        with ThreadPoolExecutor(max_workers=16) as pool:
            for names in pool.map(_ask, _SURVEY_PORTS):
                served.update(names)
    except Exception:
        for port in _SURVEY_PORTS:
            served.update(_ask(port))
    _SURVEY_CACHE.update(at=now, served=frozenset(served))
    return _SURVEY_CACHE["served"]


def _probe_seat(model: str) -> bool:
    """Is this model actually being served on the adoption window?

    The last-resort answer to "is it live" when the endpoints file cannot say,
    so a name nothing answers to can never win a substitution.

    Fail-closed: any error means "cannot confirm", which keeps the candidate
    out of the live pool rather than letting an unreachable name through on a
    technicality.
    """
    if not model:
        return False
    try:
        return model in _survey_seats()
    except Exception:
        return False


def serving() -> dict:
    """`{model_id: base_url}` for every local seat with a live endpoint.

    `installed()` answers "could be served"; this answers "is being served",
    which is the question the Model Soup card, the voice manifest's mind
    proof and the knowledge-graph indexer all need answered the same way.
    Sources, in order: seats the Arbiter in this process owns, seats another
    Friday process published to `residency/endpoints.json` (health-checked),
    and whatever the Ollama daemon lists. The base URL has no `/v1` suffix.

    Never raises; an unreachable source contributes nothing.
    """
    out: dict = {}

    def _root(base: str) -> str:
        b = str(base or "").rstrip("/")
        return b[:-3] if b.endswith("/v1") else b

    try:
        from agent_friday.services import residency_arbiter as ra
        arb = ra.get_arbiter()
        if arb is not None:
            for mid, entry in dict(getattr(arb.llama, "procs", {}) or {}).items():
                try:
                    _proc, port = entry
                    out[str(mid)] = "http://127.0.0.1:%d" % int(port)
                except Exception:
                    continue
        for mid, base in (ra._read_published() or {}).items():
            if mid in out:
                continue
            try:
                if ra._endpoint_alive(base):
                    out[str(mid)] = _root(base)
            except Exception:
                continue
    except Exception as e:
        _log.debug("serving(): arbiter view unavailable: %s", e)
    try:
        from agent_friday.routing.ollama_manager import get_manager
        mgr = get_manager()
        base = _root(getattr(mgr, "base_url", "") or "http://localhost:11434")
        for m in (mgr.list_models() or []):
            name = str((m or {}).get("name") or "")
            if name and name not in out:
                out[name] = base
    except Exception as e:
        _log.debug("serving(): daemon view unavailable: %s", e)
    return out


def refresh() -> dict:
    """Re-read the inventory and report the current role assignment."""
    installed(force=True)
    return {role: resolve(role) for role in sorted(_ROLE_TO_CAPABILITY)}


# ── healing settings that name a model which is gone ────────────────────────

# capability_routing key -> the legacy flat key that mirrors it. Kept here as
# well as in core because the heal has to fix BOTH sides: `_sync_capability_
# routing` copies the flat key over the canonical one on every save, so
# repairing only `capability_routing` lasts exactly until the next write.
_FLAT_MIRROR = {
    "reasoning": "orchestrator_model",
    "subagent": "subagent_model",
    "heavy_hitter": None,
}

# Which role's preferences to use when repairing a given capability.
_CAP_ROLE = {"reasoning": "brain", "subagent": "sidekick",
             "heavy_hitter": "heavy"}


def _is_local_name(model: str) -> bool:
    """True when this id dispatches to the local daemon.

    Cloud ids must never be touched: they are not in `ollama list` and
    "not installed" says nothing about whether they work.
    """
    try:
        from agent_friday.routing.model_router import provider_family
        return provider_family(model) == "local"
    except Exception:
        return ":" in (model or "")


def heal(settings: dict) -> list[str]:
    """Replace local model names that are no longer installed. Returns notes.

    A flat `orchestrator_model` left pointing at an uninstalled model is not
    inert: because the flat key outranks `capability_routing` on any save
    that does not explicitly set routing, every settings write re-stamps the
    dead name over whatever the model picker just chose. That is the
    "I change the model and it does not stick" defect, and fixes aimed at the
    picker cannot cure it because the picker is not the thing that is wrong.

    Deliberately conservative:
      * only LOCAL ids are considered — a cloud id absent from `ollama list`
        means nothing;
      * only when the daemon actually answered — an unreachable daemon must
        not rewrite the user's choices;
      * mutates in place and returns what it changed, so the caller can say so
        rather than healing in silence.
    """
    notes: list[str] = []
    rows = installed()
    if not rows:
        # Refusing to rewrite his choices on an unreachable daemon is right.
        # Saying nothing about it is not, and that silence has a cost we paid.
        #
        # 2026-09-18: Friday booted at 17:01 the previous evening, before the
        # FridayWeaver weights reached local disk. installed() was therefore
        # empty, this returned no notes, capability_routing.reasoning kept
        # pointing at a gemma4:12b that was installed nowhere, and every turn
        # fell through to the cloud. The user asked for a local model four
        # times and was answered by Sonnet four times, with nothing anywhere
        # saying why. A seat we could not verify must announce itself as
        # unverified, or "conservative" just means "wrong in silence".
        try:
            unchecked = sorted({
                v for v in (settings.get(f) for f in _FLAT_MIRROR.values() if f)
                if isinstance(v, str) and v and _is_local_name(v)
            })
        except Exception:
            unchecked = []
        notes.append(
            "could not read the local model inventory, so no seat was "
            "verified this load"
            + (" — these name local models that may not be installed: "
               + ", ".join(unchecked) if unchecked else ""))
        return notes
    names = {n for n, _ in rows}

    cr = settings.get("capability_routing")
    if not isinstance(cr, dict):
        return notes

    for cap, flat in _FLAT_MIRROR.items():
        entry = cr.get(cap)
        entry_model = (entry or {}).get("model") if isinstance(entry, dict) else None
        flat_model = settings.get(flat) if flat else None

        for value in (entry_model, flat_model):
            if not value or not _is_local_name(value) or value in names:
                continue
            fixed = resolve(_CAP_ROLE.get(cap, "brain"), value)
            if not fixed or fixed == value:
                continue
            if isinstance(entry, dict) and entry.get("model") == value:
                entry["model"] = fixed
            if flat and settings.get(flat) == value:
                settings[flat] = fixed
            notes.append(f"{cap}: {value} is not installed - now {fixed}")
            break

    # NESTED SEAT NAMES THE CAPABILITY MAP DOES NOT COVER.
    #
    # The loop above only walks capability_routing and its flat mirrors, so a
    # model named inside any OTHER settings block would survive every heal --
    # settings.judgment_gate.model can hold a name long after that model has
    # left the daemon.
    #
    # That one happens to be harmless at call time, because judgment_gate asks
    # resolve() and gets a substitute. But the stale value is what the Settings
    # UI shows the user, and a config that displays a model they do not have is
    # how "I changed it and it did not stick" starts. Repair the record too, not
    # just the behaviour.
    for block, role in (("judgment_gate", "judge"),):
        entry = settings.get(block)
        if not isinstance(entry, dict):
            continue
        value = entry.get("model")
        if not value or not _is_local_name(value) or value in names:
            continue
        fixed = resolve(role, value)
        if fixed and fixed != value:
            entry["model"] = fixed
            notes.append(f"{block}.model: {value} is not installed - now {fixed}")

    return notes
