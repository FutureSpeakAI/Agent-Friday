"""PuTTY private keys are recognised by content, and a large encoded body costs
a bounded amount to scan. All key material is synthetic."""
from __future__ import annotations

import base64
import os

import pytest

from agent_friday.services import secret_patterns as sp
from agent_friday.services import sensitivity_classifier as sc

PPK = (
    "PuTTY-User-Key-File-3: ssh-ed25519\n"
    "Encryption: none\n"
    "Comment: synthetic\n"
    "Public-Lines: 2\n"
    "AAAAC3NzaC1lZDI1NTE5AAAAIFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE\n"
    "Private-Lines: 1\n"
    "AAAAIFAKEPRIVATELINEFAKEPRIVATELINEFAKEPRIVATELINE0123456789\n"
    "Private-MAC: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n"
)


@pytest.mark.parametrize("text", [
    PPK,
    PPK.replace("-3:", "-2:"),
    "notes:\n" + PPK,
    base64.b64encode(PPK.encode()).decode(),
    PPK.split("Private-Lines:")[1] and "Private-Lines: 1\nAAAAIFAKEPRIVATELINE\n",
])
def test_putty_key_is_key_material(text):
    assert sp.contains_key_material(text)
    assert sp.contains_secret(text)


def test_putty_key_is_not_tier_one():
    assert sc._regex_tier(PPK) == sc.Tier.SENSITIVE


def test_prose_about_putty_is_not_flagged():
    assert not sp.contains_key_material(
        "PuTTY is an SSH client; export your key with PuTTYgen.")



def test_key_inside_a_large_message_is_still_found():
    small = base64.b64encode(PPK.encode()).decode()
    blob = base64.b64encode(os.urandom(250_000)).decode()
    assert sp.contains_key_material("attachment:\n" + small + "\n\nmore:\n" + blob)


def _work_counter(monkeypatch):
    """Encoded characters actually handed to a decoder."""
    used = [0]

    def wrap(fn):
        def counted(run):
            used[0] += len(run)
            return fn(run)
        return counted

    monkeypatch.setattr(sp, "_DECODERS", tuple((rx, wrap(fn)) for rx, fn in sp._DECODERS))
    return used


def test_key_inside_one_oversized_encoded_run_is_found():
    secret = "AKIAQWERTYUIOPASDFGH"  # pragma: allowlist secret
    body = os.urandom(160_000) + (b"\naws_key = " + secret.encode() + b"\n")
    run = base64.b64encode(body).decode()
    assert len(run) > 200_000
    assert sp.contains_secret("attachment:\n" + run)


def test_key_at_the_head_and_tail_of_an_oversized_run_is_found():
    key = b"-----BEGIN PRIVATE KEY-----\nAAAAFAKEFAKEFAKEFAKE\n-----END PRIVATE KEY-----\n"  # pragma: allowlist secret
    for body in (key + os.urandom(300_000), os.urandom(300_000) + b"\n" + key):
        assert sp.contains_key_material(base64.b64encode(body).decode())


def test_decode_work_is_bounded_by_characters_not_seconds(monkeypatch):
    used = _work_counter(monkeypatch)
    blob = base64.b64encode(os.urandom(1_000_000)).decode()
    assert not sp.contains_secret(blob)
    assert used[0] <= sp._DECODE_BUDGET_CHARS + 8, used[0]
    assert sp._DECODE_BUDGET_CHARS <= 3_000_000


def test_a_key_at_either_end_of_a_run_larger_than_the_whole_budget_is_found():
    key = b"-----BEGIN PRIVATE KEY-----\nAAAAFAKEFAKEFAKEFAKE\n-----END PRIVATE KEY-----\n"  # pragma: allowlist secret
    big = os.urandom(sp._DECODE_BUDGET_CHARS)
    assert sp.contains_key_material(base64.b64encode(big + key).decode())
    assert sp.contains_key_material(base64.b64encode(key + big).decode())
