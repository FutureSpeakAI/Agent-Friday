"""Whole-profile walks, refusal receipts, refusal voice and detector precision.

A recursive read that starts above a credential folder reaches the keys
without naming them; the refusal is recorded as a denial and never narrated
as work; the copy speaks in the first person; owner lookups that hold no
secret stay readable; and the egress detector stays linear and precise.
All values are synthetic.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from agent_friday.services import credential_paths as cred
from agent_friday.services import secret_patterns as sp

PEM = ("-----BEGIN OPENSSH PRIVATE " + "KEY-----\n"  # pragma: allowlist secret
       "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n"
       "-----END OPENSSH PRIVATE " + "KEY-----\n")  # pragma: allowlist secret


def _mk(p: Path, body="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


@pytest.fixture
def profile(test_home):
    """A profile that holds credential folders and an ordinary project."""
    _mk(test_home / ".ssh" / "id_ed25519", PEM)
    _mk(test_home / ".aws" / "credentials", "[default]\naws_access_key_id = x\n")
    _mk(test_home / "Projects" / "site" / "app.py", "print('hi')\n")
    return test_home


# ── walks that begin above a credential folder ────────────────────────────────

_WALKS = [
    "Get-ChildItem $HOME -Recurse -File | Get-Content",
    "gci $HOME -r -File -Filter credentials | gc",
    "Get-ChildItem $home -Recurse | ForEach-Object { Get-Content $_.FullName }",
    "Get-ChildItem ${HOME} -Recurse | Get-Content",
    "Get-ChildItem $PWD -Recurse -File | Get-Content",
    "Get-ChildItem (Get-Location) -Recurse | Get-Content",
    "Get-ChildItem (Get-Location).Path -rec | Get-Content",
    "Get-ChildItem ([Environment]::GetFolderPath('UserProfile')) -Recurse | Get-Content",
    "Get-ChildItem ([Environment]::GetFolderPath('MyDocuments')) -Recurse | Get-Content",
    "Get-ChildItem . -Recurse | Get-Content",
    "Get-ChildItem -Recurse | Get-Content",
    "find $HOME -type f | xargs cat",
    "robocopy {home} D:\\out /E",
    "robocopy {home} D:\\out /MIR",
    "xcopy {home} D:\\out /E",
    "tar -cf out.tar {home}",
    "tar -cf out.tar -C {home} .",
    "Compress-Archive -Path {home} -DestinationPath out.zip",
    "Compress-Archive -Path $HOME -DestinationPath out.zip",
    "Copy-Item {home}{sep}* D:\\out -Recurse",
    "cp -r {home}/* /tmp/out",
    "Get-ChildItem {parent} -Recurse | Get-Content",
]


@pytest.mark.parametrize("cmd", _WALKS)
def test_a_recursive_read_from_above_a_credential_folder_is_refused(profile, monkeypatch, cmd):
    monkeypatch.chdir(profile)
    cmd = (cmd.replace("{home}", str(profile)).replace("{parent}", str(profile.parent))
           .replace("{sep}", os.sep))
    assert cred.scan_command(cmd), cmd


_ORDINARY = [
    "Get-ChildItem {proj} -Recurse | Get-Content",
    "tar -cf out.tar {proj}",
    "Compress-Archive -Path {proj} -DestinationPath out.zip",
    "robocopy {proj} D:\\out /E",
    "Get-ChildItem $HOME -Recurse -File",
    "Get-ChildItem $HOME\\Projects -Recurse | Select-String TODO",
    "Get-ChildItem $HOME -Directory",
    "Get-Location",
]


@pytest.mark.parametrize("cmd", _ORDINARY)
def test_ordinary_folder_reads_still_run(profile, monkeypatch, cmd):
    monkeypatch.chdir(profile / "Projects" / "site")
    proj = str(profile / "Projects" / "site")
    assert cred.scan_command(cmd.replace("{proj}", proj)) is None, cmd


def test_a_walk_from_home_is_fine_when_home_holds_no_credential_folder(test_home, tmp_path,
                                                                        monkeypatch):
    """The refusal rests on what exists under the root, not on the root's name."""
    other = tmp_path / "elsewhere"
    _mk(other / "a.txt")
    monkeypatch.chdir(other)
    assert cred.scan_command("Get-ChildItem . -Recurse | Get-Content") is None


