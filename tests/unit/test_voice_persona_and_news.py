"""Friday's live voice keeps her character, sizes her answers to the moment,
delivers news on evidence, and knows what the user's vault setting allows.

From one half-hour Gemini Live session: the saved persona (a bored, dark,
funny one) went flat as soon as tool-heavy news turns began, because it
appeared once, as the first line of a long prompt, and three generic hints
later in the same prompt asked for the opposite ("warm but professional",
"composed, professional, plainspoken", "be reasonably brief"). Replies ran
short for the same reason. And with the vault open to cloud models by the
user's own setting, the session was still told it had "no path to the user's
notes" whenever the local model was not warm.
"""
import inspect

from google.genai import types

import agent_friday.core as core
import agent_friday.routes.voice as rv
from agent_friday.services import voice_persona as vp
from agent_friday.voice_personality import VoicePersonality, DEFAULT_VOICE_STYLE

STYLE = "Be as bored, funny, dark, and cynically loving as a deadpan sitcom intern."
SRC = inspect.getsource(rv).replace("\r\n", "\n")


# ── vault awareness ─────────────────────────────────────────────────────

def test_an_open_vault_is_used_not_refused():
    for ready in (True, False):
        r = vp.vault_rule(vault_open=True, local_model_ready=ready)
        assert "open" in r and "search_wiki" in r and "withheld" in r
        assert "Never tell them you cannot reach their notes" in r
        assert "local-only" not in r
    assert "ask_friday" in vp.vault_rule(True, True)
    assert "ask_friday" not in vp.vault_rule(True, False)


def test_a_locked_vault_is_explained_not_ignored():
    r = vp.vault_rule(vault_open=False, local_model_ready=True)
    assert "local-only" in r and "ask_friday" in r
    r = vp.vault_rule(vault_open=False, local_model_ready=False)
    assert "local-only" in r and "not running" in r and "ask_friday" not in r


def _manifest_with_mind(ready):
    from agent_friday.services import voice_manifest as vm
    m = vm.VoiceManifest.__new__(vm.VoiceManifest)
    m.mode = "gemini"
    m.snapshot_stage = lambda k: {"ready": ready if k == "mind" else True,
                                  "effective": {}, "proof": {"state": "proved"}, "reason": ""}
    return m


def test_the_self_description_follows_the_vault_setting():
    unready = _manifest_with_mind(False)
    opened = unready.describe_for_model(vault_open=True)
    assert "no path to" not in opened and "search_wiki" in opened and "open" in opened
    locked = unready.describe_for_model(vault_open=False)
    assert "local-only" in locked and "no path to their private vault" in locked
    # Default (every other caller) is the protective reading.
    assert unready.describe_for_model() == locked


def test_the_live_session_reads_the_real_setting():
    assert "_vault_open = not _vault_local_only()" in SRC
    assert "describe_for_model(vault_open=_vault_open)" in SRC
    assert "vault_rule(_vault_open, _mind_ready)" in SRC


# ── persona ─────────────────────────────────────────────────────────────

def test_the_persona_opens_and_closes_the_instruction():
    body = "BODY " * 5000
    out = vp.compose_live_instruction(STYLE, body)
    assert out.startswith(vp.PERSONA_HEADER)
    assert out.rstrip().endswith(vp.persona_block(STYLE).rstrip())
    assert out.count(STYLE) == 2
    assert "outranks any generic tone hint" in out
    assert vp.compose_live_instruction("", body) == body


def test_the_persona_survives_the_action_policy_seal():
    from agent_friday.services.action_policy import seal_system_prompt
    sealed = seal_system_prompt(vp.compose_live_instruction(STYLE, "body"), "test")
    assert sealed.count(STYLE) == 2 and vp.PERSONA_HEADER in sealed


def test_contradicting_chat_hints_are_removed_from_voice():
    settings = {"response_length": "standard", "communication_style": "professional"}
    ctx = core._settings_system_prefix(settings, "") + "\nREST OF CONTEXT"
    brief = core.RESPONSE_LENGTH_HINTS["standard"]
    tone = core.COMMUNICATION_STYLE_HINTS["professional"]
    assert brief in ctx and tone in ctx
    with_persona = vp.strip_text_chat_hints(ctx, keep_tone=False)
    assert brief not in with_persona and tone not in with_persona
    assert "REST OF CONTEXT" in with_persona
    # No persona saved: the chosen tone still applies; length is voice's own.
    without = vp.strip_text_chat_hints(ctx, keep_tone=True)
    assert brief not in without and tone in without
    assert "strip_text_chat_hints(full_ctx, keep_tone=not live_style)" in SRC


