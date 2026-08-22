"""Tests for the installer's model planner and executor.

These matter more than most unit tests: the planner is the installer's central
decision, and before these existed it was only ever exercised on the machine
that wrote it. Two of the bugs asserted against below were live in the first
version of the planner — including one that reproduced defect H3 from the
release audit, the very bug the module was written to prevent.
"""
from __future__ import annotations

import sys

import pytest

from agent_friday.services import model_plan as mp
from agent_friday.services import model_setup as ms


def profile(ram_gib, disk_gib, vram_gib=0, os_family="windows"):
    """A profile shaped exactly like hardware_profile.detect() emits one.

    Note `free_mib`, not `free_gib`. The first version of these tests invented
    a key the producer does not write, so they passed against a planner that
    could not read a real profile — and a machine with 242 GB free was told it
    had none. A fixture that does not match the producer proves nothing.
    """
    return {
        "os": {"family": os_family},
        "ram": {"total_mib": int(ram_gib * 1024)},
        "disk": {"free_mib": int(disk_gib * 1024)},
        "gpus": ([{"vram_total_mib": int(vram_gib * 1024)}] if vram_gib else []),
    }


def tiers(p):
    return {t["id"]: t for t in p["tiers"]}


# ── The headline claim ───────────────────────────────────────────────────────

@pytest.mark.parametrize("ram,disk,vram,os_family", [
    (32, 60, 12, "windows"),
    (16, 40, 0, "windows"),
    (8, 40, 0, "windows"),
    (32, 100, 0, "linux"),
    (16, 50, 0, "darwin"),
])
def test_memory_runs_without_a_gpu_and_downloads_nothing(ram, disk, vram, os_family):
    """Memory is the minimum requirement, needs no GPU, and needs no download.

    The claim survived a correction; the mechanism did not. It used to name two
    Ollama models that nothing in src/ loads. The real embedder is
    all-MiniLM-L6-v2, reached through sentence-transformers, which is a declared
    pip dependency — so it arrives with the install.
    """
    t = tiers(mp.plan(profile(ram, disk, vram, os_family)))
    assert t["vault"]["status"] == "ready"
    assert t["vault"]["models"] == []
    assert "all-MiniLM-L6-v2" in t["vault"]["reason"]


def test_eight_gigabytes_refuses_seats_and_names_rule_r2():
    """The README claimed 8 GB for months; R2 always disagreed."""
    t = tiers(mp.plan(profile(8, 40, 0)))
    assert t["seats"]["status"] == "refused"
    assert t["seats"]["rule"] == "R2"
    assert "16" in t["seats"]["reason"]
    # ...but memory still works. A refusal must not cascade.
    assert t["vault"]["status"] == "ready"


# ── Regressions from the first version of this module ────────────────────────

def test_gpu_gets_the_largest_model_that_fits_not_the_smallest():
    """Defect H3, reproduced by the planner written to prevent it.

    The first version compared usable VRAM against the size of card a model
    'wants' instead of what the model needs, so a 12 GiB card was handed
    gemma3:4b — the least-measured model in the system, and one that cannot
    call tools.
    """
    t = tiers(mp.plan(profile(32, 60, 12)))
    pick = t["brain"]["models"][0]
    assert pick["id"] != "gemma3:4b"
    assert pick["tools"] is True

    big = tiers(mp.plan(profile(32, 60, 24)))
    assert big["brain"]["models"][0]["id"] == "gemma4:12b"


@pytest.mark.parametrize("ram", [32, 64, 128])
def test_cpu_only_gets_the_smallest_useful_model_not_the_largest(ram):
    """Largest-that-fits is right for VRAM and wrong for CPU.

    Size costs latency directly on CPU, and CPU generation throughput is
    unmeasured for every model in the table — so picking the biggest one RAM
    happens to hold is choosing the option most likely to be unusable, on the
    strength of a number nobody has.
    """
    t = tiers(mp.plan(profile(ram, 100, 0)))
    assert t["brain"]["models"][0]["id"] == "gemma3:4b"
    assert "unmeasured" in t["brain"]["reason"]


def test_no_working_room_on_disk_refuses_memory():
    """Friday needs somewhere to write. Under 2 GiB free, say so."""
    t = tiers(mp.plan(profile(8, 1, 0)))
    assert t["vault"]["status"] == "refused"
    assert t["vault"]["rule"] == "disk"


# ── Honesty invariants ───────────────────────────────────────────────────────

@pytest.mark.parametrize("args", [
    (32, 60, 12, "windows"), (8, 40, 0, "windows"), (4, 3, 0, "windows"),
    (16, 40, 8, "windows"), (32, 100, 0, "linux"), (16, 50, 0, "darwin"),
])
def test_every_refusal_names_a_rule_and_explains_itself(args):
    for t in mp.plan(profile(*args))["tiers"]:
        if t["status"] == "refused":
            assert t.get("rule"), f"{t['id']} refused without naming a rule"
            assert len(t["reason"]) > 30, f"{t['id']} refusal is not actionable"