def test_a_non_recursive_listing_of_home_piped_to_a_reader_meets_a_top_level_key(
        profile, monkeypatch):
    monkeypatch.chdir(profile)
    _mk(profile / ".netrc", "machine x login y password z\n")
    assert cred.scan_command("Get-ChildItem $HOME -Force | Get-Content")


def test_the_walk_refusal_offers_a_way_forward(profile, monkeypatch):
    monkeypatch.chdir(profile)
    why = cred.scan_command("Get-ChildItem $HOME -Recurse | Get-Content")
    line = cred.refusal_command(why)
    assert "folder" in line and "Friday's" not in line, line
    assert "Open the file yourself" not in line, line


# ── the refusal is a denial in the receipt, and is not narrated as work ──────

@pytest.mark.parametrize("tool,args", [
    ("read_file", lambda h: {"path": str(h / ".ssh" / "id_ed25519")}),
    ("open_path", lambda h: {"path": str(h / ".ssh" / "id_ed25519")}),
    ("run_command", lambda h: {"command": "Get-ChildItem $HOME -Recurse | Get-Content"}),
    ("run_command", lambda h: {"command": f"Get-Content {h}\\.aws\\credentials"}),
    ("run_sandboxed", lambda h: {"code": f"print(open(r'{h}/.ssh/id_ed25519').read())"}),
])
def test_a_credential_refusal_is_recorded_as_denied(profile, monkeypatch, tool, args):
    from agent_friday.services import agent, model_router, tool_receipts
    monkeypatch.chdir(profile)
    told = []
    monkeypatch.setattr(model_router, "announce_tool", lambda *a, **k: told.append(a))
    tool_receipts.begin_turn()
    out = agent._execute_tool(tool, args(profile), session_ctx={"authenticated": True})
    assert out.startswith("I won't"), out
    book = tool_receipts.receipts()
    assert len(book) == 1, book
    assert book[0]["denied"] is True and book[0]["ok"] is False, book
    assert "I won't" in (book[0]["detail"] or ""), book
    assert told == [], "a refused call is not narrated as work in progress"


def test_an_ordinary_read_is_not_recorded_as_denied(profile):
    from agent_friday.services import agent, tool_receipts
    tool_receipts.begin_turn()
    f = _mk(profile / "Projects" / "site" / "notes.txt", "milk and eggs")
    out = agent._execute_tool("read_file", {"path": str(f)})
    assert "milk and eggs" in out
    assert tool_receipts.receipts()[0]["denied"] is False


# ── first-person copy ────────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", [
    ".friday/backups/x/notes.txt", ".friday/security/notes.txt", ".friday/secret_key",
])
def test_refusals_call_her_own_folders_mine(profile, rel):
    f = profile / rel
    line = cred.refusal(f)
    assert "Friday" not in line and " her " not in f" {line} ", line
    assert " my " in line, line


@pytest.mark.parametrize("cmd", [
    "Get-Content C:\\x\\.friday\\backups\\a.txt", "cat ~/.friday/security/k",
])
def test_command_refusals_call_her_own_folders_mine(cmd):
    why = cred.scan_command(cmd)
    line = cred.refusal_command(why)
    assert "Friday" not in line and " my " in line, line


@pytest.mark.parametrize("target", ["id_rsa", "start.bat", "id_ed25519", ".netrc"])
def test_a_bare_name_is_refused_without_a_dangling_location(target):
    line = cred.refusal(target)
    assert line.startswith("I won't open"), line
    assert "at ." not in line and "yourself at" not in line, line
    assert "You can open it yourself" in line, line


def test_a_named_path_still_says_where_to_open_it(test_home):
    f = _mk(test_home / ".ssh" / "id_ed25519", PEM)
    assert f"You can open it yourself at {f.parent}." in cred.refusal(f)


