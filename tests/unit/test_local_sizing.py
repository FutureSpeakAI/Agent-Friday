"""Local models sized to Friday's prompts and placed on a small card.

The failure this pins, from an 8 GB laptop: the deep-thinker model was served
at 8,192 tokens while Friday's prompts are ~20k, so every local call failed
"exceeds the available context size"; it shared the card with a 1.7B helper,
both partly offloaded (~4 tok/s); and the planner refused the 1.7B outright as
"never measured". The working arrangement, found by hand, was the big model on
the GPU at a window that holds the prompt and the 1.7B on the CPU.

Everything goes through the existing entry points: `residency_policy.plan`
(what the Arbiter seats) and `LlamaServerBackend` (what it spawns). Fixtures are
synthetic: made-up ids, declared hardware, no daemon, no GPU.
"""
from __future__ import annotations

import json
import logging
import pathlib
import subprocess

import pytest

from agent_friday.services import context_budget
from agent_friday.services import model_store
from agent_friday.services import residency_arbiter as ra
from agent_friday.services import residency_policy as rp
from tests import residency_fixtures as fx

BIG = "example-27b:test"
SMALL = "example-1.7b:test"
GOLDEN = pathlib.Path(__file__).resolve().parents[1] / "golden" / "residency"


def _laptop_8gb(baseline=120):
    """An 8 GB laptop card whose display runs on the integrated GPU."""
    return fx._profile("laptop-8gb-test", "windows", 16, 8, 16384,
                       [fx._gpu(0, "Laptop GPU 8GB", 8188, baseline)],
                       "ddr5", 76.8, free_mib=120 * 1024)


def _desktop_24gb():
    return fx._profile("desktop-24gb-test", "linux", 32, 16, 65536,
                       [fx._gpu(0, "Desktop GPU 24GB", 24564, 400)],
                       "ddr5", 89.6, free_mib=900 * 1024)


def _entry(model_id, params_b, size_bytes, window, **extra):
    """A catalog entry as residency_catalog builds one, never measured here."""
    e = {"model_id": model_id, "backend": "llama-server",
         "artifact_bytes": size_bytes, "size_bytes": size_bytes,
         "params_total_b": params_b, "params_active_b": None, "is_moe": False,
         "context_window": window, "modalities": ["completion", "tools"],
         "can_generate": True, "is_embedding": False,
         "needs_think_disabled": False, "measured": [],
         "baseline_ms_per_token": None, "est_load_s": None}
    e.update(extra)
    return e


def _pair():
    big = _entry(BIG, 27.4, 5946648928, 262144,
                 kv_layout={"full_attention_layers": 16, "kv_heads": 4, "head_dim": 256},
                 block_count=64)
    small = _entry(SMALL, 1.7, 463290464, 32768)
    return [big, small]


def _prompt_floor():
    """Friday's prompt (tool schemas + system prompt), as the code measures it
    when the live sources are not loaded, rounded up the ladder with margin."""
    prompt = context_budget.MEASURED_TOOL_TOKENS + context_budget.MEASURED_SYSTEM_PROMPT_TOKENS
    return prompt, next(r for r in (8192, 16384, 32768, 65536, 131072) if r > prompt)


# ── the planner ──────────────────────────────────────────────────────────────

def test_on_8gb_the_big_model_gets_the_gpu_and_the_helper_goes_to_the_cpu():
    plan = rp.plan(_laptop_8gb(), _pair())
    brain = plan["seats"]["interactive_brain"]
    side = plan["seats"]["sidekick"]
    prompt, _ = _prompt_floor()

    assert brain is not None and brain["model_id"] == BIG, \
        "the big reasoning model must be the brain, not the 1.7B helper"
    assert brain["device"].startswith("gpu:")
    assert brain["num_ctx"] >= prompt, "the brain's window is below Friday's prompt"
    assert brain["n_gpu_layers"] >= 48, "most of the 27B belongs on the card"

    assert side is not None and side["model_id"] == SMALL, "the 1.7B helper was refused"
    assert side["device"] == "cpu"
    assert side["n_gpu_layers"] == 0
    assert side["vram_mib"] == 0
    assert side["num_ctx"] >= prompt

    # Nothing pinned over the card.
    avail = plan["budgets"]["gpus"][0]["available_mib"]
    assert plan["pinned_vram_mib"].get("gpu:0", 0) <= avail
    # And the heavy role is the same seat, not a second copy of the 27B.
    heavy = plan["seats"]["heavy_hitter"]
    assert heavy["model_id"] == BIG and heavy["vram_mib"] == 0


