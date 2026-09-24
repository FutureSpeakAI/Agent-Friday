"""Outbound calls to Twilio's REST API, authenticated with an API key.

An API key (SK...) and its secret authenticate as HTTP Basic against the
account's REST API. The key can be revoked in the Twilio Console without
touching the account's auth token. That is why the outbound direction uses it.
(The auth token is still needed, separately, to check inbound signatures; see
signature.py.)

Plain `requests`, no Twilio SDK: the few endpoints used here do not justify a
dependency, and every request is visible in one file.

TEST CREDENTIALS. Twilio's test account (Console → Account → API keys & tokens
→ Test credentials) accepts Messages and Calls requests without sending
anything or charging, and answers the magic numbers: From `+15005550006` is a
valid sender; To `+15005550009` is a number that cannot receive SMS. A client
built with `test_mode=True` uses those; the automated suite never reaches the
network at all (it injects a fake session).

Nothing here logs a secret. Errors carry Twilio's code and message only.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

_log = logging.getLogger("friday.phone.twilio")

API = "https://api.twilio.com/2010-04-01"
PRICING = "https://pricing.twilio.com/v1"
MESSAGING = "https://messaging.twilio.com/v1"

#: Twilio's magic "valid sender" number, usable only with test credentials.
TEST_FROM = "+15005550006"

TIMEOUT_S = 15


class TwilioError(RuntimeError):
    def __init__(self, status: int, code: Any = None, message: str = ""):
        self.status = status
        self.code = code
        self.message = message
        super().__init__("Twilio %s%s: %s" % (status, " (code %s)" % code if code else "",
                                              message or "request failed"))


class TwilioClient:
    def __init__(self, account_sid: str, api_key_sid: str, api_key_secret: str,
                 *, session=None, test_mode: bool = False):
        if not (account_sid and api_key_sid and api_key_secret):
            raise ValueError("incomplete Twilio credentials")
        self.account_sid = account_sid
        self._auth = (api_key_sid, api_key_secret)
        self.test_mode = test_mode
        if session is None:
            import requests
            session = requests.Session()
        self._s = session

    def __repr__(self) -> str:                      # never show the secret
        return "TwilioClient(%s, key=%s)" % (self.account_sid, self._auth[0])

    # ── plumbing ─────────────────────────────────────────────────────────────
    def _acct(self, path: str) -> str:
        return "%s/Accounts/%s/%s" % (API, self.account_sid, path.lstrip("/"))

    def _call(self, method: str, url: str, *, data: Optional[dict] = None,
              params: Optional[dict] = None, raw: bool = False):
        r = self._s.request(method, url, auth=self._auth, data=data, params=params,
                            timeout=TIMEOUT_S)
        status = getattr(r, "status_code", 0)
        if status >= 400:
            code, msg = None, ""
            try:
                j = r.json()
                code, msg = j.get("code"), j.get("message") or ""
            except Exception:
                msg = (getattr(r, "text", "") or "")[:200]
            raise TwilioError(status, code, msg)
        if raw:
            return r.content
        if status == 204 or not getattr(r, "content", b""):
            return {}
        return r.json()

    # ── the number ───────────────────────────────────────────────────────────
    def number_info(self, phone_number: str) -> dict:
        """The IncomingPhoneNumber record for our number: its SID, its
        capabilities (voice / sms / mms / fax) and its current webhook URLs."""
        j = self._call("GET", self._acct("IncomingPhoneNumbers.json"),
                       params={"PhoneNumber": phone_number})
        rows = j.get("incoming_phone_numbers") or []
        if not rows:
            raise TwilioError(404, None, "%s is not a number on this account" % phone_number)
        n = rows[0]
        return {"sid": n.get("sid"), "phone_number": n.get("phone_number"),
                "friendly_name": n.get("friendly_name"),
                "capabilities": {k: bool(v) for k, v in (n.get("capabilities") or {}).items()},
                "sms_url": n.get("sms_url"), "voice_url": n.get("voice_url"),
                "status_callback": n.get("status_callback"),
                "sms_application_sid": n.get("sms_application_sid"),
                "voice_application_sid": n.get("voice_application_sid")}

    def point_number_at(self, number_sid: str, base_url: str) -> dict:
        """Point the number's SMS and voice webhooks at the ingress."""
        base = base_url.rstrip("/")
        return self._call("POST", self._acct("IncomingPhoneNumbers/%s.json" % number_sid),
                          data={"SmsUrl": base + "/twilio/sms", "SmsMethod": "POST",
                                "VoiceUrl": base + "/twilio/voice", "VoiceMethod": "POST",
                                "StatusCallback": base + "/twilio/call-status",
                                "StatusCallbackMethod": "POST"})

    # ── messages ─────────────────────────────────────────────────────────────
    def send_sms(self, *, to: str, from_: str, body: str,
                 status_callback: Optional[str] = None) -> dict:
        data = {"To": to, "From": TEST_FROM if self.test_mode else from_, "Body": body}
        if status_callback:
            data["StatusCallback"] = status_callback
        j = self._call("POST", self._acct("Messages.json"), data=data)
        return {"sid": j.get("sid"), "status": j.get("status"),
                "price": j.get("price"), "price_unit": j.get("price_unit"),
                "num_segments": j.get("num_segments"),
                "error_code": j.get("error_code"), "error_message": j.get("error_message")}

    def get_message(self, sid: str) -> dict:
        return self._call("GET", self._acct("Messages/%s.json" % sid))

    def redact_message(self, sid: str) -> None:
        """Blank the body Twilio keeps for a message. Twilio allows this once
        the message is in a final state; the record (numbers, time, price)
        remains."""
        self._call("POST", self._acct("Messages/%s.json" % sid), data={"Body": ""})

    # ── calls ────────────────────────────────────────────────────────────────
    def create_call(self, *, to: str, from_: str, twiml: str,
                    status_callback: Optional[str] = None) -> dict:
        data = {"To": to, "From": TEST_FROM if self.test_mode else from_, "Twiml": twiml,
                "Record": "false"}
        if status_callback:
            data["StatusCallback"] = status_callback
            data["StatusCallbackEvent"] = "completed"
        j = self._call("POST", self._acct("Calls.json"), data=data)
        return {"sid": j.get("sid"), "status": j.get("status")}

    def get_call(self, sid: str) -> dict:
        return self._call("GET", self._acct("Calls/%s.json" % sid))

    def update_call(self, sid: str, twiml: str) -> dict:
        """Replace what a live call is doing (used to hang up or redirect)."""
        return self._call("POST", self._acct("Calls/%s.json" % sid), data={"Twiml": twiml})

    # ── recordings ───────────────────────────────────────────────────────────
    def fetch_recording_wav(self, recording_sid: str) -> bytes:
        return self._call("GET", self._acct("Recordings/%s.wav" % recording_sid), raw=True)

    def delete_recording(self, recording_sid: str) -> None:
        self._call("DELETE", self._acct("Recordings/%s.json" % recording_sid))

    # ── pricing and registration (read-only) ─────────────────────────────────
    def messaging_prices(self, country: str = "US") -> dict:
        return self._call("GET", "%s/Messaging/Countries/%s" % (PRICING, country))

    def voice_number_price(self, number: str) -> dict:
        return self._call("GET", "%s/Voice/Numbers/%s" % (PRICING, number))

    def a2p_brands(self) -> list:
        j = self._call("GET", "%s/a2p/BrandRegistrations" % MESSAGING)
        return [{"sid": b.get("sid"), "status": b.get("status"),
                 "failure_reason": b.get("failure_reason")}
                for b in (j.get("data") or j.get("brand_registrations") or [])]


