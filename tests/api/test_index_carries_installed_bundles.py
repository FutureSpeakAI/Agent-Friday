"""The served page knows the installed bundle workspaces at load, so the dock
and the standalone tab can show them without a second round trip."""
from __future__ import annotations

import json
import re

import pytest

from agent_friday.services import codebases as cb
from agent_friday.services import conversations as convs
from agent_friday.services import workspace_bundles as wb


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setattr(wb, "_root", lambda: tmp_path / "workspaces")
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    yield


def _installed_from(html: str) -> list:
    m = re.search(r"window\.FRIDAY_INSTALLED_BUNDLES\s*=\s*(\[.*?\]);</script>", html, re.S)
    assert m, "the page does not carry the installed bundles"
    return json.loads(m.group(1))


def test_the_desktop_page_and_the_standalone_tab_carry_the_installed_bundles(client):
    r = client.get("/")
    assert r.status_code == 200
    assert _installed_from(r.get_data(as_text=True)) == []
    rec = cb.create("Chore wheel", template="bundle")
    ws = wb.install(rec["id"])
    got = _installed_from(client.get("/").get_data(as_text=True))
    assert [w["id"] for w in got] == [ws["id"]]
    assert got[0]["label"] == "Chore wheel" and got[0]["group"] == "mine" and got[0]["boundary"] == {"kind": "bundle"}
    # Nothing that is not the registry shape leaks into the page (no file contents, no versions list).
    assert "versions" not in got[0] and "history" not in got[0]
    # The bundle opens as its own tab like any workspace.
    r = client.get("/w/" + ws["id"])
    assert r.status_code == 200
    assert [w["id"] for w in _installed_from(r.get_data(as_text=True))] == [ws["id"]]


def test_a_label_with_markup_cannot_break_out_of_the_script(client):
    rec = cb.create("Chore </script><b>wheel", template="bundle")
    wb.install(rec["id"])
    html = client.get("/").get_data(as_text=True)
    got = _installed_from(html)
    assert got and "</script><b>" not in html.split("FRIDAY_INSTALLED_BUNDLES")[1].split("</script>")[0]