def test_the_window_never_drops_below_the_measured_prompt(monkeypatch):
    """A bigger prompt moves the window UP and the GPU layers DOWN, never the
    window down to fit the card."""
    monkeypatch.setattr(context_budget, "MEASURED_SYSTEM_PROMPT_TOKENS", 30000)
    prompt = context_budget.MEASURED_TOOL_TOKENS + 30000          # ~38.5k
    plan = rp.plan(_laptop_8gb(), _pair())
    brain = plan["seats"]["interactive_brain"]
    assert brain is not None and brain["model_id"] == BIG
    assert brain["num_ctx"] >= prompt
    assert brain["num_ctx"] == 65536
    assert 0 < brain["n_gpu_layers"] < 99, "the larger window costs layers, not context"
    assert brain["device"].startswith("gpu:")


def test_a_card_too_small_for_one_layer_puts_the_brain_on_the_cpu_at_the_full_window():
    # 1,600 - 1,024 reserve - 100 idle = 476 MiB: the compute buffers alone
    # take 400 of it, so not one layer of a 27B fits.
    tiny = fx._profile("tiny-gpu-test", "linux", 8, 4, 32768,
                       [fx._gpu(0, "Tiny GPU", 1600, 100)], "ddr4", 40.0)
    brain = rp.plan(tiny, _pair())["seats"]["interactive_brain"]
    prompt, _ = _prompt_floor()
    assert brain["model_id"] == BIG
    assert brain["device"] == "cpu" and brain["n_gpu_layers"] == 0
    assert brain["num_ctx"] >= prompt


def test_an_unmeasured_small_model_is_planned_as_estimated_not_refused():
    """24 GB holds both whole, so today's shape stands (the fast model is the
    interactive brain, the big one the quality seat) -- but neither is refused
    for being unmeasured: both are planned from the estimate and say so."""
    plan = rp.plan(_desktop_24gb(), _pair())
    brain = plan["seats"]["interactive_brain"]
    assert brain is not None and brain["model_id"] == SMALL
    # Room for both on 24 GB: the helper stays on the card, whole.
    assert brain["device"].startswith("gpu:") and brain["n_gpu_layers"] == 99
    assert brain["sizing"]["basis"] == "estimated"
    assert SMALL in brain["sizing"]["measure_later"]
    heavy = plan["seats"]["heavy_hitter"]
    assert heavy is not None and heavy["model_id"] == BIG
    assert heavy["sizing"]["basis"] == "estimated" and BIG in heavy["sizing"]["measure_later"]
    assert not [r for r in plan["refusals"]
                if r["role"] in ("interactive_brain", "heavy_hitter", "sidekick")
                and "never been measured" in r["explanation"]]


def test_the_helper_rule_can_be_switched_off(monkeypatch):
    from agent_friday.services import local_sizing as lz
    sizing = dict(lz.default_inputs(), cfg=dict(lz.DEFAULTS, helpers_to_cpu_when_tight=False))
    plan = rp.plan(_laptop_8gb(), _pair(), sizing=sizing)
    side = plan["seats"]["sidekick"]
    # Without the CPU move the helper has nowhere whole to go, and says why.
    assert side is None
    why = [r["explanation"] for r in plan["refusals"] if r["role"] == "sidekick"]
    assert why and "MiB" in why[0] and "never been measured" not in why[0]


