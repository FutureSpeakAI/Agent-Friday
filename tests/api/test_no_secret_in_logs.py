"""A stored credential never reaches a log, the console, a tool result or a settings read, whatever
its shape (BD-06, NS-23.3-3).

The log filter (services/log_redaction, BD-08) knew secret SHAPES only, and sat only on the
"friday" logger. Before:
* a key with no recognisable shape (a custom provider key, a Twilio secret) passed every
  redactor: in a log line, a print, a tool result;
* the 47 modules that log as "agent_friday.<module>" wrote their warnings to Python's
  unfiltered last-resort stderr;
* the server's console (kept by the tray in server_stderr.log) was never redacted at all;
* GET /api/settings returned keys kept in settings.json as they were;
* a tool could read settings.json, friday.log and server_stderr.log.
Synthetic values only.
"""
from __future__ import annotations

import io
import json
import logging

import pytest

from agent_friday.services import credential_store as cs
from agent_friday.services import keystore as ks

ODD_KEY = "q7Rk2mZp9WxL4vNb"     # pragma: allowlist secret  (no vendor shape at all)


def _sv():
    try:
        from agent_friday.services import secret_values
    except ImportError:
        raise AssertionError("nothing on this tree knows the values Friday holds (services/secret_values)")
    return secret_values


@pytest.fixture
def store(tmp_path, monkeypatch):
    from agent_friday import core
    home = tmp_path / ".friday"
    monkeypatch.setattr(core, "FRIDAY_DIR", home)
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    monkeypatch.setattr(cs, "_SECURITY_DIR", home / "security")
    monkeypatch.setattr(cs, "_CRED_AUDIT_LOG", home / "security" / "credential_audit.jsonl")
    monkeypatch.setattr(cs, "_PROVIDER_KEYS_DIR", home / "providers" / "keys")
    monkeypatch.setattr(ks, "KEYSTORE_PATH", home / "security" / "keystore.json")
    ks._reset_cache_for_tests()
    cs.set_provider_key("custom-llm", ODD_KEY)
    try:
        _sv().invalidate()
    except AssertionError:
        pass
    yield home
    ks._reset_cache_for_tests()


def test_a_stored_key_with_no_shape_is_taken_out_of_a_log_line(store):
    from agent_friday.services import log_redaction
    out = log_redaction.redact("provider call failed: 401 for key %s" % ODD_KEY)
    assert ODD_KEY not in out, "a stored key with no recognisable shape went into the log"


def test_module_loggers_write_through_the_filter(store):
    import agent_friday.core as core
    core._install_log_redaction()
    from agent_friday.services.log_redaction import RedactingFilter
    lg = logging.getLogger("agent_friday")
    assert lg.handlers and all(any(isinstance(f, RedactingFilter) for f in h.filters) for h in lg.handlers), \
        "agent_friday.* warnings go to an unfiltered handler"


def test_the_console_is_redacted(store):
    from agent_friday.services import log_redaction
    stream_cls = getattr(log_redaction, "RedactingStream", None)
    assert stream_cls is not None, "the server's console (server_stderr.log) is not redacted"
    buf = io.StringIO()
    stream_cls(buf).write("token refresh failed (%s)\n" % ODD_KEY)
    assert ODD_KEY not in buf.getvalue()


def test_a_tool_result_never_carries_a_stored_key(store):
    from agent_friday.services import credential_paths
    assert ODD_KEY not in credential_paths.redact_secrets("the config says key = %s" % ODD_KEY)


def test_tools_cannot_read_the_settings_or_the_logs(store):
    from agent_friday.services import credential_paths
    for name in ("settings.json", "friday.log", "server_stderr.log", "google_token.json"):
        assert credential_paths.check(store / name), "a tool may read %s" % name


def test_settings_show_a_stored_key_as_stored_and_keep_it_on_save(client, store, monkeypatch):
    import agent_friday.core as core
    settings = store / "settings.json"
    store.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"brave_api_key": ODD_KEY, "theme": "dark"}), encoding="utf-8")
    monkeypatch.setattr(core, "SETTINGS_FILE", settings, raising=False)
    core._invalidate_settings_cache()
    got = client.get("/api/settings").get_json()["settings"]
    assert got.get("theme") == "dark"
    assert ODD_KEY not in json.dumps(got), "GET /api/settings returned a stored key"
    mask = got.get("brave_api_key")
    client.post("/api/settings", json={"settings": {"brave_api_key": mask, "theme": "light"}})
    core._invalidate_settings_cache()
    saved = json.loads(settings.read_text(encoding="utf-8"))
    assert saved.get("brave_api_key") == ODD_KEY, "saving what GET showed overwrote the stored key"
    assert saved.get("theme") == "light"
