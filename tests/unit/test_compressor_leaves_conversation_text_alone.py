"""Compression never rewrites conversation text (what the user typed and what Friday replied). SYNTHETIC TEXT ONLY.

Headroom's text compressor strips function words and prefixes a note such as "[519 words compressed to 404 words ... hash=...]".
Applied to the user and assistant turns that go back to the model, the model reads its own earlier replies in that clipped
style, copies the style and the note into its next reply, and the reply the person sees reads like a telegram. The rule:
the conversation's own text is never compressed; tool output and other bulky content still is.
What each check bites on (ContextCompressor.compress / compress_new, Headroom replaced by a deterministic stand-in that does
what the real one does to prose):
  1. a reply full of articles and copulas comes back exactly as written, with no compression note anywhere in it;
  2. the user's own words are untouched the same way;
  3. a tool result inside the same list is still compressed (the saving is real, only conversation text is protected);
  4. content blocks: text blocks are kept as written, tool_use/tool_result blocks keep the compressed form;
  5. when the compressor hands back a list that does not line up with the input (a message dropped or merged), nothing is
     compressed at all: no message is guessed at;
  6. compress_new (what the tool loop calls after each round) obeys the same rule;
The check is shown failing on the mistake it exists for by running this file on the tree without the fix (every test but the
tool-result one fails there: the reply comes back clipped and carries the note).
"""
import re
import types

import pytest

from agent_friday.pipeline.context_compressor import ContextCompressor

REPLY = ("You are a journalist, after all, and the story that you are telling is about the city that was built on the river. "
         "It is the kind of place where the people who live there have been telling the same stories for a very long time. ") * 6
QUESTION = "Tell me about the river city and the people who live there, and what it was like when the harbor was built."
TOOL_OUTPUT = ("The result of the search is that the page was found and the text of the page is as follows: the harbor was "
               "built in the year that the river was moved, and it is the oldest part of the town. ") * 8
_FUNCTION_WORDS = re.compile(r"\b(?:the|a|an|is|are|was|were|be|been|am)\b\s*", re.I)


def _squeeze(text):
    """What the real compressor does to prose: drop function words, prefix its note."""
    words = len(text.split())
    clipped = _FUNCTION_WORDS.sub("", text)
    return "[%d words compressed to %d words. Retrieve more: hash=3f9a1c] %s" % (words, len(clipped.split()), clipped)


def _squeezing_headroom(messages, model=None):
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            c = _squeeze(c)
        elif isinstance(c, list):
            blocks = []
            for b in c:
                b = dict(b)
                if b.get("type") == "text":
                    b["text"] = _squeeze(b["text"])
                if b.get("type") == "tool_result" and isinstance(b.get("content"), str):
                    b["content"] = _squeeze(b["content"])
                blocks.append(b)
            c = blocks
        out.append(dict(m, content=c))
    return types.SimpleNamespace(messages=out, tokens_before=1000, tokens_after=400, tokens_saved=600)


@pytest.fixture
def compressor(monkeypatch):
    c = ContextCompressor(enabled=True, min_tokens_to_compress=1)
    monkeypatch.setattr(c, "_load_headroom", lambda: _squeezing_headroom)
    return c


def _history():
    return [
        {"role": "user", "content": QUESTION},
        {"role": "assistant", "content": REPLY},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": TOOL_OUTPUT}]},
    ]


def _conversation_text(messages):
    out = []
    for m in messages:
        c = m["content"]
        if isinstance(c, str):
            out.append(c)
        else:
            out += [b["text"] for b in c if b.get("type") == "text"]
    return out


def test_a_reply_full_of_articles_and_copulas_comes_back_exactly_as_written(compressor):
    out = compressor.compress(_history())
    assert out[1]["content"] == REPLY
    assert "words compressed" not in out[1]["content"] and "hash=" not in out[1]["content"]


def test_the_users_own_words_are_untouched_too(compressor):
    assert compressor.compress(_history())[0]["content"] == QUESTION


def test_a_tool_result_in_the_same_list_is_still_compressed(compressor):
    out = compressor.compress(_history())
    block = out[2]["content"][0]
    assert block["content"].startswith("[") and "words compressed" in block["content"], "tool output is still squeezed"
    assert compressor.get_stats()["tokens_saved"] > 0


def test_content_blocks_keep_their_text_and_squeeze_only_tool_blocks(compressor):
    history = [{"role": "assistant", "content": [{"type": "text", "text": REPLY},
                                                  {"type": "tool_use", "id": "t1", "name": "search", "input": {"q": "x"}}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": TOOL_OUTPUT},
                                            {"type": "text", "text": QUESTION}]}]
    out = compressor.compress(history)
    assert out[0]["content"][0]["text"] == REPLY and out[0]["content"][1] == history[0]["content"][1]
    assert out[1]["content"][1]["text"] == QUESTION
    assert "words compressed" in out[1]["content"][0]["content"]


def test_a_list_that_does_not_line_up_is_not_compressed_at_all(compressor, monkeypatch):
    def drops_a_message(messages, model=None):
        r = _squeezing_headroom(messages)
        r.messages = r.messages[1:]
        return r
    monkeypatch.setattr(compressor, "_load_headroom", lambda: drops_a_message)
    history = _history()
    assert compressor.compress(history) is history
    assert compressor.get_stats()["tokens_saved"] == 0


def test_the_tool_loop_path_obeys_the_same_rule(compressor):
    history = _history()
    out = compressor.compress_new(history, 1)
    assert out[1]["content"] == REPLY and "words compressed" not in "".join(_conversation_text(out))