def _sizing_off():
    from agent_friday.services import local_sizing as lz
    return dict(lz.default_inputs(), cfg=dict(lz.DEFAULTS, enabled=False))


def _same(a, b):
    return json.loads(json.dumps(a, sort_keys=True)) == json.loads(json.dumps(b, sort_keys=True))


@pytest.mark.parametrize("key", sorted(fx.ALL_PROFILES))
def test_measured_fixture_plans_are_untouched_by_the_sizing_rules(key):
    """The six golden machines carry measurements: the sizing rules must not
    move one seat. Compared whole against the committed golden plan (the same
    file test_residency_policy pins), and against the plan with sizing off."""
    profile = fx.ALL_PROFILES[key]
    plan = rp.plan(profile, fx.catalog(profile))
    golden = json.loads((GOLDEN / ("%s.json" % key)).read_text(encoding="utf-8"))
    assert _same(plan, golden), "sizing moved the golden plan for %s" % key
    assert _same(plan, rp.plan(profile, fx.catalog(profile), sizing=_sizing_off())), key
    for seat in plan["seats"].values():
        assert not (seat or {}).get("sizing"), key


@pytest.mark.parametrize("key", sorted(fx.ALL_PROFILES))
def test_a_measured_small_helper_does_not_move_a_fixture_plan(key):
    """The same machines with a measured <= 2B helper installed as well: the
    plan is what it is with the sizing rules switched off."""
    profile = fx.ALL_PROFILES[key]
    entries = fx.catalog(profile) + [fx.small_helper_entry(profile)]
    on = rp.plan(profile, entries)
    off = rp.plan(profile, entries, sizing=_sizing_off())
    assert _same(on, off), "a small helper changed the plan on %s" % key


def _measured_pair():
    """The big model and a <= 2B helper, both measured whole on the GPU."""
    big, small = _pair()
    small["context_window"] = 262144
    big["measured"] = [
        {"num_ctx": 32768, "vram_mib": 7200, "total_mib": 7200, "pct_gpu": 100,
         "backend": "ollama", "measured_at": "2026-10-01"},
        {"num_ctx": 131072, "vram_mib": 9100, "total_mib": 9100, "pct_gpu": 100,
         "backend": "ollama", "measured_at": "2026-10-01"}]
    big["baseline_ms_per_token"] = 40.0
    small["measured"] = [
        {"num_ctx": 32768, "vram_mib": 1500, "total_mib": 1500, "pct_gpu": 100,
         "backend": "ollama", "measured_at": "2026-10-01"}]
    small["baseline_ms_per_token"] = 7.0
    return [big, small]


def test_a_24gb_card_keeps_todays_plan_with_a_big_model_and_a_small_helper():
    """Both fit the card whole, so the big model is NOT made the interactive
    brain: the plan is exactly the one with the sizing rules off (the fast
    model is the brain, the big one the quality seat)."""
    on = rp.plan(_desktop_24gb(), _measured_pair())
    off = rp.plan(_desktop_24gb(), _measured_pair(), sizing=_sizing_off())
    assert _same(on, off)
    assert on["seats"]["interactive_brain"]["model_id"] == SMALL
    assert on["seats"]["heavy_hitter"]["model_id"] == BIG
    assert not on["seats"]["heavy_hitter"].get("shares_model_with")


def test_on_8gb_a_measured_pair_still_gives_the_big_model_the_card():
    """Memory is tight (7,200 + 1,500 MiB do not fit the 8 GB card), so the
    big model is the brain and the helper goes to the CPU."""
    plan = rp.plan(_laptop_8gb(), _measured_pair())
    assert plan["seats"]["interactive_brain"]["model_id"] == BIG
    assert plan["seats"]["sidekick"]["model_id"] == SMALL
    assert plan["seats"]["sidekick"]["device"] == "cpu"


