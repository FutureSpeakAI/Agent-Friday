"""BD-00, the last places positrons showed with the federation held.

tests/unit/test_held_federation.py holds the workspaces, dock, palette,
Settings tabs and Trust strip. Two places were left:

- the Content workspace's Analytics tab drew a "ψ earned from content" card on
  every install (and read a field the server never sends, so it was blank);
- the marketplace stored "auto-accept at the listing price" as given and only
  forced it off when read back through get_policy().

And the UI's only source for the switch is GET /api/settings, so that answer
must carry held_features.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
UI_FILES = pytest.mark.parametrize(
    "path", [ROOT / "index.html", ROOT / "ui_parts" / "app.html"], ids=["index.html", "app.html"])


@UI_FILES
def test_the_psi_earned_card_shows_only_with_the_federation_on(path):
    src = path.read_text(encoding="utf-8")
    assert src.count("ψ earned from content") == 1, "one analytics card"
    i = src.index("ψ earned from content")
    before = src[max(0, i - 120):i]
    assert re.search(r"fridayHeldOn\('federation'\)\s*\?\s*\[\[\s*'$", before), \
        "the ψ card is drawn behind the federation switch"


@UI_FILES
def test_the_psi_card_reads_what_the_server_sends(path):
    src = path.read_text(encoding="utf-8")
    i = src.index("ψ earned from content")
    assert "psi_earned_mpsi" in src[i:i + 200]
    route = (ROOT / "src" / "agent_friday" / "routes" / "content_pipeline.py").read_text(encoding="utf-8")
    assert '"psi_earned_mpsi"' in route


def test_auto_accept_is_never_stored_on():
    from agent_friday.services import marketplace as mkt
    mkt.update_policy({"selling": {"enabled": True, "auto_accept_at_listing_price": True}})
    with mkt._conn() as con:
        row = con.execute("SELECT value FROM policy WHERE key = 'selling'").fetchone()
    stored = json.loads(row[0] if not hasattr(row, "keys") else row["value"])
    assert stored.get("auto_accept_at_listing_price") is False, stored
    assert stored.get("enabled") is True, "the rest of the policy is saved as given"


@pytest.mark.parametrize("bad", ["x", [1], None, 3])
def test_a_policy_that_is_not_an_object_changes_nothing(bad):
    from agent_friday.services import marketplace as mkt
    before = mkt.get_policy()
    assert mkt.update_policy(bad) == before