def test_missing_gpu_says_amd_is_invisible_rather_than_rejected():
    """detect_gpus shells nvidia-smi only, so an AMD card reads as no card.

    Telling someone their GPU was evaluated and found wanting, when it was
    never seen, is the kind of confident wrongness this project has a problem
    with.
    """
    t = tiers(mp.plan(profile(16, 40, 0)))
    assert "nvidia-smi" in t["image"]["reason"]


def test_non_windows_refuses_seats_on_platform_not_on_memory():
    t = tiers(mp.plan(profile(64, 200, 0, "linux")))
    assert t["seats"]["rule"] == "platform"


# ── The unit/name mismatch that refused the vault on a 242 GB machine ────────

def test_reads_the_key_hardware_profile_actually_writes():
    """247,905 MiB free must read as ~242 GiB, not as 0 and not as 247,905."""
    p = mp.plan({"os": {"family": "windows"},
                 "ram": {"total_mib": 32768},
                 "disk": {"free_mib": 247905},
                 "gpus": []})
    assert 240 < p["hardware"]["disk_free_gib"] < 245
    assert tiers(p)["vault"]["status"] == "ready"


@pytest.mark.parametrize("shape", [
    {"ram": {"total_mib": 32768}, "gpus": []},                       # no disk
    {"disk": {"free_mib": 100000}, "gpus": []},                      # no ram
    {"ram": {}, "disk": {}, "gpus": []},                             # empty
    {"ram": {"total_mib": 32768}, "disk": {"size_mib": 5}, "gpus": []},  # wrong key
])
def test_a_missing_number_refuses_to_plan_rather_than_assuming_zero(shape):
    """A missing input is not a zero.

    Defaulting to 0.0 turned an unreadable key into a confident "you have no
    disk", which refused the vault — the minimum requirement — on a machine
    with 242 GB free.
    """
    shape = {"os": {"family": "windows"}, **shape}
    p = mp.plan(shape)
    assert p["vault_ready"] is False
    assert p["download"] == []
    t = tiers(p)
    assert "detect" in t and t["detect"]["status"] == "refused"
    assert "could not read" in t["detect"]["reason"]


@pytest.mark.parametrize("args", [
    (32, 60, 12, "windows"), (16, 40, 0, "windows"), (8, 40, 0, "windows"),
    (4, 3, 0, "windows"), (64, 500, 24, "windows"), (32, 100, 0, "linux"),
])
def test_free_space_after_install_is_never_negative(args):
    """A negative number is never a valid answer to "how much space will you
    have". The unit mismatch printed "-5.2 GiB free" to every user."""
    p = mp.plan(profile(*args))
    after = p.get("disk_after_gib")
    if after is not None:
        assert after >= 0, f"planner offered to leave {after} GiB free"


# ── Already-installed models ─────────────────────────────────────────────────

def test_does_not_propose_downloading_what_is_already_there():
    installed = ["embeddinggemma:300m", "functiongemma:270m", "gemma3:4b"]
    p = mp.plan(profile(16, 100, 0), installed=installed)
    assert p["download"] == []
    assert tiers(p)["vault"]["status"] == "ready"


def test_a_different_family_is_not_hidden_by_a_prefix_match():
    """`"qwen3.5:9b".startswith("qwen3")` is true and meaningless.

    The prefix version of the alternatives filter hid qwen3.5:9b — a suitable
    model already on the machine — behind a recommendation to download
    qwen3:8b.
    """
    p = mp.plan(profile(32, 200, 12),
                installed=["qwen3.5:9b", "Gemma4-12B", "gemma3:4b"])
    reason = tiers(p)["brain"]["reason"]
    assert "qwen3.5:9b" in reason


def test_an_installed_model_does_not_win_if_it_cannot_call_tools():
    """Saving a download is not worth silently losing tool calling."""
    p = mp.plan(profile(32, 200, 12), installed=["gemma3:4b"])
    brain = tiers(p)["brain"]
    assert brain["models"], "should still propose a tool-capable download"
    assert brain["models"][0]["tools"] is True
    assert "cannot call tools" in brain["reason"]
    assert "gemma3:4b" in brain["reason"], "should name what it declined to use"


def test_tool_incapable_branch_on_a_cpu_only_machine():
    """The same branch with no GPU, since that path was never exercised live.

    A synthetic inventory rather than waiting for a machine that happens to
    have exactly this combination installed.
    """
    p = mp.plan(profile(32, 200, 0), installed=["gemma3:4b"])
    brain = tiers(p)["brain"]
    # On CPU the planner picks the smallest useful model, which IS gemma3:4b —
    # so here the installed copy legitimately wins and there is nothing to
    # download. The tool caveat must not fire when no better option existed.
    assert brain["status"] == "ready"
    assert brain["models"] == []
    assert "cannot call tools" not in brain["reason"]


