"""North star §34.4-2: the first-run preflight reports this computer truly.

The setup program's model page runs packaging/windows/installer/probe-hardware.ps1
(ModelPicker.ps1's Get-HardwareFacts) and shows "This computer: N GB of
memory, <graphics>, N GB free"; the app's own profile (services/hardware_profile)
decides what runs later. On a real Windows machine (the CI Windows runners)
these tests run the probe for real and compare every figure with a source the
probe does not use, and with the app's own reading, so a wrong figure shows up
here rather than as a model that does not fit. Elsewhere they skip.

The tier rules themselves are tested with fixed facts in
tests/unit/test_installer_model_picker.py; this is the reading those rules
are fed.
"""
from __future__ import annotations

import json

import pytest

from tests.unit._installer_ps import INSTALLER_DIR, SHORTLIST, needs_powershell, ps_json, run_ps

pytestmark = needs_powershell


def _probe_facts(tmp_path) -> dict:
    out = tmp_path / "options.txt"
    models = tmp_path / "friday"
    cp = run_ps("& '%s' -OutFile '%s' -ShortlistPath '%s' -ModelsPath '%s'; exit $LASTEXITCODE"
                % (INSTALLER_DIR / "probe-hardware.ps1", out, SHORTLIST, models), tmp_path)
    assert cp.returncode == 0, cp.stdout[-2000:] + cp.stderr[-2000:]
    facts, tier = {}, None
    for line in out.read_text(encoding="utf-8-sig").splitlines():
        parts = line.split("\t") if "\t" in line else line.split("|")
        if parts and parts[0] == "FACT" and len(parts) >= 3:
            facts[parts[1]] = parts[2]
        elif parts and parts[0] == "TIER" and len(parts) >= 2:
            tier = parts[1]
    assert facts, "the probe wrote no facts:\n" + out.read_text(encoding="utf-8-sig")[:2000]
    facts["_tier"] = tier
    return facts


def _independent(tmp_path) -> dict:
    """The same figures from sources the probe does not read. The disk is the
    one the probe was pointed at (tmp_path's drive)."""
    drive = str(tmp_path.drive or "C:").rstrip("\\")
    return ps_json(r"""
$os = Get-CimInstance Win32_OperatingSystem
$disk = Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='" + '__DRIVE__' + "'")
$cpu = @(Get-CimInstance Win32_Processor)
$cards = @(Get-CimInstance Win32_VideoController | Where-Object {
    $_.Name -notmatch '__VIRTUAL__' })
[ordered]@{
    visible_mib = [int64][math]::Floor([int64]$os.TotalVisibleMemorySize / 1024)
    free_mib    = [int64][math]::Floor([int64]$disk.FreeSpace / 1MB)
    cores       = ($cpu | Measure-Object -Property NumberOfCores -Sum).Sum
    threads     = ($cpu | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum
    real_gpus   = $cards.Count
    nvidia_smi  = [bool](Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue)
} | ConvertTo-Json -Compress
""".replace("__DRIVE__", drive).replace("__VIRTUAL__", _virtual_adapter_pattern()), tmp_path)


def _virtual_adapter_pattern() -> str:
    """The probe's own list of display adapters that are not graphics cards
    (ModelPicker.ps1 Get-GpuFacts), so the two never disagree about one."""
    import re
    from tests.unit._installer_ps import LIB
    src = (LIB / "ModelPicker.ps1").read_text(encoding="utf-8-sig")
    m = re.search(r"-notmatch\s+'([^']*Basic Display[^']*)'", src[src.index("function Get-GpuFacts"):])
    assert m, "Get-GpuFacts has no virtual-adapter pattern to share"
    return m.group(1)


def test_the_preflight_reports_this_computers_memory_disk_and_graphics(tmp_path):
    facts = _probe_facts(tmp_path)
    ind = _independent(tmp_path)
    ram = int(facts["ram_mib"])
    # TotalVisibleMemorySize (the OS's count) and TotalPhysicalMemory (the
    # probe's source) describe the same memory through two classes. Within 2%.
    assert ram > 0 and abs(ram - ind["visible_mib"]) <= max(64, ind["visible_mib"] * 0.02), (ram, ind)
    disk = int(facts["disk_free_mib"])
    # Other test workers write to the same disk in between: within 1 GiB.
    assert disk > 0 and abs(disk - ind["free_mib"]) <= 1024, (disk, ind)
    if ind["real_gpus"] == 0 and not ind["nvidia_smi"]:
        assert int(facts.get("vram_mib") or 0) == 0, "a machine with no graphics card reported video memory"
        assert str(facts.get("vram_known", "")).lower() in ("false", "0", ""), facts
    assert facts["_tier"], "the probe chose no tier"


def test_the_preflight_and_the_app_agree_about_this_computer(tmp_path):
    from agent_friday.services import hardware_profile as hp
    facts = _probe_facts(tmp_path)
    ram = hp.detect_ram()
    assert abs(int(facts["ram_mib"]) - int(ram["total_mib"])) <= max(64, ram["total_mib"] * 0.02), (facts, ram)
    cpu = hp.detect_cpu()
    ind = _independent(tmp_path)
    assert int(cpu["threads"]) == int(ind["threads"]), (cpu, ind)


def test_the_setup_card_shows_the_same_memory(tmp_path):
    """The in-app setup card ("What this computer can run") reads
    /api/health/full's hardware block, from the Ollama manager's own reading."""
    from agent_friday.routing.ollama_manager import OllamaManager
    facts = _probe_facts(tmp_path)
    hw = OllamaManager("http://127.0.0.1:9").detect_hardware()
    shown_mib = float(hw["ram_gb"]) * 1024
    assert abs(shown_mib - int(facts["ram_mib"])) <= max(128, int(facts["ram_mib"]) * 0.02), (hw, facts)
