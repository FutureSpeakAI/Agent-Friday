"""Which module actually reads each declared model seat — and how.

Friday declares sixteen seats in ``DEFAULT_SETTINGS["capability_routing"]``.
Each is defaulted, mapped in ``seat_binding``, given a label in the Intelligence
route, and rendered in the picker. Consuming them is a separate question, and
until this module existed nothing asked it: ``function_manager``,
``memory_manager``, ``orchestrator``, ``sidekick_fast`` and ``researcher`` are
declared, defaulted, mapped, labelled — and read by nothing. A user picks a
model for one of those seats and the choice does nothing at all.

That is this codebase's signature defect, and ``services/liveness_audit.py``
already names it for subsystems:

    1. RAN       — has it executed recently?
    2. PRODUCED  — is its most recent output non-empty?
    3. CONSUMED  — does anything actually read that output?

This module asks question 3 about SEATS. It is deliberately the same
vocabulary; a fourth way of saying "consumed" is how ``embedder`` and
``embeddings_manager`` became two names for one seat.


SELECTS vs DISPLAYS — the distinction that does the work
--------------------------------------------------------
A seat can be read without being obeyed. ``capability_router.resolve()`` reads
``capability_routing["embedding"]`` at services/capability_router.py:97 — but
only to decide whether to show an availability badge. Nothing consults that
value when choosing an embedding model; ``conversation_memory.EMBED_MODEL`` is
a module constant pinned to ``all-MiniLM-L6-v2``.

So ``embedding`` has a reader and no chooser. Counting any read as "consumed"
would certify it as live, which is precisely the failure this module exists to
catch. Hence two kinds:

  * ``SELECTS``  — the read determines which model actually runs. A user's
                   choice changes behaviour. Only this counts as live.
  * ``DISPLAYS`` — the read populates a badge, report or log. A user's choice
                   changes what is shown and nothing else.

Only ``SELECTS`` makes a seat assignable.


WHAT THIS VERIFIES, AND WHAT IT DOES NOT
----------------------------------------
``verify()`` parses the named module and checks three things statically: the
module imports, the named symbol exists in it, and both the seat key and
``capability_routing`` appear as literals in its source.

That proves the named module NAMES this seat and reads the routing table. It
does NOT prove the read executes on any live path — a consumer behind a
permanently-false branch, or in a route nothing calls, passes this check.

The stronger form is a runtime recorder: funnel every seat read through one
accessor that records the key, drive the named consumer, assert the key was
touched. That was costed and declined on 2026-08-23 — seat reads are scattered
across ~18 sites in 13 files (see MODULE NOTE below), so the recorder would
require a cross-cutting refactor of files two other sessions were editing.

This is the weaker form. It is chosen deliberately and its weakness is stated
here so nobody later mistakes it for the stronger guarantee. What it CAN do
that a plain string field cannot: it fails when the named module stops reading
the key, so it cannot rot into documentation the way the roles contract did.


MODULE NOTE — seat reads are scattered, not funnelled
-----------------------------------------------------
Counted 2026-08-23: ``capability_routing`` is read at roughly eighteen sites
across thirteen modules. ``services/capability_router.py`` opens with "The
single resolver that maps a CAPABILITY to a concrete provider+model" — it is
one of the eighteen, not the funnel its docstring claims. ``routing/
model_router.py`` bypasses it three times (:307, :334, :727), loading settings
and indexing ``capability_routing`` inline.

There are also TWO live ``model_router`` modules — ``routing/model_router.py``
(dispatch, CostTracker) and ``services/model_router.py`` (Anthropic/OpenAI
transport). They are different files with different jobs and similar names,
which is worth knowing before grepping for "the" router.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

# How a seat is consumed. Only SELECTS counts as live.
SELECTS = "selects"
DISPLAYS = "displays"

#: The settings key every seat lives under. A consumer must reference it.
ROUTING_KEY = "capability_routing"


class Consumer:
    """A claim that ``module.symbol`` reads seat ``key``, verifiable by AST.

    ``mirror`` names a legacy flat settings key (``voice_model``,
    ``orchestrator_model``, ...) when the consumer reads the seat THROUGH that
    mirror rather than by its capability name. See MIRRORS below -- this field
    exists because omitting it produced a false positive on 2026-08-23.
    """

    __slots__ = ("kind", "mirror", "module", "note", "symbol")

    def __init__(self, module, symbol, kind, note="", mirror=None):
        self.module = module
        self.symbol = symbol
        self.kind = kind
        self.note = note
        self.mirror = mirror

    def __repr__(self):                                    # pragma: no cover
        return f"Consumer({self.module}.{self.symbol}, {self.kind})"


def _orphan(note):
    """A declared seat with no consumer. The note says which, and why."""
    return Consumer(None, None, None, note)


# ── The map ──────────────────────────────────────────────────────────────────
# Every key in DEFAULT_SETTINGS["capability_routing"] must appear here, or
# test_role_consumers::test_every_declared_seat_is_mapped fails. Each entry was
# established by reading the code on 2026-08-23, not by assertion.
CONSUMERS: dict[str, Consumer] = {

    # ── Consumed, and the read chooses a model ───────────────────────────────
    "reasoning": Consumer(
        "agent_friday.services.local_seats", "_configured", SELECTS,
        "_ROLE_TO_CAPABILITY maps brain/judge -> reasoning; _configured reads "
        "capability_routing[key].model and the judgment gate runs what it "
        "returns."),

    "subagent": Consumer(
        "agent_friday.services.local_seats", "_configured", SELECTS,
        "_ROLE_TO_CAPABILITY maps sidekick/extractor -> subagent."),

    "heavy_hitter": Consumer(
        "agent_friday.services.local_seats", "_configured", SELECTS,
        "_ROLE_TO_CAPABILITY maps heavy -> heavy_hitter."),

    "local": Consumer(
        "agent_friday.routes.work_plan", "_seat_for", SELECTS,
        "work_plan.py:229 maps work class reflex -> local and returns "
        "capability_routing[key].model as the model that class runs on."),

    "creative_image": Consumer(
        "agent_friday.services.creative_engine", "_configured_image_model",
        SELECTS,
        "creative_engine.py:120 reads capability_routing.creative_image to "
        "pick the image model."),

    "creative_video": Consumer(
        "agent_friday.routes.core_routes", "list_models", SELECTS,
        "core_routes.py:321 reads capability_routing.creative_video for the "
        "video model surfaced to the client."),

    # ── Read, but only to display ────────────────────────────────────────────
    "embedding": Consumer(
        "agent_friday.services.capability_router", "resolve", DISPLAYS,
        "capability_router.py:97 special-cases the embedding key to force "
        "available=True for the /api/capabilities badge. NOTHING selects an "
        "embedding model from it: conversation_memory.EMBED_MODEL is a module "
        "constant pinned to all-MiniLM-L6-v2 (conversation_memory.py:44), and "
        "the seat default (core/__init__.py:1674) already holds that same "
        "value. Changing this seat is inert -- it cannot even trip the D5 "
        "dimension guard, because that guard fires on EMBED_MODEL changing, "
        "not on this setting. Read, never obeyed."),

    # ── Declared and consumed by nothing ─────────────────────────────────────
    "orchestrator": _orphan(
        "WORKING ROLE. No module reads capability_routing.orchestrator. The "
        "seat exists so Stephen can assign a router model (contract rule R11); "
        "the routing decision is still made by classifier heuristics in "
        "routing/model_router.py, which never consults it."),

    "sidekick_fast": _orphan(
        "WORKING ROLE. No reader. local_seats._ROLE_TO_CAPABILITY has no entry "
        "mapping any role to this key, so _configured can never return it."),

    "function_manager": _orphan(
        "WORKING ROLE, unbuilt and wanted. Would let a model without native "
        "tool calling delegate to a small specialist. Absence recorded in "
        "KNOWN_ISSUES.md and explained at services/model_plan.py:37 and :474 "
        "-- 'a role in the residency contract that nothing consults'. It only "
        "bites models that cannot call tools themselves."),

    "memory_manager": Consumer(
        "agent_friday.services.memory_proposals", "seat", SELECTS,
        "WIRED 2026-08-24. memory_proposals.propose() runs fact extraction on "
        "the assigned seat, pinned with no provider fallback. Stephen had "
        "ALREADY assigned this seat (a local Gemma-4-E4B on arbiter-local) and "
        "nothing read it, while memory_dreaming's six regexes consolidated 0 "
        "durable facts from 215 turns (liveness_audit.py:12). MANUAL ONLY for "
        "now: propose() is run by hand and its output waits for approve() "
        "before anything reaches user_model. The nightly regex pass in "
        "memory_dreaming is unchanged."),

    "researcher": _orphan(
        "WORKING ROLE. No reader. Long commissions run on the subagent seat."),

    "creative_music": Consumer(
        "agent_friday.services.music_engine", "_seat_model", SELECTS,
        "WIRED 2026-08-24. resolve_music_model() now consults the seat when no "
        "model is passed explicitly. Previously inert in both directions: "
        "every caller passed model= (services/creations.py:438 hardcodes "
        "'lyria-clip') and resolution fell through to the DEFAULT_MUSIC_MODEL "
        "constant, while the music_model mirror was written and never read. "
        "settings['music_models'] remains a friendly-id -> API-string override "
        "TABLE, which is a different thing from a selection."),

    "voice": Consumer(
        "agent_friday.services.voice_engine", "_get_live_model", SELECTS,
        "Consumed THROUGH its flat mirror. core._CAP_FLAT_MAP maps voice -> "
        "voice_model and _sync_capability_routing keeps the two congruent in "
        "both directions, so a pick in the picker lands in voice_model. "
        "_get_live_model() returns settings['voice_model'] or LIVE_MODEL, and "
        "routes/voice.py:1322 and :1468 call it on the live-session path. "
        "settings['voice_engine'] is a separate MODE selector (local | gemini "
        "| auto | gpu tiers) and is not this seat's value.",
        mirror="voice_model"),

    "asr": _orphan(
        "Same as voice: engine choice comes from settings['voice_engine'] and "
        "the concrete ASR model from the local-voice-lite provider entry "
        "(provider_registry.py:302), not from this seat."),

    "tts": _orphan(
        "Same as voice. Note the residency plan has its own 'tts' SEAT "
        "(residency_policy.py:895) -- a different namespace from this "
        "capability key, and not evidence that this key is read."),
}


# ── AST verification ─────────────────────────────────────────────────────────
def _module_source(module_name):
    """(source_text, path) for an importable module."""
    mod = importlib.import_module(module_name)
    path = getattr(mod, "__file__", None)
    if not path:
        raise FileNotFoundError(f"{module_name} has no __file__")
    # utf-8-sig, not utf-8: several modules in this tree are BOM-prefixed
    # (creative_engine, core_routes, capability_router), and a leading U+FEFF
    # is a SyntaxError to ast.parse even though Python imports the file fine.
    return Path(path).read_text("utf-8-sig", errors="replace"), path


def _find_symbol(tree, symbol):
    """The FunctionDef/AsyncFunctionDef/ClassDef named ``symbol``, anywhere."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.name == symbol:
            return node
    return None


