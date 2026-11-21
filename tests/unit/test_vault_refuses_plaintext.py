"""The document vault refuses to write plain text when no key resolves (threat assessment fix #2). TEST DATA ONLY.

Before: with no vault key, _vault_write_text stored the file as plain text and only a warning said so. Now it writes nothing
and raises VaultKeyMissing, whose message is one plain line for the owner (the route shows it as it is). Reading a plain
file that already exists is unchanged (an old plain copy must stay readable until it is migrated). What each check bites on:
  1. no key: nothing is written (no file, no temp file), the error is a UserFacingError with one plain sentence;
  2. with a key the file is still encrypted and reads back (the refusal is only for the keyless case);
  3. a plain file that is already there still reads;
  (the route that creates a finance template is held by tests/api/test_vault_refuses_plaintext_route.py)
"""
import pytest

import agent_friday.services.agent as agent
from agent_friday.user_errors import UserFacingError

KEY = bytes(range(32))


def _set_key(monkeypatch, key):
    monkeypatch.setattr(agent, "_VAULT_KEY", key)
    monkeypatch.setattr(agent, "_VAULT_KEY_READY", True)


def test_no_key_writes_nothing_and_says_so_in_one_plain_line(tmp_path, monkeypatch):
    _set_key(monkeypatch, None)
    p = tmp_path / "portfolio.json"
    with pytest.raises(UserFacingError) as e:
        agent._vault_write_text(p, '{"positions": ["SECRET"]}')
    assert isinstance(e.value, agent.VaultKeyMissing)
    assert not p.exists() and list(tmp_path.iterdir()) == [], "no plain file, no temp file"
    line = e.value.user_message
    assert "\n" not in line and "plain text" in line and "not saved" in line.lower()


def test_with_a_key_the_file_is_still_encrypted_and_reads_back(tmp_path, monkeypatch):
    import agent_friday.privacy.vault_crypto as vc
    _set_key(monkeypatch, vc.derive_key("test passphrase", bytes(range(16, 32)), profile=vc.FAST_PROFILE))
    p = tmp_path / "meds.json"
    agent._vault_write_text(p, '{"medications": ["SECRET"]}')
    assert b"SECRET" not in p.read_bytes() and agent._vault_read_text(p) == '{"medications": ["SECRET"]}'


def test_a_plain_file_that_is_already_there_still_reads(tmp_path, monkeypatch):
    _set_key(monkeypatch, None)
    p = tmp_path / "legacy.json"
    p.write_text('{"legacy": true}', encoding="utf-8")
    assert agent._vault_read_text(p) == '{"legacy": true}'

