"""Shared fakes for the phone tests. Nothing here reaches the network.

The auth token and API secret below are made-up strings of the right shape;
they are not, and never were, real Twilio credentials.
"""
from __future__ import annotations

import json

import pytest

FAKE_ACCOUNT = "AC" + "0" * 32
FAKE_KEY_SID = "SK" + "1" * 32
FAKE_TOKEN = "faketoken" + "a" * 23          # pragma: allowlist secret
FAKE_SECRET = "fakesecret" + "b" * 22        # pragma: allowlist secret
PUBLIC = "https://phone.example.test"
FRIDAY_NUMBER = "+15125550100"
OWNER = "+15125550111"
STRANGER = "+15125550199"


class FakeTwilio:
    """Records every request; answers like Twilio would for the few calls used."""

    def __init__(self):
        self.calls = []
        self.price = None
        self.fail = None

    def _resp(self, status, body=None, content=None):
        class R:
            pass
        r = R()
        r.status_code = status
        r.content = content if content is not None else json.dumps(body or {}).encode()
        r.text = r.content.decode("latin-1")
        r.json = lambda: json.loads(r.content)
        return r

    def request(self, method, url, auth=None, data=None, params=None, timeout=None):
        self.calls.append({"method": method, "url": url, "data": dict(data or {}),
                           "params": dict(params or {}), "auth_user": (auth or ("",))[0]})
        if self.fail:
            return self._resp(400, {"code": self.fail, "message": "refused by fake"})
        if url.endswith("/Messages.json") and method == "POST":
            return self._resp(201, {"sid": "SM%032d" % len(self.calls), "status": "queued",
                                    "num_segments": "1"})
        if "/Messages/" in url and method == "GET":
            return self._resp(200, {"price": self.price})
        if "/Messages/" in url and method == "POST":
            return self._resp(200, {"body": ""})
        if url.endswith("/Calls.json") and method == "POST":
            return self._resp(201, {"sid": "CA%032d" % len(self.calls), "status": "queued"})
        if "/Calls/" in url and method == "GET":
            return self._resp(200, {"price": self.price})
        if url.endswith(".wav"):
            return self._resp(200, content=b"RIFFfake")
        if "/Recordings/" in url and method == "DELETE":
            return self._resp(204, content=b"")
        if "IncomingPhoneNumbers.json" in url:
            return self._resp(200, {"incoming_phone_numbers": [{
                "sid": "PN" + "2" * 32, "phone_number": FRIDAY_NUMBER,
                "capabilities": {"voice": True, "sms": True, "mms": True, "fax": False},
                "sms_url": "", "voice_url": ""}]})
        return self._resp(404, {"code": 20404, "message": "not found"})

    def sent_bodies(self):
        return [c["data"]["Body"] for c in self.calls
                if c["url"].endswith("/Messages.json") and c["method"] == "POST"]


@pytest.fixture
def phone_home(tmp_path, monkeypatch):
    """An isolated Friday home with the phone configured and switched on,
    secrets served from memory, and the approval queue in tmp."""
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    from agent_friday.phone import config
    secrets = {"auth_token": FAKE_TOKEN, "api_key_secret": FAKE_SECRET}
    monkeypatch.setattr(config, "get_secret", lambda name: secrets.get(name))
    config.update({"enabled": True, "account_sid": FAKE_ACCOUNT, "api_key_sid": FAKE_KEY_SID,
                   "phone_number": FRIDAY_NUMBER, "public_base_url": PUBLIC,
                   "owner_cell": OWNER})
    from agent_friday.services import approvals
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    return tmp_path


@pytest.fixture
def fake_twilio(monkeypatch):
    from agent_friday.phone import twilio_api
    fake = FakeTwilio()
    real = twilio_api.client_from_config

    def build(*, session=None, test_mode=False):
        return real(session=fake, test_mode=test_mode)
    monkeypatch.setattr(twilio_api, "client_from_config", build)
    return fake


@pytest.fixture
def quiet(monkeypatch):
    """Capture notifications; keep dissent/decision seams deterministic."""
    sent = []
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: sent.append(kw) or kw)
    return sent


def verify_owner(monkeypatch):
    from agent_friday.phone import config
    import time
    config.mark_owner_verified(OWNER, time.time())
