# Phone: SMS, voicemail and calls over Twilio

**Status (2026-09-24):** Phase 1 (texts in and out, owner-cell verification,
approval by text, voicemail) and the Phase 2 live-call bridge are built and
unit-tested. None of it has carried real traffic yet: that needs the owner's
credentials, a tunnel, and the first approved test text. The live-call bridge
has only run against a simulated Media Stream. Everything is **off by
default**.

Code: `src/agent_friday/phone/`, `src/agent_friday/routes/phone.py`, the
`text_by_phone` / `call_by_phone` tools and the phone-origin rule in
`services/agent.py`, the Phone section of Settings → Accounts & Keys in `index.html`
and `ui_parts/app.html`, and a PHONE section under Spending.
Tests: `tests/unit/test_phone_*.py`, `tests/api/test_phone_routes.py`.

## Who Friday may contact

Friday contacts only the owner's own phone unless the owner explicitly tells it
to contact someone else. That is enforced in `service.checkpoint`
and `service._check_target`, not in a prompt:

| Target | Text | Call |
|---|---|---|
| Verified owner cell | Sent directly: alerts, approval codes, replies. Capped per hour and day, spend hard stop, egress gate. | Approval card. |
| Any other number | Only if the owner's own message in that turn names the number, **and** an approval card is approved. | The same, and the call opens with an AI disclosure. |
| Anyone, from anything that arrived by phone | Never. | Never. |

"The owner's own message" is `session_ctx["owner_text"]`, stamped by
`prepare_confirmation_ctx` in both chat endpoints and exposed to tools as
`agent._CURRENT_OWNER_TEXT`. It is empty for background tasks and phone-origin
turns, so neither can name a number. The voice session does not stamp it yet,
so from voice Friday can text only the owner.

Every card is fingerprinted (recipient plus text), and one approval buys one
send. The approval is burned before the network call, so two racing senders
send once.

**The governance checkpoint** (`governance/action_gate.py`) sits in front of
all of this:

* `text_by_phone` and `call_by_phone` are OUTWARD tools. `call_by_phone` is
  SELF_GATED: it only ever raises its own card. `text_by_phone` is not, so in
  chat the checkpoint asks for a yes before the handler runs. From background
  or scheduled work it needs a card (or a scoped grant naming the tool).
* In `services/taint.py` their `to` argument is a recipient. A number that
  came from something Friday read gets a card, whoever typed it.
* Sends that do not run as a tool call (alerts, approval codes, replies,
  approved cards) pass `service.checkpoint`. That takes the checkpoint's two
  fail-closed steps directly: the cLaws integrity check
  (`action_gate.verify_claws`), and a signed receipt in `decision-bom.jsonl`
  (`tool: phone:<action>`). Either one failing holds the send.

The owner's cell becomes the allowlist only after verification: the owner enters it
in Settings, and Friday texts or calls a one-time code to it. Five tries, ten
minutes, five sends an hour. The call option works while carrier registration
still blocks texts.

## Exposure: the ingress

Friday runs on the owner's PC, and a public tunnel to port 3000 once exposed
the whole API. So the phone has its own public surface:

* a **separate Flask app on its own socket**, `127.0.0.1:3011`, holding only
  `/twilio/{sms,sms-status,voice,voice-done,recording,call-status,media}`.
  No Friday route exists on it, and it has no session or login, so it can
  never read a request as the local owner;
* a **named Cloudflare tunnel** whose ingress rules forward only those paths
  to 3011 and return 404 at the edge for everything else (`ops/phone-tunnel.yml`).

Every request must pass, in order: a rate limit (per client and global, with
a much smaller budget after a bad signature) → phone switched on and the auth
token present (else 503) → `X-Twilio-Signature` → the body's `AccountSid` →
not a replay → written to the spool. Any failure stops the request, and an
error in the replay store or spool is a 503, never a pass. Bodies over 32 KB
are refused before parsing.

**Which credential validates signatures.** Twilio signs webhooks with the
account's primary **auth token** (HMAC-SHA1). An API key cannot validate them;
it authenticates this machine's REST calls to Twilio. So the phone stores two
secrets, both encrypted through `credential_store.write_secret`: the API key
secret (outbound) and the auth token (inbound). `signature.py` matches the
official SDK's `RequestValidator` (3,000 randomised differential cases agreed;
the tests pin SDK-generated vectors), including JSON `bodySHA256` and the
with/without-port quirk. It checks against the **configured public URL**,
never one rebuilt from the request's Host header.

**Replay.** Twilio's signature has no timestamp, so a captured request stays
valid forever. The ingress records a hash of each accepted signed request for
30 days, and events are unique by Twilio SID, so a replay or a Twilio retry is
answered without effect.

The off switch stops the listener outright. With the phone off, the tunnel
gets a connection error and Twilio gets nothing.

## What arrives