def test_a_partial_offload_measurement_is_not_the_whole_model_cost():
    """A llama-server row is the card's peak at whatever -ngl that run used.
    5,200 MiB taken with part of the 27B on the CPU is not what the whole model
    needs; planning from it as if it were seats all 64 layers on a card that
    cannot hold them."""
    big, small = _pair()
    big["measured"] = [{"num_ctx": 32768, "vram_mib": 5200, "total_mib": 5200,
                        "backend": "llama-server", "measured_at": "2026-10-01"}]
    brain = rp.plan(_laptop_8gb(), [big, small])["seats"]["interactive_brain"]
    assert brain["model_id"] == BIG
    assert (brain.get("sizing") or {}).get("basis") == "estimated", \
        "a row with no -ngl must not be read as the whole model's cost"
    assert 0 < brain["n_gpu_layers"] < 99


def test_a_full_offload_measurement_is_the_whole_model_cost():
    big, small = _pair()
    big["measured"] = [{"num_ctx": 32768, "vram_mib": 5200, "total_mib": 5200,
                        "backend": "llama-server", "n_gpu_layers": 99,
                        "measured_at": "2026-10-01"}]
    brain = rp.plan(_laptop_8gb(), [big, small])["seats"]["interactive_brain"]
    assert brain["model_id"] == BIG
    assert (brain.get("sizing") or {}).get("basis") != "estimated"
    assert brain["vram_mib"] == 5200


def test_an_embedder_named_by_an_override_is_not_sized_to_the_chat_prompt():
    emb = _entry("example-embed:0.6b", 0.6, 639150592, 32768,
                 modalities=["embedding"], can_generate=False, is_embedding=True)
    plan = rp.plan(_laptop_8gb(), _pair() + [emb],
                   overrides={"embedder": "example-embed:0.6b"})
    seat = plan["seats"]["embedder"]
    assert not (seat or {}).get("sizing"), "an embedder carries no chat prompt"
    _, floor = _prompt_floor()
    assert (seat or {}).get("num_ctx") != floor


# ── the Arbiter ──────────────────────────────────────────────────────────────

@pytest.fixture
def quiet_inputs(monkeypatch):
    """No settings file, no live prompt assembly: the recorded figures."""
    from agent_friday import core
    monkeypatch.setattr(core, "_load_settings", lambda: {}, raising=False)
    # seat_cap reads only the memoised prompt size (never assembles it), so
    # an empty memo means the recorded figures.
    monkeypatch.setattr(context_budget, "_MEMO", {})
    monkeypatch.setattr(context_budget, "overhead", lambda **k: {
        "tool_tokens": context_budget.MEASURED_TOOL_TOKENS,
        "system_prompt_tokens": context_budget.MEASURED_SYSTEM_PROMPT_TOKENS,
        "injected_tokens": 0, "total_tokens": 0, "basis": "recorded"})


def test_a_declared_window_below_the_prompt_is_lifted_to_the_floor(monkeypatch, quiet_inputs):
    """The installer recorded 8,192 for an 8 GB laptop. Serving that against a
    ~20k prompt fails every call, so the seat is never capped below it."""
    monkeypatch.setattr(model_store, "get", lambda mid: {
        "serve_num_ctx": 8192, "context_window": 262144})
    prompt, floor = _prompt_floor()
    cap = ra.LlamaServerBackend.seat_cap(BIG)
    assert cap >= prompt
    assert cap == floor


def test_a_declared_window_above_the_floor_is_kept(monkeypatch, quiet_inputs):
    monkeypatch.setattr(model_store, "get", lambda mid: {"serve_num_ctx": 131072})
    assert ra.LlamaServerBackend.seat_cap(BIG) == 131072


def test_the_persons_override_is_kept_even_below_the_floor(monkeypatch, quiet_inputs):
    monkeypatch.setattr(model_store, "get", lambda mid: {
        "serve_num_ctx": 16384, "user_override": {"num_ctx": 16384, "keys": ["num_ctx"]}})
    assert ra.LlamaServerBackend.seat_cap(BIG) == 16384


