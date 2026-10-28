"""The Doctor's repairs (north star §28.1-1): every check that is not healthy
comes with the deterministic repairs for it, or says there is none here; no
repair needs a model; and the upgrade state has checks of its own."""
from __future__ import annotations

import ast
import importlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _dr():
    assert importlib.util.find_spec("agent_friday.services.doctor_repairs") is not None, \
        "the Doctor offers no repairs"
    return importlib.import_module("agent_friday.services.doctor_repairs")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / ".friday"
    h.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(h))
    from agent_friday.services import doctor, safe_mode
    monkeypatch.setattr(doctor, "_home", lambda: h)
    safe_mode.reset_for_tests()
    yield h
    safe_mode.reset_for_tests()


def test_no_repair_needs_a_model_or_the_network():
    dr = _dr()
    assert dr.NEEDS_MODEL is False
    tree = ast.parse((ROOT / "src" / "agent_friday" / "services" / "doctor_repairs.py").read_text(encoding="utf-8"))
    names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not ({"model_router", "anthropic", "openai", "requests", "httpx"} & (names | mods))
    for rs in dr.REPAIRS.values():
        for r in rs:
            assert set(r) <= {"id", "label", "where", "needs"} and r["label"]
            assert r["where"] == "" or r["where"].startswith("#")


def test_every_place_a_repair_points_to_is_on_the_doctor_page():
    dr = _dr()
    html = (ROOT / "static" / "doctor.html").read_text(encoding="utf-8")
    for rs in dr.REPAIRS.values():
        for r in rs:
            if r["where"] and not (r.get("needs") and importlib.util.find_spec(r["needs"]) is None):
                if r["where"] == "#localmodel":
                    continue                     # the local model section ships with its repairs
                assert 'id="%s"' % r["where"][1:] in html, r


def test_a_broken_check_gets_its_repairs_and_an_unlisted_one_says_none():
    dr = _dr()
    out = dr.offered([{"id": "boot", "state": "broken"}, {"id": "vault", "state": "degraded"},
                      {"id": "disk", "state": "healthy"}, {"id": "gpu", "state": "unavailable_by_hardware"}])
    assert set(out) == {"boot", "vault"}
    assert {r["id"] for r in out["boot"]} >= {"safe_mode_next"}
    assert out["vault"] == [dr.NONE]


def test_the_migration_check_names_each_upgrade_state(home, monkeypatch):
    _dr()
    from agent_friday.services import doctor, migrations, safe_mode
    by = lambda: {c["id"]: c for c in [doctor._check("migrations", "update status", doctor._c_migrations)]}
    monkeypatch.setattr(migrations, "latest", lambda: 1)
    (home / "schema.json").write_text(json.dumps({"schema": 1}), encoding="utf-8")
    assert by()["migrations"]["state"] == "healthy"
    (home / "schema.json").write_text(json.dumps({"schema": 5}), encoding="utf-8")
    assert by()["migrations"]["state"] == "misconfigured"
    (home / "schema.json").write_text(json.dumps({"schema": 1}), encoding="utf-8")
    safe_mode.set_next_start(True, reason="migration_failed")
    assert by()["migrations"]["state"] == "broken"
    safe_mode.set_next_start(False)
    migrations._journal_path(home).write_text("{}", encoding="utf-8")
    assert by()["migrations"]["state"] == "repairing"


def test_the_after_upgrade_check_counts_what_is_open(home):
    _dr()
    from agent_friday.services import derived_rebuild, doctor, upgrade_review
    chk = lambda: doctor._check("after_upgrade", "update status", doctor._c_after_upgrade)
    assert chk()["state"] == "healthy"
    derived_rebuild.request(["library"], "a restore", home)
    assert chk()["state"] == "repairing"
    upgrade_review.add("rebuild_failed", "library", "could not", home=home)
    c = chk()
    assert c["state"] == "degraded" and c["code"] == "open_items:1"


def test_the_report_lists_the_repairs_and_the_weekly_email_leaves_them_out(home, monkeypatch):
    _dr()
    from agent_friday.services import doctor, doctor_weekly, upgrade_review
    upgrade_review.add("rebuild_failed", "library", "could not", home=home)
    rep = doctor.build()
    assert "repairs" in rep and "after_upgrade" in rep["repairs"]
    assert any(r["id"] == "rebuild_now" for r in rep["repairs"]["after_upgrade"])
    assert "Repairs offered (none needs a model)" in doctor.render_text(rep)
    assert "Repairs offered" not in doctor_weekly._render(rep, False)
