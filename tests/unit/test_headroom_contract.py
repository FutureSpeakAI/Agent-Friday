"""headroom_contract — the reconciled display reserve.

WHY THIS EXISTS. `docs/design/implemented/headroom.md` §2.2 found six reserve constants
for five concepts, and the one that actually gates a lease --
`Arbiter.grant()`'s R-DISPLAY-RESERVE check -- resolved to 256 MiB on a
single-monitor Windows box (`hardware_profile.display_reserve_mib()`,
unclamped) while the planner assumed at least 2,560
(`hardware_profile.MIN_DISPLAY_RESERVE_MIB["windows"]`). The 2026-08-17
monitor loss happened at 322 MiB free; the gate as it existed before this
file would still pass at that level.

These tests pin: `resolve_display_reserve()` produces the reconciled figure
(§4.2, the piece of the Headroom Contract that does NOT need D1); the gate
in `Arbiter.grant()` now enforces it, not the raw unclamped formula; and
HR3's sweep -- no module outside `headroom_contract.py` and
`hardware_profile.py` may define its own display-reserve constant, with the
two documented, D1-independent exceptions this phase deliberately left in
place (`model_plan.DISPLAY_RESERVE_GIB`, an installer-time pick that already
equals the reconciled figure; `residency_policy.VRAM_RESERVE_MIB`, a
different concept -- planner slack ON TOP of the baseline, not the reserve
itself).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from agent_friday.services import headroom_contract as hc
from agent_friday.services import hardware_profile as hwp
from agent_friday.services import residency_arbiter as ra
from tests import residency_fixtures as fx

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _reset_cache():
    """The module-level cache is process-global and TTL'd; tests must not see
    each other's stale readings, so every test starts cold."""
    hc._CACHE.update({"ts": 0.0, "os_family": None, "result": None})
    yield
    hc._CACHE.update({"ts": 0.0, "os_family": None, "result": None})


# ── resolve_display_reserve() itself ─────────────────────────────────────────

def test_single_monitor_windows_clamps_up_to_the_measured_floor(monkeypatch):
    """The exact bug: display_reserve_mib() alone gives 256 on one monitor."""
    monkeypatch.setattr(hwp, "display_reserve_mib", lambda *a, **k: 256)
    result = hc.resolve_display_reserve({"os": {"family": "windows"}})
    assert result["mib"] == 2560
    assert result["basis"] == "floor_clamp"
    assert result["sources"]["display_reserve_mib_formula"] == 256
    assert result["sources"]["min_display_reserve_mib"] == 2560


def test_a_multi_monitor_reading_above_the_floor_is_used_as_is(monkeypatch):
    """Two monitors plus an indirect adapter can honestly exceed 2,560; the
    floor is a floor, not a ceiling -- the formula wins when it is bigger."""
    monkeypatch.setattr(hwp, "display_reserve_mib", lambda *a, **k: 3072)
    result = hc.resolve_display_reserve({"os": {"family": "windows"}})
    assert result["mib"] == 3072
    assert result["basis"] == "formula"


@pytest.mark.parametrize("family,floor", [("windows", 2560),
                                          ("darwin", 1024),
                                          ("linux", 512)])
def test_the_floor_is_the_os_familys_own_min_display_reserve(
        monkeypatch, family, floor):
    monkeypatch.setattr(hwp, "display_reserve_mib", lambda *a, **k: 100)
    result = hc.resolve_display_reserve({"os": {"family": family}})
    assert result["mib"] == floor
    assert hwp.MIN_DISPLAY_RESERVE_MIB[family] == floor  # not a new number


def test_an_unknown_os_family_falls_back_to_the_512_default(monkeypatch):
    monkeypatch.setattr(hwp, "display_reserve_mib", lambda *a, **k: 100)
    result = hc.resolve_display_reserve({"os": {"family": "plan9"}})
    assert result["mib"] == 512


def test_the_probe_is_cached_briefly_not_reprobed_every_call(monkeypatch):
    calls = {"n": 0}

    def _counting(*a, **k):
        calls["n"] += 1
        return 256
    monkeypatch.setattr(hwp, "display_reserve_mib", _counting)
    profile = {"os": {"family": "windows"}}
    hc.resolve_display_reserve(profile)
    hc.resolve_display_reserve(profile)
    hc.resolve_display_reserve(profile)
    assert calls["n"] == 1, (
        "detect_displays() spawns PowerShell with no cache of its own; "
        "resolve_display_reserve() must not reintroduce that cost on a "
        "5s monitor cadence or a scheduler tick")


