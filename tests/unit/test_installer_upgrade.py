"""An upgrade protects the data before it touches a file, and proves it after.

`packaging/windows/lib/Upgrade.ps1` is what `install.ps1 -InnoManaged` runs
around the copy. These tests run its functions for real against scratch install
and data folders:

  * releases rank by build sequence, in PowerShell as in Python;
  * Beta 1.0 over 5.14.3 proceeds; 5.14.3 over Beta 1.0 is refused;
  * the data home is backed up first, and the vault key files are in the copy;
  * the before/after check fails on a missing file, a changed vault key file
    and a shrunken file count, and passes on an untouched folder;
  * Friday is stopped by the processes that run from the install folder, never
    by a name pattern.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent_friday import release
from tests.unit._installer_ps import LIB, needs_powershell, ps_json, read_text, run_ps

UPGRADE = read_text(LIB / "Upgrade.ps1")
HEAD = ". '%s'\n. '%s'\n" % (LIB / "Common.ps1", LIB / "Upgrade.ps1")

TABLE = ["5.14.3", "v5.14.3", "5.9.0", "1.0.0b1", "v1.0.0-beta.1", "1.0.0", "v1.0.0-rc.2", "v1.0.0-beta.2",
         "1.1.0b1", "6.0.0", "5.14.3+meta", "1.0.0rc1", "not a version"]


@needs_powershell
def test_powershell_and_python_rank_every_version_the_same(tmp_path):
    script = HEAD + "$t = @(%s)\n$o = [ordered]@{}\nforeach ($v in $t) { $o[$v] = (Get-BuildSequence -Version $v) }\n$o | ConvertTo-Json -Compress\n" % \
        ",".join("'%s'" % v for v in TABLE)
    got = ps_json(script, tmp_path)
    for v in TABLE:
        assert got[v] == release.sequence_for_version(v), (v, got[v], release.sequence_for_version(v))


def test_the_script_keeps_the_era_floor_the_python_module_defines():
    assert re.search(r"\$script:EraFloor = %d\b" % release.ERA_FLOOR, UPGRADE)


def _install(root: Path, version: str) -> None:
    (root / "app").mkdir(parents=True)
    (root / "app" / "pyproject.toml").write_text('[project]\nversion = "%s"\n' % version, encoding="utf-8")


def _data_home(home: Path) -> None:
    (home / "vault").mkdir(parents=True)
    (home / "vault" / ".vault_config.json").write_text('{"salt_hex": "00"}', encoding="utf-8")
    (home / "vault" / ".governance-key").write_bytes(b"k" * 32)
    (home / "vault" / "note.enc").write_bytes(b"ciphertext")
    (home / "settings.json").write_text('{"a": 1}', encoding="utf-8")
    (home / "wiki").mkdir()
    (home / "wiki" / "page.md").write_text("notes", encoding="utf-8")
    (home / "cache").mkdir()
    (home / "cache" / "big.bin").write_bytes(b"x" * 1000)
    (home / "logs").mkdir()
    (home / "logs" / "friday.log").write_text("log", encoding="utf-8")


@needs_powershell
def test_beta_over_5_14_3_proceeds_and_backs_the_data_up_first(tmp_path):
    root, home, backups = tmp_path / "AgentFriday", tmp_path / "home", tmp_path / "backups"
    _install(root, "5.14.3")
    _data_home(home)
    out = ps_json(HEAD + """
$r = Invoke-UpgradePreflight -InstallRoot '%s' -NewSequence %d
[ordered]@{ found = $r.Existing.Found; version = $r.Existing.Version; seq = $r.Existing.Sequence
            backup = $r.Backup.Path; mode = $r.Backup.Mode; files = $r.Backup.Files } | ConvertTo-Json -Compress
""" % (root, release.BUILD_SEQUENCE), tmp_path, env={"FRIDAY_HOME": str(home), "FRIDAY_BACKUP_ROOT": str(backups)})
    assert out["found"] and out["version"] == "5.14.3" and out["seq"] == 51403
    assert out["mode"] == "full"
    copy = Path(out["backup"])
    assert copy.parent == backups, "the backup lives outside the data home"
    assert (copy / "vault" / ".vault_config.json").read_text(encoding="utf-8") == '{"salt_hex": "00"}'
    assert (copy / "vault" / ".governance-key").read_bytes() == b"k" * 32
    assert (copy / "settings.json").exists() and (copy / "wiki" / "page.md").exists()
    assert not (copy / "cache").exists() and not (copy / "logs").exists(), "caches and logs are not the person's work"


@needs_powershell
def test_an_older_setup_never_replaces_a_newer_install(tmp_path):
    root, home = tmp_path / "AgentFriday", tmp_path / "home"
    _install(root, "1.0.0b1")
    _data_home(home)
    out = run_ps(HEAD + "try { Invoke-UpgradePreflight -InstallRoot '%s' -NewSequence 51403 | Out-Null; 'proceeded' } catch { 'refused: ' + $_.Exception.Message }\n" % root,
                 tmp_path, env={"FRIDAY_HOME": str(home), "FRIDAY_BACKUP_ROOT": str(tmp_path / "b")})
    assert "refused" in out.stdout and "newer" in out.stdout.lower(), out.stdout + out.stderr
    assert not (tmp_path / "b").exists(), "a refusal changes nothing, not even a backup"


@needs_powershell
def test_the_same_release_may_be_reinstalled(tmp_path):
    root, home = tmp_path / "AgentFriday", tmp_path / "home"
    _install(root, "1.0.0b1")
    _data_home(home)
    out = run_ps(HEAD + "try { Invoke-UpgradePreflight -InstallRoot '%s' -NewSequence %d | Out-Null; 'proceeded' } catch { 'refused' }\n" % (root, release.BUILD_SEQUENCE),
                 tmp_path, env={"FRIDAY_HOME": str(home), "FRIDAY_BACKUP_ROOT": str(tmp_path / "b")})
    assert "proceeded" in out.stdout, out.stdout + out.stderr


@needs_powershell
def test_a_fresh_install_makes_no_backup_and_takes_a_snapshot(tmp_path):
    root, home = tmp_path / "nothing-here", tmp_path / "home"
    _data_home(home)
    out = ps_json(HEAD + """
