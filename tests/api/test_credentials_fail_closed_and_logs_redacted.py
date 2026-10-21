"""BD-08: a credential store without a soft floor, logs without secrets, one id per request.

* With Friday's keystore unusable for ANY reason (not only locked), a secret is refused, not
  written under the vault key or DPAPI instead, and the person's words carry no exception text.
* With the keystore made unreadable, every credential read path raises (or reports
  "unreadable") rather than returning a secret.
* protection_method says what a write would do: keystore / locked / unavailable.
* One logging filter on Friday's handlers redacts key-shaped values from every line (message,
  arguments, traceback), keeping the field name.
* Every API request carries one correlation id: answered as X-Friday-Request-Id, prefixed to
  the friday.log lines it writes, and signed into the action checkpoint's decision receipts.
Synthetic values only (none is a real credential).
"""
from __future__ import annotations

import io
import json
import logging

import pytest

from agent_friday.services import credential_store as cs
from agent_friday.services import keystore as ks

FAKE = b'{"fake": "not-a-real-token"}'
# Shaped like a key so the filter must catch it; not a real one.
FAKE_KEY = "sk-ant-api03-" + "Q" * 40  # pragma: allowlist secret


@pytest.fixture
def store(tmp_path, monkeypatch):
    home = tmp_path / ".friday"
    monkeypatch.setattr(cs, "_VAULT_CONFIG_FILE", home / "vault" / ".vault_config.json")
    monkeypatch.setattr(cs, "_SECURITY_DIR", home / "security")
    monkeypatch.setattr(cs, "_PROVIDER_KEYS_DIR", home / "providers" / "keys")
    monkeypatch.setattr(ks, "KEYSTORE_PATH", home / "security" / "keystore.json")
    for var in ("FRIDAY_OS_MODE", "FRIDAY_PASSWORD", "FRIDAY_VAULT_PASSPHRASE"):
        monkeypatch.delenv(var, raising=False)
    ks._reset_cache_for_tests()
    cs._VAULT_KEY = None
    cs._VAULT_KEY_READY = False
    yield home
    ks._reset_cache_for_tests()
    cs._VAULT_KEY = None
    cs._VAULT_KEY_READY = False


@pytest.mark.parametrize("error", [RuntimeError("malformed key"), OSError("disk"), PermissionError("denied"),
                                   ValueError("unknown wrap")], ids=["malformed", "io", "permission", "wrap"])
def test_any_keystore_failure_refuses_and_never_drops_a_tier(store, monkeypatch, tmp_path, error):
    def _broken(data):
        raise error
    monkeypatch.setattr(ks, "encrypt", _broken)
    monkeypatch.setattr(cs, "_vault_key", lambda: bytes(range(32)))
    monkeypatch.setattr(cs, "_dpapi", lambda data, encrypt: b"dpapi-would-have-worked")
    target = tmp_path / "creds" / "token.json"
    with pytest.raises(cs.CredentialProtectionUnavailable) as ei:
        cs.write_secret(target, FAKE)
    assert not target.exists() and not target.parent.exists(), "something was written"
    words = ei.value.user_message
    assert "Nothing was written" in words and "key store" in words
    assert str(error) not in words, "exception text reached the person's words"


def test_the_status_says_what_a_write_would_do(store, monkeypatch):
    assert cs.protection_method() == "keystore"
    monkeypatch.setattr(ks, "root_key", lambda: (_ for _ in ()).throw(RuntimeError("bad file")))
    monkeypatch.setattr(cs, "_vault_key", lambda: bytes(range(32)))
    assert cs.protection_method() == "unavailable"
    monkeypatch.setattr(ks, "root_key", lambda: (_ for _ in ()).throw(ks.KeystoreLocked("locked")))
    assert cs.protection_method() == "locked"


def test_with_the_keystore_unreadable_every_read_path_refuses(store):
    target = store / "creds" / "token.json"
    assert cs.write_secret(target, FAKE) == "keystore"
    cs.set_provider_key("example", "not-a-real-key-000000")  # pragma: allowlist secret
    blob = target.read_bytes()
    # make the keystore unreadable: its file no longer holds a key Friday can use
    ks.KEYSTORE_PATH.write_text("{ not json", encoding="utf-8")
    ks._reset_cache_for_tests()
    for name, read in (("read_secret", lambda: cs.read_secret(target)), ("unprotect", lambda: cs.unprotect(blob)),
                       ("decrypt_any", lambda: cs.decrypt_any(blob))):
        try:
            out = read()
        except Exception:
            continue                                    # refused: the secret was not returned
        raise AssertionError("%s returned %r with the keystore unreadable" % (name, out))
    assert cs.get_provider_key("example") is None
    assert cs.provider_key_status("example") == "present_but_unreadable"


# -- the logging filter -----------------------------------------------------

def _logged(*args, exc=False):
    from agent_friday.services import log_redaction
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    log = logging.getLogger("friday.test_redaction")
    log.addHandler(handler)
    log.propagate = False
    log_redaction.install(log)
    try:
        if exc:
            try:
                raise RuntimeError("upstream said: Authorization: Bearer " + "A" * 30)
            except RuntimeError:
                log.exception(*args)
        else:
            log.warning(*args)
    finally:
        log.removeHandler(handler)
        log.propagate = True
    return stream.getvalue()


