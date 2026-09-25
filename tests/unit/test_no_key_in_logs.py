"""No part of a provider key may reach a log line.

Every Gemini Live connect printed the first ten characters of the Gemini key to
stderr, and the key resolver logged eight more at INFO; the stderr file is kept
for weeks. Log lines now say whether a key is present and where it came from,
never any character of it (routing.provider_descriptors.key_presence).

Two checks: the code paths that handle the Gemini key are run with a fake key
while every log record and all stdout/stderr are captured; and the whole source
tree is scanned for a slice of anything named like a key, token or secret, so a
new preview anywhere fails here rather than in a log file.
"""
import logging
import pathlib
import re

import pytest

import agent_friday.core as core
from agent_friday.routing.provider_descriptors import key_presence, mask_key

FAKE_KEY = "AQ.FAKEfakeKEYforLOGtests1234567890abcdef"  # pragma: allowlist secret
OTHER_KEY = "AIzaFAKEsecondKEYforLOGtests0987654321"     # pragma: allowlist secret
SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "agent_friday"


def _leaks(text: str, key: str, n: int = 4) -> list:
    """Every substring of `key` at least `n` long that appears in `text`."""
    return sorted({key[i:i + n] for i in range(len(key) - n + 1) if key[i:i + n] in text})


def test_key_presence_says_nothing_about_the_key():
    assert key_presence(FAKE_KEY) == "present"
    assert key_presence("") == "MISSING" and key_presence(None) == "MISSING"
    assert _leaks(key_presence(FAKE_KEY), FAKE_KEY) == []


def test_resolving_and_replacing_the_gemini_key_logs_no_part_of_it(monkeypatch, caplog, capsys):
    from agent_friday.services import voice_engine as ve
    monkeypatch.setattr(core, "GEMINI_API_KEY", OTHER_KEY)
    monkeypatch.setattr(ve, "validate_gemini_key",
                        lambda k, force=False: (k == FAKE_KEY, "ok" if k == FAKE_KEY else "bad"),
                        raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    # The resolver short-circuits under FRIDAY_TESTING; the real path is the
    # one that logs. Every source it reads is pinned to a fake here: the
    # environment above, and the Windows registry read below.
    monkeypatch.delenv("FRIDAY_TESTING", raising=False)
    monkeypatch.setattr(ve, "_read_windows_user_env_key", lambda: "", raising=False)
    monkeypatch.setattr(ve, "_load_settings", lambda: {}, raising=False)
    caplog.set_level(logging.DEBUG)
    try:
        ve.resolve_gemini_key(update_core=True)
    except TypeError:
        ve.resolve_gemini_key()
    out = capsys.readouterr()
    text = caplog.text + out.out + out.err
    assert "GEMINI_API_KEY replaced" in caplog.text or "refreshed" in caplog.text, (
        "the key-replacement path did not run, so this test proved nothing")
    assert _leaks(text, FAKE_KEY) == [] and _leaks(text, OTHER_KEY) == [], text[-800:]


def test_the_final_voice_error_names_the_source_not_the_key(monkeypatch, caplog, capsys):
    from agent_friday.routes import voice as V
    monkeypatch.setattr(core, "GEMINI_API_KEY", FAKE_KEY)
    for valid in (True, False):
        monkeypatch.setattr(V, "validate_gemini_key",
                            lambda k, force=False, _v=valid: (_v, "detail"), raising=False)
        caplog.set_level(logging.DEBUG)
        msg = V._compose_final_voice_error(
            [("gemini-3.8-live", None, "auth", "1008 auth")], "launcher script")
        out = capsys.readouterr()
        text = msg + caplog.text + out.out + out.err
        assert _leaks(text, FAKE_KEY) == [], text[-600:]
        assert "launcher script" in msg


# Slices of names that look like secrets but are not: each with its reason.
REVIEWED = {
    ("routes/federation.py", "sender_pubkey"): "a PUBLIC key, logged to identify the peer",
    ("services/federation_transport.py", "_signing_key"): "key material passed to a KDF, not logged",
    ("services/studio_files.py", "key"): "a content hash used as a cache path",
    ("core/__init__.py", "token"): "a regex token (word), not a credential",
    ("services/judgment_gate.py", "token"): "a regex token (word), not a credential",
}
_SLICE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[['\"][A-Za-z_]+['\"]\])?\s*\)?\[\s*:\s*\d+\s*\]")
_SECRETISH = re.compile(r"(key|token|secret|password|passwd)", re.I)


def test_no_source_file_slices_a_secret():
    found = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for m in _SLICE.finditer(line):
                name = m.group(1)
                if not _SECRETISH.search(name) or name.lower() in ("keys", "keywords"):
                    continue
                if (rel, name) in REVIEWED:
                    continue
                found.append("%s:%d  %s" % (rel, lineno, line.strip()[:120]))
    assert not found, ("a secret-like value is sliced (a key preview?). Use key_presence() "
                       "in logs, or add a reviewed exception with its reason:\n" + "\n".join(found))


@pytest.mark.parametrize("key", [FAKE_KEY, OTHER_KEY])
def test_the_screen_mask_still_shows_only_four(key):
    assert mask_key(key) == "…" + key[-4:]