def test_an_embedding_model_is_never_offered_as_a_brain():
    """qwen3-embedding:0.6b is not something you can talk to.

    Found on a real inventory. The family-token filter that stopped
    over-matching qwen3.5:9b under-filtered in the other direction and let an
    embedder through. Capability, not name shape — local_seats.installed()
    answers this properly and is passed in as `conversational`.
    """
    inventory = ["qwen3-embedding:0.6b", "nomic-embed-text:v1.5", "qwen3-vl:8b"]
    # With the authoritative filter applied by the caller:
    p = mp.plan(profile(32, 200, 12), installed=inventory,
                conversational=["qwen3-vl:8b"])
    reason = tiers(p)["brain"]["reason"]
    assert "embedding" not in reason.lower()
    assert "qwen3-vl:8b" in reason

    # And when the daemon is unreachable, the labelled fallback must still not
    # offer an embedder.
    p2 = mp.plan(profile(32, 200, 12), installed=inventory)
    reason2 = tiers(p2)["brain"]["reason"]
    assert "qwen3-embedding" not in reason2
    assert "nomic-embed-text" not in reason2


def test_conversational_filter_does_not_hide_an_installed_brain():
    """`conversational` may be filtered; `installed` must not be.

    The two parameters exist because filtering the inventory before passing it
    in would make the planner re-propose downloads the user already has. The
    original version of this test guarded the vault's own embedders; the vault
    no longer downloads anything, so the same property is pinned on the brain,
    which does.
    """
    p = mp.plan(profile(32, 200, 0), installed=["gemma3:4b"], conversational=[])
    assert tiers(p)["brain"]["status"] == "ready"
    assert p["download"] == []


def test_no_model_is_installed_that_nothing_consumes():
    """The rule, pinned: if src/ does not load it, the installer does not fetch it.

    embeddinggemma:300m and functiongemma:270m were recommended on the strength
    of real benchmarks. Nothing loads either. This test exists so that cannot
    quietly come back.
    """
    for args in [(16, 100, 0), (32, 200, 12), (8, 40, 0)]:
        p = mp.plan(profile(*args))
        ids = {m["id"] for m in p["download"]}
        assert not (ids & {"embeddinggemma:300m", "functiongemma:270m"}), ids
    assert mp.VAULT_MODELS == ()


# ── The executor ─────────────────────────────────────────────────────────────

def _plan_with(ids):
    return {"download": [{"id": i, "gib": 0.5, "why": "test"} for i in ids],
            "tiers": [{"id": "vault", "status": "install",
                       "models": [{"id": i} for i in ids]}]}


def test_a_pull_that_exits_zero_but_installs_nothing_is_a_failure():
    """The rule, enforced: exit code is evidence, the inventory is proof.

    `ollama pull` can exit zero having fetched a manifest and no weights. An
    installer that believes the exit code tells the user they are set up when
    they are not — which is exactly the failure mode this codebase keeps
    producing.
    """
    plan = _plan_with(["ghost:1b"])
    report = ms.install(plan, pull_fn=lambda m: (0, "pulled!"),
                        list_fn=lambda: [], say=lambda s: None)
    assert report["ok"] is False
    assert report["failed"] == 1
    assert "NOT in the daemon" in report["results"][0].detail


def test_verified_install_is_reported_as_success():
    state = []
    plan = _plan_with(["real:1b"])

    def pull(m):
        state.append({"name": "real:1b"})
        return 0, ""

    report = ms.install(plan, pull_fn=pull, list_fn=lambda: list(state),
                        say=lambda s: None)
    assert report["ok"] is True
    assert report["results"][0].ok


def test_sibling_tag_does_not_count_as_installed():
    """The `gemma4:e2b` bug: a family match is not a tag match."""
    plan = _plan_with(["gemma4:e2b"])
    report = ms.install(plan, pull_fn=lambda m: (0, ""),
                        list_fn=lambda: [{"name": "gemma4:12b"}],
                        say=lambda s: None)
    assert report["ok"] is False


# ── The exit code, asserted at the PROCESS boundary ──────────────────────────

def test_exit_code_normalisation():
    """False must become 1, not 0.

    cmd_health returns a bool. Passed to sys.exit() raw, False is 0 — a failed
    health check reporting success.
    """
    from agent_friday.cli import _exit_code
    assert _exit_code(None) == 0
    assert _exit_code(True) == 0
    assert _exit_code(False) == 1
    assert _exit_code(0) == 0
    assert _exit_code(1) == 1
    assert _exit_code(2) == 2
    assert _exit_code("nonsense") == 0      # never crash the CLI over this


