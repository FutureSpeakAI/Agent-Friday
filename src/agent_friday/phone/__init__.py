"""Friday's phone: SMS, voicemail and calls over Twilio.

Layout, and the one rule that shapes it:

    signature.py   X-Twilio-Signature, computed exactly as Twilio computes it.
    guard.py       replay protection and rate limits for the public ingress.
    spool.py       the hand-off between the ingress and the rest of Friday.
    ingress.py     the ONLY code reachable from the internet: a separate WSGI
                   app on its own loopback port, holding Twilio's webhook
                   paths and nothing else.
    config.py      the Phone settings and the vault-held credentials.
    twilio_api.py  outbound REST calls, made with the account's API key.
    service.py     everything Friday does with a validated event.
    live_call.py   Phase 2: a live call bridged into Friday's voice pipeline.

THE RULE: nothing that arrives over the phone is the owner. The ingress is a
different Flask app on a different socket, so no Friday route can be reached
through it, and nothing it receives is ever treated as a local request. An
inbound text from the owner's own verified cell is still untrusted input: a
phone number can be spoofed, and the text is whatever the sender typed.

The modules the ingress uses (signature, guard, spool, ingress) import nothing
from `agent_friday.core`, so the public surface does not drag in the app.
"""
