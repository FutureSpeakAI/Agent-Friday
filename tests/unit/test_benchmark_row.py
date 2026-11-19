"""The model slate shows this PC's own benchmark (VRAM, speed, cold load) and uses its cold-load time as the
switch estimate until a real load has been timed. Always labelled with what it is and the date."""
from __future__ import annotations

from agent_friday.services import hardware_profile, model_slate as ms, residency_catalog as rc, seat_timing
from tests.unit.test_model_slate import CAT


def _pc(monkeypatch, tmp_path, rows):
    monkeypatch.setattr(seat_timing, "_path", lambda: tmp_path / "t.json")
    seat_timing._forget_memory_only()
    monkeypatch.setattr(ms, "_catalog", lambda: CAT)
    monkeypatch.setattr(ms, "_settings", lambda: {"model_routing": {"mode": "local_preferred"}})
    monkeypatch.setattr(ms, "_local_busy_reason", lambda mid: None)
    monkeypatch.setattr(ms, "_live_local", lambda: set())
    monkeypatch.setattr(hardware_profile, "get", lambda: {"gpus": [], "ram": {"total_mib": 1}})
    monkeypatch.setattr(rc, "measurements", lambda mid, fp: rows.get(mid, []))
    return {r["id"]: r for r in ms.slate()["models"]}


def test_the_newest_benchmark_is_shown_and_labelled(monkeypatch, tmp_path):
    rows = {"gemma4:e4b": [{"num_ctx": 8192, "vram_mib": 3081, "tok_s_median": 99.9, "cold_load_s": 27.5,
                            "measured_at": "2026-08-14", "source": "seed"},
                           {"num_ctx": 32768, "vram_mib": 3200, "tok_s_median": 90.0, "measured_at": "2026-08-15",
                            "source": "measured"}]}
    got = _pc(monkeypatch, tmp_path, rows)["gemma4:e4b"]
    assert got["benchmark"]["vram_mib"] == 3200 and got["benchmark"]["measured_at"] == "2026-08-15"
    assert got["benchmark"]["basis"] == "measured on this PC" and got["benchmark"]["source"] == "measured"


def test_a_row_shipped_with_the_app_says_so_and_is_never_called_this_pcs_own(monkeypatch, tmp_path):
    rows = {"gemma4:e4b": [{"num_ctx": 8192, "cold_load_s": 27.5, "measured_at": "2026-08-14", "source": "seed"}]}
    got = _pc(monkeypatch, tmp_path, rows)["gemma4:e4b"]
    assert "ships with the app" in got["benchmark"]["basis"] and "not on this one" in got["benchmark"]["basis"]
    assert "ships with the app" in got["switch"]["basis"] and "2026-08-14" in got["switch"]["basis"]


def test_a_benchmarked_cold_load_is_the_estimate_until_a_real_load_is_timed(monkeypatch, tmp_path):
    rows = {"gemma4:e4b": [{"num_ctx": 8192, "cold_load_s": 27.5, "measured_at": "2026-08-14", "source": "measured"}]}
    sw = _pc(monkeypatch, tmp_path, rows)["gemma4:e4b"]["switch"]
    assert sw == {"cold": True, "expected_seconds": 27.5, "basis": "measured on this PC, on 2026-08-14"}
    seat_timing.record("gemma4:e4b", 31.0)
    assert {r["id"]: r for r in ms.slate()["models"]}["gemma4:e4b"]["switch"]["expected_seconds"] == 31.0


def test_an_unbenchmarked_model_shows_nothing_invented(monkeypatch, tmp_path):
    got = _pc(monkeypatch, tmp_path, {})["gemma4:e4b"]
    assert got["benchmark"] is None and got["switch"]["expected_seconds"] is None


def test_every_model_says_what_leaves_the_house_in_words(monkeypatch, tmp_path):
    rows = _pc(monkeypatch, tmp_path, {})
    assert rows
    for r in rows.values():
        if r["local"]:
            assert r["what_leaves"] == ms.LEAVES_NOTHING
        else:
            assert r["what_leaves"].startswith("Your messages") and "go to" in r["what_leaves"]


