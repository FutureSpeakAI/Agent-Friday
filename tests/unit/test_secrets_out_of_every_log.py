"""BD-08: no secret in any log or error answer; one request id across the ledgers.

NS-26.5-5 ("redact secrets from logs and error messages") was met for the "friday"
logger tree only. These tests hold the rest:

* a record from ANY logger (a library, werkzeug, the root) is redacted before any
  handler writes it;
* print() and stray tracebacks on the server's stdout/stderr pass the same redactor,
  line by line, even when a value arrives in pieces;
* a JSON error answer (4xx/5xx) carries no key-shaped value; names and structure stay,
  and success answers are untouched;
* egress ledger rows and task-journal events written during a request carry its
  correlation id (NS-26.17-2).

Synthetic values only (none is a real credential).
"""
from __future__ import annotations

import io
import json
import logging
import sys

import pytest

from agent_friday.services import correlation
from agent_friday.services import log_redaction as lr

# Shaped like keys so the redactor must catch them; not real ones.
FAKE_KEY = "sk-ant-api03-" + "Q" * 40  # pragma: allowlist secret
FAKE_BEARER = "Z" * 32


@pytest.fixture
def everywhere():
    """install_everywhere(), undone afterwards: the factory and every filter it added."""
    factory = logging.getLogRecordFactory()
    loggers = (logging.getLogger("friday"), logging.getLogger(), logging.getLogger("werkzeug"))
    before = {id(h): list(h.filters) for lg in loggers for h in lg.handlers}
    last_before = list(logging.lastResort.filters) if logging.lastResort else None
    lr.install_everywhere()
    yield
    logging.setLogRecordFactory(factory)
    for lg in loggers:
        for h in lg.handlers:
            if id(h) in before:
                h.filters[:] = before[id(h)]
            else:
                h.filters[:] = [f for f in h.filters if not isinstance(f, lr.RedactingFilter)]
    if logging.lastResort is not None:
        logging.lastResort.filters[:] = last_before


def _logged_by(name, *args, exc=False):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)          # no filter of its own
    handler.setFormatter(logging.Formatter("%(name)s %(message)s"))
    log = logging.getLogger(name)
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    try:
        if exc:
            try:
                raise RuntimeError("upstream said: Authorization: Bearer " + FAKE_BEARER)
            except RuntimeError:
                log.exception(*args)
        else:
            log.warning(*args)
    finally:
        log.removeHandler(handler)
        log.propagate = True
        log.setLevel(logging.NOTSET)
    return stream.getvalue()


@pytest.mark.parametrize("name", ["urllib3.connectionpool", "werkzeug", "some_library", "agent_friday.core"])
def test_a_record_from_any_logger_is_redacted(everywhere, name):
    out = _logged_by(name, "request failed with key %s", FAKE_KEY)
    assert FAKE_KEY not in out and "redact" in out, out
    out = _logged_by(name, "GET /cb?state=s1&code=%s HTTP/1.1", "4%2F0A" + "x" * 30)
    assert "x" * 30 not in out and "state=s1" in out, out


def test_a_traceback_from_any_logger_is_redacted(everywhere):
    out = _logged_by("some_library", "the call failed", exc=True)
    assert FAKE_BEARER not in out and "Traceback" in out, out


def test_ordinary_lines_from_any_logger_are_left_alone(everywhere):
    out = _logged_by("werkzeug", '127.0.0.1 - - "GET /api/health HTTP/1.1" %s -', 200)
    assert out.strip().endswith('"GET /api/health HTTP/1.1" 200 -'), out


def test_a_record_written_during_a_request_carries_its_id_from_any_logger(everywhere):
    with correlation.bind("req-test-000000010"):
        out = _logged_by("some_library", "doing the thing")
    assert "[req-test-000000010] doing the thing" in out


def test_installing_twice_redacts_once(everywhere):
    lr.install_everywhere()
    with correlation.bind("req-test-000000011"):
        out = _logged_by("some_library", "once")
    assert out.count("[req-test-000000011]") == 1, out


def test_the_last_resort_handler_has_the_filter(everywhere):
    assert any(isinstance(f, lr.RedactingFilter) for f in logging.lastResort.filters)


# -- stdout and stderr ---------------------------------------------------------

def test_a_printed_line_is_redacted():
    inner = io.StringIO()
    s = lr.wrap_stream(inner)
    print("provider rejected", FAKE_KEY, file=s)
    s.flush()
    assert FAKE_KEY not in inner.getvalue() and "provider rejected" in inner.getvalue()


def test_a_value_written_in_pieces_is_still_redacted():
    inner = io.StringIO()
    s = lr.wrap_stream(inner)
    for piece in ("Authorization: Bear", "er " + FAKE_BEARER[:10], FAKE_BEARER[10:], "\n"):
        s.write(piece)
    assert FAKE_BEARER not in inner.getvalue() and inner.getvalue().endswith("\n")