def _spawn(monkeypatch, tmp_path, record, planned=None, model_id=SMALL, num_ctx=32768):
    """(argv, env) of the llama-server the arbiter would start (never run)."""
    calls = []

    class DeadProc:
        returncode = 1

        def __init__(self, cmd, **kw):
            calls.append((list(cmd), kw.get("env")))

        def poll(self):
            return 1

        def terminate(self):
            pass

    monkeypatch.setattr(subprocess, "Popen", DeadProc)
    monkeypatch.setattr(ra, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(model_store, "get", lambda mid: dict(record))
    monkeypatch.setattr(ra.LlamaServerBackend, "_kv_cache_type", lambda self: "q8_0")
    be = ra.LlamaServerBackend(binary=tmp_path / "llama-server.exe")
    if planned is not None:
        be.planned_gpu_layers = {model_id: planned}
    with pytest.raises(ra.TransitionError):
        be._spawn_once(tmp_path / "llama-server.exe", model_id, num_ctx,
                       gguf_path=tmp_path / "m.gguf", port=8199)
    return calls[0]


def _spawn_cmd(monkeypatch, tmp_path, record, planned=None):
    return _spawn(monkeypatch, tmp_path, record, planned)[0]


def _flag(cmd, name):
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == name]


def test_a_helper_planned_on_the_cpu_is_spawned_with_no_gpu_layers(monkeypatch, tmp_path, quiet_inputs):
    cmd = _spawn_cmd(monkeypatch, tmp_path, {"serve_args": ["-ngl", "56"]}, planned=0)
    assert _flag(cmd, "-ngl") == ["0"], "the plan's placement must outrank the installer's -ngl"


def test_the_persons_override_outranks_the_plan(monkeypatch, tmp_path, quiet_inputs):
    cmd = _spawn_cmd(monkeypatch, tmp_path, {
        "serve_args": ["-ngl", "99"],
        "user_override": {"n_gpu_layers": 99, "keys": ["n_gpu_layers"]}}, planned=0)
    assert _flag(cmd, "-ngl") == ["99"]


# ── a raised window re-fits the installer's GPU layers ───────────────────────

def _installer_record():
    """What the Windows installer writes for an 8 GB laptop (ModelPicker.ps1
    tier T4a): 8,192 tokens and -ngl from floor(64 * (usable - kv - compute)
    / weights), here 56 of 64 layers."""
    return {"serve_num_ctx": 8192, "context_window": 262144, "can_generate": True,
            "size_bytes": 5946648928, "params_total_b": 27.4,
            "serve_args": ["-ngl", "56", "-np", "1",
                           "--cache-type-k", "q8_0", "--cache-type-v", "q8_0"]}


def test_a_raised_window_does_not_keep_the_gpu_layers_sized_for_the_old_one(
        monkeypatch, tmp_path, quiet_inputs, caplog):
    """The 27B was placed by measurement (no planned layer count), so before
    this the record's -ngl 56 -- computed for an 8,192-token KV cache -- went
    out with the 32k window seat_cap now allows, and the card over-committed."""
    from agent_friday.services import local_sizing as lz
    _, floor = _prompt_floor()
    with caplog.at_level(logging.INFO, logger="friday.local_sizing"):
        cmd, env = _spawn(monkeypatch, tmp_path, _installer_record(),
                          model_id=BIG, num_ctx=floor)
    assert _flag(cmd, "-c") == [str(floor)], "the window is the prompt floor"
    ngl = int(_flag(cmd, "-ngl")[-1])
    assert ngl < 56, "a 4x larger KV cache cannot keep the layer count sized for 8,192"
    expect = lz.layers_for_raised_window(
        dict(_installer_record(), model_id=BIG), 56, 8192, floor, kv_type="q8_0")
    assert ngl == expect["n_gpu_layers"]
    assert ngl > 0
    assert "GPU layers 56 -> %d" % ngl in caplog.text
    assert env is None, "a GPU seat inherits the environment unchanged"