def _string_constants(tree):
    """Every string literal in the tree."""
    return {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def verify(key, consumer=None):
    """Check the consumer claim for seat ``key``. Returns (ok, reason).

    Orphans verify trivially as (True, "orphan") -- there is no claim to check.
    Whether an orphan is ALLOWED is a separate question, asked by the test.
    """
    consumer = consumer if consumer is not None else CONSUMERS.get(key)
    if consumer is None:
        return False, f"{key!r} is not present in CONSUMERS"
    if consumer.module is None:
        return True, "orphan (no claim to verify)"

    try:
        source, path = _module_source(consumer.module)
    except Exception as exc:                                # noqa: BLE001
        return False, (f"consumer module {consumer.module!r} for seat {key!r} "
                       f"could not be imported or read: {exc}")

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return False, f"{consumer.module!r} failed to parse: {exc}"

    if _find_symbol(tree, consumer.symbol) is None:
        return False, (f"seat {key!r} names consumer "
                       f"{consumer.module}.{consumer.symbol}, but no such "
                       f"function or class exists in {path}. The name is "
                       f"stale -- it was renamed or removed.")

    literals = _string_constants(tree)

    if consumer.mirror:
        # Mirror-mediated: the consumer reads the legacy flat key, and
        # core._sync_capability_routing keeps that key congruent with the seat.
        # Verify the MIRROR LINK IS REAL rather than taking the claim on trust
        # -- if _CAP_FLAT_MAP stops mapping this seat, the chain is broken and
        # the seat is orphaned again with nothing else to notice.
        try:
            from agent_friday.core import _CAP_FLAT_MAP
        except Exception as exc:                            # noqa: BLE001
            return False, f"could not read core._CAP_FLAT_MAP: {exc}"
        actual = _CAP_FLAT_MAP.get(key)
        if actual != consumer.mirror:
            return False, (
                f"seat {key!r} claims to be consumed through mirror "
                f"{consumer.mirror!r}, but core._CAP_FLAT_MAP maps it to "
                f"{actual!r}. The mirror chain is broken -- either the seat is "
                f"orphaned again, or the mirror was renamed.")
        if consumer.mirror not in literals:
            return False, (
                f"seat {key!r} is claimed to be read through mirror "
                f"{consumer.mirror!r} by {consumer.module}.{consumer.symbol}, "
                f"but {consumer.mirror!r} does not appear in {path}.")
        return True, f"verified (via mirror {consumer.mirror})"

    if key not in literals:
        return False, (f"seat {key!r} names consumer {consumer.module}."
                       f"{consumer.symbol}, but the string {key!r} does not "
                       f"appear anywhere in {path}. That module does not read "
                       f"this seat.")
    if ROUTING_KEY not in literals:
        return False, (f"seat {key!r} names consumer {consumer.module}."
                       f"{consumer.symbol}, but {ROUTING_KEY!r} does not "
                       f"appear in {path}. The module mentions the seat name "
                       f"without reading the routing table it lives in.")
    return True, "verified"


# ── Derived views ────────────────────────────────────────────────────────────
def declared_seats():
    """Every seat declared in DEFAULT_SETTINGS. The contract's own list."""
    from agent_friday.core import DEFAULT_SETTINGS
    return tuple((DEFAULT_SETTINGS.get(ROUTING_KEY) or {}).keys())


def orphans():
    """Declared seats no module reads at all."""
    return tuple(k for k in declared_seats()
                 if CONSUMERS.get(k) and CONSUMERS[k].module is None)


def display_only():
    """Declared seats something reads, but only to show."""
    return tuple(k for k in declared_seats()
                 if CONSUMERS.get(k) and CONSUMERS[k].kind == DISPLAYS)


def assignable_seats():
    """Seats a picker may legitimately offer as a choice.

    A seat is assignable when some module reads it AND that read selects a
    model. Orphans and display-only seats are excluded -- not deleted, because
    they are intended future work, but they must not be presented as choices
    that do something.
    """
    return tuple(k for k in declared_seats()
                 if CONSUMERS.get(k) and CONSUMERS[k].kind == SELECTS)


def picker_payload():
    """Per-seat assignability, for whoever owns the picker UI.

    Shape: {seat: {"assignable": bool, "kind": "selects"|"displays"|None,
                   "consumer": "module.symbol"|None, "why": str}}

    ``why`` is written for a person and is safe to surface verbatim as the
    reason a seat is shown inert.
    """
    out = {}
    for key in declared_seats():
        c = CONSUMERS.get(key)
        if c is None:
            out[key] = {"assignable": False, "kind": None, "consumer": None,
                        "why": "Not mapped in services/role_consumers.py."}
            continue
        out[key] = {
            "assignable": c.kind == SELECTS,
            "kind": c.kind,
            "consumer": (f"{c.module}.{c.symbol}" if c.module else None),
            "why": c.note,
        }
    return out
