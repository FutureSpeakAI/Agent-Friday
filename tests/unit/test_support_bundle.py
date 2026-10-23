"""The support bundle (north star §36.0-9, A3): one zip made on this PC, to
attach by hand. It holds the Doctor report, the store inventory and log
excerpts redacted word by word, and nothing the owner wrote or named."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

NAME = "Wilhelmina"
EMAIL = "wilhelmina.private@example.test"
PRIVATE_FACT = "allergic-to-quinces-7731"
KEY = "sk-ant-api03-" + "A1b2C3d4E5f6G7h8I9j0" * 3          # pragma: allowlist secret
PLAN = "move to Lisbon in March"


def _sb():
    assert importlib.util.find_spec("agent_friday.services.support_bundle") is not None, \
        "there is no support bundle"
    return importlib.import_module("agent_friday.services.support_bundle")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / ".friday"
    (h / "logs").mkdir(parents=True)
    monkeypatch.setenv("FRIDAY_HOME", str(h))
    folder = tmp_path / ("Taxes 2026 " + NAME)
    (h / "settings.json").write_text(json.dumps({"user_name": NAME, "a_secret_plan": PLAN}), encoding="utf-8")
    (h / "friday.log").write_text("\n".join([
        "a first line that may be cut",
        f"2026-10-23T09:00:00 ERROR    agent_friday.services.probe \u2014 ValueError: could not read "
        f"{folder}\\notes.txt for {EMAIL} ({PRIVATE_FACT})",
        f"2026-10-23T09:00:01 INFO     agent_friday.chat \u2014 {NAME} said {PLAN}",
        f"2026-10-23T09:00:02 ERROR    {NAME.lower()}_plugin \u2014 {NAME}VaultError: {NAME} {PLAN}",
        f"2026-10-23T09:00:03 WARNING  agent_friday.services.mail \u2014 send failed for \"{PLAN}\" "
        f"to https://mail.example.test/u/{NAME} key {KEY} read_timeout after 30 seconds",
        "Traceback (most recent call last):",
        f'  File "{folder}\\proj\\{NAME.lower()}_tool.py", line 3, in do_{NAME.lower()}',
        '  File "/opt/x/site-packages/agent_friday/services/doctor.py", line 10, in build',
        f"KeyError: '{NAME}'",
        "",
    ]), encoding="utf-8")
    (h / "logs" / "crashes.log").write_text(
        f"Fatal Python error: Segmentation fault\n\nCurrent thread 0x00001234 (most recent call first):\n"
        f'  File "{folder}\\x.py", line 9 in {NAME}\n', encoding="utf-8")
    if importlib.util.find_spec("agent_friday.services.support_bundle") is not None:
        sb = importlib.import_module("agent_friday.services.support_bundle")
        monkeypatch.setattr(sb, "_off_record", lambda: False)
        monkeypatch.setattr(sb, "_recently_off_record", lambda: False)
        monkeypatch.setattr(sb, "_last_off_record", lambda: None)
    return h


def _zip(path) -> dict:
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_the_bundle_holds_the_report_the_stores_and_redacted_logs(home, tmp_path):
    r = _sb().make(tmp_path / "out")
    files = _zip(r["file"])
    assert {"README.txt", "doctor-report.json", "doctor-report.txt", "stores.json",
            "log-excerpts.txt", "manifest.json"} <= set(files)
    rep = json.loads(files["doctor-report.json"])
    assert rep["report"] == "agent-friday-doctor" and "checks" in rep
    stores = {s["store"]: s for s in json.loads(files["stores.json"])["stores"]}
    assert stores["settings"]["location"] == "settings.json"
    logs = files["log-excerpts.txt"].decode("utf-8")
    assert "ERROR agent_friday.services.probe" in logs and "ValueError" in logs
    assert "WARNING agent_friday.services.mail" in logs and "read_timeout" in logs
    assert 'File "agent_friday/services/doctor.py", line 10, in build' in logs
    assert 'File "<other>", line 3' in logs
    assert "Fatal Python error" in logs or "Fatal" in logs
    assert "other_logger" in logs and "other_exception" in logs


def test_nothing_the_owner_wrote_or_named_is_in_it(home, tmp_path):
    r = _sb().make(tmp_path / "out")
    blob = b"\n".join(_zip(r["file"]).values()).decode("utf-8", errors="replace")
    for needle in (NAME, NAME.lower(), EMAIL, PRIVATE_FACT, KEY, "Lisbon", "Taxes", "mail.example.test",
                   "notes.txt", "a_secret_plan"):
        assert needle not in blob, needle
    assert "Lisbon" not in blob


def test_an_info_line_is_never_kept(home):
    sb = _sb()
    lines = sb.log_excerpt(home / "friday.log", [])
    assert not any(" INFO " in ln for ln in lines)
    assert not any("cut" in ln for ln in lines)          # a cut first line is dropped


@pytest.mark.parametrize("raw,keep,gone", [
    ("2026-10-23T09:00:00 ERROR    agent_friday.x \u2014 timeout after 30 seconds", "timeout after 30 seconds", ""),
    ("2026-10-23T09:00:00 ERROR    agent_friday.x \u2014 PermissionError for D:\\Shared\\Wilhelmina\\a.txt",
     "PermissionError", "Wilhelmina"),
    ("2026-10-23T09:00:00 ERROR    agent_friday.x \u2014 status=503 from ollama", "status=503", "ollama"),
    ("2026-10-23T09:00:00 ERROR    agent_friday.x \u2014 wilhelmina_notes failed", "failed", "wilhelmina"),
    ("2026-10-23T09:00:00 ERROR    agent_friday.x \u2014 could not call 07700 900123", "could not call", "900123"),
    ("2026-10-23T09:00:00 ERROR    agent_friday.x \u2014 bad code=482913 for login", "code=", "482913"),
    ("2026-10-23T09:00:00 ERROR    agent_friday.x \u2014 card 4111 1111 1111 1111 refused", "refused", "1111"),
    ("2026-10-23T09:00:00 ERROR    agent_friday.x \u2014 born 12 May 1990", "12", "1990"),
    ("2026-10-23T09:00:00 ERROR    agent_friday.x \u2014 at 51.507351 -0.127758", "at", "507351"),
    ("2026-10-23T09:00:00 ERROR    agent_friday.x \u2014 wilhelmina=1 status=503", "status=503", "wilhelmina"),
    ("2026-10-23T09:00:00 ERROR    agent_friday.x \u2014 timeout after 2500 ms", "2500 ms", ""),
    ("2026-10-23T09:00:00 ERROR    agent_friday.x \u2014 then Will said May was fine", "was", "Will"),
    ('  File "D:\\Notes\\agent_friday\\divorce_letters.py", line 4, in letters', 'File "<other>", line 4, in ~',
     "divorce"),
])
def test_a_line_keeps_its_shape_and_loses_its_words(raw, keep, gone):
    out = _sb().redact_log_line(raw, [])
    assert keep in out, out
    if gone:
        assert gone not in out, out


def test_the_owner_name_from_settings_is_withheld(home, tmp_path):
    (home / "friday.log").write_text("first\n2026-10-23T09:00:00 ERROR    agent_friday.x \u2014 hello "
                                     "wilhelmina_ok and was ok\n", encoding="utf-8")
    files = _zip(_sb().make(tmp_path / "out")["file"])
    assert "wilhelmina" not in files["log-excerpts.txt"].decode("utf-8").lower()


def test_only_lines_after_an_off_record_session_are_kept(home, tmp_path, monkeypatch):
    from datetime import datetime
    sb = _sb()
    cut = datetime.fromisoformat("2026-10-23T09:00:01.500000").timestamp()
    monkeypatch.setattr(sb, "_last_off_record", lambda: cut)
    r = sb.make(tmp_path / "out")
    logs = _zip(r["file"])["log-excerpts.txt"].decode("utf-8")
    assert "agent_friday.services.probe" not in logs          # 09:00:00, before
    assert "agent_friday.services.mail" in logs               # 09:00:03, after
    assert "logs/crashes.log" not in logs and any("Crash lines" in n for n in r["notes"])


def test_a_cloud_synced_folder_is_asked_about_and_a_relative_one_refused(home, tmp_path):
    sb = _sb()
    with pytest.raises(sb.CloudFolder):
        sb.make(tmp_path / "OneDrive" / "Documents" / "Friday Support")
    assert not (tmp_path / "OneDrive").exists()
    assert Path(sb.make(tmp_path / "OneDrive" / "Documents" / "Friday Support", cloud_ok=True)["file"]).exists()
    with pytest.raises(sb.SupportError):
        sb.make(Path("relative-folder"))


def test_the_manifest_hashes_every_file(home, tmp_path):
    files = _zip(_sb().make(tmp_path / "out")["file"])
    man = json.loads(files.pop("manifest.json"))
    assert set(man["files"]) == set(files)
    for n, b in files.items():
        assert man["files"][n] == hashlib.sha256(b).hexdigest(), n


def test_off_the_record_nothing_is_made(home, tmp_path, monkeypatch):
    sb = _sb()
    monkeypatch.setattr(sb, "_off_record", lambda: True)
    with pytest.raises(sb.SupportError):
        sb.make(tmp_path / "out")
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").iterdir())


def test_after_an_off_record_session_the_logs_are_left_out(home, tmp_path, monkeypatch):
    sb = _sb()
    monkeypatch.setattr(sb, "_recently_off_record", lambda: True)
    r = sb.make(tmp_path / "out")
    files = _zip(r["file"])
    assert "log-excerpts.txt" not in files and "boot-notes.json" not in files
    assert r["notes"] and "off the record" in files["README.txt"].decode("utf-8")


def test_it_is_never_written_inside_the_data_folder_and_never_overwrites(home, tmp_path):
    sb = _sb()
    with pytest.raises(sb.SupportError):
        sb.make(home / "inside")
    a = sb.make(tmp_path / "out")["file"]
    b = sb.make(tmp_path / "out")["file"]
    assert a != b and Path(a).exists() and Path(b).exists()
    assert not list((tmp_path / "out").glob("*.partial"))


def test_it_changes_nothing_in_the_data_folder_but_its_receipt(home, tmp_path):
    from agent_friday.services import action_ledger
    ledger = Path(action_ledger.ledger_dir()).resolve()

    def snap():
        return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in home.rglob("*")
                if p.is_file() and ledger not in p.resolve().parents}
    before = snap()
    _sb().make(tmp_path / "out")
    assert snap() == before


def test_the_command_line(home, tmp_path):
    from agent_friday import cli
    assert cli.cmd_doctor_support(out=str(tmp_path / "cli")) == 0
    assert list((tmp_path / "cli").glob("friday-support-*.zip"))
