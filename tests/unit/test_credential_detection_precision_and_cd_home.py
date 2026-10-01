"""Detection precision for the provider-key shapes, a bare `cd` as a move to the
home folder, and GNU-style recursion flags on a listing.

All values are synthetic and assembled at run time.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_friday.services import credential_paths as cred
from agent_friday.services import secret_patterns as sp
from agent_friday.services import secret_shapes
from agent_friday.services import sensitivity_classifier as sc

PEM = ("-----BEGIN OPENSSH PRIVATE " + "KEY-----\n"  # pragma: allowlist secret
       "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n"
       "-----END OPENSSH PRIVATE " + "KEY-----\n")  # pragma: allowlist secret


def _mk(p: Path, body="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# -- a word that merely ends in "sk-" is not a key ------------------------------

PROSE = [
    "switched to branch task-credential-refusal-receipts",  # pragma: allowlist secret
    "pip install flask-sqlalchemy-migration-helper",  # pragma: allowlist secret
    "branch risk-assessment-for-q3-planning done",  # pragma: allowlist secret
    "ticket ABC-12345: disk-usage-monitoring-daemon crashed",  # pragma: allowlist secret
    "see ask-the-maintainers-about-release-cadence for details",  # pragma: allowlist secret
    "desk-rearrangement-proposal-for-the-fourth-floor",  # pragma: allowlist secret
]


@pytest.mark.parametrize("text", PROSE)
def test_hyphenated_words_ending_in_sk_are_not_credentials(text):
    assert sc._regex_tier(text) == 0, text
    assert not sp.contains_secret(text), text
    assert sp.redact(text) == text, text


@pytest.mark.parametrize("text", PROSE)
def test_no_shape_matches_a_hyphenated_word_ending_in_sk(text):
    for sid, _label, pat, _target in secret_shapes.SHAPES:
        assert not __import__("re").search(pat, text), (sid, text)


@pytest.mark.parametrize("lead", ["", "key=", "token: ", "(", '"', "Authorization: Bearer "])
def test_a_real_provider_key_is_still_found_after_any_separator(lead):
    key = "sk-" + "proj-" + "a1B2" * 8  # pragma: allowlist secret
    assert sc._regex_tier(lead + key) == 3, lead
    assert key not in sp.redact("x " + lead + key + " y"), lead


# -- a bare cd is a move to the home folder -------------------------------------

@pytest.fixture
def profile(test_home, monkeypatch):
    _mk(test_home / ".ssh" / "id_ed25519", PEM)
    _mk(test_home / "Projects" / "site" / "app.py", "print('hi')\n")
    monkeypatch.chdir(test_home / "Projects" / "site")
    return test_home


@pytest.mark.parametrize("cmd", [
    "cd; grep -r BEGIN .",
    "cd && grep -r BEGIN .",
    "cd\ngrep -r BEGIN .",
    "Set-Location; Get-ChildItem -Recurse | Get-Content",
    "pushd; ls -R | xargs cat",
])
def test_a_bare_cd_moves_the_walk_root_to_home(profile, cmd):
    assert cred.scan_command(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "cd; echo done",
    "cd .; grep -r todo .",
    "cd src; grep -r todo .",
])
def test_a_bare_cd_followed_by_nothing_that_walks_runs(profile, cmd):
    assert cred.scan_command(cmd) is None, cmd


# -- GNU-style recursion on a listing is still recursion ------------------------

@pytest.mark.parametrize("cmd", [
    "ls --recursive ~/.ssh",
    "ls -laR ~/.ssh",
    "ls -lR ~/.ssh",
    "ls -Ra ~/.ssh",
    "dir --recursive ~/.ssh",
])
def test_a_recursive_listing_of_a_key_folder_is_refused(profile, cmd):
    assert cred.scan_command(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "ls ~/.ssh",
    "ls -la ~/.ssh",
    "Get-ChildItem -Force ~/.ssh",
])
def test_a_names_only_listing_of_a_key_folder_still_runs(profile, cmd):
    assert cred.scan_command(cmd) is None, cmd
