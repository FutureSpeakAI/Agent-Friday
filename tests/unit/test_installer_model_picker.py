"""The installer's model page offers only what this computer can run.

`installer/probe-hardware.ps1` (with `lib/ModelPicker.ps1`) is what the wizard
runs: it reads the machine, applies the pick rules of
`src/agent_friday/resources/bonsai2-tiers.json`, and writes the models that fit
for two seats. These tests run it for real on fabricated machines and hold its
thresholds to the JSON they restate.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.unit._installer_ps import (INSTALLER_DIR, LIB, SHORTLIST, TIERS, needs_powershell, read_text, run_ps)

PICKER = read_text(LIB / "ModelPicker.ps1")

BASE = dict(os_family="windows", ram_mib=16384, cpu_name="test", cpu_cores=8, cpu_threads=16, avx2=True,
            bandwidth_gb_s=51.2, gpu_name="", gpu_vendor="", vram_mib=0, vram_known=False, gpu_note="",
            idle_used_mib=0, disk_free_mib=200000)


def probe(tmp_path: Path, **facts) -> dict:
    """Run the wizard's own probe over fixed facts and parse its output file the
    way the wizard does (pipe-separated records)."""
    facts = dict(BASE, **facts)
    facts_file = tmp_path / "facts.json"
    facts_file.write_text(json.dumps(facts), encoding="utf-8")
    out_file = tmp_path / "options.txt"
    out = run_ps("& '%s' -OutFile '%s' -ShortlistPath '%s' -FactsJson '%s'\nexit $LASTEXITCODE\n"
                 % (INSTALLER_DIR / "probe-hardware.ps1", out_file, SHORTLIST, facts_file), tmp_path)
    assert out.returncode == 0 and out_file.exists(), out.stdout + out.stderr
    result = {"tier": "", "notes": [], "deep": [], "fast": [], "unfit": [], "facts": {}}
    for line in out_file.read_text(encoding="utf-8").splitlines():
        kind, _, rest = line.partition("|")
        if kind == "TIER":
            result["tier"] = rest
        elif kind == "NOTE":
            result["notes"].append(rest)
        elif kind == "UNFIT":
            f = rest.split("|")
            assert len(f) == 5, "the wizard reads exactly five fields: %r" % line
            result["unfit"].append(dict(zip(("seat", "id", "label", "size", "reason"), f)))
        elif kind == "FACT":
            name, _, value = rest.partition("|")
            result["facts"][name] = value
        elif kind == "OPT":
            f = rest.split("|", 10)
            assert len(f) == 11, "the wizard reads exactly eleven fields: %r" % line
            seat, mid, packing, label, nbytes, size_text, rec, older, comp, note, pick = f
            result["deep" if seat == "deep_thinker" else "fast"].append({
                "id": mid, "packing": packing, "label": label, "bytes": int(nbytes), "size": size_text,
                "recommended": rec == "1", "older": older == "1", "companions": comp == "1", "note": note,
                "pick": json.loads(pick) if pick else None})
    return result


def ids(options) -> list:
    return [o["id"] for o in options]


# ── the thresholds are the JSON's ────────────────────────────────────────────

def test_the_pick_thresholds_restate_the_tiers_file():
    doc = json.loads(TIERS.read_text(encoding="utf-8"))
    rules = {r["id"]: r["when"] for r in doc["pick"]["rules"]}
    for rule, number in (("T8", 28000), ("T7", 20000), ("T6", 13000), ("T5", 9000), ("T4", 6300), ("T4a", 3000)):
        assert str(number) in rules[rule], (rule, rules[rule])
        assert re.search(r"\b%d\b" % number, PICKER), "ModelPicker.ps1 must carry %s's threshold %d" % (rule, number)
    assert "15500" in rules["T1"] and "15500" in PICKER
    assert "30000" in rules["T2"] and "30000" in PICKER and re.search(r">= 70", rules["T2"])
    r = doc["reserves"]
    assert r["os_reserve_mib"]["windows"] == 6144 and "OsReserveMib       = 6144" in PICKER
    assert r["display_reserve_mib"]["windows"] == 2560 and "DisplayReserveMib  = 2560" in PICKER
    assert r["friday_footprint_mib"]["value"] == 1500 and "FridayFootprintMib = 1500" in PICKER
    assert r["disk_floor_mib"]["value"] == 10240 and "DiskFloorMib       = 10240" in PICKER
    assert r["compute_buffer_mib"]["ub_512"] == 400 and "ComputeBufferMib   = 400" in PICKER
    assert doc["pick"]["derived"]["weights_mib"].startswith("5671") and "WeightsPtq10Mib    = 5671" in PICKER


def test_every_tier_row_of_the_json_has_the_same_outputs_in_the_picker():
    doc = json.loads(TIERS.read_text(encoding="utf-8"))
    for rule in doc["pick"]["rules"]:
        if rule["id"] in ("T3a", "T3b", "T0"):
            continue
        out = rule["out"]
        row = re.search(r"Row '%s' [^\n]*" % rule["id"], PICKER)
        assert row, rule["id"]
        text = row.group(0)
        assert str(out["context"]).replace("by estimate_method.n_gpu_layers", "") in text or rule["id"] == "T4a", (rule["id"], text)
        assert ("'%s'" % out["kv"]) in text, (rule["id"], text)
        assert out["batch"] in text, (rule["id"], text)


# ── what fits, on the machines the tiers file describes ──────────────────────

@needs_powershell
@pytest.mark.parametrize("name,facts,tier", [
    ("24 GB card", dict(ram_mib=65536, gpu_name="NVIDIA GeForce RTX 4090", gpu_vendor="nvidia", vram_mib=24564, vram_known=True, idle_used_mib=800), "T7"),
    ("12 GB card", dict(ram_mib=32768, gpu_name="NVIDIA GeForce RTX 3060", gpu_vendor="nvidia", vram_mib=12288, vram_known=True, idle_used_mib=800), "T5"),
    ("8 GB card on Windows", dict(ram_mib=16384, gpu_name="NVIDIA GeForce RTX 3070", gpu_vendor="nvidia", vram_mib=8192, vram_known=True, idle_used_mib=700), "T4a"),
    ("16 GB, no card", dict(ram_mib=16384), "T1"),
    ("32 GB fast memory", dict(ram_mib=32768, bandwidth_gb_s=89.6), "T2"),
    ("32 GB slow memory", dict(ram_mib=32768, bandwidth_gb_s=51.2), "T1"),
    ("8 GB of memory", dict(ram_mib=8192), "T0"),
    ("no AVX2", dict(avx2=False), "T0"),
])
def test_the_tier_is_the_one_the_tiers_file_gives(tmp_path, name, facts, tier):
    assert probe(tmp_path, **facts)["tier"] == tier, name


@needs_powershell
def test_an_8_gb_and_a_6_gb_card_offload_the_layers_the_tiers_file_computes(tmp_path):
    """tiers.json T4a note: Windows 8 GB -> 56 of 64 layers; Windows 6 GB -> 33."""
    eight = probe(tmp_path, ram_mib=16384, gpu_name="NVIDIA GeForce RTX 3070", gpu_vendor="nvidia", vram_mib=8192, vram_known=True, idle_used_mib=700)
    six = probe(tmp_path, ram_mib=16384, gpu_name="NVIDIA GeForce GTX 1660", gpu_vendor="nvidia", vram_mib=6144, vram_known=True, idle_used_mib=500)
    assert eight["deep"][0]["pick"]["n_gpu_layers"] == 56
    assert six["deep"][0]["pick"]["n_gpu_layers"] == 33


@needs_powershell
def test_unknown_graphics_memory_is_treated_conservatively(tmp_path):
    """A card whose memory cannot be read (Windows' 32-bit field caps at 4 GB)
    counts as none, so the pick falls to the processor tier."""
    out = probe(tmp_path, ram_mib=32768, gpu_name="AMD Radeon RX 7900", gpu_vendor="amd", vram_mib=24000, vram_known=False)
    assert out["tier"] == "T1" or out["tier"] == "T2"
    assert out["deep"][0]["pick"]["profile"] == "cpu"


@needs_powershell
def test_the_ampere_card_gets_the_larger_packing_and_the_ada_card_does_not(tmp_path):
    ampere = probe(tmp_path, ram_mib=65536, gpu_name="NVIDIA GeForce RTX 3090", gpu_vendor="nvidia", vram_mib=24576, vram_known=True, idle_used_mib=800)
    ada = probe(tmp_path, ram_mib=65536, gpu_name="NVIDIA GeForce RTX 4090", gpu_vendor="nvidia", vram_mib=24564, vram_known=True, idle_used_mib=800)
    assert ampere["deep"][0]["packing"] == "PQ2_0"
    assert ada["deep"][0]["packing"] == "PTQ1_0", "the 4090 measured faster on PTQ1_0 (tiers.json)"


# ── the two seats, and the rules the page keeps ──────────────────────────────

@needs_powershell
def test_the_deep_seat_is_bonsai_and_the_fast_seat_is_the_voice_front(tmp_path):
    out = probe(tmp_path)
    shortlist = {m["id"]: m for m in json.loads(SHORTLIST.read_text(encoding="utf-8"))["models"]}
    assert out["deep"] and out["fast"]
    for o in out["deep"]:
        assert o["id"] in shortlist and shortlist[o["id"]]["publisher"] == "PrismML", o["id"]
    for o in out["deep"] + out["fast"]:
        assert o["bytes"] > 0 and o["size"], "every option shows its size on disk"
    assert "bonsai2:27b" in ids(out["deep"])
    # The fast seat offers what the voice stack actually serves, never a Bonsai model.
    from agent_friday.services import voice_front as vf
    assert set(ids(out["fast"])) <= set(vf.FRONT_MODELS)
    assert not any(i.startswith(("ternary-bonsai", "bonsai")) for i in ids(out["fast"]))


@needs_powershell
def test_the_fast_options_and_their_sizes_are_the_voice_artifacts(tmp_path):
    """The size shown is the front plus the speech ear and its runtime."""
    from agent_friday.services import first_run_models as frm
    from agent_friday.services import voice_artifacts as va
    out = probe(tmp_path)
    for o in out["fast"]:
        assert o["bytes"] == sum(va.size_bytes(a) for a in frm.front_artifacts(o["id"])), o["id"]
        assert "speech ear" in o["note"]


@needs_powershell
def test_the_recommended_front_follows_the_machine(tmp_path):
    """The 1.7B sits beside the deep thinker on smaller machines; the 4B where
    there is room to spare (voice_front's solo / co_resident roles)."""
    small = probe(tmp_path, ram_mib=16384)
    big = probe(tmp_path, ram_mib=65536)
    rec = lambda o: [x["id"] for x in o["fast"] if x["recommended"]]
    assert rec(small) == ["qwen3-1.7b"]
    assert rec(big) == ["qwen3-4b-instruct-2507"]
    assert set(ids(small["fast"])) == {"qwen3-1.7b", "qwen3-4b-instruct-2507"}, "both fit 16 GB; the 4B is still offered"


@needs_powershell
def test_the_voice_front_options_file_is_the_voice_artifacts(tmp_path):
    from agent_friday.services import first_run_models as frm
    from agent_friday.services import voice_artifacts as va
    from agent_friday.services import voice_front as vf
    doc = json.loads((SHORTLIST.parent / "voice_front_options.json").read_text(encoding="utf-8"))
    assert {f["id"] for f in doc["fronts"]} == set(vf.FRONT_MODELS)
    for f in doc["fronts"]:
        assert f["artifact"] == frm.FRONT_ARTIFACT[f["id"]]
        assert f["front_bytes"] == va.size_bytes(f["artifact"])
        assert f["role"] == vf.FRONT_MODELS[f["id"]]["role"]
    assert [c["artifact"] for c in doc["companions"]] == list(frm.FRONT_COMPANIONS)
    for c in doc["companions"]:
        assert c["bytes"] == va.size_bytes(c["artifact"])


@needs_powershell
def test_exactly_one_option_per_seat_is_recommended(tmp_path):
    for facts in (dict(), dict(ram_mib=32768, gpu_name="NVIDIA GeForce RTX 3060", gpu_vendor="nvidia", vram_mib=12288, vram_known=True)):
        out = probe(tmp_path, **facts)
        assert sum(o["recommended"] for o in out["deep"]) == 1
        assert sum(o["recommended"] for o in out["fast"]) == 1
        assert [o["id"] for o in out["deep"] if o["recommended"]] == ["bonsai2:27b"]


@needs_powershell
def test_a_model_that_does_not_fit_the_memory_is_not_listed(tmp_path):
    """12 GB of memory: 12288 - 6144 - 1500 = 4644 MiB of budget. The 8B needs
    2081 + 1475 = 3556 and fits; with 10 GB the 8B drops out."""
    out = probe(tmp_path, ram_mib=12288, avx2=True)
    assert out["tier"] == "T0"
    assert "bonsai2:27b" not in ids(out["deep"]), "below the floor the 27B is never offered"
    tight = probe(tmp_path, ram_mib=10240, avx2=True)
    assert "ternary-bonsai:8b" not in ids(tight["deep"]) and "ternary-bonsai:4b" in ids(tight["deep"])


@needs_powershell
def test_below_the_floor_says_so_and_offers_what_does_fit(tmp_path):
    out = probe(tmp_path, ram_mib=8192)
    assert out["tier"] == "T0" and out["deep"] == [] and out["fast"] == []
    assert any("below" in n.lower() for n in out["notes"]), out["notes"]


@needs_powershell
def test_the_disk_floor_is_kept(tmp_path):
    """A download is offered only if 10 GB stay free afterwards (rule R8)."""
    roomy = probe(tmp_path, disk_free_mib=200000)
    tight = probe(tmp_path, disk_free_mib=12000)
    assert "bonsai2:27b" in ids(roomy["deep"])
    assert "bonsai2:27b" not in ids(tight["deep"])
    assert any("drive" in n.lower() and "free" in n.lower() for n in tight["notes"]), tight["notes"]
    for o in tight["deep"] + tight["fast"]:
        assert 12000 - 8192 - o["bytes"] / 1048576 - 650 >= 10240, o


# ── what the page says when something does not fit ───────────────────────────

# The owner's own machine: 32 GB of memory and a 12 GB RTX 4070.
RTX_4070 = dict(ram_mib=32768, gpu_name="NVIDIA GeForce RTX 4070", gpu_vendor="nvidia", vram_mib=12282,
                vram_known=True, idle_used_mib=900, bandwidth_gb_s=89.6)
EVERY_OPTION = {"bonsai2:27b", "ternary-bonsai:4b", "ternary-bonsai:8b", "qwen3-4b-instruct-2507", "qwen3-1.7b"}


@needs_powershell
def test_a_roomy_rtx_4070_lists_both_seats_recommends_and_says_nothing_is_missing(tmp_path):
    out = probe(tmp_path, disk_free_mib=200000, **RTX_4070)
    assert {"bonsai2:27b", "ternary-bonsai:4b", "ternary-bonsai:8b"} <= set(ids(out["deep"]))
    assert set(ids(out["fast"])) == {"qwen3-4b-instruct-2507", "qwen3-1.7b"}
    assert [o["id"] for o in out["deep"] if o["recommended"]] == ["bonsai2:27b"]
    assert [o["id"] for o in out["fast"] if o["recommended"]] == ["qwen3-4b-instruct-2507"]
    assert out["unfit"] == [] and out["notes"] == []


@needs_powershell
def test_with_12_gb_free_every_option_is_listed_greyed_with_its_reason_and_cloud_is_offered_with_the_numbers(tmp_path):
    out = probe(tmp_path, disk_free_mib=12493, **RTX_4070)       # 12.2 GB
    assert out["deep"] == [] and out["fast"] == []
    assert {u["id"] for u in out["unfit"]} == EVERY_OPTION, "every option is listed, none silently dropped"
    for seat in ("deep_thinker", "fast_responder"):
        assert [u for u in out["unfit"] if u["seat"] == seat], "no seat heading is empty without a reason: " + seat
    for u in out["unfit"]:
        assert u["size"] and re.search(r"plus 10 GB left free on this drive", u["reason"]), u
    front = next(u for u in out["unfit"] if u["id"] == "qwen3-4b-instruct-2507")
    assert front["size"] == "2.8 GB" and front["reason"].startswith("needs 2.8 GB plus 10 GB left free on this drive"), front
    assert len(out["notes"]) == 1, "said once"
    note = out["notes"][0]
    for part in ("12.2 GB free", "Agent Friday itself needs about 8 GB", "their size plus 10 GB left free afterwards",
                 "so Windows keeps working",
                 "You can use a cloud model now and add local models later in Settings -> Models after you free some space."):
        assert part in note, (part, note)
    assert "27B" not in note, "the message is about every local model, not only the 27B"


@needs_powershell
def test_with_24_gb_free_the_27b_is_greyed_and_the_rest_are_selectable_without_forcing_cloud(tmp_path):
    out = probe(tmp_path, disk_free_mib=24576, **RTX_4070)       # 24 GB: Agent Friday 8 + 27B 6.1 + 10 floor does not fit
    assert [u["id"] for u in out["unfit"]] == ["bonsai2:27b"]
    assert out["unfit"][0]["seat"] == "deep_thinker" and "plus 10 GB left free on this drive" in out["unfit"][0]["reason"]
    assert set(ids(out["deep"])) == {"ternary-bonsai:4b", "ternary-bonsai:8b"}
    assert set(ids(out["fast"])) == {"qwen3-4b-instruct-2507", "qwen3-1.7b"}
    assert out["notes"] == [], "something fits in both seats, so no cloud message and the choice is the person's"


@needs_powershell
def test_a_machine_too_small_for_a_model_says_how_much_memory_it_needs(tmp_path):
    out = probe(tmp_path, ram_mib=8192)
    by_id = {u["id"]: u for u in out["unfit"]}
    assert set(by_id) == EVERY_OPTION
    assert by_id["bonsai2:27b"]["reason"] == "needs 16 GB of memory"
    assert re.fullmatch(r"needs \d+ GB of memory", by_id["qwen3-4b-instruct-2507"]["reason"]), by_id["qwen3-4b-instruct-2507"]
    assert len(out["notes"]) == 1 and "below what local models need" in out["notes"][0]


def test_the_disk_estimate_for_agent_friday_is_the_one_install_ps1_checks():
    install = read_text(LIB.parent / "install.ps1")
    assert re.search(r"\$neededGb = 8\b", install)
    assert re.search(r"FridayDiskGb\s*=\s*8\b", PICKER)


@needs_powershell
def test_the_27b_carries_the_serving_numbers_the_app_will_use(tmp_path):
    out = probe(tmp_path)
    big = next(o for o in out["deep"] if o["id"] == "bonsai2:27b")
    assert big["pick"]["tier"] == "T1" and big["pick"]["context"] == 8192 and big["pick"]["n_gpu_layers"] == 0
    # the same dictionary the app turns into the model's serve_num_ctx / serve_args
    from agent_friday.services import first_run_models as frm
    serve = frm.serve_for_pick(big["pick"])
    assert serve and serve["serve_num_ctx"] == 8192 and serve["serve_args"][:2] == ["-ngl", "0"]


@needs_powershell
def test_the_companion_projector_is_fetched_only_when_the_tier_uses_it(tmp_path):
    cpu = probe(tmp_path)
    gpu = probe(tmp_path, ram_mib=65536, gpu_name="NVIDIA GeForce RTX 4090", gpu_vendor="nvidia", vram_mib=24564, vram_known=True, idle_used_mib=800)
    cpu27 = next(o for o in cpu["deep"] if o["id"] == "bonsai2:27b")
    gpu27 = next(o for o in gpu["deep"] if o["id"] == "bonsai2:27b")
    assert cpu27["companions"] is False and gpu27["companions"] is True
    assert gpu27["bytes"] > cpu27["bytes"], "the size shown includes what will actually download"


# ── the page itself, as the wizard script states it ──────────────────────────

ISS = read_text(INSTALLER_DIR / "AgentFriday.iss")


def test_the_page_carries_the_words_the_owner_asked_for():
    for text in ("Fast responder (voice and quick replies)", "Deep thinker",
                 "Use a cloud model instead (set up a key after install)",
                 "Download these models when Agent Friday first starts", "(Recommended)"):
        assert text in ISS, text


def test_an_option_that_does_not_fit_is_listed_greyed_with_its_reason_on_its_own_line():
    block = ISS[ISS.index("procedure AddSeat"):ISS.index("procedure BuildModelChoices")]
    # caption, reason as the sub-line, level 1, unticked, disabled
    assert "ModelList.AddRadioButton(Unfit[I].Caption + ' - ' + Unfit[I].SizeText, Unfit[I].Reason, 1, False, False, nil)" in block
    assert "Unfit[I].Seat = Seat" in block
    # a seat with nothing at all still says why
    assert "No local model could be checked on this computer." in block
    # a greyed option is never one the person can choose
    assert "DeepItem" in block and "FastItem" in block


def test_cloud_is_forced_only_when_a_seat_has_nothing_to_choose():
    block = ISS[ISS.index("procedure BuildModelChoices"):ISS.index("procedure CurPageChanged")]
    assert "(GetArrayLength(DeepItem) = 0) or (GetArrayLength(FastItem) = 0)" in block
    assert block.count("CloudCheck.Enabled := False") == 1 and block.count("ForcedCloud := True") == 1
    assert "' -> '" in ISS and "#$2192" in ISS, "the note's arrow is drawn in the wizard, which reads the file as ANSI"


def test_nothing_is_preselected():
    """The recommendation is a label. The only box the script ever ticks itself
    is the cloud box, and only when nothing local fits."""
    ticks = re.findall(r"^\s*(\w+)\.Checked := True;", ISS, flags=re.M)
    assert ticks == ["CloudCheck"], ticks
    # every model option is added unticked, as a radio in its seat's group
    assert "ModelList.AddRadioButton(RadioCaption(Opts[I]), '', 1, False, True, nil)" in ISS
    assert "ModelList.AddGroup(Heading, '', 0, nil)" in ISS


def test_next_is_blocked_until_both_seats_and_the_consent_are_chosen():
    block = ISS[ISS.index("function NextButtonClick"):ISS.index("procedure InitializeWizard")]
    assert "(D < 0) or (F < 0)" in block and "ConsentCheck.Checked" in block and "Result := False" in block
    assert "DiskFloorMib" in block


def test_the_silent_default_downloads_nothing():
    # The branch that records a silent install's choices, not the model page's early exit.
    start = ISS.index("// Command line only.")
    block = ISS[start:ISS.index("end", ISS.index("if ChosenCloud then", start))]
    assert "ModelsCloud|0" in block and "ConsentDownload|0" in block
    assert "(not ChosenConsent)" in block, "models without consent fall back to cloud"


@needs_powershell
def test_memory_held_by_an_earlier_friday_is_not_counted_against_the_card(tmp_path):
    """Setup stops Friday before it installs, so Friday's own seats in video
    memory must not make a good card look full (it read as the CPU tier on a
    12 GB card that was holding the 27B)."""
    import os
    import shutil
    import subprocess

    local = tmp_path / "local"
    (local / "AgentFriday" / "python").mkdir(parents=True)
    fake = local / "AgentFriday" / "python" / "pythonw.exe"
    shutil.copy(Path(os.environ["SystemRoot"]) / "System32" / "ping.exe", fake)
    script = (". '%s'\n$r = Test-FridayIsRunning\n[string]$r\n" % (LIB / "ModelPicker.ps1"))
    env = {"LOCALAPPDATA": str(local), "USERPROFILE": str(tmp_path / "home")}
    assert run_ps(script, tmp_path, env=env).stdout.strip().splitlines()[-1] == "False"
    proc = subprocess.Popen([str(fake), "-n", "60", "127.0.0.1"], stdout=subprocess.DEVNULL)
    try:
        import time
        time.sleep(0.8)
        assert run_ps(script, tmp_path, env=env).stdout.strip().splitlines()[-1] == "True"
    finally:
        proc.kill()
    assert "Test-FridayIsRunning" in PICKER and "idle_used_mib = 0" in PICKER