$r = Invoke-UpgradePreflight -InstallRoot '%s' -NewSequence %d
[ordered]@{ found = $r.Existing.Found; backup = ($null -ne $r.Backup); files = $r.Snapshot.Files; hashes = $r.Snapshot.Hashes.Count } | ConvertTo-Json -Compress
""" % (root, release.BUILD_SEQUENCE), tmp_path, env={"FRIDAY_HOME": str(home), "FRIDAY_BACKUP_ROOT": str(tmp_path / "b")})
    assert out["found"] is False and out["backup"] is False
    assert out["files"] == 7 and out["hashes"] == 3, "every file counted; vault config, governance key and settings guarded"


CHECK = HEAD + """
$home_ = '%s'
$before = Get-DataSnapshot -DataHome $home_
%s
$after = Get-DataSnapshot -DataHome $home_
$c = Compare-DataSnapshots -Before $before -After $after
[ordered]@{ ok = $c.Ok; problems = @($c.Problems); before = $c.FilesBefore; after = $c.FilesAfter } | ConvertTo-Json -Compress
"""


@needs_powershell
@pytest.mark.parametrize("name,change,ok,needle", [
    ("untouched", "", True, ""),
    ("logs grow", "Set-Content -LiteralPath (Join-Path $home_ 'logs\\new.log') -Value 'x'", True, ""),
    ("vault key file changed", "Set-Content -LiteralPath (Join-Path $home_ 'vault\\.vault_config.json') -Value 'tampered'", False, ".vault_config.json changed"),
    ("vault key file removed", "Remove-Item -LiteralPath (Join-Path $home_ 'vault\\.governance-key')", False, ".governance-key is missing"),
    ("a note removed", "Remove-Item -LiteralPath (Join-Path $home_ 'wiki\\page.md')", False, "file(s) after the upgrade"),
    ("settings changed", "Set-Content -LiteralPath (Join-Path $home_ 'settings.json') -Value '{}'", False, "settings.json changed"),
    ("data folder gone", "Remove-Item -LiteralPath $home_ -Recurse -Force", False, "gone"),
])
def test_the_after_check_catches_what_went_wrong(tmp_path, name, change, ok, needle):
    home = tmp_path / "home"
    _data_home(home)
    got = ps_json(CHECK % (home, change), tmp_path)
    assert got["ok"] is ok, (name, got)
    if needle:
        assert any(needle in p for p in got["problems"]), (name, got["problems"])


@needs_powershell
def test_the_data_check_writes_its_record(tmp_path):
    home, logs = tmp_path / "home", tmp_path / "logs"
    _data_home(home)
    got = ps_json(HEAD + """
$before = Get-DataSnapshot -DataHome '%s'
$c = Test-DataHomeIntact -Before $before -LogDir '%s'
[ordered]@{ ok = $c.Ok } | ConvertTo-Json -Compress
""" % (home, logs), tmp_path)
    assert got["ok"] is True
    rec = json.loads((logs / "data-check.json").read_text(encoding="utf-8"))
    assert rec["ok"] is True and rec["files_before"] == rec["files_after"] == 7 and rec["guarded_files_after"] == 3


@needs_powershell
def test_a_big_data_home_falls_back_to_the_essentials(tmp_path):
    home = tmp_path / "home"
    _data_home(home)
    got = ps_json(HEAD + """
$script:BackupBudgetBytes = 10
$r = Backup-FridayData -DataHome '%s' -BackupRoot '%s'
[ordered]@{ mode = $r.Mode; path = $r.Path; skipped = @($r.Skipped).Count } | ConvertTo-Json -Compress
""" % (home, tmp_path / "b"), tmp_path)
    copy = Path(got["path"])
    assert got["mode"] == "essentials" and got["skipped"] == 1
    assert (copy / "vault" / ".vault_config.json").exists() and (copy / "settings.json").exists()
    assert not (copy / "wiki").exists(), "the essentials are the vault and the settings"


def test_friday_is_stopped_by_path_never_by_name():
    assert "Get-ProcessesRunningFrom" in UPGRADE
    assert "StartsWith($rootFull" in UPGRADE
    assert not re.search(r"Stop-Process\s+-Name|taskkill|Get-Process\s+-Name|\.ProcessName\s+-(like|match)", UPGRADE, flags=re.I)


@needs_powershell
def test_a_process_outside_the_install_folder_is_left_alone(tmp_path):
    root = tmp_path / "AgentFriday"
    root.mkdir()
    out = ps_json(HEAD + """
$n = @(Get-ProcessesRunningFrom -Root '%s').Count
$stopped = Stop-FridayGracefully -InstallRoot '%s' -GraceSeconds 1
[ordered]@{ running = $n; stopped = $stopped } | ConvertTo-Json -Compress
""" % (root, root), tmp_path)
    assert out == {"running": 0, "stopped": 0}
