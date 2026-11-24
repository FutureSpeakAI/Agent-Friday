"""pipeline/conversation_text.keep_conversation_text: conversation text is never rewritten, over every small case. SYNTHETIC TEXT ONLY.

The rule: given the message list before compression and the one the compressor returned, the result has every user and assistant
text exactly as in the original; a tool_result block, a tool_use block and a message of any other role keep the compressed form;
a returned list that does not line up (length, roles) gives None. Checked for every combination of message role and content shape
(a string, text blocks, mixed blocks, blocks that no longer line up) rather than a few chosen ones.
"""
import itertools

import pytest

from agent_friday.pipeline.conversation_text import keep_conversation_text

TEXT = "You are a journalist, after all, and the story is the one that was told."
CLIPPED = "[12 words compressed to 7 words. hash=ab12] You journalist, after all, story one told."


def _shapes(clipped):
    """(content before, content after compression) pairs, for each content shape."""
    text, squeezed = TEXT, CLIPPED if clipped else TEXT
    tool_in = {"type": "tool_result", "tool_use_id": "t1", "content": "long output " * 20}
    tool_out = dict(tool_in, content="short") if clipped else tool_in
    use = {"type": "tool_use", "id": "t1", "name": "search", "input": {"q": "x"}}
    return {
        "string": (text, squeezed),
        "text_blocks": ([{"type": "text", "text": text}], [{"type": "text", "text": squeezed}]),
        "mixed": ([{"type": "text", "text": text}, tool_in, use], [{"type": "text", "text": squeezed}, tool_out, use]),
        "blocks_lost": ([{"type": "text", "text": text}, tool_in], [{"type": "text", "text": squeezed}]),
    }


ROLES = ("user", "assistant", "system")


@pytest.mark.parametrize("role,shape", list(itertools.product(ROLES, ("string", "text_blocks", "mixed", "blocks_lost"))))
def test_every_role_and_shape_keeps_conversation_text_and_nothing_else(role, shape):
    before, after = _shapes(True)[shape]
    original, compressed = [{"role": role, "content": before}], [{"role": role, "content": after}]
    out = keep_conversation_text(original, compressed)
    assert out is not None and len(out) == 1 and out[0]["role"] == role
    if role in ("user", "assistant"):
        content = out[0]["content"]
        if shape == "string":
            assert content == TEXT
        else:
            texts = [b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"]
            assert texts == [TEXT], "the conversation text is as written"
            if shape == "mixed":
                assert content[1]["content"] == "short", "a tool_result keeps the compressed form"
    else:
        assert out[0]["content"] == after, "another role is not conversation: the compressor's form stands"


def test_a_list_that_does_not_line_up_gives_none():
    one = [{"role": "user", "content": TEXT}]
    two = [{"role": "user", "content": TEXT}, {"role": "assistant", "content": TEXT}]
    assert keep_conversation_text(two, one) is None and keep_conversation_text(one, two) is None
    assert keep_conversation_text(one, [{"role": "assistant", "content": CLIPPED}]) is None
    assert keep_conversation_text(one, "not a list") is None and keep_conversation_text(one, [None]) is None
    assert keep_conversation_text(one, one) == one


def test_the_inputs_are_not_modified_and_uncompressed_input_comes_back_equal():
    original = [{"role": "assistant", "content": [{"type": "text", "text": TEXT}]}]
    compressed = [{"role": "assistant", "content": [{"type": "text", "text": CLIPPED}]}]
    out = keep_conversation_text(original, compressed)
    assert compressed[0]["content"][0]["text"] == CLIPPED and out[0]["content"][0]["text"] == TEXT
    assert keep_conversation_text(original, original) == original