* **A text from the verified cell** runs one agent turn with
  `session_ctx={"origin": "phone", "authenticated": False}`.
  `_governance_check` refuses every tool above ring 0 for `origin: phone`,
  whatever else the context says. The text is wrapped in an UNVERIFIED
  envelope, passed through `action_policy.strip_authority_overrides`, and noted
  in the taint ledger (`services/taint.py`) as content Friday read, so any
  detail copied from it into an action is flagged. The reply goes to the
  verified cell, never to the sender's number, and through the egress gate.
* **"YES 123456" / "NO 123456"** from the verified cell decides the approval
  that code was issued for. A bare "yes" never does. Codes are single-use,
  hashed at rest, and die with their card. Five wrong codes in an hour switch
  approval-by-text off and say so. It is **off by default**, because it is
  weaker than the app: anyone holding the phone can answer, and Twilio sees the
  code.
* **A text from anyone else** goes to no agent and gets no reply (a reply
  costs money and confirms the number is live). It becomes a notification
  marked untrusted.
* **A call** hears the greeting, which says Friday is an AI, and can leave up
  to two minutes of voicemail. The recording is fetched, transcribed on this
  machine (the local voice engine if ready, otherwise faster-whisper on CPU),
  deleted from Twilio, and only the transcript is kept, encrypted.

## Phase 2: live calls

`live_call.py` bridges Twilio Media Streams (8 kHz mu-law over the ingress
WebSocket, signature checked on the upgrade) into the desktop's own local VAD,
STT and TTS (`services/local_voice.py`), with a pure-Python G.711 codec
(`audioop` is gone in Python 3.13). A live call is offered only when
`live_calls` is on, the caller ID is the verified cell, and the local engine
is ready; otherwise it falls back to voicemail. The engine is shared with the
desktop voice session and push-to-transcribe, so they compete for it.

* **Disclosure.** On an outbound call to anyone but the owner, Twilio speaks
  the fixed disclosure from TwiML before the stream starts. A bridge failure
  cannot skip it.
* **The mid-call approval pattern.** Agent turns in a call are phone-origin
  (ring 0). When an action comes up, Friday says it must check with the owner
  and writes `[[ASK_OWNER: …]]`. The bridge strips that from speech, raises
  an approval card (texted with a code if approval-by-text is on), and tells
  the caller once it is decided. Nothing is ever done from the call itself.

## Cost and privacy

* Prices arrive from Twilio after the fact. `sync_prices_and_redact` fills
  them into the ledger and records each once in `cost_meter` as provider
  `twilio`, kind `phone`, so they appear in Cost & Usage (which also has a
  PHONE section with the current per-text and per-minute rates from the
  Pricing API). Sends go through `spend_guard.check`.
* **What Twilio keeps, and what Friday asks it to drop.** With "Ask Twilio to
  forget" on (the default), message bodies are blanked (`POST Body=""`) once
  final, and recordings are deleted once transcribed. Friday never asks Twilio
  to record or transcribe a call (`Record=false`).
* **What Twilio can still see:** numbers, times, durations and prices for
  every text and call (those records stay in the account); the text of every
  message while in flight; the audio of every call while it is live; the
  approval titles and codes Friday texts. The carriers see in-flight content
  too. The egress gate withholds private content from anything Friday texts.
* Locally, the spool keeps an inbound text only until it is handled, then
  scrubs it. The ledger holds no message bodies.

## Carrier registration (A2P 10DLC)

US carriers filter or block application texts from 10DLC numbers whose
brand/campaign registration is not approved. The owner's registration was
submitted and is under review. Settings shows the status, every text approval
card carries a warning while it is not "approved", and a carrier block (error
30034, 30007 or 30032) shows in the log as undelivered, with the likely
reason. Calls are unaffected.

## Setting it up (the owner's steps)

1. **Settings → Accounts & Keys → Phone:** paste the account SID, the new API key SID and
   secret, the **auth token**, and Friday's number. Switch the phone on.
2. **Your cell:** enter it, press "Call me with a code" (works during 10DLC
   review) or "Text me a code", and type the code back.
3. **Check SMS and voice with Twilio:** reads the number's capabilities.
4. **Tunnel.** Cloudflare Zero Trust → Networks → Tunnels → Create a tunnel
   (Cloudflared) → name it `friday-phone` → install the connector with the
   command it shows (Windows, as a service) → **Public hostname**: subdomain
   `phone`, your domain, path `^/twilio/(sms|sms-status|voice|voice-done|recording|call-status|media)$`,
   service `HTTP` `127.0.0.1:3011` → save. Do **not** add a hostname for
   port 3000. (Or run it locally from `ops/phone-tunnel.yml` after
   `cloudflared tunnel login` and `cloudflared tunnel create friday-phone`.)
5. **Settings → Accounts & Keys → Phone → Public address:** `https://phone.<your-domain>`, then
   "Point the number at Friday".
6. Check from any machine: `https://phone.<your-domain>/api/health` is 404,
   and an unsigned `POST /twilio/sms` is 403.
7. **First live test:** "Send one test text to my cell" creates an approval
   card; approving it sends exactly one text.
