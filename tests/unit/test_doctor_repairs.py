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


MODEL_OR_NETWORK = {"model_router", "anthropic", "openai", "requests", "httpx", "ollama", "google.genai"}


def _imports(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for n in ast.walk(tree):                       # module-level and inside functions
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            out |= {n.module or ""} | {a.name for a in n.names}
    return {x for part in out for x in (part, part.rsplit(".", 1)[-1])}


def test_no_repair_needs_a_model_or_the_network():
    dr = _dr()
    assert dr.NEEDS_MODEL is False
    targets = {"doctor_repairs", "safe_mode", "authority"} | {
        r["needs"].rsplit(".", 1)[-1] for rs in dr.REPAIRS.values() for r in rs
        if r.get("needs") and importlib.util.find_spec(r["needs"]) is not None}
    for name in sorted(targets):
        p = ROOT / "src" / "agent_friday" / "services" / (name + ".py")
        bad = _imports(p) & MODEL_OR_NETWORK
        assert not bad, (name, bad)
    for rs in dr.REPAIRS.values():
        for r in rs:
            assert set(r) <= {"id", "label", "where", "needs", "codes"} and r["label"]
            assert r["where"] == "" or r["where"].startswith("#")


def test_every_place_a_repair_points_to_is_on_the_doctor_page():
    dr = _dr()
    html = (ROOT / "static" / "doctor.html").read_text(encoding="utf-8")
    for rs in dr.REPAIRS.values():
        for r in rs:
            if r["where"] and not (r.get("needs") and importlib.util.find_spec(r["needs"]) is None):
                assert 'id="%s"' % r["where"][1:] in html, r


def test_repairs_fit_the_code_and_a_check_that_found_nothing_gets_none():
    dr = _dr()
    ids = lambda out, cid: [r["id"] for r in out[cid]]
    out = dr.offered([{"id": "migrations", "state": "misconfigured", "code": "newer_folder:2>1"}])
    assert ids(out, "migrations") == ["update_friday"]                  # never "retry the upgrade"
    out = dr.offered([{"id": "migrations", "state": "broken", "code": "upgrade_failed_safe_mode_held"}])
    assert "migration_retry" in ids(out, "migrations") and "leave_safe_mode" in ids(out, "migrations")
    out = dr.offered([{"id": "receipt_chain", "state": "unverified", "code": "no_key_to_check_with"},
                      {"id": "rules", "state": "unverified", "code": "self_test_not_run"},
                      {"id": "vault", "state": "unverified", "code": "check_failed:OSError"}])
    assert set(out) == {"vault"}                                         # only a check that failed to run
    out = dr.offered([{"id": "safe_mode", "state": "blocked_by_policy", "code": "safe_mode_failed_starts"}])
    assert "leave_safe_mode" not in ids(out, "safe_mode")


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


def test_the_doctor_page_links_each_repair_to_its_section():
    _dr()
    html = (ROOT / "static" / "doctor.html").read_text(encoding="utf-8")
    i = html.index("Repairs offered (none needs a model)")
    blk = html[i - 600:i + 900]
    assert "rep.repairs" in blk and "a.href = r.where" in blk and "textContent = r.label" in blk
    assert "innerHTML" not in blk
