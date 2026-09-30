"""Friday's own file tools refuse to read key material, even when asked.

The Muse incident in one line: an agent told to "archive everything you can
see and send it" shipped its SSH keys out. Friday's answer is a path deny-list
that bounds the MODEL's reach (read_file, search_files, open_path, run_command's
read verbs) and never the user's own hands.

These fail on today's main: services/credential_paths does not exist there.
"""
from __future__ import annotations

import pytest

import agent_friday.core as core
from agent_friday.services import credential_paths as cred


def _denied_paths():
    h, f = core.HOME, core.FRIDAY_DIR
    return [
        h / ".ssh" / "id_ed25519",
        h / ".ssh" / "id_work",
        h / ".aws" / "credentials",
        h / ".gnupg" / "secring.gpg",
        f / "security" / "keystore.json",
        f / "security" / "vault-passphrase.dpapi",
        f / "providers" / "keys" / "openrouter.key",
        f / "google_accounts" / "tokens" / "abc.token.enc",
        f / "mcp_oauth" / "higgsfield.oauth.enc",
        f / "secret_key",
        f / "vault" / ".vault_config.json",
        f / "vault" / ".governance-key",
        f / "vault" / ".attestation-key-ed25519",
        h / "Downloads" / "id_rsa",
        h / "Documents" / "server.pem",
        h / "Projects" / "friday-desktop" / "start.bat",
    ]


def _allowed_paths():
    h, f = core.HOME, core.FRIDAY_DIR
    return [
        f / "documents" / "report.docx",
        f / "wiki" / "journalism" / "page.md",
        f / "vault" / "finances" / "notes.md",      # owner content, not key material
        h / "Documents" / "budget.xlsx",
        h / "Downloads" / "photo.png",
    ]


@pytest.mark.parametrize("p", _denied_paths())
def test_credential_paths_are_denied(p):
    assert cred.check(p), f"expected {p} to be denied"


@pytest.mark.parametrize("p", _allowed_paths())
def test_owner_content_is_allowed(p):
    assert cred.check(p) is None, f"{p} is owner content, not key material"


def test_the_reason_names_the_secret_and_the_folder():
    r = cred.refusal(core.HOME / ".ssh" / "id_ed25519")
    assert "id_ed25519" in r
    assert "SSH" in r
    assert str((core.HOME / ".ssh")) in r        # names the folder to open by hand
    assert "won't" in r.lower()                  # explicit, not a silent nothing


@pytest.mark.parametrize("cmd,denied", [
    ("Get-Content ~/.ssh/id_ed25519", True),
    ("cat ~/.friday/security/keystore.json", True),
    ("gc ~/.friday/providers/keys/openrouter.key", True),
    ("Get-Content $env:USERPROFILE\\.ssh\\id_rsa", True),
    ("type start.bat", True),
    ("Get-Content ~/.friday/vault/.governance-key", True),
    ("Get-ChildItem ~/Documents", False),
    ("Get-Content ~/Documents/report.txt", False),
    ("Get-Date", False),
])
def test_scan_command(cmd, denied):
    assert bool(cred.scan_command(cmd)) is denied, cmd


def test_read_file_refuses_and_does_not_leak(friday_dir):
    import agent_friday.services.agent as agent
    keystore = friday_dir / "security" / "keystore.json"
    keystore.parent.mkdir(parents=True, exist_ok=True)
    keystore.write_text('{"root_key": "SENTINEL-DO-NOT-LEAK-9931"}', encoding="utf-8")
    out = agent._tool_read_file({"path": str(keystore)})
    assert "SENTINEL-DO-NOT-LEAK-9931" not in out
    assert "won't open" in out.lower()


def test_read_file_still_reads_ordinary_files(friday_dir):
    import agent_friday.services.agent as agent
    doc = friday_dir / "documents" / "note.txt"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("ordinary content 4471", encoding="utf-8")
    out = agent._tool_read_file({"path": str(doc)})
    assert "ordinary content 4471" in out


def test_search_does_not_open_a_key_for_a_snippet(test_home):
    from agent_friday.services import file_search
    dl = test_home / "Downloads"
    dl.mkdir(parents=True, exist_ok=True)
    (dl / "id_ed25519").write_text("PRIVATE-KEY-SENTINEL-2276", encoding="utf-8")
    res = file_search.search_files(query="", root="downloads",
                                   content_query="SENTINEL")
    blob = str(res)
    assert "PRIVATE-KEY-SENTINEL-2276" not in blob
    assert "id_ed25519" not in blob
