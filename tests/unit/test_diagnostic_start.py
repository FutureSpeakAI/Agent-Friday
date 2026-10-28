"""Diagnostic start mode (north star §33.5-6): `friday diagnose` starts Friday
once in safe mode, records how far the start got, and saves a Doctor report
with that timeline on this PC. No environment values or paths go in it,
nothing is sent, and the next start is normal."""
from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLANTED = "planted-value-zq81-never-in-a-report"


def _ds():
    assert importlib.util.find_spec("agent_friday.services.diagnostic_start") is not None, \
        "there is no diagnostic start mode"
    return importlib.import_module("agent_friday.services.diagnostic_start")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / ".friday"
    h.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(h))
    monkeypatch.setenv("FRIDAY_DIAGNOSTIC_START", "1")
    monkeypatch.delenv("FRIDAY_SAFE_START", raising=False)
    from agent_friday.services import safe_mode
    safe_mode.reset_for_tests()
    if importlib.util.find_spec("agent_friday.services.diagnostic_start") is not None:
        ds = importlib.import_module("agent_friday.services.diagnostic_start")
        monkeypatch.setattr(ds, "_TIMELINE", [])
        monkeypatch.setattr(ds, "_STATE", {})
        monkeypatch.setattr(ds, "_T0", [__import__("time").monotonic()])
    yield h
    safe_mode.reset_for_tests()


def _small_doctor(monkeypatch):
    from agent_friday.services import doctor
    monkeypatch.setattr(doctor, "build", lambda: {"report": "agent-friday-doctor", "id": "doctor-x",
                                                  "checks": [{"id": "rules", "state": "healthy", "code": "ok"}]})


def test_a_diagnostic_start_is_safe_mode_for_that_start_only(home, monkeypatch):
    ds = _ds()
    from agent_friday.services import safe_mode
    st = safe_mode.state()
    assert ds.active() and st["active"] is True and st["how"] == "diagnostic"
    assert not safe_mode.allows("cloud") and not safe_mode.allows("scheduler")
    assert not safe_mode.marker_path().exists()                     # nothing kept for the next start
    monkeypatch.delenv("FRIDAY_DIAGNOSTIC_START")
    safe_mode.reset_for_tests()
    assert safe_mode.state()["active"] is False and not ds.active()


def test_the_timeline_keeps_phase_names_times_and_short_words_only(home):
    ds = _ds()
    ds.mark("migrations", ran=True, where="C:\\Users\\someone\\secret", why=PLANTED * 2)
    ds.mark("migrations", ran=False)                                 # the first one stands
    ds.mark("not_a_phase")
    ds.mark("serving")
    tl = ds.timeline()
    assert [r["phase"] for r in tl] == ["migrations", "serving"]
    assert tl[0]["ran"] is True and "where" not in tl[0] and "why" not in tl[0]
    assert tl[0]["at_s"] <= tl[1]["at_s"]


def test_the_report_has_the_doctor_and_the_timeline_and_no_environment(home, monkeypatch):
    ds = _ds()
    monkeypatch.setenv("SOME_TOKEN_NOBODY_SHIPS", PLANTED)
    ds.mark("migrations", ran=False)
    ds.mark("safe_mode", active=True)
    rep = ds.run_report({"ok": True, "from": 0, "to": 1, "backup": "C:\\Users\\x\\.friday.pre-migration-1"})
    assert rep["safe_mode"] is True and rep["entered"] == "diagnostic start" and rep["finished"] is False
    assert rep["migrations"] == {"ok": True, "from": 0, "to": 1}               # no paths
    assert rep["doctor"].get("checks"), "the report carries no Doctor run"
    files = sorted(p.name for p in (home / "diagnostics").iterdir())       # progress file gone too
    assert files == [rep["id"] + ".json", rep["id"] + ".txt"]
    for name in files:
        body = (home / "diagnostics" / name).read_text(encoding="utf-8")
        assert PLANTED not in body and "SOME_TOKEN_NOBODY_SHIPS" not in body
    txt = (home / "diagnostics" / (rep["id"] + ".txt")).read_text(encoding="utf-8")
    assert "Start timeline" in txt and "not reached: imports, instance_lock, boot_checks, serving" in txt
    assert ds.latest()["id"] == rep["id"]


def test_a_doctor_that_fails_is_reported_not_raised(home, monkeypatch):
    ds = _ds()
    from agent_friday.services import doctor

    def boom():
        raise RuntimeError("a check hung up")
    monkeypatch.setattr(doctor, "build", boom)
    rep = ds.run_report()
    assert rep["doctor"]["failed"] == "RuntimeError" and rep["migrations"] == "none to run"
    txt = (home / "diagnostics" / (rep["id"] + ".txt")).read_text(encoding="utf-8")
    assert "Doctor run failed: RuntimeError" in txt


