"""Higgsfield dispatch — the other half of "never offer what nothing can call".

The picker gains ~60 Higgsfield models. These tests pin that a picked model
actually routes, that a paid-for generation is never silently lost, and that
cost is reported honestly (a real number or None, never a guess).
"""
from __future__ import annotations

import pytest

from agent_friday.services import higgsfield_generate as hg

JOB = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
URL = "https://cdn.higgsfield.ai/out/%s.png" % JOB


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(hg.time, "sleep", lambda *_a: None)


# ── Catalogue membership gates dispatch ──────────────────────────────────────

def test_only_enumerated_ids_are_claimed(monkeypatch):
    """An id the catalogue has never carried must NOT be claimed — the caller
    has to fall through to its other providers."""
    import agent_friday.services.model_discovery as md
    monkeypatch.setattr(md, "cached_model_ids",
                        lambda name: ["nano_banana_pro", "seedance_2_0"])
    assert hg.is_higgsfield_model("nano_banana_pro") is True
    assert hg.is_higgsfield_model("gemini-nano-banana-2") is False
    assert hg.is_higgsfield_model("") is False
    assert hg.is_higgsfield_model(None) is False


def test_membership_never_raises_without_a_cache(monkeypatch):
    import agent_friday.services.model_discovery as md
    monkeypatch.setattr(md, "cached_model_ids",
                        lambda name: (_ for _ in ()).throw(OSError("no cache")))
    assert hg.is_higgsfield_model("nano_banana_pro") is False


# ── Job id / URL extraction ──────────────────────────────────────────────────

def test_job_ids_found_at_any_depth():
    assert hg._job_ids({"data": {"jobs": [{"job_id": JOB}]}}) == [JOB]
    assert hg._job_ids(f"submitted job {JOB} ok") == [JOB]
    assert hg._job_ids({"nope": "not-a-uuid"}) == []


def test_job_ids_are_deduped_and_ordered():
    other = "11111111-2222-4333-8444-555555555555"
    got = hg._job_ids({"a": {"job_id": JOB}, "b": [{"id": other}, {"id": JOB}]})
    assert got == [JOB, other]


def test_collect_urls_prefers_raw_over_preview():
    """minUrl/thumbnailUrl are downscaled previews — filing one as the
    creation substitutes something worse for what was paid for."""
    out = []
    hg._collect_urls({"generation": {"status": "completed", "results": {
        "rawUrl": URL, "minUrl": URL + "?min", "thumbnailUrl": URL + "?t"}}}, out)
    assert out[0] == URL


# ── Cost ─────────────────────────────────────────────────────────────────────

def test_cost_preflight_does_not_submit(monkeypatch):
    seen = {}

    def fake_call(tool, args, timeout=120.0):
        seen["tool"], seen["params"] = tool, args["params"]
        return {"credits": 22.5}

    monkeypatch.setattr(hg, "_call", fake_call)
    assert hg.estimate_credits("video", "seedance_2_0", "a cat") == 22.5
    assert seen["tool"] == "generate_video"
    assert seen["params"]["get_cost"] is True


def test_unknown_cost_is_none_never_a_guess(monkeypatch):
    monkeypatch.setattr(hg, "_call", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("estimate endpoint down")))
    assert hg.estimate_credits("image", "nano_banana_pro", "x") is None


def test_nested_credits_are_found(monkeypatch):
    monkeypatch.setattr(hg, "_call",
                        lambda *a, **k: {"data": {"cost": {"credits": 7}}})
    assert hg.estimate_credits("image", "gpt_image_2") == 7


# ── End to end ───────────────────────────────────────────────────────────────

def _wire(monkeypatch, *, submit, wait=None, download=None):
    def fake_call(tool, args, timeout=120.0):
        if args.get("params", {}).get("get_cost"):
            return {"credits": 2}
        if tool == "jobs_wait":
            return wait if wait is not None else {"all_terminal": True}
        return submit

    monkeypatch.setattr(hg, "_call", fake_call)
    from agent_friday.services import creative_store
    monkeypatch.setattr(
        creative_store, "download_output",
        download or (lambda url, **k: {"ok": True, "path": "/tmp/out.png"}))


