"""Routine background jobs send a slim job prompt, not the chat prompt.

The Front Page editor, the weekly digest and the afternoon briefing used to
send the whole chat system prompt (~16.7k tokens: persona, self-knowledge, the
capability index, the user model, vault context) to do a job whose real input
is the material it is handed. On an 8k local window that request was refused
before it started; on a cloud seat it was paid for every run.

services/job_prompt.py builds the slim prompt; these tests pin what it holds
and that the routine jobs' own entry points send it. The entry-point tests
stub the model at `_generate_text` (the seam the real calls go through) and
read the system prompt it was handed: on the old code it is the full chat
prompt, carrying the capability index and thousands of tokens.
"""
from __future__ import annotations

import json
import logging

import pytest

import agent_friday.core as core
from agent_friday.services.clock import now_local


@pytest.fixture
def job_prompt():
    # Imported per test, so the entry-point tests below fail on the old code by
    # their assertion (the full prompt was sent), not by an ImportError.
    from agent_friday.services import job_prompt as jp
    return jp

#: A line every full chat prompt carries (model_router: CAPABILITY_INDEX).
FULL_PROMPT_MARK = "== CAPABILITY DISCOVERY =="
#: A slim prompt is a few hundred tokens; the chat prompt is ~13-17k.
SLIM_TOKEN_BOUND = 600
AGENT = "NOVA TEST"            # stored in capitals, said "Nova Test"
SAID = "Nova Test"


@pytest.fixture
def named_agent(monkeypatch):
    real = core._load_settings

    def _settings():
        s = dict(real() or {})
        s["agent_name"] = AGENT
        return s
    monkeypatch.setattr(core, "_load_settings", _settings)


def _tokens(text):
    return len(text or "") // 4


# ── The helper ──────────────────────────────────────────────────────────────

def test_slim_prompt_has_no_persona_or_tool_catalogue(named_agent, job_prompt):
    jp = job_prompt.build(job_prompt.Job("Synthetic job", "Summarise the items."),
                          "ITEM ONE\nITEM TWO")
    assert jp.slim
    assert SAID in jp.system
    assert now_local().strftime("%Y-%m-%d") in jp.system
    assert "Summarise the items." in jp.system
    assert FULL_PROMPT_MARK not in jp.system
    assert "== SELF-KNOWLEDGE ==" not in jp.system
    assert "== USER MODEL ==" not in jp.system
    assert "ACTION PERMISSION POLICY" not in jp.system   # no tools, no policy
    assert jp.tools == []
    assert jp.messages == [{"role": "user", "content": "ITEM ONE\nITEM TWO"}]
    assert jp.estimated_tokens < SLIM_TOKEN_BOUND


def test_only_the_named_tools_ride_along(named_agent, job_prompt):
    jp = job_prompt.build(job_prompt.Job("Synthetic job"), "m",
                          tools=("search_email", "no_such_tool_xyz"))
    assert [t["name"] for t in jp.tools] == ["search_email"]
    # A job that can call a tool carries the action policy.
    assert "ACTION PERMISSION POLICY" in jp.system
    assert FULL_PROMPT_MARK not in jp.system


def test_each_build_logs_its_token_estimate(named_agent, job_prompt, caplog):
    with caplog.at_level(logging.INFO, logger="friday.job_prompt"):
        jp = job_prompt.build(job_prompt.Job("Synthetic job"), "material")
    lines = [r.getMessage() for r in caplog.records if r.name == "friday.job_prompt"]
    assert any("Synthetic job" in m and ("~%d tokens" % jp.estimated_tokens) in m
               for m in lines)


def test_turning_slim_off_sends_the_jobs_full_prompt(monkeypatch, job_prompt):
    monkeypatch.setattr(job_prompt, "enabled", lambda: False)
    jp = job_prompt.build(job_prompt.Job("Synthetic job"), "m",
                          full_system=lambda: "THE FULL PROMPT")
    assert not jp.slim and jp.system == "THE FULL PROMPT"


def test_the_setting_defaults_on():
    assert core.DEFAULT_SETTINGS["job_prompts"]["slim"] is True


# ── The routine jobs, through their own entry points ────────────────────────

def test_afternoon_briefing_sends_the_slim_prompt(named_agent, monkeypatch):
    import agent_friday.services.model_router as mr
    import agent_friday.services.news_engine as ne
    from agent_friday.services import scheduler
    seen = {}

    def _model(messages, system=None, **kw):
        seen["system"] = system
        seen["messages"] = messages
        return "briefing body"
    monkeypatch.setattr(mr, "_generate_text", _model)
    monkeypatch.setattr(mr, "_predict_route_provider", lambda **kw: "local")
    monkeypatch.setattr(ne, "_gather_live_briefing_context", lambda: "SYNTHETIC LIVE DATA")
    monkeypatch.setattr(ne, "_notify_briefing", lambda date_str: None)

    scheduler._afternoon_briefing_job()

    system = seen["system"]
    assert FULL_PROMPT_MARK not in system
    assert _tokens(system) < SLIM_TOKEN_BOUND
    assert SAID in system
    # The material still reaches the model, in the request.
    assert "SYNTHETIC LIVE DATA" in seen["messages"][0]["content"]


