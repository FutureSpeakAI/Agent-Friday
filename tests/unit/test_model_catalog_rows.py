"""The Models screen's rows: shortlist plus installed plus pasted, each file
with a verdict for the machine it is asked about, Bonsai 2 labelled as the
standard and never pre-ticked, and a pasted id costing one anonymous read.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import model_catalog_rows as rows
from agent_friday.services import model_fit as mf
from agent_friday.services import model_shortlist as sl
from tests import residency_fixtures as fx
from tests.unit.test_update_check import _identity_leaks


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "calibration_path", lambda: tmp_path / "calibration.json")
    from agent_friday.services import model_store
    monkeypatch.setattr(model_store, "available", lambda: {})
    from agent_friday.services import residency_catalog as rc
    monkeypatch.setattr(rc, "measurements", lambda mid, fp: [])
    # The reference overhead, not the live tool registry's size, so a row's
    # verdict here does not move when a tool is added elsewhere.
    from agent_friday.services import context_budget
    monkeypatch.setattr(context_budget, "overhead_tokens", lambda: 25000)
    sl.reload_for_tests()


def test_the_reference_machine_sees_bonsai2_as_the_standard_with_an_honest_verdict():
    out = rows.catalog_payload(fx.P1)
    b = next(r for r in out["models"] if r["id"] == "bonsai2:27b")
    assert b["friday_standard"] is True and b["installed"] is False
    assert "ticked" not in json.dumps(b) and "selected" not in json.dumps(b), \
        "the standard is a label, never a pre-selection"
    ptq = next(f for f in b["files"] if f["packing"] == "PTQ1_0")
    # By the planner's budgets (1 GB reserve beyond the display reserve) the
    # 27B is close to the edge on a 12 GB card: it serves, and the screen
    # says "tight" rather than rounding that up to "runs well".
    assert ptq["verdict"] in ("runs_well", "tight") and ptq["context"] >= 32768
    assert ptq["speed"]["basis"] == "about"
    assert ptq["why"]["vram_available_mib"] > 0 and ptq["seat"]["weights_mib"] == 5671
    assert b["licence_class"] == "permissive" and b["telemetry"] == "none"
    assert out["machine"]["tier"] == "a 12 GB card"
    assert "never leaves" in out["machine"]["profile_path"]


def test_a_measurement_for_this_machine_beats_the_estimate(monkeypatch):
    from agent_friday.services import residency_catalog as rc
    monkeypatch.setattr(rc, "measurements", lambda mid, fp: (
        [{"num_ctx": 49152, "tok_s_median": 33.2}] if mid == "bonsai2:27b" else []))
    out = rows.catalog_payload(fx.P1)
    b = next(r for r in out["models"] if r["id"] == "bonsai2:27b")
    assert all(f["speed"]["basis"] == "measured" and f["speed"]["tok_s"] == 33.2 for f in b["files"])
    other = next(r for r in out["models"] if r["id"] == "ternary-bonsai:4b")
    assert other["files"][0]["speed"]["basis"] == "about", "a measurement names one model only"


def test_a_published_figure_is_used_when_the_card_matches():
    p = json.loads(json.dumps(fx.P3))        # RTX 4090
    out = rows.catalog_payload(p)
    b = next(r for r in out["models"] if r["id"] == "bonsai2:27b")
    ptq = next(f for f in b["files"] if f["packing"] == "PTQ1_0")
    assert ptq["speed"] == {"tok_s": 91.1, "basis": "published"}


def test_a_small_laptop_gets_partial_or_tight_never_a_bare_yes():
    out = rows.catalog_payload(fx.P2)
    b = next(r for r in out["models"] if r["id"] == "bonsai2:27b")
    assert {f["verdict"] for f in b["files"]} <= {"partial", "tight", "wont_fit"}
    assert all(f["verdict_word"] for f in b["files"])


def test_pretend_i_have_a_bigger_card_is_marked_simulated():
    sim = mf.what_if(fx.P2, vram_total_mib=24 * 1024, gpu_name="NVIDIA GeForce RTX 4090")
    out = rows.catalog_payload(sim)
    assert out["machine"]["simulated"] is True
    b = next(r for r in out["models"] if r["id"] == "bonsai2:27b")
    assert b["best"] == "runs_well"


def test_an_installed_model_outside_the_shortlist_is_sized_from_its_file(tmp_path, monkeypatch):
    from agent_friday.services import model_store
    f = tmp_path / "other.gguf"
    f.write_bytes(b"x" * 1000)
    monkeypatch.setattr(model_store, "available", lambda: {"other:1b": {
        "path": str(f), "size_bytes": 2 * 2 ** 30, "quantization": "Q4_K_M", "architecture": "llama",
        "label": "Other 1B", "engine": None, "origin": {"repo": "x/other", "licence": "mit"}}})
    monkeypatch.setattr(rows, "_layout_from_file", lambda path, arch: None)
    out = rows.catalog_payload(fx.P1)
    o = next(r for r in out["models"] if r["id"] == "other:1b")
    assert o["installed"] is True and o["source"] == "installed"
    assert o["files"][0]["seat"]["weights_mib"] == 2048
    assert "not counted" in o["files"][0]["seat"]["kv_basis"]
    assert o["licence_class"] == "permissive"


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def test_a_pasted_hugging_face_id_costs_one_anonymous_read(monkeypatch):
    calls = []

    def _get(url, **kw):
        calls.append({"url": url, "kwargs": kw})
        return _Resp({"cardData": {"license": "apache-2.0"}, "gated": False, "siblings": [
            {"rfilename": "Model-7B-Q4_K_M.gguf", "size": 4 * 2 ** 30, "lfs": {"sha256": "a" * 64}},
            {"rfilename": "README.md", "size": 10},
        ]})
    monkeypatch.setattr(rows, "_http_get", _get)
    out = rows.check_hf_repo("someone/Model-7B-GGUF", fx.P1)
    assert out["status"] == "ok"
    m = out["model"]
    assert m["files"][0]["gib"] == 4.0 and m["files"][0]["sha256"] == "a" * 64
    assert m["files"][0]["verdict"] in ("runs_well", "tight")
    assert "not counted" in m["kv_note"]
    assert len(calls) == 1 and "?" in calls[0]["url"] and "blobs=true" in calls[0]["url"]
    headers = {k.lower() for k in (calls[0]["kwargs"].get("headers") or {})}
    assert not headers and not calls[0]["kwargs"].get("params")
    blob = calls[0]["url"] + json.dumps(calls[0]["kwargs"], default=str)
    assert "RTX" not in blob and _identity_leaks(blob, our_version="9.9.9") == []


def test_a_repo_without_gguf_files_is_refused_plainly(monkeypatch):
    monkeypatch.setattr(rows, "_http_get", lambda url, **kw: _Resp({"siblings": [{"rfilename": "model.safetensors", "size": 1}]}))
    out = rows.check_hf_repo("x/y", fx.P1)
    assert out["status"] == "error" and "no GGUF" in out["error"]


def test_licence_classes_name_restricted_and_non_commercial_terms():
    assert rows.licence_class("apache-2.0") == "permissive"
    assert rows.licence_class("cc-by-4.0") == "attribution"
    assert rows.licence_class("gemma") == "restricted"
    assert rows.licence_class("cc-by-nc-4.0") == "non-commercial"
    assert rows.licence_class(None) == "unknown"