def test_each_phase_is_on_disk_so_a_start_that_stops_still_shows_how_far_it_got(home, monkeypatch):
    ds = _ds()
    _small_doctor(monkeypatch)
    ds.mark("migrations", ran=False)
    ds.mark("imports")
    prog = list((home / "diagnostics").glob("progress-*.json"))
    assert len(prog) == 1
    assert [r["phase"] for r in json.loads(prog[0].read_text(encoding="utf-8"))["timeline"]] == \
        ["migrations", "imports"]
    prog[0].rename(prog[0].with_name("progress-999999.json"))   # as if that process had ended
    rep = ds.settle_unfinished()
    assert rep and rep["finished"] is False and [r["phase"] for r in rep["timeline"]] == ["migrations", "imports"]
    txt = (home / "diagnostics" / (rep["id"] + ".txt")).read_text(encoding="utf-8")
    assert "ended before it was serving" in txt and "not reached: safe_mode" in txt
    assert not list((home / "diagnostics").glob("progress-*.json")) and ds.settle_unfinished() is None


def test_a_report_saved_twice_in_one_start_is_saved_once(home, monkeypatch):
    ds = _ds()
    _small_doctor(monkeypatch)
    a, b = ds.run_report(), ds.run_report()
    assert a["id"] == b["id"] and len(list((home / "diagnostics").glob("diagnostic-*.json"))) == 1


def test_the_next_start_can_be_chosen_once_and_is_used_once(home, monkeypatch):
    ds = _ds()
    monkeypatch.delenv("FRIDAY_DIAGNOSTIC_START")
    assert ds.claim_next_start() is False                       # nothing chosen: a normal start
    assert ds.set_next_start(True) is True and ds.next_marker().exists()
    assert ds.claim_next_start() is True and not ds.next_marker().exists()
    import os
    assert os.environ.get("FRIDAY_DIAGNOSTIC_START") == "1" and os.environ.get("FRIDAY_SAFE_START") == "1"
    monkeypatch.delenv("FRIDAY_DIAGNOSTIC_START")
    monkeypatch.delenv("FRIDAY_SAFE_START")
    assert ds.claim_next_start() is False                        # and the start after it is normal


def test_a_saved_report_in_an_odd_shape_still_renders(home):
    ds = _ds()
    out = ds.render_text({"id": "x", "timeline": [{"phase": "imports", "at_s": "soon"}, "junk", 3],
                          "doctor": "not a dict"})
    assert "imports" in out and "not reached" in out


def test_the_newest_five_reports_are_kept(home, monkeypatch):
    ds = _ds()
    _small_doctor(monkeypatch)
    for _ in range(7):
        ds._STATE.clear()                                        # one report per start
        ds.run_report()
    d = home / "diagnostics"
    assert len(list(d.glob("diagnostic-*.json"))) == 5 and len(list(d.glob("diagnostic-*.txt"))) == 5


def test_a_normal_start_writes_no_diagnostic_report(home, monkeypatch):
    ds = _ds()
    monkeypatch.delenv("FRIDAY_DIAGNOSTIC_START")
    ds.mark("serving")
    assert ds.run_report() is None and ds.timeline() == [] and not (home / "diagnostics").exists()


def test_friday_diagnose_starts_once_in_diagnostic_mode_and_not_over_a_running_friday():
    _ds()
    src = (ROOT / "src" / "agent_friday" / "cli.py").read_text(encoding="utf-8")
    assert 'add_parser("diagnose"' in src, "there is no `friday diagnose`"
    assert "def cmd_diagnose(" in src
    i = src.index("def cmd_diagnose(")
    body = src[i:src.index("\ndef ", i + 10)]
    assert body.index("_port_free(SERVER_PORT)") < body.index('os.environ["FRIDAY_DIAGNOSTIC_START"] = "1"') \
        < body.index("cmd_start()") < body.index("settle_unfinished()")
    assert "set_next_start" not in body                              # one start only


def test_the_server_marks_each_phase_and_reports_once_serving():
    _ds()
    src = (ROOT / "src" / "agent_friday" / "server.py").read_text(encoding="utf-8")
    for ph in ("migrations", "imports", "safe_mode", "instance_lock", "boot_checks", "serving"):
        assert 'mark("%s"' % ph in src, "the server never marks the %s phase" % ph
    assert src.index("claim_next_start()") < src.index("_mig_boot.run_pending()") \
        < src.index('mark("migrations"') < src.index("= _acquire_single_instance_lock()")
    assert "on_bound=(lambda: _diag.mark(\"serving\"))" in src                # once the port is bound
    i = src.index("except OSError as _bind_err:")
    assert "run_report(" in src[i:i + 300]                          # a bind failure still reports
    assert src.index("_DIAG_T0 = ") < src.index("recover_interrupted_restore()")
    pooled = (ROOT / "src" / "agent_friday" / "services" / "pooled_server.py").read_text(encoding="utf-8")
    assert "on_bound()" in pooled


def test_the_doctor_page_shows_the_diagnostic_report():
    _ds()
    html = (ROOT / "static" / "doctor.html").read_text(encoding="utf-8")
    assert 'id="diagnostic"' in html and "/api/doctor/diagnostic" in html and "friday diagnose" in html


def test_the_saved_report_is_valid_json_with_its_version(home, monkeypatch):
    ds = _ds()
    _small_doctor(monkeypatch)
    rep = ds.run_report()
    on_disk = json.loads((home / "diagnostics" / (rep["id"] + ".json")).read_text(encoding="utf-8"))
    assert on_disk["report"] == "agent-friday-diagnostic-start" and on_disk["report_version"] == 1
    assert on_disk["sharing"].startswith("by hand only")