def test_the_mood_no_longer_overrides_the_persona():
    p = VoicePersonality()
    with_persona = p.build_system_instruction("BASE", mood="neutral", persona=True)
    assert DEFAULT_VOICE_STYLE not in with_persona and "Keep your character" in with_persona
    assert "warm but professional" not in with_persona
    # Without a persona the mood still sets the style.
    assert DEFAULT_VOICE_STYLE in p.build_system_instruction("BASE", mood="neutral")
    assert "persona=bool(live_style)" in SRC


def test_every_leg_carries_the_persona_and_compression():
    """A resumed leg is built from the same kwargs as the first: same
    instruction, same compression; only the handle is added."""
    sys_text = vp.compose_live_instruction(STYLE, "context")
    kwargs = dict(
        response_modalities=[types.Modality.AUDIO],
        system_instruction=types.Content(parts=[types.Part(text=sys_text)]),
        context_window_compression=types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow()),
    )
    first = rv._leg_config(types, kwargs)
    resumed = rv._leg_config(types, kwargs, handle="h-123")
    for cfg in (first, resumed):
        assert cfg.system_instruction.parts[0].text.count(STYLE) == 2
        assert cfg.context_window_compression is not None
    assert resumed.session_resumption.handle == "h-123"
    assert first.session_resumption is None
    assert "_cfg_try = _leg_config(types, per_model_kwargs, _with_handle)" in SRC


def test_the_bridge_repeats_the_persona_across_reconnects_and_turns():
    note = vp.persona_reminder(STYLE)
    assert STYLE in note and "do not answer" in note
    assert vp.persona_reminder("") == ""
    due = [n for n in range(1, 20) if vp.persona_due(n)]
    assert due == [vp.PERSONA_REPIN_EVERY_TURNS * k
                   for k in range(1, 20 // vp.PERSONA_REPIN_EVERY_TURNS + 1)
                   if vp.PERSONA_REPIN_EVERY_TURNS * k < 20]
    assert "_persona_note = persona_reminder(live_style)" in SRC
    # Sent after a reconnect, and between turns, never as a turn of its own.
    assert SRC.count('turns={"role": "user", "parts": [{"text": _persona_note}]},') == 2
    assert "_repin = persona_due(_voiced_turns[0])" in SRC


# ── length ──────────────────────────────────────────────────────────────

def test_length_follows_the_moment_and_the_users_cues():
    r = vp.VOICE_LENGTH_RULE
    for cue in ("tell me more", "keep it short", "news", "explanation", "story"):
        assert cue in r
    assert "CRITICAL LENGTH RULE" not in SRC
    assert "+ VOICE_LENGTH_RULE +" in SRC


# ── news ────────────────────────────────────────────────────────────────

def test_the_evidence_constitution():
    c = vp.NEWS_EVIDENCE_CONSTITUTION
    for must in ("source of every claim", "confirmed", "alleged", "analysis",
                 "speculation as fact", "evidence is thin", "sources disagree",
                 "Correction", "judge for themselves", "no-fabrication"):
        assert must in c, must


def test_anchor_style_is_a_style_not_an_impersonation():
    a = vp.VOICE_ANCHOR_RULES
    assert "context" in a and "connect related stories" in a and "analysis" in a
    assert "never claim to be, or imitate, any real journalist" in a
    assert "never in the facts" in a
    for name in ("Jennings", "Maddow", "Plaza"):
        assert name not in a and name not in vp.WRITTEN_NEWS_RULES


def test_every_news_surface_carries_the_rules():
    import agent_friday.routes.news as news
    import agent_friday.services.news_engine as ne
    import agent_friday.services.scheduler as sch
    assert "+ VOICE_ANCHOR_RULES +" in SRC
    assert "WRITTEN_NEWS_RULES" in inspect.getsource(news.generate_briefing)
    assert "WRITTEN_NEWS_RULES" in inspect.getsource(sch._afternoon_briefing_job)
    prompt, _ = ne._build_anchor_briefing({"headline": "H", "lead": {}})
    assert vp.NEWS_EVIDENCE_CONSTITUTION in prompt