def test_a_different_os_family_is_not_served_a_stale_cached_reading(
        monkeypatch):
    monkeypatch.setattr(hwp, "display_reserve_mib", lambda *a, **k: 100)
    win = hc.resolve_display_reserve({"os": {"family": "windows"}})
    lin = hc.resolve_display_reserve({"os": {"family": "linux"}})
    assert win["mib"] == 2560
    assert lin["mib"] == 512


# ── the actual gate: Arbiter.grant()'s R-DISPLAY-RESERVE check ───────────────

class _FakeOllama:
    name = "ollama"

    def __init__(self):
        self._res = {}

    def resident(self):
        return dict(self._res)

    def load(self, model_id, *a, **k):
        self._res[model_id] = 1000

    def evict(self, model_id):
        self._res.pop(model_id, None)

    def evict_all(self):
        self._res.clear()


class _FakeLlama:
    name = "llama-server"

    def __init__(self):
        self.procs = {}

    def resident(self):
        return {m: 0 for m in self.procs}

    def load(self, model_id, *a, **k):
        self.procs[model_id] = (object(), 0)
        return 1.0

    def evict(self, model_id):
        self.procs.pop(model_id, None)

    def evict_all(self):
        self.procs.clear()


def _arb():
    a = ra.Arbiter(profile=fx.P1, entries=fx.catalog(fx.P1),
                   ollama=_FakeOllama(), llama=_FakeLlama(),
                   gguf_paths={"gemma4:12b": "/x/12b.gguf",
                               "gemma4:e2b": "/x/e2b.gguf"})
    a.compute_plan()
    a.boot(measure_baseline=False)
    return a


def test_the_gate_now_requires_2560_not_256_on_single_monitor_windows(
        monkeypatch):
    """The exact before/after this phase closes.

    Before: `vram_headroom()` reserved 256 MiB (the raw, unclamped
    `display_reserve_mib()` formula on one monitor), so 2,082 MiB free would
    have passed. After: `Arbiter.grant()` reconciles through
    `resolve_display_reserve()`, which clamps to `MIN_DISPLAY_RESERVE_MIB
    ["windows"]` = 2,560, and the same 2,082 MiB free is refused.
    """
    monkeypatch.setattr(hwp, "display_reserve_mib", lambda *a, **k: 256)
    monkeypatch.setattr(
        hwp, "detect_gpus",
        lambda: [{"index": 0, "vram_total_mib": 12282,
                  "vram_used_mib": 12282 - 2082}])  # 2,082 MiB free

    # The raw, unreconciled figure -- what the gate used to enforce.
    before = hwp.vram_headroom()
    assert before["display_reserve_mib"] == 256
    assert before["ok"] is True, (
        "sanity check: 2,082 MiB free clears a 256 MiB reserve -- this is "
        "the state the 2026-08-17 monitor loss happened in (322 MiB free), "
        "and the un-reconciled gate would have let it through")

    # The reconciled figure -- what the gate enforces now.
    reserve = hc.resolve_display_reserve(fx.P1)
    assert reserve["mib"] == 2560

    arb = _arb()
    result = arb.grant("heavy_turn")
    assert result["ok"] is False
    assert result["refused"]["rule_id"] == "R-DISPLAY-RESERVE"
    assert "2560" in result["error"]


def test_the_gate_still_passes_with_real_headroom(monkeypatch):
    """Not a regression that refuses everything: comfortable free VRAM still
    grants, at the reconciled (higher) reserve."""
    monkeypatch.setattr(hwp, "display_reserve_mib", lambda *a, **k: 256)
    monkeypatch.setattr(
        hwp, "detect_gpus",
        lambda: [{"index": 0, "vram_total_mib": 12282, "vram_used_mib": 500}])
    arb = _arb()
    result = arb.grant("heavy_turn")
    assert result.get("refused", {}).get("rule_id") != "R-DISPLAY-RESERVE"


# ── gpu_headroom's two consumers agree with the same figure ──────────────────

