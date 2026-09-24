"""Against Twilio itself, with Twilio's TEST credentials: nothing is sent and
nothing is charged.

Deselected by default (`network`). To run:
    set TWILIO_TEST_ACCOUNT_SID=AC...   (Console → API keys & tokens → Test credentials)
    set TWILIO_TEST_AUTH_TOKEN=...
    pytest tests/unit/test_phone_twilio_test_credentials.py --run-network -n 0

Test credentials authenticate as account SID + test auth token (they do not
support API keys), so the client is built with the account SID in both places.
The magic numbers are Twilio's: From +15005550006 is a valid sender, To
+15005550009 cannot receive texts.
"""
import os

import pytest

from agent_friday.phone import twilio_api

pytestmark = pytest.mark.network

SID = os.environ.get("TWILIO_TEST_ACCOUNT_SID", "")
TOKEN = os.environ.get("TWILIO_TEST_AUTH_TOKEN", "")


@pytest.fixture
def client():
    if not (SID and TOKEN):
        pytest.skip("set TWILIO_TEST_ACCOUNT_SID and TWILIO_TEST_AUTH_TOKEN")
    return twilio_api.TwilioClient(SID, SID, TOKEN, test_mode=True)


def test_a_text_is_accepted_without_being_sent(client):
    out = client.send_sms(to="+15125550100", from_="ignored", body="Friday test-credential check")
    assert out["sid"] and out["status"] in ("queued", "sent")


def test_an_undeliverable_number_is_refused_with_twilios_code(client):
    with pytest.raises(twilio_api.TwilioError) as e:
        client.send_sms(to="+15005550009", from_="ignored", body="x")
    assert e.value.code == 21614