def test_happy_path_lands_bytes_on_disk(monkeypatch):
    _wire(monkeypatch,
          submit={"job_id": JOB},
          wait={"all_terminal": True,
                "jobs": [{"generation": {"status": "completed",
                                         "results": {"rawUrl": URL}}}]})
    res = hg.generate("image", "a cat", model="nano_banana_pro")
    assert res["status"] == "ok"
    assert res["files"] == [{"path": "/tmp/out.png", "url": URL}]
    assert res["credits"] == 2
    assert res["provider"] == "higgsfield"


def test_free_trial_allowance_is_never_spent_silently(monkeypatch):
    """`use_unlim` is pinned False: an allowance is the user's to spend
    deliberately, not something a settings picker consumes for them."""
    seen = {}

    def fake_call(tool, args, timeout=120.0):
        params = args["params"]
        if params.get("get_cost"):
            return {"credits": 2}
        seen.update(params)
        return {"job_id": JOB, "generation": {"status": "completed",
                                              "results": {"rawUrl": URL}}}

    monkeypatch.setattr(hg, "_call", fake_call)
    from agent_friday.services import creative_store
    monkeypatch.setattr(creative_store, "download_output",
                        lambda url, **k: {"ok": True, "path": "/tmp/o.png"})
    hg.generate("image", "a cat", model="nano_banana_pro")
    assert seen["use_unlim"] is False


def test_unlim_question_is_surfaced_not_answered(monkeypatch):
    _wire(monkeypatch, submit={"unlim_choice": "spend 1 free generation?"})
    res = hg.generate("image", "a cat", model="nano_banana_pro")
    assert res["status"] == "error"
    assert "unlim_choice" in res


def test_finished_but_unsaved_reports_url_and_cost(monkeypatch):
    """Paid for, produced, not saved — the reply must say all three so the
    file can be rescued before the seven-day deletion clock runs out."""
    _wire(monkeypatch,
          submit={"job_id": JOB},
          wait={"all_terminal": True,
                "jobs": [{"generation": {"status": "completed",
                                         "results": {"rawUrl": URL}}}]},
          download=lambda url, **k: {"ok": False, "error": "disk full"})
    res = hg.generate("image", "a cat", model="nano_banana_pro")
    assert res["status"] == "error"
    assert URL in res["reason"]
    assert res["credits"] == 2
    assert res["job_ids"] == [JOB]


def test_submit_failure_is_a_status_not_an_exception(monkeypatch):
    monkeypatch.setattr(hg, "_call", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("[mcp error] connector down")))
    res = hg.generate("image", "a cat", model="nano_banana_pro")
    assert res["status"] == "unavailable"
    assert "connector down" in res["reason"]


def test_poll_budget_exhaustion_keeps_the_job_id(monkeypatch):
    _wire(monkeypatch, submit={"job_id": JOB},
          wait={"all_terminal": False, "jobs": []})
    monkeypatch.setattr(hg, "_MAX_WAITS", 2)
    res = hg.generate("image", "a cat", model="nano_banana_pro")
    assert res["status"] == "error"
    assert JOB in res["reason"]


def test_unsupported_kind_is_refused():
    assert hg.generate("hologram", "x", model="m")["status"] == "error"


def test_count_is_clamped_to_the_documented_range(monkeypatch):
    seen = {}

    def fake_call(tool, args, timeout=120.0):
        params = args["params"]
        if params.get("get_cost"):
            return {"credits": 1}
        seen.update(params)
        return {"generation": {"status": "completed",
                               "results": {"rawUrl": URL}}}

    monkeypatch.setattr(hg, "_call", fake_call)
    from agent_friday.services import creative_store
    monkeypatch.setattr(creative_store, "download_output",
                        lambda url, **k: {"ok": True, "path": "/tmp/o.png"})
    hg.generate("image", "a cat", model="nano_banana_pro", n=99)
    assert seen["count"] == 4


def test_constraints_come_from_the_enumerated_catalogue(monkeypatch):
    import agent_friday.services.model_discovery as md
    monkeypatch.setattr(md, "cached_models", lambda name: ([
        {"id": "seedance_2_0", "constraints": {"duration": {"default": 5}}},
    ], False))
    assert hg.model_constraints("seedance_2_0") == {"duration": {"default": 5}}
    assert hg.model_constraints("nano_banana_pro") == {}
