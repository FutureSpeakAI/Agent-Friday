"""Every Settings control in the audited inventory has a handler and a reader.

settings_controls.json is the machine-readable form of the Settings audit: one
row per control, with the section it sits in now, the setting keys it writes or
the route it calls, and the server code that reads what it writes (a file and a
literal that proves the consumer). This test is the part of the audit that runs
anywhere, with no browser:

  * handler: the UI source (index.html, static/*.js) names the key or the route
    the control uses, so a control cannot outlive the code behind it;
  * reader: the named file exists and contains the named literal, so a key
    cannot lose its consumer and leave a control that saves into the void;
  * a control marked dead is not allowed to stay in the inventory: it is fixed
    or removed, and the row says which;
  * every section the inventory names is a section the rail draws.

The Playwright spec tests/app/specs/settings_walk.spec.ts is the other half:
it drives the same controls in a real page and reads each change back.
"""
from __future__ import annotations

import json
import os
import pathlib
import re

import pytest

ROOT = pathlib.Path(os.environ.get("SETTINGS_TEST_ROOT") or pathlib.Path(__file__).resolve().parents[2])
DATA = pathlib.Path(__file__).with_name("settings_controls.json")
ROWS = json.loads(DATA.read_text(encoding="utf-8"))
LIVE = [r for r in ROWS if r["status"] != "removed"]

RAIL = {"General", "Voice", "Models", "Spending", "Privacy & Data", "Connections",
        "Appearance", "Advanced", "About"}
HELD = "Held (hidden unless held_features.federation)"
UI_STATUSES = {"ok", "fixed", "duplicate-accepted", "duplicate-resolved"}


def _ui_corpus() -> str:
    parts = [(ROOT / "index.html").read_text(encoding="utf-8")]
    for p in sorted((ROOT / "static").glob("*.js")):
        parts.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


CORPUS = _ui_corpus()


def _ident(row) -> str:
    return "%s | %s | %s" % (row["section"], row["panel"][:40], row["label"][:50])


def _keys(row):
    out = []
    for k in row["keys"]:
        k = k.strip()
        if re.fullmatch(r"[A-Za-z0-9_.]+", k):
            out.append(k)
    return out


def _route_path(row):
    m = re.search(r"/api/[^\s<{*?]*", row["route"] or "")
    if not m:
        return ""
    return m.group(0).rstrip("/")


def _route_in_ui(path: str) -> bool:
    """The route, or its parent: a client may build 'base + /leaf' at run time."""
    if path in CORPUS:
        return True
    parent = path.rsplit("/", 1)[0]
    return parent.count("/") >= 3 and parent in CORPUS


def test_every_row_is_in_a_section_the_rail_draws():
    for r in ROWS:
        assert r["section"] in RAIL or r["section"] == HELD, _ident(r)


def test_the_inventory_has_no_dead_rows_left():
    assert not [_ident(r) for r in ROWS if r["status"] == "dead"]
    assert {r["status"] for r in ROWS} <= UI_STATUSES | {"removed"}


def test_every_live_row_says_what_it_did_when_it_was_fixed():
    for r in ROWS:
        if r["status"] in ("fixed", "removed", "duplicate-resolved"):
            assert r["note"].strip(), _ident(r)


@pytest.mark.parametrize("row", LIVE, ids=[_ident(r) for r in LIVE])
def test_the_ui_names_the_key_or_route_each_control_uses(row):
    keys = _keys(row)
    path = _route_path(row)
    if not keys and not path:
        pytest.skip("a UI-only control: no key or route to find")
    hits = [k for k in keys if k.split(".")[-1] in CORPUS]
    if path and _route_in_ui(path):
        hits.append(path)
    assert hits, "%s: the UI source names neither %s nor %s" % (_ident(row), keys, path)


@pytest.mark.parametrize("row", [r for r in LIVE if r["reader_file"]],
                         ids=[_ident(r) for r in LIVE if r["reader_file"]])
def test_the_named_reader_exists_and_consumes_the_setting(row):
    f = ROOT / row["reader_file"]
    assert f.is_file(), "%s: reader file %s is missing" % (_ident(row), row["reader_file"])
    needle = row["reader_needle"]
    assert needle, _ident(row)
    assert needle in f.read_text(encoding="utf-8", errors="replace"), (
        "%s: %s no longer contains %r" % (_ident(row), row["reader_file"], needle))


def test_a_control_that_writes_a_setting_names_who_reads_it():
    """A row with setting keys and no reader is the placebo this audit exists to catch."""
    missing = [_ident(r) for r in LIVE
               if _keys(r) and not r["reader_file"] and r["status"] != "duplicate-resolved"
               and not re.search(r"client|browser|ui-only|local ?storage|display", r["note"], re.I)]
    assert not missing, "writes a setting nobody is recorded as reading:\n  " + "\n  ".join(missing)
