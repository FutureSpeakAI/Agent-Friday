"""The Inno Setup script, its build, its workflow and the scripts it drives.

Inno Setup cannot be run in a unit test (and the setup program is never run on a
developer machine), so what can be checked is checked here: the script says what
the owner decided, the pieces that must agree with the Python side do, and the
PowerShell it drives behaves.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent_friday import release
from agent_friday.services import first_run_models as frm
from tests.unit._installer_ps import (INSTALLER_DIR, ISS, LIB, REPO, WINDOWS_DIR, needs_powershell, ps_json, read_text)

SCRIPT = read_text(ISS)
WORKFLOW = read_text(REPO / ".github" / "workflows" / "installer.yml")
BUILD = read_text(WINDOWS_DIR / "build-installer.ps1")
INSTALL = read_text(WINDOWS_DIR / "install.ps1")
UNINSTALL = read_text(WINDOWS_DIR / "uninstall.ps1")
SHORTCUTS = read_text(LIB / "Shortcuts.ps1")
AUTOSTART = read_text(WINDOWS_DIR / "autostart.ps1")


def setup_value(name: str) -> str:
    m = re.search(r"^%s=(.*)$" % re.escape(name), SCRIPT, flags=re.M)
    assert m, "[Setup] has no %s" % name
    return m.group(1).strip()


# ── what the owner decided ───────────────────────────────────────────────────

def test_it_is_per_user_with_no_administrator_prompt():
    assert setup_value("PrivilegesRequired") == "lowest"
    assert "PrivilegesRequiredOverridesAllowed" not in SCRIPT, "no way to ask for administrator"
    assert "HKLM" not in SCRIPT and "{commonappdata}" not in SCRIPT and "{pf}" not in SCRIPT


def test_it_installs_where_the_old_installer_did_so_an_upgrade_lands_in_place():
    assert setup_value("DefaultDirName") == r"{localappdata}\AgentFriday"
    assert re.search(r"\$InstallRoot\s*=\s*\(Join-Path \$env:LOCALAPPDATA 'AgentFriday'\)", INSTALL), "install.ps1's own default is the same folder"


def test_the_app_id_is_a_fixed_guid():
    guid = re.search(r'#define AppGuid "([0-9A-F-]{36})"', SCRIPT).group(1)
    assert guid == "BE782F40-3D1C-4F12-8635-4F7561938F75"
    assert setup_value("AppId") == "{{{#AppGuid}}"


def test_the_name_the_version_and_the_output_file():
    assert '#define ReleaseName "Agent Friday Beta 1.0"' in SCRIPT
    assert setup_value("AppName") == "{#ReleaseName}"
    assert setup_value("OutputBaseFilename") == "AgentFriday-Setup-{#AppTag}"
    assert release.RELEASE_NAME == "Agent Friday Beta 1.0"


def test_the_rocket_icon_is_used_everywhere():
    assert setup_value("SetupIconFile").endswith(r"assets\icons\futurespeak.ico")
    assert setup_value("UninstallDisplayIcon") == r"{app}\AgentFriday.ico"
    assert re.search(r'Source: "\{#RepoRoot\}\\assets\\icons\\futurespeak\.ico"; DestDir: "\{app\}"; DestName: "AgentFriday\.ico"', SCRIPT)
    icons = SCRIPT[SCRIPT.index("[Icons]"):SCRIPT.index("[InstallDelete]")]
    entries = [ln for ln in icons.splitlines() if ln.startswith("Name:")]
    assert len(entries) == 5
    for ln in entries:
        assert r'IconFilename: "{app}\AgentFriday.ico"' in ln, ln
    assert "friday.ico" not in SCRIPT.replace("AgentFriday.ico", "")
    assert r"app\assets\icons\futurespeak.ico" in INSTALL and r"app\assets\icons\futurespeak.ico" in AUTOSTART


def test_the_wizard_images_are_used_when_present_and_the_defaults_when_not():
    assert re.search(r'#if FileExists\(AddBackslash\(SourcePath\) \+ "wizard\.bmp"\)\s*\nWizardImageFile=wizard\.bmp\s*\n#endif', SCRIPT)
    assert re.search(r'#if FileExists\(AddBackslash\(SourcePath\) \+ "wizard-small\.bmp"\)\s*\nWizardSmallImageFile=wizard-small\.bmp\s*\n#endif', SCRIPT)


def test_it_carries_its_own_python_and_never_downloads_a_model():
    assert "StageDir" in SCRIPT and "DestDir: \"{tmp}\\af-setup\"" in SCRIPT
    assert "-SkipOllama" in SCRIPT, "no Ollama, no gemma: the model page replaces them"
    for word in ("DownloadTemporaryFile", "idpAddFile", "http://", "huggingface"):
        assert word not in SCRIPT.lower(), word


def test_it_sends_nothing_anywhere():
    assert "telemetry" not in SCRIPT.lower() or "no telemetry" in SCRIPT.lower()
    urls = set(re.findall(r"https?://[^\s\"']+", SCRIPT))
    assert urls == {"https://futurespeak.ai", "https://github.com/FutureSpeakAI/Agent-Friday/issues",
                    "https://github.com/FutureSpeakAI/Agent-Friday/releases"}, urls


# ── shortcuts and autostart ──────────────────────────────────────────────────

def test_the_shortcuts_are_named_agent_friday_and_land_on_the_desktop_and_in_the_start_menu():
    assert r'Name: "{userdesktop}\{#AppName}"' in SCRIPT, "the Desktop follows a OneDrive-redirected Desktop"
    assert r'Name: "{userprograms}\{#AppName}\{#AppName}"' in SCRIPT
    assert '#define AppName "Agent Friday"' in SCRIPT


def test_setup_verifies_the_shortcuts_exist_and_retries_through_the_shell():
    assert "function VerifyShortcuts" in SCRIPT and "FileExists(ShortcutPath('desktop'))" in SCRIPT
    assert "procedure EnsureShortcuts" in SCRIPT and "ensure-shortcuts.ps1" in SCRIPT
    assert "WriteSetupLog('Desktop shortcut" in SCRIPT, "the result is logged"
    post = SCRIPT[SCRIPT.index("procedure CurStepChanged"):SCRIPT.index("Uninstall: keep")]
    assert post.index("RunEngine();") < post.index("EnsureShortcuts();")


def test_nothing_in_the_installer_scripts_assumes_the_desktop_is_under_the_profile():
    ps = [WINDOWS_DIR / "install.ps1", WINDOWS_DIR / "uninstall.ps1", WINDOWS_DIR / "autostart.ps1",
          WINDOWS_DIR / "ensure-shortcuts.ps1"] + sorted(LIB.glob("*.ps1"))
    for f in ps:
        text = read_text(f)
        assert not re.search(r"GetFolderPath\(\s*'Desktop'\s*\)", text) or f.name == "Shortcuts.ps1", \
            "%s asks .NET for the Desktop directly; use Get-DesktopDir" % f.name
    body = SHORTCUTS[SHORTCUTS.index("function Get-DesktopDir"):SHORTCUTS.index("function Get-StartMenuDir")]
    body = body[body.index("#>"):]
    assert body.index("Get-SpecialDir") < body.index("Get-KnownFolderPath") < body.index("User Shell Folders") \
        < body.index("USERPROFILE"), "the profile folder is the last resort, not the first"
    assert "OneDrive" not in body.split("#>", 1)[1], "Windows' own answer is never overridden by a OneDrive guess"
    assert "0x8000u" in SHORTCUTS, "the shell lookup creates a missing Desktop (KF_FLAG_CREATE) instead of answering empty"
    assert "$fb = Join-Path $env:USERPROFILE 'Desktop'" not in SHORTCUTS


def test_the_autostart_entry_is_agent_friday_and_the_old_one_is_removed():
    assert r'Name: "{userstartup}\{#AppName}"' in SCRIPT and "Tasks: autostart" in SCRIPT
    assert r'Name: "{userstartup}\FRIDAY Desktop.lnk"' in SCRIPT
    assert 'ValueName: "FRIDAY Desktop"; Flags: deletevalue' in SCRIPT
    assert "Tasks: not autostart" in SCRIPT, "an answer of no is honoured over an entry a previous install left"
    assert re.search(r'Name: "autostart".*Flags: unchecked', SCRIPT), "autostart is off unless asked for"


# ── upgrade ──────────────────────────────────────────────────────────────────

def test_the_script_refuses_to_replace_a_newer_install():
    start = SCRIPT.index("function InitializeSetup")
    block = SCRIPT[start:SCRIPT.index("The model page", start)]
    assert "PrevSequence > {#BuildSequence}" in block and "Result := False" in block


def test_the_sequence_arithmetic_is_the_python_modules():
    block = SCRIPT[SCRIPT.index("BEGIN SEQUENCE ARITHMETIC"):SCRIPT.index("END SEQUENCE ARITHMETIC")]
    assert "EraFloor = %d" % release.ERA_FLOOR in block and "LegacyMajor = 5" in block
    fn = SCRIPT[SCRIPT.index("function SequenceForVersion"):SCRIPT.index("function JsonText")]
    assert "Nums[0] * 10000 + Nums[1] * 100 + Nums[2]" in fn, "the 5.x line: major*10000 + minor*100 + patch"
    assert "EraFloor + Nums[0] * 1000000 + Nums[1] * 10000 + Nums[2] * 100 + Stage" in fn
    assert "Base := 1" in fn and "Base := 50" in fn and "Stage := 99" in fn and "> 49" in fn and "> 98" in fn
    assert re.search(r"\(Lab = 'rc'\) or \(Lab = 'c'\)", fn)


def test_the_setup_program_and_the_release_agree_on_the_sequence():
    assert release.BUILD_SEQUENCE == release.sequence_for_version(release.RELEASE_TAG)
    assert "-BuildSequence {#BuildSequence}" in SCRIPT
    assert "build_sequence" in INSTALL, "the manifest carries the sequence a later setup reads"
    assert "'build_sequence'" in SCRIPT


def test_install_ps1_protects_the_data_before_it_changes_anything():
    assert INSTALL.index("Invoke-UpgradePreflight") < INSTALL.index("'app.copy'")
    assert INSTALL.index("Test-DataHomeIntact") > INSTALL.index("'app.copy'")
    assert ". (Join-Path $Here 'lib\\Upgrade.ps1')" in INSTALL
    assert "if (-not $InnoManaged) {\n$null = Invoke-Step -Id 'shortcuts.icons'" in INSTALL.replace("\r\n", "\n")


# ── uninstall ────────────────────────────────────────────────────────────────

def test_uninstall_keeps_the_data_unless_told_otherwise():
    fn = SCRIPT[SCRIPT.index("function InitializeUninstall"):SCRIPT.index("procedure CurUninstallStepChanged")]
    assert "RemoveEverything := False" in fn
    assert "MB_YESNO or MB_DEFBUTTON1" in fn, "the first question defaults to Yes = keep"
    assert "MB_YESNO or MB_DEFBUTTON2" in fn, "the destructive confirmation defaults to No"
    assert "UninstallSilent" in fn and "REMOVEDATA|0" in fn, "a silent uninstall keeps the data unless /REMOVEDATA=1"


def test_uninstall_runs_the_scripts_that_keep_the_vault_and_its_key_together():
    fn = SCRIPT[SCRIPT.index("procedure CurUninstallStepChanged"):]
    assert "uninstall.ps1" in fn and "-Unattended -InnoManaged" in fn
    assert fn.index("RemoveEverything") < fn.index("Exec(")
    assert "-RemoveEverything" in fn
    assert re.search(r"if \(-not \$InnoManaged\) \{\r?\n\$null = Invoke-Step -Id 'uninstall\.root'", UNINSTALL), "Inno removes the folder itself"
    assert "Type: filesandordirs; Name: \"{app}\"" in SCRIPT


# ── the first-run file the app reads ─────────────────────────────────────────

def test_the_json_setup_writes_is_the_json_the_app_reads(tmp_path, monkeypatch):
    for key in ('"consent_download"', '"cloud"', '"seats"', '"fast_responder"', '"deep_thinker"', '"model_id"',
                '"packing"', '"with_companions"', '"pick"', '"build_sequence"'):
        assert key in SCRIPT, key
    # the layout WriteFirstRun produces, with a pick as the probe writes it
    pick = json.dumps({"tier": "T1", "packing": "PTQ1_0", "n_gpu_layers": 0, "context": 8192, "kv": "f16",
                       "slots": 1, "batch": "-b 2048 -ub 512", "mmproj": False, "profile": "cpu"}, separators=(",", ":"))
    text = ('{\r\n  "schema": 1,\r\n  "written_by": "installer",\r\n  "release": "1.0.0b1",\r\n'
            '  "build_sequence": 101000001,\r\n  "cloud": false,\r\n  "consent_download": true,\r\n'
            '  "total_bytes": 7000000000,\r\n  "hardware": { "tier": "T1", "ram_mib": 16384, "vram_mib": 0, "disk_free_mib": 90000 },\r\n'
            '  "seats": {\r\n'
            '    "fast_responder": { "model_id": "qwen3-4b-instruct-2507", "packing": "Q4_K_M" },\r\n'
            '    "deep_thinker": { "model_id": "bonsai2:27b", "packing": "PTQ1_0", "with_companions": false, "pick": ' + pick + ' }\r\n'
            '  }\r\n}\r\n')
    path = tmp_path / "first-run.json"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("FRIDAY_FIRST_RUN_FILE", str(path))
    req = frm.load_request()
    assert req["cloud"] is False and req["consent"] is True and req["problems"] == []
    assert req["seats"]["deep_thinker"]["with_companions"] is False
    assert req["seats"]["deep_thinker"]["serve"]["serve_num_ctx"] == 8192
    assert req["seats"]["fast_responder"]["model_id"] == "qwen3-4b-instruct-2507"


def test_the_cloud_file_has_no_seats_and_downloads_nothing(tmp_path, monkeypatch):
    path = tmp_path / "first-run.json"
    path.write_text('{ "schema": 1, "cloud": true, "consent_download": false,\r\n  "seats": {} }\r\n', encoding="utf-8")
    monkeypatch.setenv("FRIDAY_FIRST_RUN_FILE", str(path))
    req = frm.load_request()
    assert req["cloud"] is True and req["seats"] == {}


# ── the build and the workflow ───────────────────────────────────────────────

def test_the_build_stages_the_payload_and_compiles_it_with_inno_setup():
    assert "ISCC.exe" in BUILD and "Inno Setup 6" in BUILD and "-IsccPath" in BUILD
    assert "installer\\AgentFriday.iss" in BUILD
    assert "AgentFriday-Setup-$releaseTag.exe" in BUILD and ".sha256" in BUILD
    assert "CreateFromDirectory" not in BUILD, "no zip any more"
    # the payload checks are kept, and run before the compile
    for kept in ("Verifying the payload is the committed tree", "Checking the vendored libraries", "$leakPatterns", "build.wheelhouse"):
        assert kept in BUILD
        assert BUILD.index(kept) < BUILD.index("Compiling the installer with Inno Setup"), kept
    assert "Get-BuildSequence" in BUILD and "build.sequence" in BUILD, "the build refuses a version and sequence that disagree"


def test_the_old_zip_path_is_gone():
    assert not (WINDOWS_DIR / "Install Agent Friday.cmd").exists()
    assert not (REPO / "AgentFriday.spec").exists()
    assert not (REPO / "scripts" / "install.bat").exists() and not (REPO / "scripts" / "install.ps1").exists()
    assert (REPO / "scripts" / "install.sh").exists(), "Linux and macOS keep theirs"


def test_the_tag_check_accepts_the_beta_tag_against_the_pep440_version():
    assert "(a|b|rc)" in WORKFLOW and "-$word.$($m.Groups[3].Value)" in WORKFLOW
    # the same normalisation, run: 1.0.0b1 -> v1.0.0-beta.1
    m = re.match(r"^(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?$", "1.0.0b1")
    word = {"a": "alpha", "b": "beta", "rc": "rc"}[m.group(2)]
    assert "v%s-%s.%s" % (m.group(1), word, m.group(3)) == release.RELEASE_TAG


def test_the_workflow_has_the_jobs_the_owner_asked_for():
    for job in ("build:", "fresh-install:", "upgrade:", "release:"):
        assert re.search(r"^  %s" % job, WORKFLOW, flags=re.M), job
    assert 'default: "v5.14.3"' in WORKFLOW and "|| 'v5.14.3'" in WORKFLOW
    assert "AgentFriday-Setup-*.zip" in WORKFLOW, "v5.14.3 ships a zip"
    assert "-UpgradeExe" in WORKFLOW and "-Setup $exe.FullName" in WORKFLOW
    assert "choco install innosetup" in WORKFLOW


def test_the_release_job_publishes_the_latest_release_not_a_draft():
    """Beta 1.0 replaces 5.x: releases/latest (the README's Download link) must
    land on it, and GitHub skips pre-releases for 'latest'."""
    rel = WORKFLOW[WORKFLOW.index("  release:"):]
    assert "--latest" in rel and "--prerelease" not in rel and "--draft" not in rel
    assert "PLACEHOLDER" in rel and "a placeholder is left in the notes" in rel, \
        "the notes get the installer's real SHA-256, and a leftover placeholder stops the release"
    assert 'needs: [fresh-install, upgrade]' in rel
    assert "--title \"Agent Friday Beta 1.0\"" in rel and "--notes-file" in rel
    assert ".exe.sha256" in rel and "AgentFriday-Setup-*.exe" in rel
    assert "Build sequence" in rel, "the notes carry the line the updater orders releases by"


def test_permissions_are_minimal_and_actions_stay_pinned():
    assert WORKFLOW.count("contents: write") == 1, "write access in the release job only"
    top = WORKFLOW[:WORKFLOW.index("jobs:")]
    assert "permissions:\n  contents: read" in top
    for use in re.findall(r"uses: ([^\s]+)", WORKFLOW):
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", use), "pin by commit sha: %s" % use


def test_the_release_notes_carry_the_build_sequence_the_updater_reads():
    notes = (REPO / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    m = re.search(r"(?im)^build sequence:\s*(\d+)\s*$", notes)
    assert m and int(m.group(1)) == release.BUILD_SEQUENCE


# ── PowerShell, run for real ─────────────────────────────────────────────────

@needs_powershell
def test_a_shortcut_is_created_where_asked_and_the_desktop_is_resolved_through_the_shell(tmp_path):
    # A profile that has no Desktop folder yet: the shell lookup used to answer empty here, so the
    # three sources disagreed and a link could land where Windows does not show it.
    home = tmp_path / "home"
    home.mkdir()
    link = tmp_path / "out" / "Agent Friday.lnk"
    target = tmp_path / "Agent Friday.cmd"
    target.write_text("@echo off\r\n", encoding="utf-8")
    got = ps_json(". '%s'\n. '%s'\n$p = New-Shortcut -LinkPath '%s' -TargetPath '%s' -WorkingDirectory '%s'\n"
                  "$d = Get-DesktopDir\n$k = Get-KnownFolderPath -Name 'Desktop'\n"
                  "$n = New-Shortcut -LinkPath (Join-Path $d 'Agent Friday.lnk') -TargetPath '%s' -WorkingDirectory '%s'\n"
                  "$l = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Agent Friday.lnk'\n$landed = Test-Path -LiteralPath $l\n"
                  "if ($landed) { Remove-Item -LiteralPath $l -Force }\n"
                  "[ordered]@{ made = [bool]$p; exists = (Test-Path -LiteralPath '%s'); desktop = $d; known = $k; net = [Environment]::GetFolderPath('Desktop'); landed = $landed } | ConvertTo-Json -Compress\n"
                  % (LIB / "Common.ps1", LIB / "Shortcuts.ps1", link, target, tmp_path, target, tmp_path, link), tmp_path,
                  env={"USERPROFILE": str(home), "HOME": str(home)})
    assert got["made"] and got["exists"]
    assert got["desktop"], "a Desktop folder was found"
    assert got["known"].lower() == got["net"].lower() == got["desktop"].lower(), "the shell, .NET and Get-DesktopDir agree on this machine"
    assert got["landed"], "the shortcut is where GetFolderPath says the Desktop is"


# ── offline install from the bundled wheelhouse ──────────────────────────────

def test_the_build_collects_every_requirement_and_proves_the_wheelhouse_complete():
    assert "'-m', 'pip', 'download'" in BUILD
    for tier in ("core", "recommended", "memory", "judgment"):
        assert "'%s'" % tier in BUILD
    assert "'install', '--dry-run', '--no-index'" in BUILD, "coverage is proved by resolving offline"
    assert BUILD.index("'download'") < BUILD.index("'--dry-run'") < BUILD.index("Compiling the installer with Inno Setup")
    assert "The wheelhouse does not cover" in BUILD


@needs_powershell
def test_pip_runs_offline_when_a_wheelhouse_is_shipped_and_online_only_on_request(tmp_path):
    house = tmp_path / "wheelhouse"
    house.mkdir()
    empty = tmp_path / "empty"
    empty.mkdir()
    (house / "x-1-py3-none-any.whl").write_bytes(b"PK")
    got = ps_json(". '%s'\n. '%s'\n"
                  "$a = Get-PipBaseArgs -WheelhouseDir '%s'\n"
                  "$b = Get-PipBaseArgs -WheelhouseDir '%s'\n"
                  "$c = Get-PipBaseArgs -WheelhouseDir $null\n"
                  "$script:PipAllowNetwork = $true\n"
                  "$d = Get-PipBaseArgs -WheelhouseDir '%s'\n"
                  "[ordered]@{ shipped = ($a -contains '--no-index'); links = ($a -contains '--find-links'); empty = ($b -contains '--no-index');"
                  " none = ($c -contains '--no-index'); allow = ($d -contains '--no-index'); allowlinks = ($d -contains '--find-links') } | ConvertTo-Json -Compress\n"
                  % (LIB / "Common.ps1", LIB / "Deps.ps1", house, empty, house), tmp_path)
    assert got == {"shipped": True, "links": True, "empty": False, "none": False, "allow": False, "allowlinks": True}


def test_a_missing_wheel_is_a_clear_failure_not_a_trip_to_pypi():
    deps = read_text(LIB / "Deps.ps1")
    assert "The bundled wheelhouse has no wheel for" in deps and "-AllowNetwork" in deps
    assert "[switch] $AllowNetwork" in INSTALL and "$script:PipAllowNetwork = [bool]$AllowNetwork" in INSTALL
    assert "if (-not $script:PipAllowNetwork) { $base += '--no-index' }" in deps, "the pyautogui family too"


def test_sherpa_onnx_is_installed_from_the_wheelhouse_at_the_voice_installers_pin():
    from agent_friday.services import voice_artifacts as va
    req = read_text(WINDOWS_DIR / "requirements" / "recommended.txt")
    assert "sherpa-onnx==%s" % va.ARTIFACTS["sherpa-onnx"]["version"] in req


def _pascal_routines(script: str):
    """(name, body) for every procedure/function in the [Code] section."""
    code = script.split("[Code]", 1)[1]
    parts = re.split(r"(?mi)^(?:procedure|function)\s+(\w+)", code)
    return list(zip(parts[1::2], parts[2::2]))


def test_a_silent_setup_never_waits_on_a_box_nobody_can_answer():
    """/SUPPRESSMSGBOXES only silences SuppressibleMsgBox. A plain MsgBox in a silent
    install opens on a desktop nobody watches and setup waits for ever (a CI fresh
    install hung 60 minutes on the model page's box). Every routine that calls a plain
    MsgBox must leave first when the install or uninstall is silent."""
    offenders = []
    for name, body in _pascal_routines(SCRIPT):
        first_box = body.find("MsgBox(")
        while first_box > 0 and body[first_box - 1].isalnum():   # SuppressibleMsgBox is fine
            first_box = body.find("MsgBox(", first_box + 1)
        if first_box < 0:
            continue
        guard = re.search(r"if\s+\(?\s*(?:not\s+)?\(?\s*(WizardSilent|UninstallSilent)", body[:first_box])
        if not guard:
            offenders.append(name)
    assert not offenders, "plain MsgBox reachable in a silent run: %s" % offenders