def test_relays_are_named_cloud_tags_name_ollama_com_and_unverified_local_rows_are_not_called_private():
    for prov, label in (("openrouter", "OpenRouter"), ("huggingface", "Hugging Face (Inference Providers)")):
        sentence = ms._what_leaves({"provider": prov, "provider_label": label})
        assert label in sentence and "actually runs the model" in sentence
    assert "actually runs" not in ms._what_leaves({"provider": "anthropic", "provider_label": "Anthropic"})
    tag = ms._what_leaves({"id": "gpt-oss:120b-cloud", "local": True, "provider": "ollama-local",
                           "provider_label": "Local (Ollama)"})
    assert "ollama.com" in tag and "Local (Ollama)" not in tag and tag != ms.LEAVES_NOTHING
    assert ms._what_leaves({"id": "gemma4:e4b", "local": True, "provider": "no-such-provider"}) == ms.LEAVES_NOTHING
    assert "your own network" in ms.LEAVES_NOTHING


def test_a_cloud_tag_row_is_cloud_everywhere_not_only_in_the_privacy_sentence():
    assert ms.is_local({"id": "gpt-oss:120b-cloud", "local": True, "provider": "ollama-local"}) is False
    assert ms.is_local({"id": "gemma4:e4b", "local": True, "provider": "no-such-provider"}) is True




def test_a_footprint_record_never_hides_the_benchmark_and_a_damaged_load_time_is_not_shown(monkeypatch, tmp_path):
    rows = {"gemma4:e4b": [
        {"num_ctx": 8192, "vram_mib": 3081, "tok_s_median": 99.9, "cold_load_s": -5, "measured_at": "2026-08-14",
         "source": "measured"},
        {"num_ctx": None, "load_s": 9.0, "measured_at": "2026-11-18", "source": "measured"}]}   # newer footprint
    got = _pc(monkeypatch, tmp_path, rows)["gemma4:e4b"]
    assert got["benchmark"]["vram_mib"] == 3081 and got["benchmark"]["cold_load_seconds"] is None
    assert got["switch"]["expected_seconds"] is None                     # -5 s is not an estimate


def test_the_cold_load_estimate_comes_from_the_newest_row_that_has_one_and_other_numbers_are_checked(monkeypatch, tmp_path):
    rows = {"gemma4:e4b": [
        {"num_ctx": 8192, "vram_mib": 3081, "tok_s_median": 99.0, "cold_load_s": 20.5, "measured_at": "2026-08-14",
         "source": "seed"},
        {"num_ctx": 32768, "vram_mib": "x", "tok_s_median": float("nan"), "measured_at": "2026-08-15", "source": "seed"}]}
    got = _pc(monkeypatch, tmp_path, rows)["gemma4:e4b"]
    assert got["switch"]["expected_seconds"] == 20.5 and "2026-08-14" in got["switch"]["basis"]
    assert got["benchmark"]["num_ctx"] == 32768 and got["benchmark"]["vram_mib"] is None
    assert got["benchmark"]["tokens_per_second"] is None            # NaN never reaches a device


def test_a_cloud_tag_model_is_blocked_in_local_only_mode_and_listed_cloud_to_the_phone(monkeypatch, tmp_path):
    cat = {"roles": {"orchestrator": [{"id": "gpt-oss:120b-cloud"}]},
           "models": [{"id": "gpt-oss:120b-cloud", "provider": "ollama-local", "provider_label": "Local (Ollama)",
                       "roles": ["orchestrator"], "local": True, "available": True, "needs_key": False}]}
    m = cat["models"][0]
    monkeypatch.setattr(ms, "_catalog", lambda: cat)
    monkeypatch.setattr(ms, "_settings", lambda: {"model_routing": {"mode": "local_only"}})
    monkeypatch.setattr(ms, "_local_busy_reason", lambda mid: None)
    monkeypatch.setattr(ms, "_live_local", lambda: set())
    row = {r["id"]: r for r in ms.slate()["models"]}["gpt-oss:120b-cloud"]
    assert row["local"] is False and row["can_serve"] is False and row["blocked"]["code"] == "local_only_mode"
    from agent_friday.tv_gateway import models as gw
    phone = {r["id"]: r for r in gw.rows(None, cat, {"model_routing": {"mode": "local_only"}})}["gpt-oss:120b-cloud"]
    assert phone["kind"] == "cloud"


def test_an_unreachable_cloud_tag_model_is_told_to_the_phone_as_offline_not_as_not_running_on_the_pc():
    from agent_friday.tv_gateway import models as gw
    tag = {"id": "gpt-oss:120b-cloud", "label": "gpt-oss cloud", "provider": "ollama-local",
           "provider_label": "Local (Ollama)", "local": True, "available": False, "needs_key": False}
    assert "offline or unreachable" in gw.why_unavailable(tag, {}) and "not running" not in gw.why_unavailable(tag, {})