def test_an_open_line_is_redacted_when_flushed_and_plain_text_is_unchanged():
    inner = io.StringIO()
    s = lr.wrap_stream(inner)
    s.write("api_key=" + "k" * 24)
    assert inner.getvalue() == "", "an open line is held until it ends or is flushed"
    s.flush()
    assert "k" * 24 not in inner.getvalue()
    inner2 = io.StringIO()
    s2 = lr.wrap_stream(inner2)
    assert s2.write("model answered in 812 ms\n") == len("model answered in 812 ms\n")
    assert inner2.getvalue() == "model answered in 812 ms\n"


def test_a_wrapped_stream_keeps_its_own_attributes_and_is_wrapped_once():
    inner = io.StringIO()
    s = lr.wrap_stream(inner)
    assert lr.wrap_stream(s) is s
    assert s.getvalue() == "" and s.closed is False
    assert lr.wrap_stream(None) is None


def test_install_streams_wraps_stdout_and_stderr(monkeypatch):
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    lr.install_streams()
    print("token=" + "t" * 24)
    sys.stderr.write("Traceback: refresh_token=" + "r" * 24 + "\n")
    sys.stdout.flush()
    assert "t" * 24 not in out.getvalue() and "token=" in out.getvalue()
    assert "r" * 24 not in err.getvalue()


def test_the_server_wraps_its_streams_at_start():
    from pathlib import Path
    import agent_friday
    src = (Path(agent_friday.__file__).parent / "server.py").read_text(encoding="utf-8")
    main = src[src.index("if __name__ == '__main__':"):]
    assert "install_streams()" in main[:3000], "the server's __main__ does not wrap stdout/stderr early"


# -- error answers ---------------------------------------------------------------

def _answer(body, status, mimetype="application/json"):
    from agent_friday.core import app, _redact_error_answer
    raw = body if isinstance(body, (str, bytes)) else json.dumps(body)
    resp = app.response_class(raw, status=status, mimetype=mimetype)
    return _redact_error_answer(resp)


def test_an_error_answer_quoting_a_key_is_redacted():
    resp = _answer({"error": "upstream said: Authorization: Bearer " + FAKE_BEARER,
                    "detail": {"tried": ["key " + FAKE_KEY]}, "code": 502}, 502)
    body = resp.get_json()
    text = json.dumps(body)
    assert FAKE_BEARER not in text and FAKE_KEY not in text
    assert body["code"] == 502 and body["error"].startswith("upstream said"), "structure kept"


def test_a_non_json_error_answer_marked_json_is_redacted_too():
    resp = _answer("refresh_token=" + "r" * 24, 500)
    assert "r" * 24 not in resp.get_data(as_text=True)


def test_success_and_html_answers_are_untouched():
    ok = {"items": ["api_key=" + "a" * 24]}       # the owner's own data, not an error
    assert _answer(ok, 200).get_json() == ok
    page = "<p>token=" + "h" * 24 + "</p>"
    assert _answer(page, 404, mimetype="text/html").get_data(as_text=True) == page


def test_the_error_answer_redactor_is_registered():
    from agent_friday.core import app, _redact_error_answer, _idempotency_finish
    funcs = app.after_request_funcs.get(None, [])
    assert _redact_error_answer in funcs
    # Flask runs these last-registered first: the answer is redacted before the
    # idempotency store keeps it for a replay.
    assert funcs.index(_redact_error_answer) > funcs.index(_idempotency_finish)


def test_an_error_answer_that_cannot_be_checked_is_replaced(monkeypatch):
    monkeypatch.setattr(lr, "redact_json", lambda obj: (_ for _ in ()).throw(RuntimeError("x")))
    resp = _answer({"error": "anything"}, 500)
    assert resp.get_json() == {"error": "Something went wrong; the details are in Friday's log."}


# -- one id across the ledgers -----------------------------------------------------

def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_egress_rows_written_in_a_request_carry_its_id(tmp_path):
    from agent_friday.services import egress_gate as eg
    log = tmp_path / "egress.jsonl"
    with correlation.bind("req-test-000000020"):
        eg.record_binary_egress("example", "image", action="allow", reason="test", log_path=log)
        eg._log("example", "prompt", 1, "allow", "test", log_path=log)
    eg.record_binary_egress("example", "image", action="allow", reason="test", log_path=log)
    rows = _rows(log)
    assert [r.get("correlation_id") for r in rows] == ["req-test-000000020", "req-test-000000020", None]


def test_journal_events_written_in_a_request_carry_its_id(tmp_path, monkeypatch):
    from agent_friday.services import task_journal as tj
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    try:
        with correlation.bind("req-test-000000030"):
            tj.append("t-corr-1", "note", text="started from a request")
        tj.append("t-corr-1", "note", text="on the task's own thread")
        events = tj.read("t-corr-1")
        assert [e.get("correlation_id") for e in events] == ["req-test-000000030", None]
    finally:
        tj.reset_for_tests()
