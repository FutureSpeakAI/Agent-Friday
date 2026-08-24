"""Every declared model seat must name a module that actually reads it.

WHAT THIS CATCHES
-----------------
A seat can be declared in DEFAULT_SETTINGS, defaulted, mapped in seat_binding,
labelled in the Intelligence route and rendered in the picker -- and read by
nothing. The user picks a model, the setting persists, the UI shows the new
value, and nothing whatsoever changes. Five of Friday's sixteen seats are in
exactly that state.

That is this codebase's signature defect (see services/liveness_audit.py, which
asks the same CONSUMED question about subsystems), so it gets a test.

WHAT THIS IS NOT
----------------
These are STATIC checks. `verify()` parses the named consumer module and
asserts the symbol exists and the seat key is read there. It cannot prove the
read happens on a live path -- a consumer behind a dead branch passes.

The stronger form is a runtime recorder, declined on 2026-08-23 because seat
reads are scattered across ~18 sites in 13 modules and funnelling them would
mean a cross-cutting refactor of files other sessions were editing. The
weakness is documented at length in services/role_consumers.py. Do not read a
pass here as proof that a seat is live on any real request.

WHAT IT STILL BUYS
------------------
A plain string field naming a consumer would rot exactly the way the roles
contract rotted -- silently, into documentation. This fails the moment the
named module stops reading the key or the named symbol is renamed away.
"""
import pytest

from agent_friday.services import role_consumers as rc

# ── The allow-list ───────────────────────────────────────────────────────────
# Seats known to have no consumer, each with a verdict. These are NOT bugs to
# be silenced -- they are recorded work, and the test below fails if a NEW
# orphan appears or if one of these quietly gains a consumer without being
# promoted out of the list.
#
# unbuilt-and-wanted : the capability is intended; the seat is waiting on code
# dead              : nothing wants it; the seat should be retired
KNOWN_ORPHANS = {
    "orchestrator":     "unbuilt-and-wanted",
    "sidekick_fast":    "unbuilt-and-wanted",
    "function_manager": "unbuilt-and-wanted",
    "researcher":       "unbuilt-and-wanted",
    "asr":              "dead: engine picks by tier, not by model id",
    "tts":              "dead: engine picks by tier, not by model id",
}

# Seats that reach their consumer through a legacy flat key rather than by
# capability name. core._CAP_FLAT_MAP defines the link; _sync_capability_routing
# keeps the pair congruent. Listed explicitly because MISSING one of these is
# how 'voice' was wrongly filed as an orphan on 2026-08-23 -- the AST check
# looked for the capability name, the consumer read the mirror, and the seat
# looked dead while being perfectly live.
MIRRORED_SEATS = {"reasoning", "subagent", "creative_image", "creative_music",
                  "voice"}

# Seats something reads, but only to display. Not assignable either: a choice
# that changes a badge and nothing else is still a choice that does nothing.
KNOWN_DISPLAY_ONLY = {
    "embedding": (
        "capability_router.resolve() reads it for the availability badge. "
        "No module selects an embedding model from it -- "
        "conversation_memory.EMBED_MODEL is a pinned constant."),
}


def test_every_declared_seat_is_mapped():
    """No seat may be declared without an entry saying who consumes it."""
    declared = set(rc.declared_seats())
    mapped = set(rc.CONSUMERS)
    missing = sorted(declared - mapped)
    assert not missing, (
        "These seats are declared in DEFAULT_SETTINGS['capability_routing'] "
        f"but absent from CONSUMERS: {missing}.\n"
        "Add an entry to services/role_consumers.py naming the module and "
        "symbol that reads the seat, or _orphan() with the reason nothing "
        "does. A seat with no entry is a seat nobody has checked.")

    stale = sorted(mapped - declared)
    assert not stale, (
        f"CONSUMERS names seats that are no longer declared: {stale}. "
        "Remove them -- a consumer claim for a seat that does not exist is "
        "the same rot in the other direction.")


def test_every_consumer_claim_resolves():
    """A named consumer must import, exist, and actually read the seat.

    This is the anti-rot check. It is what a plain string field cannot do.
    """
    failures = []
    for seat in rc.declared_seats():
        ok, reason = rc.verify(seat)
        if not ok:
            failures.append(f"  {seat}: {reason}")
    assert not failures, (
        "Consumer claims that no longer hold:\n" + "\n".join(failures) +
        "\n\nEither the consumer moved (update services/role_consumers.py) or "
        "the seat stopped being read (demote it to _orphan() and say so).")