_POOL = [
    {"category": "AI/Tech", "source": "example.com", "score": 9,
     "title": "A thing happened", "snippet": "details", "url": "https://a/1",
     "color": "tech"},
    {"category": "Politics", "source": "example.org", "score": 7,
     "title": "Another thing", "snippet": "more", "url": "https://a/2",
     "color": "politics"},
]


def test_front_page_editor_sends_the_slim_prompt(named_agent, monkeypatch):
    import agent_friday.services.news_engine as ne
    seen = {}

    def _model(messages, system=None, **kw):
        seen["system"] = system
        return json.dumps({"lead_index": 0, "lead_note": "Why.", "headline": "A Day",
                           "section_context": {"AI/Tech": "a", "Politics": "b"}})
    monkeypatch.setattr(ne, "_generate_text", _model)
    monkeypatch.setattr(ne, "_predict_route_provider", lambda **kw: "local")
    monkeypatch.setattr(ne, "_gated_vault_control", lambda: None)
    monkeypatch.setattr(ne, "_answering_model", lambda before=None: "m")

    ed = ne._editorialize_front_page(_POOL, slot="morning")

    assert "degraded" not in ed
    system = seen["system"]
    assert FULL_PROMPT_MARK not in system
    assert _tokens(system) < SLIM_TOKEN_BOUND
    assert SAID in system


# ── The owner's News interests ride in the material ─────────────────────────

INTEREST_A, INTEREST_B = "Synthetic Beat Alpha", "Synthetic Beat Beta"
#: What the full chat prompt would carry from the vault and the user model.
#: None of it may reach a slim job's request.
VAULT_MARK = "SYNTHETIC-VAULT-NOTE-7731"
USER_MODEL_MARK = "SYNTHETIC-USER-MODEL-4410"


@pytest.fixture
def news_interests(monkeypatch, tmp_path):
    """Topic priorities and one switched-off section in the owner's News
    settings, and a full-prompt builder that would leak vault and user-model
    text if it were ever called."""
    import agent_friday.services.news_engine as ne
    real = core._load_settings

    def _settings():
        s = dict(real() or {})
        s["agent_name"] = AGENT
        s["news_priorities"] = [INTEREST_A, INTEREST_B]
        s["user_model"] = USER_MODEL_MARK
        return s
    monkeypatch.setattr(core, "_load_settings", _settings)
    prefs = tmp_path / "briefing_prefs.json"
    prefs.write_text(json.dumps({"categories_enabled": {"Politics": False}}), encoding="utf-8")
    monkeypatch.setattr(ne, "BRIEFING_PREFS_FILE", prefs)
    monkeypatch.setattr(ne, "_get_friday_system_prompt",
                        lambda *a, **k: "== USER MODEL ==\n%s\n%s" % (USER_MODEL_MARK, VAULT_MARK))
    monkeypatch.setattr(ne, "_gated_vault_control", lambda: VAULT_MARK)
    monkeypatch.setattr(ne, "_predict_route_provider", lambda **kw: "local")
    monkeypatch.setattr(ne, "_answering_model", lambda before=None: "m")
    return ne


def _assert_interests_and_nothing_private(seen):
    user = seen["messages"][0]["content"]
    assert "OWNER'S NEWS INTERESTS" in user
    assert "%s, %s" % (INTEREST_A, INTEREST_B) in user, "the priorities, in their order"
    assert "Sections they switched off: Politics" in user
    sent = seen["system"] + "\n" + user
    assert VAULT_MARK not in sent and USER_MODEL_MARK not in sent
    assert "== USER MODEL ==" not in sent


def test_the_front_page_editor_is_handed_the_owners_news_interests(news_interests, monkeypatch):
    ne = news_interests
    seen = {}

    def _model(messages, system=None, **kw):
        seen["system"], seen["messages"] = system, messages
        return json.dumps({"lead_index": 0, "lead_note": "Why.", "headline": "A Day",
                           "section_context": {"AI/Tech": "a", "Politics": "b"}})
    monkeypatch.setattr(ne, "_generate_text", _model)

    ne._editorialize_front_page(_POOL, slot="morning")

    _assert_interests_and_nothing_private(seen)


def test_the_weekly_digest_is_handed_the_owners_news_interests(news_interests, monkeypatch, tmp_path):
    ne = news_interests
    seen = {}

    def _model(messages, system=None, **kw):
        seen["system"], seen["messages"] = system, messages
        return json.dumps({"top_stories": [], "trends": [], "editorial": "A week."})
    monkeypatch.setattr(ne, "_generate_text", _model)
    monkeypatch.setattr(ne, "_gather_weekly_editions", lambda days=7: [])
    monkeypatch.setattr(ne, "WEEKLY_DIGESTS_DIR", tmp_path / "weekly")

    ne._generate_weekly_digest()

    _assert_interests_and_nothing_private(seen)


def test_no_news_interests_adds_no_block(monkeypatch, tmp_path):
    import agent_friday.services.news_engine as ne
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {"news_priorities": []})
    monkeypatch.setattr(ne, "BRIEFING_PREFS_FILE", tmp_path / "absent.json")
    assert ne._news_interests_block() == ""