def test_the_ledger_uses_one_marker_for_a_withheld_credential():
    from agent_friday.services import task_ledger
    text = "key sk-" + "a1b2c3d4e5f6g7h8i9j0k1 and token ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3"
    out = task_ledger.tier_safe(text)
    assert "[redacted key]" not in out, out
    assert out.count("[credential withheld]") >= 1, out
    assert "sk-a1b2" not in out and "ghp_A1b2" not in out, out


# ── owner lookups that hold no secret stay readable ──────────────────────────

@pytest.mark.parametrize("rel,body", [
    (".ssh/config", "Host box\n  HostName 10.0.0.2\n  User me\n"),
    (".ssh/known_hosts", "box ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\n"),
    (".ssh/id_ed25519.pub", "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl me\n"),
    (".aws/config", "[default]\nregion = us-east-1\noutput = json\n"),
    (".config/gh/config.yml", "git_protocol: https\neditor: vim\n"),
])
def test_owner_lookups_that_hold_no_secret_are_not_refused(test_home, rel, body):
    f = _mk(test_home / rel, body)
    assert cred.check(f) is None, rel


@pytest.mark.parametrize("rel,body", [
    (".aws/config", "[default]\naws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY1\n"),
    (".ssh/config", "Host box\n  # -----BEGIN OPENSSH PRIVATE " + "KEY-----\n"),  # pragma: allowlist secret
    (".config/gh/config.yml", "oauth_token: ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3\n"),
])
def test_the_same_lookup_is_refused_when_it_actually_holds_a_secret(test_home, rel, body):
    # test_home is shared across the session: the secret-bearing lookup must not
    # outlive this test, or later tests that treat the lookup as clean see it.
    f = _mk(test_home / rel, body)
    try:
        assert cred.check(f)
    finally:
        f.unlink(missing_ok=True)


@pytest.mark.parametrize("rel", [
    ".ssh/id_ed25519", ".ssh/id_rsa", ".aws/credentials", ".config/gh/hosts.yml",
    ".ssh/sub/config",
])
def test_key_and_credential_files_stay_closed(test_home, rel):
    assert cred.check(_mk(test_home / rel, "x")), rel


def test_a_link_named_config_into_a_key_is_still_refused(test_home):
    key = _mk(test_home / ".ssh" / "id_ed25519", PEM)
    link = test_home / ".ssh" / "config"
    link.unlink(missing_ok=True)
    try:
        link.symlink_to(key)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    try:
        assert cred.check(link)
    finally:
        link.unlink(missing_ok=True)   # the shared test home must not keep a config that points at a key


def test_a_lookup_refusal_never_calls_a_credentials_file_key_material(test_home):
    f = _mk(test_home / ".aws" / "credentials", "[default]\n")
    line = cred.refusal(f)
    assert "Key material" not in line, line


# ── egress detector: linear time and precision ───────────────────────────────

def test_a_large_hex_or_alphanumeric_text_is_classified_in_linear_time():
    import secrets
    for text in (secrets.token_hex(60_000), "a1" * 60_000):
        t = time.perf_counter()
        sp.contains_secret(text)
        elapsed = time.perf_counter() - t
        assert elapsed < 1.5, f"{len(text)} chars took {elapsed:.2f}s"


def test_detection_of_a_url_login_is_unchanged():
    assert sp.contains_secret("git clone https://bob:s3cretpw@example.test/r.git")
    assert sp.contains_secret("prefix" + "x" * 5000 + " postgres://bob:s3cretpw@db.test/x")


@pytest.mark.parametrize("line", [
    "Password = ********",  # pragma: allowlist secret
    "password: ********",  # pragma: allowlist secret
    "Password: ••••••••",  # pragma: allowlist secret
    "password: minimum-8 characters",  # pragma: allowlist secret
    "password: max-64",
])
def test_a_mask_or_a_policy_line_is_not_a_secret(line):
    assert not sp.contains_secret(line), line


@pytest.mark.parametrize("line", [
    "password=hunter2x!", "Password: correct-horse-9", "passwd = Tr0ub4dor&3",  # pragma: allowlist secret
    "password: xxxxxxx1"
])
def test_a_real_looking_password_is_still_a_secret(line):
    assert sp.contains_secret(line), line
