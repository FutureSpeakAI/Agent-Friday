"""The evidence pack, built and SAVED ON THE PC for a trigger from an owner phone (K6 brief, decision of 2026-11-17).

Stephen approved full control of the PC from his owner phone: the phone may trigger the pack, the file stays on the PC,
and nothing carries the bytes to the phone. These tests hold the service side (services/evidence_pack_export.export_to_disk):

* the pack is a real signed zip the pack's own checker passes, written under the folder it was given;
* the caller gets the path, the manifest hash and the entry count, never the bytes;
* the ledger line names who triggered it (`device:<id>`), and `verify()` still passes;
* it is written whole or not at all: no `.part` file is left, and a failed rename leaves no pack;
* an unreadable archive writes nothing.
"""
import hashlib
import json
import time
import zipfile

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import action_ledger as L
from agent_friday.services import evidence_pack as pack
from agent_friday.services import evidence_pack_checker as checker
from agent_friday.services import evidence_pack_export as export


def _day():
    return time.strftime("%Y-%m-%d", time.gmtime())


def _range():
    start = int(time.mktime(time.strptime(_day(), "%Y-%m-%d"))) - 86400
    return pack.Range(start, start + 3 * 86400)


@pytest.fixture
def seeded(client):
    for tool, decision in (("send_email", "allow"), ("create_calendar_event", "deny")):
        action_gate._receipt({"tool": tool, "class": "outward", "decision": decision, "surface": "test"})


def test_the_pack_is_saved_on_the_pc_as_a_zip_the_checker_passes(seeded, tmp_path):
    saved = export.export_to_disk(_range(), "test", "device:abc", folder=tmp_path)
    assert saved.path == str(tmp_path / saved.filename) and (tmp_path / saved.filename).is_file()
    with zipfile.ZipFile(saved.path) as z:
        files = {n: z.read(n) for n in z.namelist()}
    manifest = json.loads(files["manifest.json"])
    problems, _ = checker.check_files(files, manifest["public_key"])
    assert problems == [] and saved.entries == manifest["entries"] >= 2
    assert saved.manifest_sha256 == hashlib.sha256(files["manifest.json"]).hexdigest()


def test_the_caller_gets_a_path_and_numbers_never_the_bytes(seeded, tmp_path):
    saved = export.export_to_disk(_range(), "test", "owner", folder=tmp_path)
    assert {f.name for f in saved.__dataclass_fields__.values()} == {"path", "filename", "manifest_sha256", "entries", "recorded"}
    assert all(isinstance(v, (str, int, bool)) for v in vars(saved).values())


def test_the_ledger_line_names_the_device_that_triggered_it_and_verify_passes(seeded, tmp_path):
    before = len(L.entries(["export"]))
    export.export_to_disk(_range(), "test", "device:abc", folder=tmp_path)
    lines = L.entries(["export"], newest_first=False)
    assert len(lines) == before + 1 and lines[-1]["rec"]["by"] == "device:abc"
    assert L.verify(force=True)["ok"] is True


def test_the_default_export_is_still_by_the_owner(seeded):
    export.export(_range(), "test")
    assert L.entries(["export"], newest_first=False)[-1]["rec"]["by"] == "owner"


def test_it_is_written_whole_or_not_at_all(seeded, tmp_path, monkeypatch):
    export.export_to_disk(_range(), "test", "owner", folder=tmp_path)
    assert not list(tmp_path.glob("*.part"))
    other = tmp_path / "second"

    def refuse(self, target):
        raise OSError("disk full")
    monkeypatch.setattr(type(tmp_path), "replace", refuse)
    with pytest.raises(OSError):
        export.export_to_disk(_range(), "test", "owner", folder=other)
    monkeypatch.undo()
    assert not list(other.glob("*.zip"))


def test_an_unreadable_archive_writes_nothing(seeded, tmp_path, monkeypatch):
    def unreadable(*a, **k):
        raise L.LedgerError("the archive file is missing")
    monkeypatch.setattr(L, "entries", unreadable)
    with pytest.raises(pack.PackRefused):
        export.export_to_disk(_range(), "test", "owner", folder=tmp_path)
    monkeypatch.undo()
    assert not list(tmp_path.iterdir())


def test_the_default_folder_is_inside_the_friday_home():
    from agent_friday.core import FRIDAY_DIR
    assert export.packs_dir() == FRIDAY_DIR / "evidence_packs"
