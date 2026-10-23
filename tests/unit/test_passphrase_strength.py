"""The bundle passphrase strength meter (services/passphrase_strength.py).

A Friday Bundle carries the vault passphrase and the governance key inside its
encryption, so its passphrase is checked wherever one is set: the Doctor page,
the command line and the uninstall prompt (which runs the command line).
"""
from __future__ import annotations

import pytest


def _ps():
    from agent_friday.services import passphrase_strength
    return passphrase_strength

WEAK = ["xxxxxxxxxxxxxxxxxxxx", "password1234", "qwertyuiop123456", "abcdefghijklmnop",
        "correct horse battery staple", "correct horse battery staple 42", "summer2024summer",
        "my friday backup 2024", "iloveyou12345", "aaaaaaaaaaaaaaaa", "short",
        # words mode and mixed strings
        "aaaa bbbb cccc dddd", "abcd efgh ijkl mnop", "qwer tyui asdf zxcv", "zzzz yyyy xxxx wwww",
        "abc abd abe abf", "dog cat sun hat", "P@ssw0rd2024!!", "a1b2c3d4e5f6g7", "1q2w3e4r5t6y7u",
        "!@#$%^&*()_+", "L3tm31n2023",
        # repeated units, shifted walks, stepped digits
        "Aa1!Aa1!Aa1!", "Zz9(Zz9(Zz9(", "Ab!Ab!Ab!Ab!", "12!@12!@12!@", "P4ssw0rdP4ssw0rd",
        "13579135791357913579!", "01/01/2000 01/01/2000", "!Q@W#E$R%T^Y", "1234 5678 9012 3456",
        # look-alike spellings of words, password-list words
        "Tr0ub4dor&3xy", "Dr4g0nSl4y3r!", "M0nk3yB4n4n4!", "Ch4rl13B0y!!", "J3nn1f3r1985!",
        "L1v3rp00l!23", "Sh4d0wM4st3r", "W3lc0m3H0m3!", "S3cur1ty2024!", "B4s3b4ll!F4n",
        "Tr0ub4dor&3Tr0ub4dor&3", "monkey dragon shadow master", "John Smith 1980", "Kitty!Cat!123",
        # words in another case, names with years
        "PASSWORDpassword", "DRAGONdragon99", "PassWORD2024word", "BUTTERflyBUTTER", "Schmidt1985!!",
        "Schwarzenegger1!", "worldwide2024!!"]
STRONG = ["orbit lantern pepper tundra violet 42", "violet anchor drizzle mammoth",
          "kL9#vQ2!mZ7@pX4$", "ci clean machine walnut harbour quartz"]


@pytest.mark.parametrize("pw", WEAK)
def test_weak_passphrases_are_refused_with_a_reason(pw):
    ps = _ps()
    a = ps.assess(pw)
    assert not a["ok"] and a["score"] <= 2, a
    assert ps.refusal(pw) and a["feedback"]


@pytest.mark.parametrize("pw", STRONG)
def test_strong_passphrases_pass(pw):
    ps = _ps()
    a = ps.assess(pw)
    assert a["ok"] and a["score"] >= 3, a
    assert ps.refusal(pw) is None


def test_the_meter_never_returns_the_passphrase():
    a = _ps().assess("orbit lantern pepper tundra violet 42")
    assert "orbit" not in repr(a)


def test_a_bundle_refuses_a_weak_passphrase(tmp_path, monkeypatch):
    from agent_friday.services import friday_bundle as fb
    home = tmp_path / "home"
    (home / "core_memory").mkdir(parents=True)
    (home / "core_memory" / "ME.md").write_text("x", encoding="utf-8")
    with pytest.raises(fb.BundleError) as e:
        fb.create(tmp_path / "out.fridaybundle", "password1234", home=home)
    assert "guess" in str(e.value) or "weak" in str(e.value)
    assert not (tmp_path / "out.fridaybundle").exists()


def test_the_command_line_refuses_a_weak_passphrase_from_the_environment(tmp_path, monkeypatch, capsys):
    import sys
    from agent_friday import cli
    monkeypatch.setenv("FRIDAY_BUNDLE_PASSPHRASE", "xxxxxxxxxxxxxxxx")
    monkeypatch.setattr(sys, "argv", ["friday", "bundle", "create", "--out", str(tmp_path / "x.fridaybundle")])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 1
    assert not (tmp_path / "x.fridaybundle").exists()


def test_a_very_long_input_is_refused_quickly():
    """The run check is linear: a long input is assessed quickly."""
    import time
    t = time.time()
    a = _ps().assess("ab" * 50000)
    assert time.time() - t < 1.0 and not a["ok"]
    t = time.time()
    _ps()._run_length("ab" * 5000)
    assert time.time() - t < 0.5


def test_random_strings_and_random_words_still_pass():
    """Reading look-alike digits as letters never turns random strings into
    "words"; random letters are not words."""
    import random
    import string
    ps = _ps()
    r = random.Random(20261022)
    al = string.ascii_letters + string.digits
    ok = sum(ps.assess("".join(r.choice(al) for _ in range(16)))["ok"] for _ in range(400))
    assert ok >= 360, ok
    words = ["orbit", "lantern", "pepper", "tundra", "violet", "anchor", "drizzle", "mammoth",
             "quartz", "harbour", "walnut", "glacier", "saddle", "trumpet", "meadow"]
    assert all(ps.assess(" ".join(r.sample(words, 4)))["ok"] for _ in range(100))
