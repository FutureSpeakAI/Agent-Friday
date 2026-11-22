"""The reply a person sees, and the transcript behind it, are exactly what Friday wrote. SYNTHETIC TEXT ONLY.

Through the real chat route with the real ContextCompressor (Headroom replaced by a stand-in that clips function words and
prefixes a "[N words compressed ...]" note, as the real one does to prose):
  1. turn one's reply is returned to the person exactly as the model wrote it;
  2. on the next turn the model is sent that reply exactly as written: no clipped words, no compression note, so it has no
     clipped style to copy into the reply after it;
  3. the same holds for the person's own earlier message;
  4. the reply to the second turn is also exactly as written, and nothing in what the person sees carries the note.
The unit rule is in tests/unit/test_compressor_leaves_conversation_text_alone.py.
"""
import re
import types

from tests.api.test_eval_weekend_plan_calendar_text import (  # noqa: F401 - fixtures and the scripted model
    _model, _turn, cloud_only, _fresh_confirmations)

FIRST_MESSAGE = "Tell me about the river city and the people who live there, and what it was like when the harbor was built."
REPLY = ("You are a journalist, after all, and the story that you are telling is about the city that was built on the river. "
         "It is the kind of place where the people who live there have been telling the same stories for a very long time. ") * 4
REPLY = REPLY.strip()  # the chat route trims trailing space; the rule under test is about words, not whitespace
SECOND_REPLY = "And the harbor is as old as the rest of the town, and it was the first thing that was built there."
_FUNCTION_WORDS = re.compile(r"\b(?:the|a|an|is|are|was|were|be|been|am)\b\s*", re.I)


def _squeezing_compressor(monkeypatch):
    from agent_friday.pipeline.context_compressor import ContextCompressor

    def squeeze(text):
        clipped = _FUNCTION_WORDS.sub("", text)
        return "[%d words compressed to %d words. Retrieve more: hash=3f9a1c] %s" % (
            len(text.split()), len(clipped.split()), clipped)

    def headroom(messages, model=None):
        asked.append(len(messages))
        out = []
        for m in messages:
            c = m.get("content")
            if isinstance(c, str):
                c = squeeze(c)
            elif isinstance(c, list):
                c = [dict(b, text=squeeze(b["text"])) if b.get("type") == "text" else b for b in c]
            out.append(dict(m, content=c))
        return types.SimpleNamespace(messages=out, tokens_before=2000, tokens_after=800, tokens_saved=1200)

    asked = []
    comp = ContextCompressor(enabled=True, min_tokens_to_compress=1)
    comp.asked = asked
    monkeypatch.setattr(comp, "_load_headroom", lambda: headroom)
    import agent_friday.routes.chat as chat_mod
    monkeypatch.setattr(chat_mod, "_get_context_compressor", lambda cfg: comp)
    return comp


def _text_of(content):
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def test_the_reply_and_the_history_the_model_is_sent_are_exactly_as_written(cloud_only, monkeypatch):  # noqa: F811 - pytest fixture
    comp = _squeezing_compressor(monkeypatch)
    _model(monkeypatch, call_tools=False, final=REPLY)
    first = _turn(cloud_only, FIRST_MESSAGE)
    assert first["response"] == REPLY and first["friday_msg"]["text"] == REPLY

    nxt = _model(monkeypatch, call_tools=False, final=SECOND_REPLY)
    second = _turn(cloud_only, "And is the harbor as old as the rest of the town?")
    assert comp.asked, "the compressor was never asked: this test would prove nothing"
    sent = nxt.sent[0]["messages"]
    assistant = [_text_of(m["content"]) for m in sent if m["role"] == "assistant"]
    user = [_text_of(m["content"]) for m in sent if m["role"] == "user"]
    assert REPLY in assistant or any(REPLY in a for a in assistant), \
        "the model was shown its own earlier reply clipped: %r" % assistant[-1][:120]
    assert FIRST_MESSAGE in user or any(FIRST_MESSAGE in u for u in user)
    assert not any("words compressed" in t or "hash=" in t for t in assistant + user), \
        "a compression note reached the history the model reads"
    assert second["response"] == SECOND_REPLY and "words compressed" not in second["response"]
