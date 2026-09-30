"""The weekly step: who proposes Friday's next look, when, and what it may send.

docs/design/active/avatar-visual-genome.md §4 (the weekly step), §5 (signals
and the author). The genome, its bounds and the signed history live in
services/avatar_genome.py; this module decides when a step runs and asks an
author for one.

- **On by default.** The first time this runs, the announcement moment is
  recorded and the first step falls due a week later.
- **Catch-up is one step.** A step is due when a week has passed since the
  last one; a machine that was off for five weeks takes one ordinary step on
  its first hour up, never a burst.
- **The author is the user's choice, frontier by default.** "frontier" lets
  Friday pick a frontier model and credits the model the provider reports;
  the user may instead choose any cloud model, any local model, or "seeded"
  (no model). The choice sticks.
- **Never a silent switch.** When the chosen author is unavailable (for the
  default: no key, no cloud consent, or local-only mode), no step runs and the
  week does not move. One plain notice a week offers both fixes: connect a
  cloud model, or use the named local model instead.
- **Numbers only.** What a model is sent is built against an allowlist of
  numbers, booleans and fixed enums; the seed and the sigil never go.
- **Signals are counted nightly from the activity ledger** (which never holds
  text), off-the-record rows excluded, and deleted once a step uses them.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import threading
import time
from datetime import date, datetime, timedelta

from agent_friday.core import FRIDAY_DIR
from agent_friday.services import avatar_genome as g

_log = logging.getLogger("friday.avatar_growth")
_LOCK = threading.RLock()

WEEK_S = 7 * 86400
SIGNAL_DAYS_MAX = 28
LOCAL_DEFER_MAX_S = 24 * 3600
EVOLUTION_FILE = FRIDAY_DIR / "evolution.json"
PERSONALITY_FILE = FRIDAY_DIR / "personality.json"

DEFAULTS = {"enabled": True, "author": "frontier", "apply_mode": "auto"}
APPLY_MODES = ("auto", "ask")
CLOUD_PROVIDERS = ("anthropic", "openrouter")
_MODEL_ID = re.compile(r"^[A-Za-z0-9._\-/:]{1,120}$")

SIGNAL_KEYS = ("turns", "tools", "tools_failed", "subagents", "local_calls")
DECLINE_FADE_S = 12 * WEEK_S


# ═══════════════════════════════════════════════════════════════════════════
#  Settings (kept in the avatar's own state.json, never settings.json)
# ═══════════════════════════════════════════════════════════════════════════

def _valid_author(a) -> bool:
    if a in ("frontier", "seeded"):
        return True
    if not isinstance(a, str):
        return False
    if a.startswith("local:"):
        m = a[len("local:"):]
        return bool(m) and bool(_MODEL_ID.match(m))
    if a.startswith("cloud:"):
        parts = a.split(":", 2)
        return (len(parts) == 3 and parts[1] in CLOUD_PROVIDERS
                and bool(parts[2]) and bool(_MODEL_ID.match(parts[2])))
    return False


def settings(*, now=None) -> dict:
    """The growth settings, with the announcement recorded on first use."""
    with _LOCK:
        st = g.state()
        changed = False
        for k, v in DEFAULTS.items():
            if k not in st:
                st[k] = v
                changed = True
        if st.get("announced_at") is None:
            t = float(now if now is not None else time.time())
            st["announced_at"] = t
            st["last_step_at"] = t
            changed = True
        for k, v in (("declined", {}), ("skips", []), ("pending", None), ("waiting", None)):
            if k not in st:
                st[k] = v
                changed = True
        if changed:
            g.save_state(st)
        return st


def set_settings(*, enabled=None, author=None, apply_mode=None, now=None) -> dict:
    with _LOCK:
        st = settings(now=now)
        if author is not None:
            if not _valid_author(author):
                raise ValueError("not a model Friday can use: %r" % (author,))
            st["author"] = author
            st["waiting"] = None
        if enabled is not None:
            st["enabled"] = bool(enabled)
        if apply_mode is not None:
            if apply_mode not in APPLY_MODES:
                raise ValueError("apply mode must be one of %s" % (APPLY_MODES,))
            st["apply_mode"] = apply_mode
        g.save_state(st)
        return st


def due(*, now=None) -> bool:
    st = settings(now=now)
    now = float(now if now is not None else time.time())
    return bool(st.get("enabled")) and now >= float(st.get("last_step_at") or 0) + WEEK_S


# ═══════════════════════════════════════════════════════════════════════════
#  Availability of the chosen author
# ═══════════════════════════════════════════════════════════════════════════

def _consent_allows_cloud():
    try:
        from agent_friday.privacy import cloud_consent as cc
        s = cc.resolve()
        if not s.answered:
            return False, "cloud use has not been allowed yet"
        if s.choice == cc.CHOICE_LOCAL:
            return False, "Friday is set to keep everything on this computer"
        return True, ""
    except Exception:
        return False, "cloud consent could not be read"


def _frontier_available():
    """(ok, reason, candidates): candidates are "provider:model" strings."""
    ok, why = _consent_allows_cloud()
    if not ok:
        return False, why, []
    try:
        from agent_friday.services import one_key
        cands = []
        if one_key.anthropic_ready():
            cands.append("anthropic:" + one_key._default_claude_model())
        if one_key.openrouter_ready():
            cands.append("openrouter:" + one_key.openrouter_id_for(one_key._default_claude_model()))
        if not cands:
            return False, "no cloud model is connected", []
        return True, "", cands
    except Exception:
        return False, "no cloud model is connected", []


def _cloud_available(provider):
    ok, why = _consent_allows_cloud()
    if not ok:
        return False, why
    try:
        from agent_friday.services import one_key
        ready = one_key.anthropic_ready() if provider == "anthropic" else one_key.openrouter_ready()
        return (True, "") if ready else (False, "no %s key is connected" % provider)
    except Exception:
        return False, "no %s key is connected" % provider


def _local_seat():
    """The local model this install runs, if one is configured."""
    try:
        from agent_friday.core import _load_settings
        rc = (_load_settings() or {}).get("model_routing") or {}
        m = rc.get("local_model")
        return m if isinstance(m, str) and _MODEL_ID.match(m) else None
    except Exception:
        return None


def _local_available(model) -> bool:
    try:
        from agent_friday.services import local_call
        if local_call.seat_endpoint(model):
            return True
        return model in local_call._daemon_tags()
    except Exception:
        return False


def _idle_blocked() -> str:
    try:
        from agent_friday.services.scheduler import idle_work_blocked_reason
        return idle_work_blocked_reason() or ""
    except Exception:
        return ""


def availability(author):
    """(ok, reason, fixes) for the chosen author. Never switches it."""
    fixes = []
    if author == "seeded":
        return True, "", fixes
    if author == "frontier":
        ok, why, _ = _frontier_available()
    elif author.startswith("cloud:"):
        ok, why = _cloud_available(author.split(":", 2)[1])
    else:
        model = author[len("local:"):]
        ok = _local_available(model)
        why = "" if ok else "the local model %s is not running" % model
        if not ok:
            fixes.append("choose_another")
        return ok, why, fixes
    if not ok:
        fixes.append("connect_cloud")
        seat = _local_seat()
        if seat:
            fixes.append("use_local:" + seat)
    return ok, why, fixes


# ═══════════════════════════════════════════════════════════════════════════
#  What the step is about: the structure on screen, and the week's signals
# ═══════════════════════════════════════════════════════════════════════════

def _read_json(path):
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def current_structure() -> str:
    """The structure the user is running: the pinned one, else the four-day
    calendar's (routes/insights.py). Time-lapse cycling does not count."""
    data = {**_read_json(PERSONALITY_FILE), **_read_json(EVOLUTION_FILE)}
    pin = data.get("preferred_scene_index")
    if isinstance(pin, int) and not isinstance(pin, bool) and 0 <= pin < len(g.STRUCTURE_IDS):
        return g.STRUCTURE_IDS[pin]
    try:
        first = date.fromisoformat(str(data.get("first_launch")))
    except (TypeError, ValueError):
        first = date.today()
    days = max(1, (date.today() - first).days + 1)
    return g.STRUCTURE_IDS[((days - 1) // 4) % len(g.STRUCTURE_IDS)]


def _signals_dir():
    return g.AVATAR_DIR / "signals"


def _ledger_rows(since, until):
    from agent_friday.services import activity_ledger
    return activity_ledger.read(limit=100000, since=since, until=until)


def nightly_signals(*, day=None):
    """Count one day's on-record activity into signals/<day>.json. Numbers
    only; off-the-record rows are skipped; a day with nothing on the record
    writes nothing."""
    d = date.fromisoformat(day) if day else date.today() - timedelta(days=1)
    start = datetime(d.year, d.month, d.day).timestamp()
    rows = [r for r in (_ledger_rows(start, start + 86400) or []) if not r.get("off_record")]
    if not rows:
        return None
    row = {k: 0 for k in SIGNAL_KEYS}
    for r in rows:
        kind = r.get("kind")
        if kind == "model_invocation":
            row["turns"] += 1
            if _is_local_provider(r.get("provider")):
                row["local_calls"] += 1
        elif kind == "tool_call":
            row["tools"] += 1
            if r.get("ok") is False:
                row["tools_failed"] += 1
        elif kind == "subagent_spawn":
            row["subagents"] += 1
    _signals_dir().mkdir(parents=True, exist_ok=True)
    (_signals_dir() / ("%s.json" % d.isoformat())).write_text(json.dumps(row), encoding="utf-8")
    return row


def _is_local_provider(p):
    try:
        from agent_friday.services.egress_gate import is_local_provider
        return bool(p) and is_local_provider(str(p))
    except Exception:
        return False


def _collect_signals():
    """The unconsumed rows, newest SIGNAL_DAYS_MAX, summed; and their files."""
    d = _signals_dir()
    files = sorted(d.glob("*.json"))[-SIGNAL_DAYS_MAX:] if d.exists() else []
    total = {k: 0 for k in SIGNAL_KEYS}
    for f in files:
        row = _read_json(f)
        for k in SIGNAL_KEYS:
            v = row.get(k)
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                total[k] += v
    return total, files


# ═══════════════════════════════════════════════════════════════════════════
#  The payload: numbers, booleans and fixed enums only (§5.3)
# ═══════════════════════════════════════════════════════════════════════════

def build_payload(genome, *, target, step_number, signals, simplify) -> dict:
    gen = g.clamp_absolute(genome)
    bounds = {"/".join(p): spec for p, spec in g._iter_genes(target)}
    return {
        "schema": "friday.avatar.evolve/2",
        "step": int(step_number),
        "target_structure": target,
        "genome": {k: v for k, v in gen.items()},
        "bounds": {k: {kk: (list(vv) if isinstance(vv, tuple) else vv)
                       for kk, vv in spec.items()} for k, spec in bounds.items()},
        "signals": {k: int(signals.get(k, 0)) for k in SIGNAL_KEYS},
        "simplify": bool(simplify),
    }


def _allowed_strings(target):
    enums = {"friday.avatar.evolve/2", target, "f", "i", "e"}
    for genes in g.SHARED_GENES.values():
        for spec in genes.values():
            enums.update(spec.get("values", ()))
    return enums


def payload_problems(payload) -> list:
    """Every string in the payload must be a fixed enum or a known key.
    Returns [] for a payload that may leave."""
    problems = []
    target = payload.get("target_structure")
    if target not in g.STRUCTURE_IDS:
        problems.append("target_structure is not a structure")
    allowed = _allowed_strings(target)
    keys = set(SIGNAL_KEYS) | {"schema", "step", "target_structure", "genome", "bounds",
                               "signals", "simplify", "structures", "facets",
                               "kind", "min", "max", "step", "default", "values", "every",
                               "count", "anchor_hue", "scheme_changed_at",
                               "symmetry_changed_at", "ease_changed_at"}
    keys |= set(g.SHARED_GENES) | set(g.STRUCTURE_IDS)
    for genes in list(g.SHARED_GENES.values()) + list(g.STRUCTURE_GENES.values()):
        keys |= set(genes)
    bound_keys = set()
    for p, _ in g._iter_genes(target):
        bound_keys.add("/".join(p))

    def walk(v, path):
        if isinstance(v, dict):
            for k, vv in v.items():
                if k not in keys and not (path[:1] == ["bounds"] and k in bound_keys):
                    problems.append("unexpected key %s" % "/".join(path + [str(k)]))
                walk(vv, path + [str(k)])
        elif isinstance(v, list):
            for i, vv in enumerate(v):
                walk(vv, path + [str(i)])
        elif isinstance(v, str):
            if v not in allowed:
                problems.append("free text at %s" % "/".join(path))
        elif v is None or isinstance(v, (bool, int, float)):
            pass
        else:
            problems.append("unexpected value at %s" % "/".join(path))
    walk(payload, [])
    if set(payload.get("signals", {})) - set(SIGNAL_KEYS):
        problems.append("unknown signal")
    return problems


SYSTEM_PROMPT = (
    "You are shaping the look of Friday, a personal AI assistant shown as a "
    "holographic structure. You receive her current visual genome, the bounds "
    "of every gene, and a week of activity counts. Propose her next small "
    "change. Stay inside the bounds; small, legible steps are best. When "
    "'simplify' is true, move genes back toward calm and simple. Reply with "
    "JSON only: {\"proposed_genome\": <the whole genome>, \"rationale\": "
    "<at most 280 characters, plain words>, \"name\": <a short title for this look>}."
)


# ═══════════════════════════════════════════════════════════════════════════
#  Authors
# ═══════════════════════════════════════════════════════════════════════════

class AuthorFailed(RuntimeError):
    pass


_WORDS_A = ("Tide", "Glass", "Quiet", "North", "Lumen", "Harbor", "Cinder", "Vale",
            "Cobalt", "Drift", "Signal", "Ember", "Frost", "Loom", "Relay", "Meridian")
_WORDS_B = ("Lattice", "Current", "Weave", "Arc", "Field", "Crown", "Pulse", "Grid",
            "Orbit", "Shell", "Spire", "Wake", "Knot", "Veil", "Prism", "Choir")


def _decline_weight(gene_path, now):
    st = g.state()
    at = (st.get("declined") or {}).get(gene_path.split("/")[0])
    if not at:
        return 1.0
    age = max(0.0, float(now) - float(at))
    return 0.15 + 0.85 * min(1.0, age / DECLINE_FADE_S)


def _seeded_propose(genome, *, target, signals, step_number, simplify, now=None, attempt=0):
    """Friday's own change, no model: a deterministic draw from the seed and
    the step number, steered by the week's counts."""
    now = float(now if now is not None else time.time())
    rng = random.Random(int.from_bytes(
        hashlib.sha256(g.seed() + b"step" + str(step_number).encode()
                       + b"/" + str(attempt).encode()).digest()[:8], "big"))
    gen = g.clamp_absolute(genome)
    prop = json.loads(json.dumps(gen))
    genes = list(g._iter_genes(target))
    weights = []
    busy = min(1.0, (signals.get("tools", 0) + signals.get("subagents", 0) * 5) / 200.0)
    for path, spec in genes:
        w = 1.0
        if path[0] == "form":
            w += busy
        if path[0] == "structures":
            w += 0.5
        w *= _decline_weight("/".join(path), now)
        weights.append(w)
    picks = set()
    for _ in range(rng.randint(1, 3)):
        picks.add(rng.choices(range(len(genes)), weights=weights)[0])
    for i in sorted(picks):
        path, spec = genes[i]
        cur = g._get(prop, path)
        if spec["kind"] == "e":
            if not simplify:
                g._set(prop, path, rng.choice([v for v in spec["values"] if v != cur] or [cur]))
            continue
        if simplify:
            d = spec["default"]
            nv = cur + max(-spec["step"], min(spec["step"], d - cur))
        elif spec["kind"] == "i":
            nv = cur + rng.choice((-1, 1)) * spec["step"]
        else:
            nv = cur + rng.choice((-1, 1)) * spec["step"] * rng.choice((0.5, 1.0))
        nv = int(round(nv)) if spec["kind"] == "i" else round(nv, 6)
        g._set(prop, path, nv)
    if signals.get("subagents", 0) > 0 and rng.random() < 0.5:
        prop["facets"] = gen["facets"] + 1
    name = "%s %s" % (rng.choice(_WORDS_A), rng.choice(_WORDS_B))
    reason = ("A calmer, simpler look." if simplify else
              "A small change of Friday's own, shaped by a %s week."
              % ("busy" if busy > 0.3 else "quiet"))
    return {"proposed_genome": prop, "rationale": reason, "name": name}


def _call_model(author, system, user):
    """One call to the chosen model. Returns (text, report); report["model"]
    is the model the provider says answered. Raises on failure."""
    if author.startswith("local:"):
        from agent_friday.services import local_call
        model = author[len("local:"):]
        text = local_call.call(system, user, model, json_mode=True, max_tokens=1200)
        if not text:
            raise AuthorFailed("the local model %s did not answer" % model)
        return text, {"model": model}
    provider, model = author.split(":", 1)
    from agent_friday.services import model_router
    msgs = [{"role": "user", "content": user}]
    if provider == "anthropic":
        rep = {}
        text = model_router._call_claude(msgs, system=system, model=model, max_tokens=1200,
                                         report=rep)
        return text, rep
    from agent_friday.services import one_key
    text, _trace = model_router._call_openai(msgs, system=system, model=model, max_tokens=1200,
                                             provider=one_key.OPENROUTER, fallback_models=None)
    return text, {"model": None}


def _parse(text):
    try:
        from agent_friday.services.local_call import extract_json
        d = extract_json(text)
    except Exception:
        d = None
    if not isinstance(d, dict) or not isinstance(d.get("proposed_genome"), dict):
        return None
    return {"proposed_genome": d["proposed_genome"],
            "rationale": str(d.get("rationale") or "")[:280],
            "name": str(d.get("name") or "")[:60] or None}


def _pick_frontier(candidates):
    """Friday's pick among the frontier models she can use: one that has not
    shaped her in the last three steps, if there is one."""
    recent = [((s.get("author") or {}).get("requested") or (s.get("author") or {}).get("model"))
              for s in g.history()[-3:]]
    fresh = [c for c in candidates if c.split(":", 1)[1] not in recent]
    choice = (fresh or candidates)[0]
    model = choice.split(":", 1)[1]
    why = ("Chose %s: it has not shaped Friday in her last three looks." % model if fresh
           and recent else "Chose %s: the frontier model connected here." % model)
    return choice, why


def _model_propose(author_key, payload, why=None):
    """Ask a model; one reformat retry to the same model. Returns (proposal,
    credit) or raises AuthorFailed."""
    user = json.dumps(payload, sort_keys=True)
    problems = payload_problems(payload)
    if problems:
        raise AuthorFailed("the payload failed its own allowlist: %s" % problems[0])
    last = None
    for attempt in range(2):
        text, rep = _call_model(author_key, SYSTEM_PROMPT, user if attempt == 0 else
                                user + "\n\nReply with the JSON object only.")
        parsed = _parse(text)
        if parsed:
            requested = author_key.split(":", 1)[1]
            reported = (rep or {}).get("model")
            credit = {"requested": requested, "model": reported or requested,
                      "rationale": parsed["rationale"]}
            if reported and reported != requested:
                credit["warning"] = ("asked for %s but %s answered" % (requested, reported))
            if why:
                credit["why_this_model"] = why
            return parsed, credit
        last = text
    raise AuthorFailed("the model's reply was not usable%s" % ("" if last is None else ""))


# ═══════════════════════════════════════════════════════════════════════════
#  The step
# ═══════════════════════════════════════════════════════════════════════════

def _notify(title, body, key):
    try:
        import agent_friday.notifications_engine as ne
        ne.push(title=title, body=body, priority="low", source="avatar", kind="avatar",
                dedupe_key=key, target={"workspace": "desktop"})
    except Exception as e:
        _log.debug("avatar notice not sent: %s", e)


def _wait(st, author, why, fixes, now):
    week = int(now // WEEK_S)
    first = not st.get("waiting") or st["waiting"].get("week") != week
    st["waiting"] = {"since": (st.get("waiting") or {}).get("since", now), "author": author,
                     "reason": why, "fixes": fixes, "week": week}
    g.save_state(st)
    if first:
        title = ("Evolution is waiting for a cloud model" if author == "frontier"
                 or author.startswith("cloud:") else "Evolution is waiting for %s"
                 % author.split(":", 1)[-1])
        _notify(title, why[:1].upper() + why[1:] + ".", "avatar-waiting-%d" % week)
    return {"status": "waiting", "reason": why, "fixes": fixes}


def run_step(*, manual=False, now=None, author=None) -> dict:
    """One step, if the chosen author can take it. Never switches author."""
    with _LOCK:
        now = float(now if now is not None else time.time())
        st = settings(now=now)
        if not st.get("enabled") and not manual:
            return {"status": "off"}
        author = author or st["author"]
        ok, why, fixes = availability(author)
        if not ok:
            return _wait(st, author, why, fixes, now)
        if author.startswith("local:") and not manual:
            blocked = _idle_blocked()
            if blocked:
                return {"status": "deferred", "reason": blocked}
        target = current_structure()
        parent = g.active_step()
        parent_gen = g.active_genome()
        number = (parent or {}).get("step", 0) + 1
        simplify = number % 4 == 0
        signals, files = _collect_signals()
        payload = build_payload(parent_gen, target=target, step_number=number,
                                signals=signals, simplify=simplify)
        digest = "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True)
                                            .encode()).hexdigest()
        try:
            if author == "seeded":
                # A draw can clamp to nothing (a gene already at its bound);
                # the seeded author then draws again, up to five times.
                for attempt in range(5):
                    prop = _seeded_propose(parent_gen, target=target, signals=signals,
                                           step_number=number, simplify=simplify, now=now,
                                           attempt=attempt)
                    trial, moved_ = g.clamp_step(parent_gen, prop["proposed_genome"],
                                                 target=target, step_number=number)
                    if moved_ and not g.validate(trial):
                        break
                credit = {"path": "seeded", "model": None}
            elif author == "frontier":
                _ok, _why, cands = _frontier_available()
                choice, why_model = _pick_frontier(cands)
                prop, c = _model_propose(choice, payload, why_model)
                credit = dict(c, path="cloud", provider=choice.split(":", 1)[0])
            elif author.startswith("cloud:"):
                _, provider, model = author.split(":", 2)
                prop, c = _model_propose(provider + ":" + model, payload)
                credit = dict(c, path="cloud", provider=provider, chosen_by="you")
            else:
                prop, c = _model_propose(author, payload)
                credit = dict(c, path="local", provider="local")
        except Exception as e:
            reason = str(e) if isinstance(e, AuthorFailed) else "the model call failed"
            skips = (st.get("skips") or [])[-49:] + [{"at": now, "author": author,
                                                      "reason": reason}]
            g.update_state(skips=skips, last_step_at=now, waiting=None)
            _notify("Friday's look stayed the same this week",
                    reason[:1].upper() + reason[1:] + ". You can try again.",
                    "avatar-skip-%d" % int(now // WEEK_S))
            return {"status": "skipped", "reason": reason}
        child, moved = g.clamp_step(parent_gen, prop["proposed_genome"], target=target,
                                    step_number=number)
        problems = g.validate(child)
        if problems:
            # Keep the proposal's form; fall back to the parent's colours.
            child["palette"] = dict(parent_gen["palette"])
            moved = [m for m in moved if not m.startswith("palette/")]
            problems = g.validate(child)
        if problems or not moved and child.get("facets") == parent_gen.get("facets"):
            reason = problems[0] if problems else "the proposal did not change anything"
            skips = (st.get("skips") or [])[-49:] + [{"at": now, "author": author,
                                                      "reason": reason}]
            g.update_state(skips=skips, last_step_at=now, waiting=None)
            return {"status": "skipped", "reason": reason}
        ask = st.get("apply_mode") == "ask"
        kind = "simplify" if simplify else ("manual" if manual else "growth")
        step = g.commit_step(child, parent=(parent or {}).get("content_hash"), kind=kind,
                             target=target, reason=prop.get("rationale") or "",
                             author=credit, input_digest=digest, name=prop.get("name"),
                             activate=not ask,
                             sent=payload if credit.get("path") == "cloud" else None)
        for f in files:
            try:
                f.unlink()
            except OSError:
                pass
        g.update_state(last_step_at=now, waiting=None,
                       pending=step["content_hash"] if ask else None)
        who = credit.get("model") or "Friday"
        _notify(("A new look is waiting for you" if ask else "Friday's look changed"),
                "%s%s (made by %s)." % (step.get("name") or "A new look",
                                        (": " + step["reason"]) if step.get("reason") else "",
                                        who),
                "avatar-step-%s" % step["content_hash"][-12:])
        return {"status": "pending" if ask else "stepped", "step": step["content_hash"],
                "moved": moved}


def tick(*, now=None) -> dict:
    """The hourly check (the scheduler's avatar_growth_check)."""
    now = float(now if now is not None else time.time())
    st = settings(now=now)
    if not st.get("enabled"):
        return {"status": "off"}
    if not due(now=now):
        return {"status": "not_due"}
    return run_step(now=now)


def evolve_now(*, now=None) -> dict:
    return run_step(manual=True, now=now)


def use_local_instead(model, *, now=None) -> dict:
    """The notice's second fix: make `model` the author (it sticks) and run
    the waiting step with it."""
    set_settings(author="local:" + str(model), now=now)
    return run_step(manual=True, now=now)


def apply_pending() -> dict:
    with _LOCK:
        st = settings()
        h = st.get("pending")
        if not h:
            return {"status": "none"}
        g.rollback(h)
        g.update_state(pending=None)
        return {"status": "applied", "step": h}


def _moved_sections(step):
    return sorted({d["gene"].split("/")[0] for d in (step or {}).get("diff") or []})


def decline_pending(*, now=None) -> dict:
    with _LOCK:
        now = float(now if now is not None else time.time())
        st = settings()
        h = st.get("pending")
        if not h:
            return {"status": "none"}
        declined = dict(st.get("declined") or {})
        for sec in _moved_sections(g.load_step(h)):
            declined[sec] = now
        g.update_state(pending=None, declined=declined)
        return {"status": "declined", "step": h}


def undo(*, now=None) -> dict:
    """One-click undo: back to the parent, and the undone kind of change is
    weighted down for a while."""
    with _LOCK:
        now = float(now if now is not None else time.time())
        cur = g.active_step()
        if cur is None:
            return {"status": "none"}
        g.undo()
        st = settings()
        declined = dict(st.get("declined") or {})
        for sec in _moved_sections(cur):
            declined[sec] = now
        g.update_state(declined=declined)
        return {"status": "undone", "step": cur["content_hash"]}


def status() -> dict:
    st = settings()
    return {k: st.get(k) for k in ("enabled", "author", "apply_mode", "last_step_at",
                                   "announced_at", "waiting", "pending")} | {
        "next_due": float(st.get("last_step_at") or 0) + WEEK_S,
        "local_seat": _local_seat(),
        "skips": (st.get("skips") or [])[-5:],
    }


def nightly() -> None:
    """The nightly job: count yesterday, and purge looks the user deleted
    more than 30 days ago (the only code that removes history)."""
    nightly_signals()
    g.purge()


def register_jobs() -> None:
    """The hourly step check and the nightly signal count."""
    from agent_friday.services import scheduler
    scheduler.register_builtin_task(
        "avatar_growth_check", lambda: tick(), label="Avatar: weekly look check",
        default_trigger="interval", default_spec={"every_minutes": 60}, notify="silent")
    scheduler.register_builtin_task(
        "avatar_signals_nightly", lambda: nightly(), label="Avatar: count the day",
        default_trigger="daily", default_spec={"hour": 3, "minute": 40}, notify="silent")
