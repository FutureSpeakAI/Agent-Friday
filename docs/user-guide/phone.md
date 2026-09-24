# Phone

Friday can have its own phone number: it can text you, take voicemail, let you
approve actions by text, and (experimentally) talk with you on a call. It uses
your own Twilio account. **The phone is off by default**, and while it is off
nothing listens and nothing is sent.

## What it does, and who it may contact

- **You, on your verified cell.** Friday may text you on its own: alerts,
  approval codes, and replies to your texts. Only after you have proved the
  number is yours with a one-time code.
- **Anyone else, only when you say so.** Friday texts or calls another number
  only when your own message in that conversation names the number **and** you
  approve a card for that exact text or call.
- **Every call waits for a card**, including calls to you. A call to anyone
  else opens by saying Friday is an AI assistant.
- **Texts from your cell** get a reply (you can turn this off). A conversation
  that arrives by phone is read-only: Friday can look things up but cannot act.
  Anything that arrives by phone is treated as untrusted, because a caller ID
  can be spoofed.
- **Voicemail.** Callers hear the greeting (which says Friday is an AI) and can
  leave a message. It is transcribed on this PC, then deleted from Twilio.
- **Approve by text** (off by default). Pending approval cards are texted to
  you with a one-time code; reply `YES <code>` or `NO <code>`. A bare "yes"
  does nothing, and five wrong codes switch it off. This is weaker than the
  app: anyone holding your phone can answer.
- **Live calls from your cell** (off by default, early). Talk with Friday in
  its local voice. Falls back to voicemail if local voice is not ready. Not yet
  tested on real calls.

## What you need

- A Twilio account and a phone number that can send SMS and receive calls.
- A Twilio **API key** (SID starting `SK` and its secret) for sending, and the
  account's **auth token** for checking that incoming webhooks really come from
  Twilio.
- A domain on Cloudflare and the free `cloudflared` connector, to give Twilio
  an HTTPS address that reaches only Friday's phone listener.
- For texting US numbers: **A2P 10DLC registration** of your brand and
  campaign with Twilio. Until carriers approve it, texts may be filtered or
  blocked; calls are not affected. This is a carrier requirement, handled in
  Twilio's console, and can take days to weeks.

## Costs

Twilio bills your account directly: a monthly charge for the number, a charge
per text, and a charge per minute of calls, plus any registration fees for
10DLC. Friday records each charge as Twilio prices it, shows totals and the
current US rates under **Settings → Spending**, and the spending caps apply to
phone sends too. Check Twilio's pricing page for current prices.

## Setting it up

All of this is in **Settings → Accounts & Keys → Phone**.

1. **Account.** Paste the account SID (`AC…`), the API key SID (`SK…`) and
   secret, the auth token, and Friday's number. The secrets are encrypted on
   this PC and never shown again. Switch the phone on.
2. **Your cell.** Enter your number and press **Call me with a code** (works
   while 10DLC registration is pending) or **Text me a code**, then type the
   code back.
3. **Check SMS and voice with Twilio.** Confirms the number's capabilities.
4. **Tunnel.** In Cloudflare Zero Trust, create a tunnel with a public
   hostname such as `phone.<your-domain>` whose service is
   `http://127.0.0.1:3011` and whose path is limited to
   `^/twilio/(sms|sms-status|voice|voice-done|recording|call-status|media)$`.
   The repository's `ops/phone-tunnel.yml` is a template for running it
   locally. **Never point a tunnel at port 3000**, which is Friday itself.
5. **Public address.** Enter `https://phone.<your-domain>` and press **Point
   the number at Friday**, which sets the number's SMS and voice webhooks in
   Twilio.
6. **Check.** From any other device, `https://phone.<your-domain>/api/health`
   should return 404, and an unsigned request to `/twilio/sms` should be
   refused.
7. **Test.** Press **Send one test text to my cell**. It creates an approval
   card; approving it sends exactly one text.

Then set the 10DLC status in the same section to match what Twilio shows.

## How it is protected

- Twilio's webhooks are served by a separate listener on `127.0.0.1:3011` that
  has no Friday routes. Nothing that arrives there is treated as you.
- Every request is checked for Twilio's signature (against the public address
  you entered), the account, replays and rate limits, and is refused on any
  error.
- Texts Friday sends pass the egress gate, so private content is withheld.
- With **Ask Twilio to forget** on (the default), message bodies are blanked at
  Twilio once delivered and voicemail recordings are deleted once transcribed.
  Twilio still keeps numbers, times, durations and prices, and sees message
  text and call audio in transit.

## Turning it off

Switch the phone off in the same section. The listener stops, and Twilio's
requests fail at the tunnel. To remove it entirely, delete the tunnel in
Cloudflare and point the number's webhooks elsewhere in Twilio.