@pytest.mark.parametrize("args", [
    ("provider key %s rejected", FAKE_KEY),
    ("calling https://api.example.test/v1?api_key=%s", "abcdef" + "1234567890"),  # pragma: allowlist secret
    ("token refresh failed: refresh_token=%s", "1//0g" + "FAKE" * 4),  # pragma: allowlist secret
    ("auth header was Authorization: Bearer %s", "fake" + "-bearer-" * 3),  # pragma: allowlist secret
    ("proxy https://someone:%s@proxy.example.test", "not" + "-a-password"),  # pragma: allowlist secret
], ids=["vendor-key", "query-key", "refresh-token", "bearer", "url-password"])
def test_a_log_line_with_a_key_shaped_value_is_redacted(args):
    out = _logged(*args)
    assert args[1] not in out, out
    assert "redact" in out or "withheld" in out.lower(), out


def test_quoted_and_cookie_values_are_redacted():
    out = _logged('payload {"password": "%s", "user": "x"}', "correct horse battery staple")  # pragma: allowlist secret
    assert "horse" not in out and '"user": "x"' in out
    out = _logged("sent Cookie: %s", "session=" + "s" * 24)  # pragma: allowlist secret
    assert "s" * 24 not in out


def test_the_filter_never_raises_into_the_code_that_logged():
    class Hostile:
        def __str__(self):
            raise RuntimeError("cannot print me")
    out = _logged(Hostile())
    assert "cannot print me" not in out


def test_the_stale_key_repair_never_moves_a_keystore_key_back_to_the_vault_key(store, monkeypatch, tmp_path):
    from agent_friday import core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path / "home-for-backups")   # its backup lands here
    cs.set_provider_key("example", "not-a-real-key-000000")  # pragma: allowlist secret
    path = cs._provider_key_path("example")
    before = path.read_bytes()
    monkeypatch.setattr(cs, "_derive_fresh_vault_key", lambda: bytes(range(32)))
    out = cs.reencrypt_stale_provider_keys(["example"])
    assert "example" in out["unchanged"] and path.read_bytes() == before


def test_the_task_journal_never_writes_plain_text_when_nothing_can_encrypt(tmp_path, monkeypatch):
    from agent_friday.services import task_journal as tj
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    monkeypatch.setattr(tj, "_encrypt_enabled", lambda: True)

    def _refused(data):
        raise cs.CredentialProtectionUnavailable("refused in this test")
    monkeypatch.setattr(cs, "protect", _refused)
    tj.reset_for_tests()
    assert tj.append("task-plain-1", "note", text="the owner's private note") is None, "the write did not fail"
    on_disk = b"".join(p.read_bytes() for p in (tmp_path / "tasks").rglob("*") if p.is_file())
    assert b"private note" not in on_disk
    tj.reset_for_tests()


def test_a_traceback_is_redacted_too():
    out = _logged("the call failed", exc=True)
    assert "A" * 30 not in out and "Traceback" in out


def test_ordinary_lines_are_left_alone():
    out = _logged("model %s answered in %d ms with max_tokens=%d", "local-seat", 812, 4096)
    assert "local-seat" in out and "812" in out and "max_tokens=4096" in out
    for line in ('provider said {"code": "rate_limit_exceeded"}', "error code: ERR_CONNECTION_REFUSED",
                 "exit code: -1073741819", "session: 7f3a9c2b started"):
        assert _logged("%s", line).strip().endswith(line), line


def test_a_redacted_value_reads_cleanly():
    out = _logged('{"password": "%s"}', "correct horse")  # pragma: allowlist secret
    assert '"password": "[redacted]"' in out and "]]" not in out
    out = _logged("callback %s", "https://app.example.test/cb?state=s1&code=" + "4%2F0A" + "x" * 30)
    assert "x" * 30 not in out and "state=s1" in out


# -- one correlation id per request ----------------------------------------

def test_every_request_answers_with_one_correlation_id(client):
    r = client.get("/api/health")
    cid = r.headers.get("X-Friday-Request-Id", "")
    assert cid.startswith("req-") and len(cid) > 8
    r2 = client.get("/api/health", headers={"X-Request-Id": "trace-0123456789"})
    assert r2.headers.get("X-Friday-Request-Id") == "c-trace-0123456789", "a caller's id is marked as the caller's"
    for bad in ("bad id with spaces", 'x"<script>alert(1)</script>', "a" * 200):
        r3 = client.get("/api/health", headers={"X-Request-Id": bad})
        assert r3.headers.get("X-Friday-Request-Id", "").startswith("req-"), "an unsafe id was echoed"
    from agent_friday.services import correlation
    assert correlation.current() == "", "the id outlived its request"


def test_the_log_lines_and_receipts_of_a_request_carry_its_id(tmp_path, monkeypatch):
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import correlation
    monkeypatch.setattr(ag, "receipts_path", lambda: tmp_path / "decision-bom.jsonl")
    with correlation.bind("req-test-000000001"):
        out = _logged("doing the thing")
        ag._receipt({"tool": "example", "decision": "allow"})
    assert "[req-test-000000001] doing the thing" in out
    row = json.loads((tmp_path / "decision-bom.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["correlation_id"] == "req-test-000000001"
    assert ag.verify_receipt(row), "the id is signed with the receipt"
