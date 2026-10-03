"""After install, measured beats estimated: the bench writes a measurement
row for this machine, a receipt, and the calibration every "about" row
uses."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_friday.services import model_bench as mb
from agent_friday.services import model_fit as mf
from agent_friday.services import model_store
from agent_friday.services import residency_catalog as rc
from tests import residency_fixtures as fx


@pytest.fixture
def installed(tmp_path, monkeypatch):
    f = tmp_path / "Ternary-Bonsai-2-27B-PTQ1_0.gguf"
    f.write_bytes(b"GGUF" + b"\x00" * 100)
    monkeypatch.setattr(model_store, "get", lambda mid: {
        "path": str(f), "engine": str(tmp_path / "fork" / "llama-server.exe"),
        "size_bytes": 5946648928, "serve_num_ctx": 131072} if mid == "bonsai2:27b" else None)
    monkeypatch.setattr(mb, "receipts_dir", lambda: tmp_path / "bench")
    monkeypatch.setattr(mf, "calibration_path", lambda: tmp_path / "calibration.json")
    recorded = []
    monkeypatch.setattr(rc, "record_measurement", lambda mid, fp, row: recorded.append((mid, fp, row)))
    return {"file": f, "recorded": recorded}


def _runner(result):
    calls = []

    def run(binary, gguf, ngl, kv):
        calls.append({"binary": binary, "gguf": gguf, "ngl": ngl, "kv": kv})
        return dict(result)
    run.calls = calls
    return run


def test_a_bench_records_a_measurement_row_and_a_receipt(installed):
    run = _runner({"prompt_tok_s": 467.0, "decode_tok_s": 33.2, "peak_vram_delta_mib": 8900, "wall_s": 41.0})
    out = mb.bench("bonsai2:27b", profile=fx.P1, runner=run, through_lease=False)
    assert out["status"] == "measured"
    assert run.calls[0]["ngl"] == 99 and run.calls[0]["kv"] == "q8_0"
    mid, fp, row = installed["recorded"][0]
    assert mid == "bonsai2:27b" and fp == rc.profile_fingerprint(fx.P1)
    assert row["tok_s_median"] == 33.2 and row["vram_mib"] == 8900 and row["num_ctx"] == 131072
    receipt = json.load(open(mb.receipt_path("bonsai2:27b"), encoding="utf-8"))
    assert receipt["status"] == "measured" and receipt["result"]["decode_tok_s"] == 33.2


def test_a_bench_calibrates_every_about_speed(installed):
    before = mf.decode_estimate(5946648928, fx.P1, gpu_name="NVIDIA GeForce RTX 4070")
    assert before["calibration"]["basis"] == "uncalibrated"
    run = _runner({"prompt_tok_s": 467.0, "decode_tok_s": 33.2, "peak_vram_delta_mib": 8900, "wall_s": 41.0})
    out = mb.bench("bonsai2:27b", profile=fx.P1, runner=run, through_lease=False)
    assert out["calibration"]["basis"] == "calibrated"
    after = mf.decode_estimate(5946648928, fx.P1, gpu_name="NVIDIA GeForce RTX 4070")
    assert abs(after["tok_s"] - 33.2) < 0.5
    assert "estimated_before" in out["calibration"]


def test_a_bench_goes_through_the_arbiter_lease_and_reports_the_swap(installed):
    class _Arb:
        def heavy_job(self, kind, job, **kw):
            assert kind == "bench_job" and kw.get("expect_files") is False
            return {"ok": True, "result": job(), "restored": True, "verified": True, "receipt": "r.json"}
    run = _runner({"prompt_tok_s": 400.0, "decode_tok_s": 30.0, "peak_vram_delta_mib": 8000, "wall_s": 30.0})
    out = mb.bench("bonsai2:27b", profile=fx.P1, runner=run, arbiter=_Arb())
    assert out["status"] == "measured" and out["swap"] == {"receipt": "r.json", "restored": True, "verified": True}


def test_a_failed_bench_leaves_a_failed_receipt_and_no_row(installed):
    def boom(*a):
        raise RuntimeError("llama-bench exited 1: invalid ggml type 143")
    out = mb.bench("bonsai2:27b", profile=fx.P1, runner=boom, through_lease=False)
    assert out["status"] == "failed" and "ggml type 143" in out["error"]
    assert installed["recorded"] == []
    assert mb.last_receipt("bonsai2:27b")["status"] == "failed"


def test_the_bench_binary_sits_beside_the_engine(tmp_path, monkeypatch):
    fork = tmp_path / "fork"
    fork.mkdir()
    (fork / "llama-bench.exe").write_bytes(b"x")
    monkeypatch.setattr(mb, "runtime_dir", lambda: tmp_path / "nowhere")
    assert mb.bench_binary_for(str(fork / "llama-server.exe")) == fork / "llama-bench.exe"
    assert mb.bench_binary_for(None) is None


def test_llama_bench_json_is_parsed_into_prompt_and_decode_rows():
    rows = mb._parse_bench_json('noise\n[{"n_prompt": 512, "n_gen": 0, "avg_ts": 467.1}, {"n_prompt": 0, "n_gen": 128, "avg_ts": 33.4}]\n')
    assert rows[0]["avg_ts"] == 467.1 and rows[1]["n_gen"] == 128
