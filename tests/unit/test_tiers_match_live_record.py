"""The T5 row of bonsai2-tiers.json is the measured serving baseline, not a draft.

Three configurations for the reference 12 GB tier disagreed: tiers.json
said 49K / q8_0 / two unified slots / -ub 2048 with the projector loaded,
while the live model record, the one measured best (511 tok/s prefill, 45
tok/s decode, 1,455 MiB free), serves 131K / q4_0 KV / one slot / -b 4096
-ub 512 with the projector on demand. Two unified slots re-read the whole
standing prompt on every turn (52,963 ms, twice), and -ub 2048 at q4_0
decoded at 11.8 tok/s. The baseline below is what the live record declares;
the T5 pick rule and tier row must say the same thing.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TIERS = REPO / "src" / "agent_friday" / "resources" / "bonsai2-tiers.json"

# The live record's serving flags for bonsai2:27b on the reference machine
# (`serve_num_ctx` and `serve_args`), restated as the T5 baseline (D-Q1).
LIVE_RECORD = {
    "context": 131072,
    "kv": "q4_0",
    "slots": 1,
    "batch": "-b 4096 -ub 512",
    "mmproj": "on_demand",
    "packing": "PTQ1_0",
}


def _doc():
    return json.loads(TIERS.read_text(encoding="utf-8"))


def _t5_rule():
    return next(r for r in _doc()["pick"]["rules"] if r["id"] == "T5")["out"]


def _t5_tier():
    return next(t for t in _doc()["tiers"] if t["id"] == "T5")["config"]


def test_the_t5_pick_rule_is_the_live_record():
    out = _t5_rule()
    assert out["context"] == LIVE_RECORD["context"]
    assert out["kv"] == LIVE_RECORD["kv"]
    assert out["slots"] == LIVE_RECORD["slots"]
    assert out["batch"] == LIVE_RECORD["batch"]
    assert out["mmproj"] == LIVE_RECORD["mmproj"]
    assert out["packing"] == LIVE_RECORD["packing"]


def test_the_t5_tier_row_agrees_with_its_pick_rule():
    cfg = _t5_tier()
    out = _t5_rule()
    for key in ("context", "kv", "slots", "batch", "mmproj", "packing"):
        assert cfg[key] == out[key], key


def test_no_gpu_tier_runs_two_unified_slots():
    """A unified KV buffer keeps no per-slot prefix: measured 52,963 ms re-reads."""
    doc = _doc()
    for line in doc["runtime"]["flags"]["gpu"]:
        assert "--kv-unified" not in line, line
