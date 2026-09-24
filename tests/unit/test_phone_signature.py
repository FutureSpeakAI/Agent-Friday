"""X-Twilio-Signature validation, pinned to Twilio's own implementation.

Every expected signature below was produced by the official `twilio` Python
SDK's `RequestValidator.compute_signature` (twilio 9.11.1), not by the code
under test. Vector A is the long-standing example from Twilio's docs and SDK
tests (auth token "12345").
"""
from werkzeug.datastructures import MultiDict

from agent_friday.phone import signature as S

TOKEN = "12345"  # pragma: allowlist secret  (Twilio's documented example token)
DOC_URL = "https://mycompany.com/myapp.php?foo=1&bar=2"
DOC_PARAMS = {"CallSid": "CA1234567890ABCDE", "Caller": "+12349013030", "Digits": "1234",  # pragma: allowlist secret
              "From": "+12349013030", "To": "+18005551212"}  # pragma: allowlist secret  (Twilio's documented example numbers)
SIG_A = "0/KCTR6DLpKmkAf8muzZqo1nDgQ="          # DOC_URL, DOC_PARAMS
SIG_B = "EpDEmp1PyjDYp77YxYU3GILBWzE="          # same, URL written with :443
SIG_C = "IvYavak98yoAoW95WqEhA1nZ6fM="          # repeated MediaUrl + Body
SIG_D = "hPXmLwIy3Fgqv1i9KPmH/HhQ6zo="          # JSON body via bodySHA256
SIG_E = "oVvxRrbyq0KAZw3Vv+ltlAwMInU="          # a Media Streams WebSocket URL
JSON_BODY = b'{"CallSid":"CA1234567890ABCDE","Caller":"+12349013030"}'  # pragma: allowlist secret
JSON_URL = ("https://example.com/myapp?bodySHA256="
            "5ccde7145dfb8f56479710896586cb9d5911809d83afbe34627818790db0aec9")


def test_compute_matches_the_sdk_on_the_documented_example():
    assert S.compute(TOKEN, DOC_URL, DOC_PARAMS) == SIG_A


def test_validates_the_documented_example():
    assert S.validate(TOKEN, DOC_URL, DOC_PARAMS, SIG_A)


def test_a_signature_made_with_the_port_validates_without_it_and_back():
    port_url = DOC_URL.replace("mycompany.com", "mycompany.com:443")
    assert S.validate(TOKEN, DOC_URL, DOC_PARAMS, SIG_B)
    assert S.validate(TOKEN, port_url, DOC_PARAMS, SIG_A)


def test_repeated_params_are_signed_like_the_sdk():
    md = MultiDict([("MediaUrl", "b"), ("MediaUrl", "a"), ("Body", "hi")])
    assert S.compute(TOKEN, "https://phone.example.test/twilio/sms", md) == SIG_C
    assert S.validate(TOKEN, "https://phone.example.test/twilio/sms", md, SIG_C)


def test_json_body_is_checked_through_body_sha256():
    assert S.validate(TOKEN, JSON_URL, None, SIG_D, raw_json_body=JSON_BODY)
    assert not S.validate(TOKEN, JSON_URL, None, SIG_D, raw_json_body=JSON_BODY + b" ")
    no_hash = JSON_URL.split("?")[0]
    assert not S.validate(TOKEN, no_hash, None, S.compute(TOKEN, no_hash), raw_json_body=JSON_BODY)


def test_websocket_upgrade_is_signed_with_no_params():
    assert S.validate(TOKEN, "wss://phone.example.test/twilio/media", [], SIG_E)


def test_any_change_breaks_it():
    for k in DOC_PARAMS:
        tampered = dict(DOC_PARAMS, **{k: DOC_PARAMS[k] + "x"})
        assert not S.validate(TOKEN, DOC_URL, tampered, SIG_A), k
    assert not S.validate(TOKEN, DOC_URL, dict(DOC_PARAMS, Extra="1"), SIG_A)
    assert not S.validate(TOKEN, DOC_URL.replace("foo=1", "foo=2"), DOC_PARAMS, SIG_A)
    assert not S.validate(TOKEN, DOC_URL.replace("https", "http"), DOC_PARAMS, SIG_A)
    assert not S.validate("54321", DOC_URL, DOC_PARAMS, SIG_A)


def test_it_fails_closed_on_missing_pieces():
    assert not S.validate("", DOC_URL, DOC_PARAMS, SIG_A)
    assert not S.validate([], DOC_URL, DOC_PARAMS, SIG_A)
    assert not S.validate(TOKEN, DOC_URL, DOC_PARAMS, "")
    assert not S.validate(TOKEN, "", DOC_PARAMS, SIG_A)


def test_a_rotation_window_accepts_either_token():
    assert S.validate(["new-token-value", TOKEN], DOC_URL, DOC_PARAMS, SIG_A)
    assert not S.validate(["new-token-value"], DOC_URL, DOC_PARAMS, SIG_A)
