"""The phone hears the answer as it is written, never a tool round's narration (KG latency fix 3).

Measured on 2026-11-26: the Pixel's "first word" on a knowledge question was 54.2 s, which was the
whole turn. owner_turn relayed only the final reply because the desktop's deltas can carry the
narration a model writes before it calls a tool. The cloud loop now says where each round starts
and the moment it calls a tool (model_router.ROUND_SINK), so the relay holds a round's text until it
is clearly an answer (COMMIT_CHARS, no tool call), then streams it. Synthetic fixtures only.
"""
import pytest

from agent_friday.tv_gateway import owner_turn
from tests.unit.test_owner_turn_stream import _clean, ask, client, events, phone, spoken, wire  # noqa: F401

ANSWER = ("The core philosophy of the Northwind look is signal over noise: every screen should read like a "
          "field report, with one clear claim, the evidence under it, and nothing decorative that does not "
          "carry meaning. Colour is used for state, never for mood, and type stays plain.")


def _pieces(text, n=6):
    step = max(1, len(text) // n)
    return [text[i:i + step] for i in range(0, len(text), step)]


def _cloud_turn(narration="Let me check your notes.", answer=ANSWER, final=None):
    frames = [{"started": True},
              {"round": {"kind": "start", "round": 1}},
              {"delta": narration},
              {"round": {"kind": "tool_use", "round": 1}},
              {"tool": {"name": "search_wiki"}},
              {"round": {"kind": "start", "round": 2}}]
    frames += [{"delta": p} for p in _pieces(answer)]
    frames.append({"done": True, "payload": {"response": final if final is not None else answer,
                                             "model": "claude-opus-5-5", "seat": "cloud"}})
    return frames


def _text_events(r):
    return [e for e in events(r) if e.get("type") == "content_block_delta"]


def test_the_answer_streams_to_the_phone_in_pieces(client, wire):
    wire(_cloud_turn())
    r = ask(client, phone())
    assert len(_text_events(r)) >= 2, "the answer arrived as one block at the end of the turn"
    assert spoken(r) == ANSWER


def test_a_tool_round_s_narration_never_reaches_the_phone(client, wire):
    wire(_cloud_turn(narration="Checking your notes for the Northwind file now."))
    r = ask(client, phone())
    assert "Checking your notes" not in r.get_data(as_text=True)
    assert spoken(r) == ANSWER


def test_a_short_answer_is_sent_whole_at_the_end(client, wire):
    wire(_cloud_turn(answer="Signal over noise."))
    r = ask(client, phone())
    assert spoken(r) == "Signal over noise." and len(_text_events(r)) == 1


def test_a_placeholder_stops_the_stream_and_the_final_answer_is_sent(client, wire):
    raw = "[PII:PERSON_1] " + ANSWER
    wire(_cloud_turn(answer=raw, final="Libby " + ANSWER))
    r = ask(client, phone())
    assert "[PII:" not in r.get_data(as_text=True)
    assert spoken(r).endswith("Libby " + ANSWER)


def test_without_round_signals_only_the_final_answer_is_sent(client, wire):
    """A local seat or any loop that does not announce rounds: exactly as before."""
    frames = [{"started": True}] + [{"delta": p} for p in _pieces(ANSWER)] + [
        {"done": True, "payload": {"response": ANSWER, "model": "bonsai2:27b", "seat": "local"}}]
    wire(frames)
    r = ask(client, phone())
    assert len(_text_events(r)) == 1 and spoken(r) == ANSWER


def test_a_streamed_start_that_the_final_answer_does_not_continue_is_followed_by_the_final(client, wire):
    wire(_cloud_turn(final="A different final answer, as saved on the PC."))
    r = ask(client, phone())
    assert spoken(r).endswith("A different final answer, as saved on the PC.")


def test_a_placeholder_split_over_deltas_never_reaches_the_phone(client, wire):
    """The critic's probe: "[" in one delta, "PII:email:3f2a]" in the next, after the stream is live."""
    frames = _cloud_turn()[:-1] + [{"delta": " Ask ["}, {"delta": "PII:email:3f2a] for details."},
                                   {"done": True, "payload": {"response": ANSWER + " Ask sam@example.test for details.",
                                                              "model": "claude-opus-5-5", "seat": "cloud"}}]
    wire(frames)
    r = ask(client, phone())
    assert "PII:" not in r.get_data(as_text=True)
    assert spoken(r).endswith("Ask sam@example.test for details.")


def test_a_think_tag_split_over_deltas_stops_the_stream(client, wire):
    frames = _cloud_turn()[:-1] + [{"delta": " <thi"}, {"delta": "nk>private reasoning"},
                                   {"done": True, "payload": {"response": ANSWER, "model": "claude-opus-5-5",
                                                              "seat": "cloud"}}]
    wire(frames)
    r = ask(client, phone())
    assert "private reasoning" not in r.get_data(as_text=True) and spoken(r) == ANSWER


def test_deltas_after_the_round_ends_are_not_streamed(client, wire):
    """A fallback model publishing deltas after the cloud round closed is held to the final reply."""
    frames = _cloud_turn()[:-1] + [{"round": {"kind": "end", "round": 2}},
                                   {"delta": " FALLBACK DRAFT TEXT"},
                                   {"done": True, "payload": {"response": ANSWER, "model": "claude-opus-5-5",
                                                              "seat": "cloud"}}]
    wire(frames)
    r = ask(client, phone())
    assert "FALLBACK DRAFT" not in r.get_data(as_text=True) and spoken(r) == ANSWER
