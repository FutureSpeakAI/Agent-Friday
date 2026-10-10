"""The voice front's prompt plus tool contract always leaves room for a reply
and for history, on every front model.

run_turn refuses with "The local voice context is full" when the room left
after the system prompt, the history and the tool declarations is under 128
tokens, and it drops history first whenever the room is under the reply
allowance. A front whose standing prompt and contract already fill the window
can therefore not take a single turn. The budget below is the invariant: the
room after the real front prompt and the real curated contract, measured the
way run_turn measures it, covers the largest spoken reply plus a few turns of
history, for every model in FRONT_MODELS.
"""
import json

import pytest

import agent_friday.routes.voice as rv
from agent_friday.services import voice_front as vf
from agent_friday.services.voice_engine import build_voice_tool_contract

#: The largest spoken reply a voice turn asks for (voice_delivery.reply_budget
#: tops out at 1400) and the history a first few exchanges need.
REPLY_ALLOWANCE = 1400
HISTORY_RESERVE = 1500

#: A saved personality of realistic length (the persona opens AND closes the
#: prompt, so it is paid for twice).
PERSONALITY = (
    "You are Friday: calm, perceptive, quietly funny. You speak with quiet "
    "confidence, you disagree plainly when you have a reason, and you never "
    "perform enthusiasm. You like precise words, dry understatement and a good "
    "question. When the user is tired you get shorter and kinder; when they are "
    "excited you match them without gushing. You keep running jokes you have "
    "earned and drop the ones that stopped landing."
)


def _room(window, system, user="hi", tools=None):
    convo = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return window - len(json.dumps({"messages": convo, "tools": tools or []})) // 4 - 512


def _fat_context():
    """A standing prompt the size of the real one: a 36K-char SELF.md, a large
    user model, heuristics and honesty sections."""
    return "\n\n".join([
        "== AGENT PERSONALITY ==\nYou are Friday, a calm, perceptive AI partner.",
        "== ASIMOV cLAWS (compiled, non-negotiable) ==\n" + "A law. " * 60,
        "== USER MODEL ==\nName: Sam. " + "Prefers plain answers; works late. " * 160,
        "== LEARNED HEURISTICS ==\n" + "When asked X, do Y. " * 120,
        "== SELF-KNOWLEDGE ==\n# SELF.md\n" + "I am Agent Friday. " * 1900,
        "== HONEST LIMITS ==\n" + "I cannot do this. " * 200,
        "== CAPABILITY DISCOVERY ==\n" + "Use discover_capabilities. " * 80,
        "== THIS SEAT ==\n" + "Seat facts. " * 110,
    ])


@pytest.fixture(params=["real", "fat"])
def prompt_source(request, monkeypatch):
    monkeypatch.setattr(rv, "_get_voice_style_prompt", lambda: PERSONALITY)
    if request.param == "fat":
        from agent_friday.services import model_router
        monkeypatch.setattr(model_router, "_get_friday_system_prompt",
                            lambda **kw: _fat_context())
    return request.param


@pytest.fixture(scope="module")
def contract():
    return build_voice_tool_contract()


@pytest.mark.parametrize("model", sorted(vf.FRONT_MODELS))
def test_every_front_model_has_room_for_a_reply_and_history(model, contract, prompt_source):
    spec = vf.FRONT_MODELS[model]
    settings = {"voice_style_prompt": PERSONALITY, "voice_response_depth": "detailed"}
    system = rv._build_front_system_prompt(settings, contract, spec["label"])
    room = _room(spec["ctx"], system, tools=contract["tools"])
    need = REPLY_ALLOWANCE + HISTORY_RESERVE
    assert room >= need, (
        f"{model}: ctx {spec['ctx']}, system "
        f"{len(json.dumps(system)) // 4} tok, contract "
        f"{len(json.dumps(contract['tools'])) // 4} tok, room {room} < {need}")


@pytest.mark.parametrize("model", sorted(vf.FRONT_MODELS))
def test_a_first_turn_runs_on_every_front_model(model, contract, prompt_source, monkeypatch):
    spec = vf.FRONT_MODELS[model]
    system = rv._build_front_system_prompt({}, contract, spec["label"])
    sent = []

    class Resp:
        def raise_for_status(self):
            pass

        def iter_lines(self, decode_unicode=True):
            yield "data: " + json.dumps({"choices": [{"delta": {"content": "Hello."},
                                                      "finish_reason": None}]})
            yield "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]})
            yield "data: [DONE]"

        def close(self):
            pass

    def fake_post(self, body, stream):
        sent.append(body)
        return Resp()
    monkeypatch.setattr(vf.FrontSeat, "_post", fake_post)
    seat = vf.FrontSeat()
    seat.model = model
    out = seat.run_turn(system, [{"role": "user", "content": "hello"}], contract,
                        max_tokens=REPLY_ALLOWANCE)
    assert out == "Hello."
    assert sent[0]["max_tokens"] >= REPLY_ALLOWANCE, sent[0]["max_tokens"]


def test_the_rules_that_must_never_be_budgeted_away_stay(contract, prompt_source):
    system = rv._build_front_system_prompt({}, contract, "Qwen3-1.7B")
    for must in ("ACTION PERMISSION POLICY",            # authority and approvals
                 "Never state that an action succeeded",  # honesty about results
                 "EVIDENCE FIRST",                       # news honesty
                 "YOUR DEEPER MIND",                     # the handoff
                 "USING YOUR TOOLS",                    # tool-use rules
                 "THE TOOLS YOU HOLD IN THIS CONVERSATION",
                 "never claim to be, or imitate, any real journalist",
                 "personality never changes permissions"):
        assert must in system, must


def test_the_owner_and_her_character_survive_the_budget(contract, prompt_source):
    system = rv._build_front_system_prompt({}, contract, "Qwen3-1.7B")
    assert PERSONALITY[:60] in system
    if prompt_source == "fat":
        assert "== USER MODEL ==" in system and "Name: Sam." in system
        assert "== SELF-KNOWLEDGE ==" not in system, "SELF.md is the deeper mind's"
    assert "your deeper mind holds your full memory" in system


def test_the_front_digest_leaves_the_identity_document_to_the_deeper_mind():
    from agent_friday.services import voice_context_digest as d
    ctx = _fat_context()
    out = d.front_digest(ctx, 1200)
    assert "SELF-KNOWLEDGE" not in out and "SELF.md" not in out
    assert out.index("AGENT PERSONALITY") < out.index("USER MODEL")
    assert len(out) <= 1200 * 4
    # the main digest still carries it
    assert "SELF-KNOWLEDGE" in d.digest(ctx)


def test_a_long_personality_is_cut_at_a_sentence_before_it_is_said_twice():
    long = "She is dry and precise. " * 80
    cut = rv._front_persona_style(long)
    assert len(cut) <= rv.FRONT_PERSONA_MAX_CHARS and cut.endswith("precise.")
    assert rv._front_persona_style(PERSONALITY) == PERSONALITY


def test_the_budget_follows_the_window_and_the_contract(contract):
    tools = contract["tools"]
    small = vf.system_budget_tokens(20000, tools)
    big = vf.system_budget_tokens(24000, tools)
    assert big - small == 4000
    assert vf.system_budget_tokens(16384, tools) > 2500
    assert vf.system_budget_tokens(4096, tools) == 0
