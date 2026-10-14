"""Every list of routing modes in the code agrees with `cloud_intent.MODES`.

Four modes exist: local_only, local_preferred, cloud_preferred, cloud_only (and
`smart`, the old spelling of local_preferred). A new mode that is added to one
tuple and forgotten in another is a hole in a gate, so this test finds every
literal list of mode names under src/ and checks it, then checks the lists that
are keyed by mode (settings validation, seat transparency, the pickers, the
user guide).

Laya in every mode addendum 2026-10-10, section 5 (cloud_preferred, LM-D1).
"""
from __future__ import annotations

import ast
import pathlib
import re

from agent_friday.services import cloud_intent as ci
from agent_friday.services import cloud_mode_policy as cmp

SRC = pathlib.Path(ci.__file__).resolve().parents[1]
REPO = SRC.parents[1]
NAMES = set(ci.MODES) | {"smart"}

ALL = frozenset(ci.MODES)
LOCAL = frozenset({"local_only", "local_preferred"})
CLOUD = frozenset({"cloud_preferred", "cloud_only"})

#: Literal lists that are deliberately none of ALL / LOCAL / CLOUD, with why.
SPECIAL = {
    ("services/cloud_intent.py", frozenset({"local_preferred", "local_only", "smart"})):
        "the local modes plus the old smart spelling",
    ("services/setup_reader.py", frozenset({"cloud_only", "cloud_preferred", "local_preferred"})):
        "modes under which a cloud reader may even be offered (local_only never offers one)",
    ("services/work_order_returns.py", frozenset({"cloud_only", "cloud_preferred", "local_preferred"})):
        "modes in which a work order may return a free-text (P2) summary; local_only "
        "returns at most an aggregate, and an unknown mode falls to the stricter class",
}


def _literal_lists():
    for p in sorted(SRC.rglob("*.py")):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        rel = p.relative_to(SRC).as_posix()
        for n in ast.walk(tree):
            if (isinstance(n, (ast.Tuple, ast.List, ast.Set)) and len(n.elts) >= 2
                    and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                            for e in n.elts)):
                vals = [e.value for e in n.elts]
                if all(v in NAMES for v in vals):
                    yield rel, n.lineno, frozenset(vals)


def test_every_literal_list_of_modes_is_a_known_group():
    found = list(_literal_lists())
    assert found, "the scan found no mode lists at all, so it is broken"
    bad = []
    for rel, line, group in found:
        if group in (ALL, LOCAL, CLOUD):
            continue
        if (rel, group) in SPECIAL:
            continue
        bad.append("%s:%d %s" % (rel, line, sorted(group)))
    assert not bad, (
        "a list of routing modes that is neither all four, the two local modes, "
        "nor the two cloud modes (add the missing mode, or register the list in "
        "SPECIAL with its reason):\n  " + "\n  ".join(bad))


def test_the_canonical_lists_are_consistent():
    assert set(ci.MODES) == {"local_only", "local_preferred", "cloud_preferred", "cloud_only"}
    assert set(ci.CLOUD_MODES) == CLOUD
    assert set(cmp.CLOUD_MODES) == CLOUD and set(cmp.LOCAL_MODES) == LOCAL
    # ci.LOCAL_MODES is the local pair plus the legacy alias.
    assert set(ci.LOCAL_MODES) == LOCAL | {"smart"}
    assert not (set(ci.LOCAL_MODES) & set(ci.CLOUD_MODES))
    assert set(ci.UI_HIDDEN_MODES) <= set(ci.MODES)
    for old, new in ci.MODE_ALIASES.items():
        assert new in ci.MODES and old not in ci.MODES


def test_settings_validation_accepts_every_mode():
    from agent_friday.services import laya_pilot, setup_complete
    assert set(setup_complete.ROUTING_MODES) == ALL
    assert set(laya_pilot._MODES) == ALL


def test_the_cloud_reader_gate_follows_the_cloud_modes():
    from agent_friday.services import setup_reader
    assert CLOUD <= set(setup_reader.CLOUD_MODES)
    assert "local_only" not in setup_reader.CLOUD_MODES


def test_seat_transparency_says_what_every_mode_means():
    from agent_friday.services import seat_transparency
    assert ALL <= set(seat_transparency._MODE_MEANING)


def _pickers():
    """The mode ids each picker offers, read from source (no server)."""
    intel = (SRC / "routes" / "intelligence.py").read_text(encoding="utf-8-sig")
    block = intel.split('"routing_modes": [', 1)[1].split("],", 1)[0]
    picker = set(re.findall(r'\{"id": "([a-z_]+)"', block))
    from agent_friday.services import onboarding_copy
    consent = {m for m, _label, _detail in onboarding_copy.ROUTING_CHOICES}
    return picker, consent


def test_the_pickers_hide_what_is_still_hidden_and_show_the_rest():
    picker, consent = _pickers()
    shown = ALL - set(ci.UI_HIDDEN_MODES)
    assert picker == shown, "Settings > Models picker offers %s, expected %s" % (
        sorted(picker), sorted(shown))
    assert consent == shown, "the consent screen offers %s, expected %s" % (
        sorted(consent), sorted(shown))


def test_the_user_guide_names_every_mode():
    text = (REPO / "docs" / "user-guide" / "configuration.md").read_text(encoding="utf-8")
    missing = [m for m in ci.MODES if m not in text]
    assert not missing, "docs/user-guide/configuration.md does not mention: %s" % missing
