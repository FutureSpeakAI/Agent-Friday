"""Unit tests for the content-pipeline chat/agent tools (spec §11 tail, §10.2).

Covers: registration (CLAUDE_TOOLS / CLAUDE_TOOL_HANDLERS / TOOL_RINGS — all
four Ring 2 per §11), the natural-language publish-time parser, the §10.2
voice path (create → compose → schedule → Queue deep link in ONE call),
optimal-time fallback, the schedule tool's compose-if-needed rail, the status
tool's drilldown + queue overview + HELD messaging, and the repurpose spread.
All offline — the composer's LLM/gate rails are monkeypatched stubs.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import agent as agent_mod  # noqa: E402
from agent_friday.services import content_composer as cc  # noqa: E402
from agent_friday.services import content_pipeline as cpl  # noqa: E402

REWRITTEN = "[[tool-test-rewrite]]"
FUTURE = "2026-12-01T09:00:00Z"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    """Hermetic store + composer: tmp DB/voice cards, stubbed LLM/gate rails."""
    monkeypatch.setattr(cpl, "DB_PATH", tmp_path / "content_pipeline.db")
    monkeypatch.setattr(cpl, "CONTENT_DIR", tmp_path / "content")
    monkeypatch.setattr(cpl, "PUBLISH_LOG", tmp_path / "content" / "publish_log.jsonl")
    monkeypatch.setattr(cc, "VOICE_CARDS_DIR", tmp_path / "voice_cards")
    monkeypatch.setattr(cc, "_gate_prompt", lambda prompt, provider=None: prompt)
    monkeypatch.setattr(cc, "_friday_prompt", lambda: "SYSTEM-PROMPT-STUB")
    monkeypatch.setattr(cc, "_generate", lambda messages, system=None, **kw: REWRITTEN)
    monkeypatch.setattr(cc, "_pinned_tags", lambda platform: [])
    yield


def _call(name, inp):
    out = agent_mod.CLAUDE_TOOL_HANDLERS[name](inp)
    assert isinstance(out, str)
    return out


def _call_json(name, inp):
    out = json.loads(_call(name, inp))
    assert out.get("status") == "ok", out
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  Registration — the §11 contract
# ─────────────────────────────────────────────────────────────────────────────

TOOL_NAMES = ("content_create_post", "content_schedule_post",
              "content_post_status", "content_repurpose")


class TestRegistration:
    def test_tools_registered(self):
        names = {t["name"] for t in agent_mod.CLAUDE_TOOLS}
        for n in TOOL_NAMES:
            assert n in names
            assert n in agent_mod.CLAUDE_TOOL_HANDLERS

    def test_all_four_ring_2(self):
        # Spec §11: Ring 2, governance-gated like all network tools.
        for n in TOOL_NAMES:
            assert agent_mod.TOOL_RINGS[n] == 2

    def test_schemas_required_fields(self):
        by_name = {t["name"]: t for t in agent_mod.CLAUDE_TOOLS}
        assert by_name["content_create_post"]["input_schema"]["required"] == [
            "body", "platforms"]
        assert by_name["content_schedule_post"]["input_schema"]["required"] == [
            "post_id"]
        # status + repurpose have no required fields (overview / body-or-id).
        assert "required" not in by_name["content_post_status"]["input_schema"]
        assert "required" not in by_name["content_repurpose"]["input_schema"]


# ─────────────────────────────────────────────────────────────────────────────
#  Publish-time parsing (parsed time of "optimal/parsed", §10.2)
# ─────────────────────────────────────────────────────────────────────────────

class TestParseWhen:
    def test_iso_passthrough(self):
        assert agent_mod._content_parse_when(
            "2026-12-01T09:00:00Z", "UTC") == FUTURE

    def test_tomorrow_morning_utc(self):
        got = agent_mod._content_parse_when("tomorrow morning", "UTC")
        want = (datetime.now(timezone.utc) + timedelta(days=1)).strftime(
            "%Y-%m-%dT09:00:00Z")
        assert got == want

    def test_tonight_is_20h_and_future(self):
        got = agent_mod._content_parse_when("tonight", "UTC")
        dt = cpl._parse_instant(got)
        assert dt.hour == 20 and dt > datetime.now(timezone.utc)

    def test_in_two_hours(self):
        got = cpl._parse_instant(agent_mod._content_parse_when("in 2 hours", "UTC"))
        want = datetime.now(timezone.utc) + timedelta(hours=2)
        assert abs((got - want).total_seconds()) < 300

    def test_weekday_with_clock_time(self):
        got = cpl._parse_instant(agent_mod._content_parse_when("friday 3pm", "UTC"))
        assert got.weekday() == 4 and got.hour == 15
        assert got > datetime.now(timezone.utc)

    def test_unparseable_returns_none(self):
        assert agent_mod._content_parse_when("whenever feels right", "UTC") is None
        assert agent_mod._content_parse_when("", "UTC") is None


# ─────────────────────────────────────────────────────────────────────────────
#  content_create_post — the §10.2 voice path
# ─────────────────────────────────────────────────────────────────────────────

class TestCreatePost:
    def test_draft_only_composes_targets(self):
        out = _call_json("content_create_post", {
            "body": "Shipping day. The pipeline is live.",
            "platforms": ["linkedin", "bluesky"]})
        assert out["post_status"] == "DRAFT"
        assert out["queue_link"] == f"/?workspace=content&tab=queue&post={out['post_id']}"
        post = cpl.get_post(out["post_id"])["post"]
        assert post["status"] == "DRAFT"
        assert {t["platform"] for t in post["targets"]} == {"linkedin", "bluesky"}
        assert all(t["adapted_body"] for t in post["targets"])

    def test_voice_path_one_call_to_queue_link(self):
        # "Friday, post this to LinkedIn and Bluesky tomorrow morning."
        out = _call_json("content_create_post", {
            "body": "Big announcement — details inside.",
            "platforms": ["linkedin", "bluesky"],
            "publish_at": "tomorrow morning", "timezone": "UTC"})
        assert out["post_status"] == "SCHEDULED"
        assert out["time_source"] == "parsed"
        want = (datetime.now(timezone.utc) + timedelta(days=1)).strftime(
            "%Y-%m-%dT09:00:00Z")
        assert out["publish_at"] == want
        assert out["queue_link"] in out["message"]
        post = cpl.get_post(out["post_id"])["post"]
        assert post["status"] == "SCHEDULED"
        assert all(t["publish_at"] == want for t in post["targets"])
        assert all(t["adapted_body"] for t in post["targets"])

    def test_optimal_time_resolves_future_slot(self):
        out = _call_json("content_create_post", {
            "body": "Slot me at the best time.",
            "platforms": ["linkedin"], "optimal_time": True})
        assert out["post_status"] == "SCHEDULED"
        assert out["time_source"] == "optimal"
        dt = cpl._parse_instant(out["publish_at"])
        assert dt is not None and dt > datetime.now(timezone.utc)

    def test_unparseable_time_falls_back_to_optimal_with_warning(self):
        out = _call_json("content_create_post", {
            "body": "Sometime nice please.",
            "platforms": ["linkedin"],
            "publish_at": "whenever feels right"})
        assert out["post_status"] == "SCHEDULED"
        assert out["time_source"] == "optimal"
        assert any("could not parse" in w for w in out.get("warnings", []))

    def test_missing_body_and_platforms_error(self):
        assert "'body' is required" in _call("content_create_post",
                                             {"platforms": ["linkedin"]})
        assert "'platforms' is required" in _call("content_create_post",
                                                  {"body": "text"})


# ─────────────────────────────────────────────────────────────────────────────
#  content_schedule_post
# ─────────────────────────────────────────────────────────────────────────────

class TestSchedulePost:
    def test_composes_then_schedules_explicit_iso(self):
        # Draft made outside the tool: targets have no adapted bodies yet.
        post = cpl.create_post(title="T", body="Raw body",
                               platforms=["linkedin"])["post"]
        assert not post["targets"][0]["adapted_body"]
        out = _call_json("content_schedule_post", {
            "post_id": post["id"], "publish_at": FUTURE})
        assert out["publish_at"] == FUTURE
        assert out["time_source"] == "parsed"
        fresh = cpl.get_post(post["id"])["post"]
        assert fresh["status"] == "SCHEDULED"
        assert fresh["targets"][0]["adapted_body"]
        assert fresh["targets"][0]["publish_at"] == FUTURE

    def test_no_time_defaults_to_optimal(self):
        post = cpl.create_post(body="B", platforms=["linkedin"])["post"]
        out = _call_json("content_schedule_post", {"post_id": post["id"]})
        assert out["time_source"] == "optimal"
        assert cpl._parse_instant(out["publish_at"]) > datetime.now(timezone.utc)

    def test_unknown_post_errors(self):
        assert "not found" in _call("content_schedule_post",
                                    {"post_id": "post_nope"})
        assert "'post_id' is required" in _call("content_schedule_post", {})


# ─────────────────────────────────────────────────────────────────────────────
#  content_post_status
# ─────────────────────────────────────────────────────────────────────────────

class TestPostStatus:
    def test_single_post_drilldown(self):
        created = _call_json("content_create_post", {
            "body": "Status me.", "platforms": ["linkedin", "bluesky"],
            "publish_at": FUTURE})
        out = _call_json("content_post_status", {"post_id": created["post_id"]})
        assert out["post_status"] == "SCHEDULED"
        assert {t["platform"] for t in out["targets"]} == {"linkedin", "bluesky"}
        assert all(t["status"] == "PENDING" for t in out["targets"])
        assert "held_note" not in out

    def test_held_target_surfaces_note(self):
        created = _call_json("content_create_post", {
            "body": "Hold me.", "platforms": ["linkedin"],
            "publish_at": FUTURE})
        tid = cpl.get_post(created["post_id"])["post"]["targets"][0]["id"]
        assert cpl.set_target_status(tid, "HELD")["ok"]
        out = _call_json("content_post_status", {"post_id": created["post_id"]})
        assert out["targets"][0]["status"] == "HELD"
        assert "release" in out["held_note"]

    def test_queue_overview_lists_upcoming(self):
        created = _call_json("content_create_post", {
            "body": "Queue me.", "platforms": ["linkedin"],
            "publish_at": FUTURE})
        out = _call_json("content_post_status", {})
        assert out["queue_link"] == "/?workspace=content&tab=queue"
        rows = [r for r in out["upcoming"] if r["post_id"] == created["post_id"]]
        assert rows and rows[0]["platform"] == "linkedin"
        assert rows[0]["publish_at"] == FUTURE

    def test_unknown_post_errors(self):
        assert "not found" in _call("content_post_status", {"post_id": "post_x"})


# ─────────────────────────────────────────────────────────────────────────────
#  content_repurpose
# ─────────────────────────────────────────────────────────────────────────────

class TestRepurpose:
    def test_default_text_spread(self):
        out = _call_json("content_repurpose", {
            "body": "A long editorial worth spinning out everywhere."})
        assert "linkedin" in out["spread"] and "twitter" in out["spread"]
        assert out["compose_link"].endswith(out["post_id"])
        post = cpl.get_post(out["post_id"])["post"]
        assert post["status"] == "DRAFT"                 # drafts, not scheduled
        assert post["source"]["kind"] == "repurpose"
        assert {t["platform"] for t in post["targets"]} == set(out["spread"])
        assert all(t["adapted_body"] for t in post["targets"])

    def test_custom_spread_and_source_post(self):
        src = cpl.create_post(title="Origin", body="Origin body",
                              platforms=["linkedin"])["post"]
        out = _call_json("content_repurpose", {
            "post_id": src["id"], "platforms": ["bluesky", "mastodon"]})
        assert out["spread"] == ["bluesky", "mastodon"]
        post = cpl.get_post(out["post_id"])["post"]
        assert post["body"] == "Origin body"
        assert post["source"]["ref"] == src["id"]

    def test_needs_body_or_post_id(self):
        assert "pass 'body' text or the 'post_id'" in _call(
            "content_repurpose", {})
        assert "not found" in _call("content_repurpose", {"post_id": "post_x"})
