"""The key-shaped-input guard: one list of shapes, shared by server and browser.

Every fake credential below is assembled at runtime so no literal in this file
has a credential's shape: the commit scanner reads test files too.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from agent_friday.services import secret_shapes as ss

ROOT = Path(__file__).resolve().parents[2]


def _k(prefix, body_len=40, alphabet="a1B2c3D4e5"):
    return prefix + (alphabet * 10)[:body_len]


CASES = [
    (_k("sk-" + "ant-"), "provider:anthropic"),
    (_k("sk-" + "or-v1-"), "provider:openrouter"),
    (_k("sk-" + "proj-"), "provider:openai"),
    ("AI" + "za" + ("x1Y2" * 9)[:35], "provider:google-gemini"),
    (_k("gh" + "p_", 36), "connector:github"),
    (_k("xox" + "b-", 30), "connector:slack"),
    (_k("h" + "f_", 34), "provider:huggingface"),
    ("S" + "K" + "0123456789abcdef" * 2, "twilio"),
    ("s" + "k_" + "0123456789abcdef" * 3, "provider:elevenlabs"),
    ("f" + "c-" + "0123456789abcdef" * 2, "provider:firecrawl"),
    ("123456789:" + ("AbCdEfGhIj" * 4)[:35], "channel:telegram"),
]


@pytest.mark.parametrize("value,target", CASES)
def test_each_shape_is_recognised_and_names_where_it_belongs(value, target):
    hit = ss.looks_like_secret(value)
    assert hit is not None, value[:6]
    assert hit["target"] == target
    assert value not in repr(hit), "the detector must never echo the value"


@pytest.mark.parametrize("value", [
    "Hi, I'm Sam",
    "I work at Acme and like short answers.",
    "https://example.org/some/long/path/that/goes/on/for/a/while/indeed",
    "My handle is @samexample",
    "Casual, please. No exclamation marks!",
    "skip",
])
def test_ordinary_answers_are_not_mistaken_for_keys(value):
    assert ss.looks_like_secret(value) is None


def test_a_key_inside_a_sentence_is_still_caught():
    assert ss.looks_like_secret("here you go " + _k("sk-" + "ant-") + " thanks")


def test_a_long_bare_token_is_caught_generically():
    hit = ss.looks_like_secret("Zq" + "7Kx9" * 9)
    assert hit and hit["id"] == "generic"


def test_every_pattern_compiles_the_same_in_javascript_terms():
    """No inline flags, lookbehind or named groups: the browser compiles these
    strings with new RegExp()."""
    for entry in ss.for_client()["shapes"] + [ss.for_client()["generic"]]:
        p = entry["pattern"]
        assert "(?i" not in p and "(?<" not in p and "(?P" not in p, p
        re.compile(p)


def test_every_shape_the_commit_scanner_knows_is_in_the_shared_list():
    """The pre-commit scanner keeps its own copy because it runs outside the
    package. This holds the two together."""
    spec = importlib.util.spec_from_file_location(
        "security_scan_for_test", ROOT / ".githooks" / "security_scan.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ours = {pat for _id, _l, pat, _t in ss.SHAPES}
    theirs = {rx.pattern for _name, rx, _check in mod.SHAPE_RULES}
    missing = theirs - ours
    assert not missing, "shapes the scanner knows but the chat guard does not: %s" % missing