def test_every_declared_seat_has_a_live_consumer():
    """THE contract: a seat offered as a choice must change what runs.

    Set KNOWN_ORPHANS = {} and run this to see the failure it is built to
    produce. It names every seat a user can pick that does nothing.
    """
    unexpected = sorted(set(rc.orphans()) - set(KNOWN_ORPHANS))
    assert not unexpected, (
        "These declared seats are read by NOTHING, and are not on the known "
        f"list: {unexpected}.\n\n"
        "A seat that nothing consumes is a control the user can set while the "
        "system ignores it -- the defect this test exists to catch. Do one of:\n"
        "  1. Wire it: make some module read "
        "capability_routing[<seat>] and act on it, then add a Consumer(...) "
        "entry naming that module and symbol.\n"
        "  2. Retire it: remove it from DEFAULT_SETTINGS['capability_routing'] "
        "and from seat_binding.SEAT_TO_CAPABILITY.\n"
        "  3. Record it: add it to KNOWN_ORPHANS in this file with a verdict "
        "of 'unbuilt-and-wanted' or 'dead', and confirm it is excluded from "
        "assignable_seats() so the picker cannot offer it.")

    healed = sorted(set(KNOWN_ORPHANS) - set(rc.orphans()))
    assert not healed, (
        f"These seats are on KNOWN_ORPHANS but now have a consumer: {healed}. "
        "Good -- remove them from KNOWN_ORPHANS so the list keeps meaning "
        "something. A stale allow-list is how a test stops being a test.")


def test_orphans_are_not_assignable():
    """A seat nothing reads must not be offered as a choice."""
    assignable = set(rc.assignable_seats())
    offered = sorted(set(rc.orphans()) & assignable)
    assert not offered, (
        f"Orphaned seats are marked assignable: {offered}. They must not "
        "render as choices in the picker -- they are not deleted (they are "
        "intended future work) but they cannot be presented as doing "
        "something.")


def test_display_only_seats_are_not_assignable():
    """A read that only paints a badge is not consumption."""
    assert set(rc.display_only()) == set(KNOWN_DISPLAY_ONLY), (
        f"display-only seats changed: {sorted(rc.display_only())} vs "
        f"{sorted(KNOWN_DISPLAY_ONLY)}. If a seat moved between DISPLAYS and "
        "SELECTS, that is a real behaviour change -- confirm which and update "
        "both this file and services/role_consumers.py.")

    assignable = set(rc.assignable_seats())
    offered = sorted(set(rc.display_only()) & assignable)
    assert not offered, (
        f"Display-only seats are marked assignable: {offered}. A choice that "
        "changes what a badge says and nothing else is still a choice that "
        "does nothing.")


def test_mirrored_seats_match_core_cap_flat_map():
    """The mirror list here must match core's, or the reasoning below is void."""
    from agent_friday.core import _CAP_FLAT_MAP
    assert set(_CAP_FLAT_MAP) == MIRRORED_SEATS, (
        f"core._CAP_FLAT_MAP now maps {sorted(_CAP_FLAT_MAP)} but this file "
        f"expects {sorted(MIRRORED_SEATS)}. A seat gained or lost a legacy "
        "flat mirror -- recheck whether it is consumed through it before "
        "trusting any orphan verdict for it.")


@pytest.mark.parametrize("seat", sorted(MIRRORED_SEATS))
def test_mirrored_seat_orphan_verdicts_account_for_the_mirror(seat):
    """A mirrored seat called an orphan must say the MIRROR was checked too.

    This is the regression guard for the 2026-08-23 false positive. 'voice'
    was filed as dead because the AST check looked for the capability name
    while services/voice_engine.py reads settings['voice_model'] -- the
    mirror. The seat was live the whole time. A mirrored seat is exactly the
    case where "no literal in the consumer module" does NOT mean "unread", so
    declaring one dead requires having looked at the other key and said so.
    """
    consumer = rc.CONSUMERS[seat]
    if consumer.module is not None:
        return                                  # consumed; nothing to justify
    from agent_friday.core import _CAP_FLAT_MAP
    mirror = _CAP_FLAT_MAP[seat]
    note = consumer.note
    assert mirror in note or "MIRROR CHECKED" in note.upper(), (
        f"seat {seat!r} is mirrored to {mirror!r} in core._CAP_FLAT_MAP and is "
        f"filed as an orphan, but its note never mentions {mirror!r}. Check "
        f"whether anything reads {mirror!r} for selection before calling this "
        "seat dead -- that omission is exactly how 'voice' was misfiled.")


def test_assignable_seats_are_exactly_the_selecting_ones():
    """assignable == SELECTS, with no third category smuggled in."""
    expected = {s for s in rc.declared_seats()
                if rc.CONSUMERS[s].kind == rc.SELECTS}
    assert set(rc.assignable_seats()) == expected


def test_picker_payload_covers_every_seat_with_a_reason():
    """The UI hand-off must explain every inert seat in words."""
    payload = rc.picker_payload()
    assert set(payload) == set(rc.declared_seats())
    for seat, row in payload.items():
        assert isinstance(row["assignable"], bool)
        if not row["assignable"]:
            assert row["why"].strip(), (
                f"seat {seat!r} is not assignable and carries no reason. The "
                "picker needs something to show; an unexplained disabled "
                "control is its own small dishonesty.")


@pytest.mark.parametrize("seat", sorted(KNOWN_ORPHANS))
def test_known_orphan_has_a_verdict(seat):
    """Every recorded orphan says whether it is wanted or dead."""
    verdict = KNOWN_ORPHANS[seat]
    assert verdict.startswith(("unbuilt-and-wanted", "dead")), (
        f"seat {seat!r} has verdict {verdict!r}; it must begin "
        "'unbuilt-and-wanted' or 'dead' so the list stays actionable.")
    assert rc.CONSUMERS[seat].note.strip(), (
        f"seat {seat!r} is an orphan with no note in role_consumers.py "
        "explaining what was checked.")