def test_gpu_headroom_check_defaults_to_the_reconciled_reserve(monkeypatch):
    from agent_friday.services import gpu_headroom as gh
    monkeypatch.setattr(hwp, "display_reserve_mib", lambda *a, **k: 256)
    monkeypatch.setattr(
        gh, "gpu_memory",
        lambda: [{"name": "fixture-gpu", "total_mib": 12282,
                  "used_mib": 0, "free_mib": 3000}])
    result = gh.check(1000)
    assert result["reserve_mib"] == hc.resolve_display_reserve(hwp.get())["mib"]


def test_gpu_headroom_display_at_risk_defaults_to_the_reconciled_reserve(
        monkeypatch):
    from agent_friday.services import gpu_headroom as gh
    monkeypatch.setattr(hwp, "display_reserve_mib", lambda *a, **k: 256)
    monkeypatch.setattr(
        gh, "gpu_memory",
        lambda: [{"name": "fixture-gpu", "total_mib": 12282,
                  "used_mib": 0, "free_mib": 3000}])
    result = gh.display_at_risk()
    assert result["threshold_mib"] == hc.resolve_display_reserve(hwp.get())["mib"]


def test_gpu_headroom_still_honours_an_explicit_override(monkeypatch):
    """A caller that wants a specific reserve (a test, a fixture, a future
    override) is not overridden BACK to the reconciled figure."""
    from agent_friday.services import gpu_headroom as gh
    monkeypatch.setattr(
        gh, "gpu_memory",
        lambda: [{"name": "fixture-gpu", "total_mib": 12282,
                  "used_mib": 0, "free_mib": 3000}])
    result = gh.check(1000, reserve_mib=42)
    assert result["reserve_mib"] == 42


# ── HR3: one reserve, one place it is defined ────────────────────────────────

_DISPLAY_RESERVE_CONST = re.compile(
    r"^[A-Za-z_]*DISPLAY_RESERVE[A-Za-z_]*\s*[:=]", re.MULTILINE)

# (relative path) -> reason the exception is legitimate and documented.
_ALLOWED = {
    "src/agent_friday/services/hardware_profile.py":
        "the measured constants headroom_contract reads FROM -- the source "
        "of truth, not a duplicate (MIN_DISPLAY_RESERVE_MIB)",
    "src/agent_friday/services/model_plan.py":
        "DISPLAY_RESERVE_GIB -- the installer's pre-app rung pick, D1-"
        "independent per docs/design/implemented/headroom.md §2.2; already equals the "
        "reconciled figure (2.5 GiB == 2,560 MiB) and is cross-referenced "
        "at its definition",
}


def _tracked_py_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", "src/agent_friday", "*.py"],
        cwd=REPO, capture_output=True, check=True,
    ).stdout.decode("utf-8", "replace")
    return [p for p in out.split("\0") if p and p.endswith(".py")
            and p.startswith("src/agent_friday")]


def test_no_module_defines_its_own_display_reserve_constant():
    """HR3: `headroom_contract()` -- here, `resolve_display_reserve()` -- is
    the only definition of the display reserve; every other module reads
    from it. The two exceptions are named, documented, and D1-independent."""
    offenders = []
    for rel in _tracked_py_files():
        if rel == "src/agent_friday/services/headroom_contract.py":
            continue  # the definition itself
        path = REPO / rel
        text = path.read_text(encoding="utf-8", errors="replace")
        if _DISPLAY_RESERVE_CONST.search(text) and rel not in _ALLOWED:
            offenders.append(rel)
    assert not offenders, (
        "New module-level DISPLAY_RESERVE constant(s) outside "
        "headroom_contract.py and the documented exceptions:\n  "
        + "\n  ".join(offenders)
        + "\n\nRead from headroom_contract.resolve_display_reserve() "
          "instead, or add a justified entry to _ALLOWED here."
    )


def test_the_scanner_actually_fires_on_a_new_constant():
    """The guard must be shown to fire, or a silent regex miss is
    indistinguishable from an honest pass."""
    assert _DISPLAY_RESERVE_CONST.search(
        "MY_OWN_DISPLAY_RESERVE_MIB = 999\n")
    assert not _DISPLAY_RESERVE_CONST.search(
        "# this file merely mentions the display reserve in a comment\n")
