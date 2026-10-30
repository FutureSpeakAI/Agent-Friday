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
    try:
        _sv().invalidate()            # the next test reads its own values
    except AssertionError:
        pass


def test_a_stored_key_with_no_shape_is_taken_out_of_a_log_line(store):
    from agent_friday.services import log_redaction
    out = log_redaction.redact("provider call failed: 401 for key %s" % ODD_KEY)
    assert ODD_KEY not in out, "a stored key with no recognisable shape went into the log"


def test_module_loggers_write_through_the_filter(store):
    import agent_friday.core as core
    from agent_friday.services.log_redaction import RedactingFilter
    route = getattr(core, "route_module_loggers", None)
    assert route is not None, "agent_friday.* warnings go to Python's unfiltered last-resort stderr"
    src, dst = logging.Logger("t-friday"), logging.Logger("t-agent-friday")
    h = logging.StreamHandler(io.StringIO())
    h.addFilter(RedactingFilter())
    src.addHandler(h)
    assert route(src, dst) is True
    assert dst.handlers == [h] and dst.propagate is False and dst.level == logging.WARNING
    # with nothing configured (tests), nothing changes: pytest's capture keeps working
    bare, other = logging.Logger("t-bare"), logging.Logger("t-other")
    assert route(bare, other) is False and other.propagate is True and not other.handlers


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
    for name in ("settings.json", "friday.log", "friday.log.1", "server_stderr.log",
                 "server_stderr.log.2", "google_token.json"):
        assert credential_paths.check(store / name), "a tool may read %s" % name


def test_settings_show_a_stored_key_as_stored_and_keep_it_on_save(client, store, monkeypatch):
    import agent_friday.core as core
    settings = store / "settings.json"
    store.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"elevenlabs_api_key": ODD_KEY, "elevenlabs_model": "m-one"}),
                        encoding="utf-8")
    monkeypatch.setattr(core, "SETTINGS_FILE", settings, raising=False)
    core._invalidate_settings_cache()
    got = client.get("/api/settings").get_json()["settings"]
    assert got.get("elevenlabs_model") == "m-one"
    assert ODD_KEY not in json.dumps(got), "GET /api/settings returned a stored key"
    mask = got.get("elevenlabs_api_key")
    r = client.post("/api/settings", json={"settings": {"elevenlabs_api_key": mask, "elevenlabs_model": "m-two"}})
    assert ODD_KEY not in r.get_data(as_text=True), "saving settings answered with the stored key"
    core._invalidate_settings_cache()
    saved = json.loads(settings.read_text(encoding="utf-8"))
    assert saved.get("elevenlabs_api_key") == ODD_KEY, "saving what GET showed overwrote the stored key"
    assert saved.get("elevenlabs_model") == "m-two"


def test_a_key_saved_in_settings_is_known_at_once(store):
    sv = _sv()
    import agent_friday.core as core
    core._save_settings({"elevenlabs_api_key": "Zx9Lm2Qp7Rt4Vw8N"})   # pragma: allowlist secret
    assert "Zx9Lm2Qp7Rt4Vw8N" not in sv.redact_known("voice failed with Zx9Lm2Qp7Rt4Vw8N")
