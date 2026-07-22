"""
Agent Friday — Interest Model (read-only assembly)
FutureSpeak.AI · Asimov's Mind

V6_WHOLENESS_SPEC.md §4 Phase 4 / AUTONOMY_SPEC.md §8 (A2 — Dissent-Lite). Feeds
services/dissent_gate.py. This module answers one question: *what has the user
signalled they want*, assembled from durable stores Friday already keeps —
distinct from the *instruction* she's just been given this turn. The dissent
gate compares the two; this module only assembles the "what they want" side
and never judges, scores, or mutates anything.

Assembled from four sources:
  * preferences  — durable, non-prohibitive facts from user_model.py (likes,
                    workflow habits, expertise) via note_fact()/profile().
  * constraints  — the SAME user_model store, but queried DIRECTLY and scoped
                    to fact categories that read as an explicit boundary (see
                    CONSTRAINT_CATEGORIES), via user_model.facts_by_categories()
                    — deliberately NOT via profile()['facts'], which is
                    user_model's _recent_facts(50): a GLOBAL top-50 across ALL
                    categories combined, meant for the Settings UI. Routing
                    constraints through that shared, capped view let unrelated
                    fact volume (ordinary preferences accumulating over weeks)
                    silently push a real "never do X" constraint out of the
                    top 50 with no count, warning, or any surfaced signal —
                    see count_constraint_facts() / assemble_interest_model()'s
                    constraints_truncated_count for how that gap is now made
                    visible instead. record_constraint() is the intended way
                    to write a constraint.
  * safety       — the subscribed content_policies packs + moderation family
                    mode. Informational only here: content_policies.py remains
                    the one and only enforcement point for the H1-H4 floor
                    (see dissent_gate.py's Law-1 precedence) — this module
                    never re-implements or second-guesses that floor, it just
                    reports which packs are active so a dissent statement can
                    reference "you've got family-safe on" where relevant.
  * goals        — NOT YET REAL. AUTONOMY_SPEC Phase A3 (Durable Goals) hasn't
                    landed, so there is no goals store to read from. Instead of
                    importing a module that doesn't exist yet, this exposes a
                    swappable provider hook (register_goals_provider) that
                    defaults to returning [] and is expected to be replaced by
                    a real `services.goals.active_goals`-shaped callable once
                    A3 ships. Nothing here breaks, changes behavior, or needs
                    editing when that happens — A3 just calls
                    register_goals_provider(services.goals.active_goals).

Design rules (consistent with the rest of the codebase):
  * Leaf-ish module — no Flask, no routes import. Lazy cross-service imports
    (user_model / content_policies / moderation) at call time, mirroring
    model_router's and persona_eval's own lazy-import style, so importing this
    module stays cheap and it degrades gracefully if a sibling service is
    unavailable for any reason.
  * Read-only. Nothing in this module writes to user_model, content_policies,
    or moderation stores except record_constraint(), which is an explicit,
    named convenience for STORING a new constraint (a thin wrapper over
    user_model.note_fact) — assemble_interest_model() itself never writes.
  * Never raises to the caller. Every public function returns a well-formed
    value (a dict or a list) on any internal failure; failures are logged, not
    propagated — the dissent gate must be able to run even in a degraded
    environment (a fresh install with an empty user_model.db, for instance).
  * Local-only. No network, no LLM calls — this is heuristic assembly of
    already-local data, same posture as user_model.py itself.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger("friday.interest_model")

# Fact categories (as stored via user_model.note_fact(category=...)) that read
# as an explicit boundary rather than a mere preference. record_constraint()
# below always writes "constraint"; the other two are accepted for callers
# that already used a synonymous category before this module existed.
CONSTRAINT_CATEGORIES = frozenset({"constraint", "boundary", "restriction"})

_DEFAULT_MAX_FACTS = 20
_DEFAULT_MAX_CONSTRAINTS = 40

# Internal safety cap on how many constraint-category facts are pulled from
# the store BEFORE max_items slices the result — deliberately much larger
# than any realistic number of explicitly-recorded constraints, so a
# constraint's visibility depends only on how many OTHER CONSTRAINTS exist,
# never on unrelated (preference/bio/expertise) fact volume. See
# gather_constraints() / count_constraint_facts().
_CONSTRAINT_FETCH_CAP = 2000


# ═══════════════════════════════════════════════════════════════════════════
#  PREFERENCES / CONSTRAINTS — both read from user_model.py's durable facts
# ═══════════════════════════════════════════════════════════════════════════

def _user_model_facts() -> List[Dict[str, Any]]:
    """Every durable fact user_model.py currently holds. [] on any failure —
    a brand-new install with no user_model.db yet is a normal, not an error,
    state."""
    try:
        from agent_friday.services import user_model as um
        facts = (um.profile() or {}).get("facts") or []
        return facts if isinstance(facts, list) else []
    except Exception as e:
        _log.debug("user_model facts unavailable, degrading to []: %s", e)
        return []


def gather_preferences(max_items: int = _DEFAULT_MAX_FACTS) -> List[Dict[str, Any]]:
    """Durable facts that are NOT explicit constraints — what the user likes,
    knows, or works like, as opposed to a "never do X" boundary."""
    out = []
    for f in _user_model_facts():
        cat = str(f.get("category") or "").strip().lower()
        if cat in CONSTRAINT_CATEGORIES:
            continue
        text = str(f.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "text": text,
            "category": cat or "general",
            "confidence": float(f.get("confidence") or 0.0),
            "source": str(f.get("source") or ""),
        })
    return out[:max_items]


def _constraint_facts() -> List[Dict[str, Any]]:
    """Every constraint-category fact, queried DIRECTLY from user_model.py
    and scoped to CONSTRAINT_CATEGORIES via facts_by_categories() — NEVER
    routed through user_model.profile()['facts'], which is a GLOBAL top-50
    across ALL categories combined (user_model._recent_facts, ranked for the
    Settings UI). Filtering that shared, capped view meant a constraint could
    silently vanish the moment 50+ OTHER (non-constraint) facts outranked it,
    with no count, warning, or any surfaced signal that truncation occurred.
    Querying category-scoped instead means a constraint's visibility depends
    only on how many OTHER CONSTRAINTS exist, never on unrelated fact volume.
    [] on any failure — a brand-new install with no user_model.db yet is a
    normal, not an error, state."""
    try:
        from agent_friday.services import user_model as um
        facts = um.facts_by_categories(CONSTRAINT_CATEGORIES, limit=_CONSTRAINT_FETCH_CAP)
        return facts if isinstance(facts, list) else []
    except Exception as e:
        _log.debug("user_model constraint facts unavailable, degrading to []: %s", e)
        return []


def count_constraint_facts() -> int:
    """The TRUE total number of constraint-category facts currently stored —
    independent of gather_constraints()'s max_items and of
    _CONSTRAINT_FETCH_CAP. Lets a caller (assemble_interest_model, a future
    A3 approvals surface, tests) detect whether either cap actually
    truncated anything, so that gap is a countable, visible fact rather than
    a silent drop. 0 on any failure (degrades the same way every other reader
    in this module does)."""
    try:
        from agent_friday.services import user_model as um
        return int(um.count_facts_by_categories(CONSTRAINT_CATEGORIES) or 0)
    except Exception as e:
        _log.debug("count_constraint_facts unavailable, degrading to 0: %s", e)
        return 0


def gather_constraints(max_items: int = _DEFAULT_MAX_CONSTRAINTS) -> List[Dict[str, Any]]:
    """Durable facts stored under a constraint-like category, queried
    directly and scoped to CONSTRAINT_CATEGORIES (see _constraint_facts) —
    NOT filtered out of user_model.profile()'s globally-capped-at-50 view,
    which would let unrelated fact volume silently push a real constraint
    out of sight (see module docstring). See dissent_gate.py for how these
    get matched against an instruction — only constraints PHRASED as a
    prohibition ("never...", "don't...", "always ask before...") are ever
    eligible to trigger a conflict; a constraint fact that doesn't read that
    way is still returned here (for legibility/audit) but dissent_gate's
    heuristic will not key off it."""
    out = []
    for f in _constraint_facts():
        text = str(f.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "text": text,
            "category": str(f.get("category") or "").strip().lower(),
            "confidence": float(f.get("confidence") or 0.0),
            "source": str(f.get("source") or ""),
        })
    return out[:max_items]


def record_constraint(text: str, *, confidence: float = 0.9,
                       source: str = "user_stated") -> Dict[str, Any]:
    """Durably store an explicit constraint. Thin, named wrapper over
    user_model.note_fact("constraint", ...) — exists so callers have one
    obvious way to record a boundary rather than reaching into user_model's
    generic category string themselves. Phrase `text` as a prohibition (see
    gather_constraints docstring) if you want it to ever drive a dissent."""
    if not text or not str(text).strip():
        return {"ok": False, "error": "empty constraint text"}
    try:
        from agent_friday.services import user_model as um
        return um.note_fact("constraint", str(text).strip(),
                             confidence=confidence, source=source)
    except Exception as e:
        _log.warning("record_constraint failed: %s", e)
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
#  SAFETY POSTURE — informational only (content_policies stays authoritative)
# ═══════════════════════════════════════════════════════════════════════════

def gather_safety_posture() -> Dict[str, Any]:
    """A read-only summary of the active content floor / moderation posture.
    Never enforces anything itself — content_policies.py + moderation.py stay
    the single source of truth; dissent_gate.py consults THEM directly for
    the actual Law-1 check. This is purely descriptive context (e.g. so a
    dissent statement can say "you've got family-safe on")."""
    posture: Dict[str, Any] = {
        "family_mode": False,
        "subscribed_packs": [],
        "always_on_pack": None,
    }
    try:
        from agent_friday.services import moderation
        policy = moderation.get_policy() or {}
        posture["family_mode"] = bool(policy.get("family_mode", False))
    except Exception as e:
        _log.debug("moderation policy unavailable: %s", e)
    try:
        from agent_friday.services import content_policies as cp
        packs = cp.get_subscribed_packs() or []
        posture["subscribed_packs"] = sorted({
            str(p.get("pack_id")) for p in packs if p.get("pack_id")
        })
        posture["always_on_pack"] = cp.ALWAYS_ON_PACK
    except Exception as e:
        _log.debug("content_policies subscriptions unavailable: %s", e)
    return posture


# ═══════════════════════════════════════════════════════════════════════════
#  GOALS — deferred to Phase A3; a defensive, swappable no-op provider today
# ═══════════════════════════════════════════════════════════════════════════

def _no_goals() -> List[Dict[str, Any]]:
    return []


_goals_provider: Callable[[], List[Dict[str, Any]]] = _no_goals


def register_goals_provider(fn: Optional[Callable[[], List[Dict[str, Any]]]]) -> None:
    """Swap in a real goals source once Phase A3 (services/goals.py) exists.
    Passing None restores the default no-op (always [])."""
    global _goals_provider
    _goals_provider = fn if fn is not None else _no_goals


def get_active_goals() -> List[Dict[str, Any]]:
    """Whatever the registered provider returns, defensively. Never raises —
    a broken/unregistered provider degrades to [] rather than breaking the
    interest model (and therefore the dissent gate) it feeds."""
    try:
        result = _goals_provider()
        return list(result) if result else []
    except Exception as e:
        _log.warning("goals provider raised, degrading to []: %s", e)
        return []


# ═══════════════════════════════════════════════════════════════════════════
#  ASSEMBLY — the one function dissent_gate.py actually calls
# ═══════════════════════════════════════════════════════════════════════════

def assemble_interest_model(*, max_preferences: int = _DEFAULT_MAX_FACTS,
                             max_constraints: int = _DEFAULT_MAX_CONSTRAINTS
                             ) -> Dict[str, Any]:
    """The read-only "what the user has signalled they want" snapshot.

    Returns:
        {
          "preferences": [{"text", "category", "confidence", "source"}, ...],
          "constraints": [{"text", "category", "confidence", "source"}, ...],
          "constraints_truncated_count": int,  # 0 unless max_constraints (or
                                                # the internal fetch cap) cut
                                                # off real, stored constraints
                                                # — see count_constraint_facts().
                                                # Non-zero is a visible signal
                                                # that boundaries exist beyond
                                                # what this snapshot returned,
                                                # never a silent drop.
          "safety": {"family_mode", "subscribed_packs", "always_on_pack"},
          "goals": [...],   # [] until Phase A3 registers a real provider
        }

    Deliberately excludes any wall-clock field — this is a pure function of
    current store state, not an event, so two calls with unchanged stores
    return equal dicts (useful for tests and for anything that wants to diff
    "did the interest model change").
    """
    constraints = gather_constraints(max_constraints)
    total_constraints = count_constraint_facts()
    return {
        "preferences": gather_preferences(max_preferences),
        "constraints": constraints,
        "constraints_truncated_count": max(0, total_constraints - len(constraints)),
        "safety": gather_safety_posture(),
        "goals": get_active_goals(),
    }