def client_from_config(*, session=None, test_mode: bool = False) -> Optional[TwilioClient]:
    """A client from the vault-held credentials, or None when any is missing."""
    from agent_friday.phone import config
    creds = config.api_credentials()
    if not creds:
        return None
    return TwilioClient(*creds, session=session, test_mode=test_mode)


def summarize_prices(messaging: dict, voice: dict) -> dict:
    """Pull the few numbers Settings shows out of the pricing API's answers.

    Returns None for anything the answer did not contain rather than a guess.
    """
    out: dict = {"sms_outbound_usd": None, "sms_inbound_usd": None,
                 "voice_outbound_per_min_usd": None, "voice_inbound_per_min_usd": None}
    try:
        for p in messaging.get("outbound_sms_prices") or []:
            for pr in p.get("prices") or []:
                if pr.get("number_type") == "local":
                    out["sms_outbound_usd"] = float(pr.get("current_price"))
                    break
            if out["sms_outbound_usd"] is not None:
                break
        for pr in messaging.get("inbound_sms_prices") or []:
            if pr.get("number_type") == "local":
                out["sms_inbound_usd"] = float(pr.get("current_price"))
    except Exception:
        pass
    try:
        ob = voice.get("outbound_call_price") or {}
        if ob.get("current_price") is not None:
            out["voice_outbound_per_min_usd"] = float(ob["current_price"])
        ib = voice.get("inbound_call_price") or {}
        if ib.get("current_price") is not None:
            out["voice_inbound_per_min_usd"] = float(ib["current_price"])
    except Exception:
        pass
    return out