def test_a_failed_install_exits_the_process_non_zero(tmp_path, monkeypatch):
    """Assert on `$?`, not on the report.

    The report said "0 of 2 models installed and verified" and "Vault memory is
    NOT working", cmd_models correctly returned 1, and main() discarded it — so
    a CI job checking the exit status was told the install succeeded. Testing
    that the report says "failed" would have passed against that bug. Only the
    process exit code catches it.
    """
    from agent_friday import cli
    from agent_friday.services import hardware_profile, model_plan, model_setup

    monkeypatch.setattr(hardware_profile, "get", lambda *a, **k: {
        "os": {"family": "linux"}, "ram": {"total_mib": 16384},
        "disk": {"free_mib": 51200}, "gpus": []})
    monkeypatch.setattr(model_plan, "plan", lambda profile, **kw: {
        "hardware": {"ram_gib": 16, "disk_free_gib": 50, "gpu_count": 0,
                     "vram_gib": 0.0, "os_family": "linux"},
        "tiers": [{"id": "vault", "name": "Vault", "status": "install",
                   "reason": "x", "models": [{"id": "a:1b"}]}],
        "download": [{"id": "a:1b", "gib": 0.5, "why": "x"}],
        "download_gib": 0.5, "disk_after_gib": 49.5, "disk_warning": False,
        "vault_ready": True})
    monkeypatch.setattr(model_plan, "render", lambda p: "")
    monkeypatch.setattr(model_setup, "install", lambda plan, **kw: {
        "results": [], "ok": False, "installed": 0, "failed": 1,
        "summary": "0 of 1 models installed and verified."})
    monkeypatch.setattr(model_setup, "vault_status",
                        lambda r, p: (False, "Vault memory is NOT working"))
    monkeypatch.setattr(sys, "argv", ["friday", "models", "--install"])

    # main() must RAISE SystemExit with a non-zero code. Not "return a value the
    # caller could inspect" — the entry point is `agent_friday.cli:main`, so
    # whatever main() does IS the process exit status. Before the fix it fell
    # off the end returning None, which exits 0.
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1, (
        f"a totally failed install exited {exc.value.code}; a script checking "
        f"$? would be told it worked")


def test_prewarm_runs_even_when_there_are_no_models_to_download(monkeypatch):
    """"Nothing to install" is about Ollama models, not the lazy assets.

    An early `return 0` on an empty download plan put the prewarm call on an
    unreachable line, so any machine whose models were already present — a
    re-run after an interrupted install, or after pulling a model by hand —
    silently fetched nothing, and MiniLM, faster-whisper and the Piper voice
    still arrived mid-conversation.

    It survived because the only machine available to test on already had its
    models, which is precisely the state that skipped the code. This test
    forces that state.
    """
    from agent_friday import cli
    from agent_friday.services import (hardware_profile, model_plan,
                                       model_setup, prewarm)

    monkeypatch.setattr(hardware_profile, "get", lambda *a, **k: {
        "os": {"family": "windows"}, "ram": {"total_mib": 16384},
        "disk": {"free_mib": 307200}, "gpus": []})
    monkeypatch.setattr(model_plan, "plan", lambda profile, **kw: {
        "hardware": {"ram_gib": 16, "disk_free_gib": 300, "gpu_count": 0,
                     "vram_gib": 0.0, "os_family": "windows"},
        "tiers": [{"id": "vault", "name": "Memory", "status": "ready",
                   "reason": "x", "models": []}],
        "download": [], "download_gib": 0.0, "disk_after_gib": 300.0,
        "disk_warning": False, "vault_ready": True})
    monkeypatch.setattr(model_plan, "render", lambda p: "")
    monkeypatch.setattr(model_setup, "vault_status", lambda r, p: (True, "ok"))

    reached = {"prewarm": False}

    def _spy(say=print, only=None):
        reached["prewarm"] = True
        return {"steps": [], "ok": True, "summary": "reached"}

    monkeypatch.setattr(prewarm, "prewarm", _spy)
    monkeypatch.setattr(sys, "argv", ["friday", "models", "--install"])

    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert reached["prewarm"], (
        "prewarm was skipped because the model plan had nothing to download")
    assert exc.value.code == 0


def test_vault_failure_is_reported_separately_from_the_count():
    """A run that installs a brain and fails the embedder has not succeeded."""
    plan = {"download": [{"id": "embeddinggemma:300m", "gib": 0.6, "why": "x"}],
            "tiers": [{"id": "vault", "status": "install",
                       "models": [{"id": "embeddinggemma:300m"}]}]}
    report = ms.install(plan, pull_fn=lambda m: (1, "network unreachable"),
                        list_fn=lambda: [], say=lambda s: None)
    ok, msg = ms.vault_status(report, plan)
    assert ok is False
    assert "NOT working" in msg
