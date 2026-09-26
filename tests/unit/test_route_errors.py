"""A route that fails says WHAT failed and gives an error id; it never puts the
exception's own text, or a traceback, into the response. The full exception
goes to the local log under the same id.

`UserFacingError` is the one narrow path whose message is shown verbatim,
because code raises it on purpose with text written for the user.
"""
from __future__ import annotations

import logging
import re

import pytest
from flask import Flask

from agent_friday.routes import _errors
from agent_friday.routes._errors import api_error, error_text, html_error
from agent_friday.user_errors import (
    UserFacingError,
    UserFacingLookupError,
    UserFacingValueError,
)

INTERNAL = "SECRET-INTERNAL-/path/detail"  # pragma: allowlist secret


@pytest.fixture
def app():
    return Flask(__name__)


def _raise(exc):
    try:
        raise exc
    except BaseException as e:  # noqa: BLE001 - the helper takes any exception
        return e


def test_api_error_says_what_failed_with_an_id_and_no_internals(app, caplog):
    exc = _raise(RuntimeError(INTERNAL))
    with app.test_request_context("/api/things", method="POST"):
        with caplog.at_level(logging.ERROR, logger="friday.routes.errors"):
            resp, status = api_error(exc, "Couldn't save the thing")
    body = resp.get_json()
    raw = resp.get_data(as_text=True)
    assert status == 500
    assert body["status"] == "error"
    assert re.fullmatch(r"[0-9a-f]{8}", body["error_id"])
    assert body["message"] == "Couldn't save the thing (error %s)" % body["error_id"]
    assert INTERNAL not in raw and "SECRET-INTERNAL" not in raw
    assert "Traceback" not in raw and "RuntimeError" not in raw
    # The log has everything the response left out, under the same id.
    [rec] = [r for r in caplog.records if body["error_id"] in r.getMessage()]
    assert rec.levelno == logging.ERROR
    assert "POST /api/things" in rec.getMessage()
    assert rec.exc_info and rec.exc_info[1] is exc
    assert INTERNAL in caplog.text and "Traceback" in caplog.text


@pytest.mark.parametrize("shape,key,envelope", [
    ("status", None, {"status": "error"}),
    ("ok", None, {"ok": False}),
    ("bare", None, {}),
    ("status", "error", {"status": "error"}),
    ("ok", "reason", {"ok": False}),
])
def test_api_error_keeps_the_envelope_the_ui_reads(app, shape, key, envelope):
    with app.test_request_context("/x"):
        resp, status = api_error(_raise(OSError(INTERNAL)), "Couldn't do it", 502,
                                 shape=shape, key=key, results=[])
    body = resp.get_json()
    field = key or {"status": "message", "ok": "error", "bare": "error"}[shape]
    assert status == 502
    for k, v in envelope.items():
        assert body[k] == v
    assert body[field].startswith("Couldn't do it (error ")
    assert body["results"] == []
    assert INTERNAL not in resp.get_data(as_text=True)


def test_each_failure_gets_its_own_id(app):
    with app.test_request_context("/x"):
        ids = {api_error(_raise(RuntimeError("x")), "w")[0].get_json()["error_id"]
               for _ in range(20)}
    assert len(ids) == 20


def test_a_plain_value_error_is_not_trusted(app):
    """Only UserFacingError is shown verbatim; a ValueError's text is not."""
    with app.test_request_context("/x"):
        resp, status = api_error(_raise(ValueError(INTERNAL)), "Couldn't parse it", 400)
    assert status == 400
    assert INTERNAL not in resp.get_data(as_text=True)
    assert resp.get_json()["error_id"]


def test_a_user_facing_error_is_shown_as_written(app, caplog):
    with app.test_request_context("/x"):
        with caplog.at_level(logging.ERROR, logger="friday.routes.errors"):
            resp, status = api_error(_raise(UserFacingValueError("Pick a date first.")),
                                     "Couldn't save", 400)
    body = resp.get_json()
    assert status == 400
    assert body["message"] == "Pick a date first."
    assert "error_id" not in body
    assert not caplog.records  # a user mistake is not a server error


def test_a_user_facing_error_may_carry_its_status(app):
    with app.test_request_context("/x"):
        resp, status = api_error(_raise(UserFacingError("Not yours.", status=403)), "w")
        _, kept = api_error(_raise(UserFacingValueError("Bad.")), "w", 409)
    assert status == 403 and resp.get_json()["message"] == "Not yours."
    assert kept == 409


def test_user_facing_subclasses_stay_catchable_as_their_builtin():
    with pytest.raises(ValueError):
        raise UserFacingValueError("x")
    with pytest.raises(KeyError):
        raise UserFacingLookupError("No such post.")
    assert str(UserFacingLookupError("No such post.")) == "No such post."


def test_error_text_for_bodies_that_are_not_an_error_envelope(app, caplog):
    with app.test_request_context("/x"):
        with caplog.at_level(logging.ERROR, logger="friday.routes.errors"):
            text = error_text(_raise(ConnectionError(INTERNAL)), "Couldn't reach the model")
    assert text.startswith("Couldn't reach the model (error ")
    assert INTERNAL not in text
    assert INTERNAL in caplog.text


def test_html_error_escapes_and_hides_internals(app):
    with app.test_request_context("/cb"):
        page, status = html_error(_raise(RuntimeError(INTERNAL + "<script>")),
                                  "Token exchange failed")
    assert status == 500
    assert page.startswith("<h2>Token exchange failed</h2>")
    assert INTERNAL not in page and "<script>" not in page
    assert re.search(r"Error [0-9a-f]{8}\.", page)


def test_works_outside_a_request(caplog):
    with caplog.at_level(logging.ERROR, logger="friday.routes.errors"):
        eid = _errors.log_failure(_raise(RuntimeError("x")), "background")
    assert eid in caplog.text