def test_a_window_not_raised_keeps_the_declared_layers(monkeypatch, tmp_path, quiet_inputs):
    rec = dict(_installer_record(), serve_num_ctx=65536)
    cmd = _spawn(monkeypatch, tmp_path, rec, model_id=BIG, num_ctx=65536)[0]
    assert _flag(cmd, "-ngl")[-1] == "56"


# ── a CPU seat keeps off the card ────────────────────────────────────────────

def test_a_cpu_seat_is_spawned_with_the_gpu_hidden(monkeypatch, tmp_path, quiet_inputs):
    """-ngl 0 alone still lets a CUDA build allocate its compute buffer on
    the card the big model was planned to have."""
    cmd, env = _spawn(monkeypatch, tmp_path, {"serve_args": ["-ngl", "56"]}, planned=0)
    assert _flag(cmd, "-ngl")[-1] == "0"
    assert _flag(cmd, "--device") == ["none"]
    assert env is not None and env.get("CUDA_VISIBLE_DEVICES") == "-1"


def test_a_gpu_seat_is_not_hidden_from_the_gpu(monkeypatch, tmp_path, quiet_inputs):
    cmd, env = _spawn(monkeypatch, tmp_path, {"serve_args": ["-ngl", "56"]}, planned=99)
    assert "--device" not in cmd
    assert env is None


def test_hiding_the_gpu_from_cpu_seats_can_be_switched_off(monkeypatch, tmp_path, quiet_inputs):
    from agent_friday import core
    monkeypatch.setattr(core, "_load_settings",
                        lambda: {"local_sizing": {"cpu_seats_hide_gpu": False}}, raising=False)
    cmd, env = _spawn(monkeypatch, tmp_path, {"serve_args": ["-ngl", "56"]}, planned=0)
    assert _flag(cmd, "-ngl")[-1] == "0"
    assert "--device" not in cmd and env is None


# ── only chat models are lifted to the prompt floor ──────────────────────────

def test_an_embedders_declared_window_is_not_lifted(monkeypatch, quiet_inputs):
    monkeypatch.setattr(model_store, "get", lambda mid: {
        "serve_num_ctx": 2048, "is_embedding": True, "can_generate": False})
    assert ra.LlamaServerBackend.seat_cap("example-embed:0.6b") == 2048


def test_a_vision_models_declared_window_is_not_lifted(monkeypatch, quiet_inputs):
    monkeypatch.setattr(model_store, "get", lambda mid: {
        "serve_num_ctx": 4096, "kind": "vision", "modalities": ["vision"]})
    assert ra.LlamaServerBackend.seat_cap("example-vision:1b") == 4096


# ── seat_cap never assembles the system prompt ───────────────────────────────

def test_seat_cap_does_not_build_the_system_prompt(monkeypatch, quiet_inputs):
    """Building it reaches seat_cap again (via _smart_context_budget ->
    tool_budget._window -> _spawn_cap); a cold memo recursed to the limit."""
    def _boom(**k):
        raise AssertionError("seat_cap assembled the system prompt")
    monkeypatch.setattr(context_budget, "overhead", _boom)
    monkeypatch.setattr(context_budget, "system_prompt_tokens", _boom)
    monkeypatch.setattr(model_store, "get", lambda mid: {
        "serve_num_ctx": 8192, "context_window": 262144})
    _, floor = _prompt_floor()
    assert ra.LlamaServerBackend.seat_cap(BIG) == floor


def test_seat_cap_uses_the_memoised_prompt_size_when_there_is_one(monkeypatch, quiet_inputs):
    monkeypatch.setattr(context_budget, "_MEMO", {"overhead": (0.0, {
        "tool_tokens": 9000, "system_prompt_tokens": 40000, "basis": "measured"})})
    monkeypatch.setattr(model_store, "get", lambda mid: {
        "serve_num_ctx": 8192, "context_window": 262144})
    assert ra.LlamaServerBackend.seat_cap(BIG) == 65536
