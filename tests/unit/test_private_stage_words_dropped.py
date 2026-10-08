"""Rows in Health, Finance and Family reach Friday as a kind and a position only. Every word their
page sends is dropped on the server, not just a row's title and name: a selection's label, a
filter's value and label, a field's label, and a kind that is not one of the known kinds.
Another workspace keeps its words. Synthetic stage."""
from __future__ import annotations

import json

from agent_friday.services import screen_stage as ss

SECRET = ("Metformin", "metformin", "Diabetes", "Dr Rivera", "500mg")


def _stage(ws, ref):
    return {"workspace": ws,
            "items": [{"ref": ref + "-1", "n": 1, "title": "Metformin 500mg", "who": "Dr Rivera",
                       "facets": {"kind": "medication"}},
                      {"ref": ref + "-2", "n": 2, "facets": {"kind": "Metformin 500mg twice daily"}}],
            "selection": {"refs": [ref + "-1"], "label": "Diabetes meds", "source": "owner"},
            "filters": [{"key": "q", "value": "metformin", "label": "metformin"}],
            "fields": [{"key": "note", "label": "Dr Rivera note"}]}


def test_a_private_workspace_keeps_no_word_its_page_sent():
    out = ss.bound_stage(_stage("health", "health:med"))
    dumped = json.dumps(out)
    assert not [w for w in SECRET if w in dumped], dumped
    kinds = [it["facets"].get("kind") for it in out["items"]]
    assert kinds == ["medication", None], "a known kind stays, a free-text one is dropped"
    assert out["selection"]["count"] == 1 and out["filters"][0]["key"] == "q"


def test_another_workspace_keeps_its_words():
    st = _stage("messages", "mail:acct:thread")
    for it in st["items"]:
        it["facets"] = {"lane": "people"}
    out = ss.bound_stage(st)
    assert out["selection"]["label"] == "Diabetes meds" and out["filters"][0]["value"] == "metformin"
    assert out["items"][0]["title"] == "Metformin 500mg"
