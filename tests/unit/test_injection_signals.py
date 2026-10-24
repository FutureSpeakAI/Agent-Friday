"""Finding instruction-shaped outside text, even disguised (services/injection_signals; BD-24 U2).

On a tree without the module each test fails at its fixture with the reason.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "prompt_injection"


@pytest.fixture
def sig():
    try:
        return importlib.import_module("agent_friday.services.injection_signals")
    except ImportError:
        pytest.fail("outside text is never checked for instructions: services/injection_signals does not exist")


HOSTILE = sorted(p.name for p in FIXTURES.iterdir() if p.name not in ("README.md", "disguised.json"))


@pytest.mark.parametrize("name", HOSTILE)
def test_every_fixture_medium_is_found(sig, name):
    assert sig.scan((FIXTURES / name).read_text(encoding="utf-8")), name


@pytest.mark.parametrize("variant,how", [("paraphrase", None), ("base64", "base64"), ("hex", "hex"),
                                         ("rot13", "rot13"), ("lookalike", None), ("spaced", "spaced"),
                                         ("split", "split")])
def test_a_disguise_is_seen_through_and_named(sig, variant, how):
    text = json.loads((FIXTURES / "disguised.json").read_text(encoding="utf-8"))[variant]
    found = sig.scan(text)
    assert found, variant
    if how:
        assert any(s.endswith(":" + how) for s in found), (variant, found)


@pytest.mark.parametrize("text", [
    "Hi Stephen, the council meeting moved to Thursday. Please send me the draft by Friday.",
    "Your package has shipped. Track it at https://ups.com/track?id=1Z999",
    "Ignore the noise from the construction next door, the interview went well.",
    "The new rules for parking take effect Monday.",
    "Can you forward this to Dana? Thanks, Mia",
    "Meeting notes: action items - Dana to send budget to finance@council.gov by Tuesday.",
    "The model must be retrained every quarter according to the paper.",
    "Run the numbers again before Friday.",
    "Session token: Q2LCAgc2Vzc2lvbiBpZDogOWY4ZTdkNmM1YjRhMzIxMA==",  # pragma: allowlist secret
    "See you Friday: bring the slides.",
    "You are now subscribed to our newsletter.",
    "Security notice for your account: we noticed a new sign-in.",
    "Important notice for customers: prices change.",
    "New rules: no parking on Sundays.",
    "Run the script to install dependencies.",
    "Forward this email to a friend at friend@example.com",
    "Please send your feedback to feedback@example.com",
    "Assistant: Sure, here is the summary.",
    "Developer mode is enabled on this device.",
    "Grant access to the shared folder for Mia.",
    "Delete all the old files from the shared drive before Friday.",
    "Please accept this invitation. Reply to this email to confirm. Contact support@example.com for help.",
    "Click 'Accept all cookies' to continue.",
    "Don't tell anyone about the surprise party.",
])
def test_ordinary_text_is_not_flagged(sig, text):
    assert sig.scan(text) == []


@pytest.mark.parametrize("text", ["\n" * 200_000, "SYSTEM" + " " * 200_000, ". \n" * 60_000,
                                  "a\n" * 100_000])
def test_a_hostile_page_of_blanks_cannot_stall_her(sig, text):
    import time
    t0 = time.monotonic()
    sig.scan(text)
    assert time.monotonic() - t0 < 5.0


def test_it_never_raises_and_reads_words_for_the_card(sig):
    assert sig.scan(None) == [] and isinstance(sig.scan(b"\xff\xfe"), list)
    words = sig.describe(["override:base64", "secrecy"])
    assert "ignore her rules" in words and "base64" in words
    assert sig.describe([]) == ""
