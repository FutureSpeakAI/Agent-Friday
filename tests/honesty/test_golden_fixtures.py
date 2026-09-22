"""The honesty fixtures, checked for the things a fixture can be wrong about.

Spec 3.5. `tests/honesty/golden/` has held twelve fixtures and no runner: the
cases were written down after real incidents and then nothing ever read them.
A fixture nobody executes is a note, and a note does not fail.

TWO FILES, TWO JOBS, and the split is the point.

This one is deterministic, offline, and runs in the default suite. It cannot
tell you whether Friday is honest - only a model can answer that - but it can
tell you that the material for asking is intact: every fixture parses, has an
id, names a category the runner understands, and records which real incident
it came from. That last one matters most. Every item here exists because
Friday did something untrue on a specific day, and a fixture that loses its
provenance becomes a rule nobody can argue with because nobody remembers why.

`test_honesty_runner.py` is the other half: it replays these against a live
seat and judges the answers. It is opt-in (`--run-honesty`) because it needs a
model, takes minutes and is not deterministic. Gating commits on it would make
the suite slow and flaky; leaving it unwritten left the fixtures inert.
"""
from __future__ import annotations

import json
import pathlib

import pytest

GOLDEN = pathlib.Path(__file__).parent / "golden"

#: Categories the runner knows how to judge. A fixture outside this set would
#: be collected, executed and then scored by nothing.
KNOWN_CATEGORIES = {
    "zero_tool",            # answer without calling anything; no invented result
    "completion_honesty",   # the write fails; claiming success is the failure
    "sycophancy",           # agreement is not evidence
    "challenge",            # user disputes a tool-backed fact; re-check, do not fold
    "date_discipline",      # the clock is authoritative, not the model's arithmetic
    "connection_state",     # re-read the connection, never recite it from memory
}


def _fixtures():
    return sorted(GOLDEN.glob("*.json"))


def test_the_fixtures_are_actually_there():
    """If this directory empties, every test below passes vacuously."""
    found = _fixtures()
    assert len(found) >= 12, (
        "expected the twelve golden honesty fixtures, found %d - an empty "
        "corpus makes every other test in this file true and meaningless"
        % len(found))


@pytest.mark.parametrize("path", _fixtures(), ids=lambda p: p.stem)
def test_each_fixture_is_wellformed(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "a fixture must be a JSON object"

    assert data.get("id"), "%s has no id" % path.name
    assert data.get("prompt"), "%s has no prompt to send" % path.name

    cat = data.get("category")
    assert cat in KNOWN_CATEGORIES, (
        "%s is category %r, which the runner cannot score. Either add it to "
        "KNOWN_CATEGORIES and teach the runner, or the fixture will be "
        "executed and judged by nothing." % (path.name, cat))

    # PROVENANCE. Every item here exists because Friday did something untrue on
    # a specific day. A fixture that loses that becomes a rule nobody can argue
    # with because nobody remembers why it is there.
    notes = str(data.get("notes") or "")
    assert len(notes) > 40, (
        "%s has no notes worth the name. Record which incident it came from - "
        "a fixture without provenance cannot be revised, only obeyed."
        % path.name)


def test_every_category_has_a_scorer_in_the_runner():
    """A fixture the runner cannot score is executed and judged by nothing.

    That looks exactly like passing, which is why this check lives HERE, in
    the file that always runs, rather than beside the runner. The first
    version of it sat in test_honesty_runner.py, inherited that module's
    `honesty` marker, and was skipped along with everything else - inert for
    precisely the reason the fixtures themselves had been inert.
    """
    from tests.honesty.test_honesty_runner import SCORED_CATEGORIES
    present = {json.loads(p.read_text(encoding="utf-8"))["category"]
               for p in _fixtures()}
    missing = sorted(present - set(SCORED_CATEGORIES))
    assert not missing, (
        "these fixture categories would run and be judged by nothing: %s"
        % missing)
    # And the reverse: a scorer for a category no fixture uses is dead code
    # that reads as coverage.
    unused = sorted(set(SCORED_CATEGORIES) - present)
    assert not unused, (
        "the runner scores categories no fixture uses: %s" % unused)


def test_fixture_ids_are_unique():
    """Duplicated ids silently collapse two cases into one in any report."""
    ids = []
    for p in _fixtures():
        ids.append(json.loads(p.read_text(encoding="utf-8")).get("id"))
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, "duplicate fixture ids: %s" % dupes


@pytest.mark.parametrize("path", _fixtures(), ids=lambda p: p.stem)
def test_setup_turns_alternate_and_end_on_the_assistant(path):
    """A replayed conversation that does not alternate is not a conversation.

    These fixtures work by putting words in the assistant's mouth and then
    asking a follow-up - `connstate_recheck` replays three contradictory
    connection-state claims before asking which is true. If the setup does not
    end on the assistant, the fixture's own prompt is not a follow-up to
    anything and the case being tested does not arise.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    turns = data.get("setup_turns") or []
    if not turns:
        return
    roles = [t.get("role") for t in turns]
    assert all(r in ("user", "assistant") for r in roles), \
        "%s has a setup turn that is neither user nor assistant" % path.name
    for a, b in zip(roles, roles[1:]):
        assert a != b, "%s has two %s turns in a row" % (path.name, a)
    assert roles[-1] == "assistant", (
        "%s ends its setup on a %s turn, so its prompt does not follow "
        "anything the assistant said" % (path.name, roles[-1]))


@pytest.mark.parametrize("path", _fixtures(), ids=lambda p: p.stem)
def test_a_challenge_fixture_carries_both_values(path):
    """A challenge case is only scoreable if the runner knows which value is
    the truth and which is the user's (wrong) assertion."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("category") != "challenge":
        return
    assert data.get("correct_value"), \
        "%s is a challenge with no correct_value to hold out for" % path.name
    assert data.get("wrong_value"), \
        "%s is a challenge with no wrong_value to detect folding to" % path.name
    assert data["correct_value"] != data["wrong_value"]
