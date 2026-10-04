"""Passages stay within the encoder's window and keep the blocks they cover."""
from __future__ import annotations

from agent_friday.services.library import structure


def _sec(texts):
    return {"blocks": [{"kind": "para", "text": t, "ord": i, "page": 1} for i, t in enumerate(texts)]}


def test_no_passage_exceeds_the_limit_even_for_one_enormous_paragraph():
    huge = ". ".join(f"Sentence number {i} says something plain" for i in range(400)) + "."
    unbroken = "x" * 5000
    for texts in ([huge], [unbroken], ["short"] * 200, [huge, "tail"]):
        for p in structure.build_passages(_sec(texts)):
            assert p["chars"] <= structure.PASSAGE_MAX_CHARS, p["chars"]
            assert p["text"].strip()


def test_small_paragraphs_pack_together_and_name_their_blocks():
    ps = structure.build_passages(_sec(["a" * 300, "b" * 300, "c" * 300, "d" * 300]))
    assert [p["blocks"] for p in ps] == [[0, 1], [2, 3]]


def test_no_text_is_lost_when_a_long_paragraph_is_split():
    huge = " ".join(f"word{i}." for i in range(1000))
    joined = " ".join(p["text"] for p in structure.build_passages(_sec([huge])))
    assert joined.split() == huge.split()


def test_profiles_are_short_deterministic_and_unmodelled():
    secs = structure.build_sections(
        [{"kind": "heading", "text": "Lease", "level": 1, "ord": 0, "page": 1},
         {"kind": "para", "text": "The tenant shall pay rent monthly. More follows here.", "ord": 1, "page": 1}], "Lease")
    a = structure.document_profile("Lease", secs)
    assert a == structure.document_profile("Lease", secs)
    assert len(a) <= structure.PROFILE_CHARS
    assert "\n" not in a
