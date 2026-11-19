# Phone: switch the PC's model and voice per conversation

Contract for the phone lane (and any owner-bound tablet or watch). All paths are on the PC's gateway
(`/tv/v1/...`), after the normal session (challenge, open, bearer). Only an **owner device** (paired and bound to the
owner at the PC) may choose; a TV or family device sees the lists (the voice list's per-conversation binding is read-only and carries no conversation text) and gets `403 owner_only` on any switch.

A choice is **per conversation**: it never changes the PC's own settings or any other chat. Nothing is switched
quietly and nothing is answered by a different model or voice without saying so.

## 1. Read what the PC offers

| Call | Gives |
|---|---|
| `GET /tv/v1/models` | every chat model: id, name, provider, local or cloud, `available` and `reason`, speed, `selected` |
| `GET /tv/v1/voices` | every voice engine and its voices: provider, `local`/`cloud`, `key_present`, `can_serve` and `blocked.reason`, `latency_class` (a labelled guide), `what_leaves` (in words), `voices[]`, `voices_complete` |
| `GET /tv/v1/voices?conversation_id=C` | the same, plus `conversation`: the engine and voice C is bound to |

Show `reason` / `blocked.reason` as written; they are the PC's own words (Local only, key missing, not installed).

## 2. Switch the model for one conversation

`POST /tv/v1/model/switch` `{ "model": "<id, display name or family word>", "conversation_id": "C"? }`

* No `conversation_id`: the PC starts the chat on that model and returns its id. Keep it and send it with every turn.
  If that switch is refused (`seat_refused` / `seat_pending`) the refusal still carries the new chat's `conversation_id`: retry INTO it, do not send another switch without one.
* `200`: `{ ok, conversation_id, model, asked, applies: "this_conversation", takes_effect: "next_turn", facts, served }`.
  `facts` is the PC's own row for the model: `switch` (`cold`, `expected_seconds`, `basis`: a cold local model needs
  loading; the number is measured on this PC or benchmarked, labelled), `measured` (median and p90 first-token wait
  and cache share from real answered turns, or null), `benchmark` (VRAM, tokens/s, cold load, date), `capabilities`
  (declared by the provider, labelled as such). `switch`, `measured` and `benchmark` appear once the model-slate timing work ships (#848, #862); until
  then they are absent, so treat every field in `facts` as optional. Show "about N s the first time" from `switch` when it is present and `cold` is true.
* Refusals (the model is not bound; a refused switch that had to START a chat returns that chat's `conversation_id`: retry INTO it): `400 unknown_model` (with `did_you_mean`), `409 model_unavailable`,
  `409 seat_refused` (the PC would not bind it; the reason is in `error`), `404 unknown_conversation` (no such chat), `409 seat_pending` (the PC is still checking its local models: **retry** after a moment, `retry: true`), `400 bad_request` (also a chat id that is not letters, digits, `_` and `-`), `403 owner_only`,
  `503 slate_unavailable` (the PC could not check the model), `503 chat_unavailable` (the PC's chat store did not answer a create, lookup or write).
* Sending `model` in a `/tv/v1/messages` body does the same bind and then runs the turn (its refusals, and `503` when the desktop does not take the turn, carry the new chat's `conversation_id` too: retry into it); use `model/switch` when the
  user picks a model without sending a message.

## 3. Switch the voice engine for one conversation

`POST /tv/v1/voice/switch` `{ "engine": "local"|"local-gpu"|"gemini"|"openai"|"cloud:<provider>", "voice"?, "conversation_id": "C" }`

* Local engines and cloud *synthesis* bind at once: `200 { applies: "this_conversation", bound, what_leaves,
  takes_effect: "next_reply" }`.
* A cloud **speech** engine (Gemini Live, OpenAI Realtime) is never switched from the phone: `409 needs_choice`
  with `what_leaves` and `asked_on_pc`. The PC raises its own "just this call, or always" question for the owner;
  `applies` is `nothing_changed`. Show `what_leaves` and tell the user to answer on the PC.
* Other refusals: `400 unknown_engine` / `unknown_voice`, `409 engine_unavailable` / `local_only_mode`, `403 owner_only`.
* Live engines bind a voice at connect time: `takes_effect` is `next_connection`, never "now".

## 4. See what is bound and what really answered

`GET /tv/v1/conversations/C/state` →
`{ conversation_id, model: { bound, facts }, voice: { engine, voice, still_valid, what_leaves, notice } | null,
last_served: { model, provider, seat, local, requested, expected, substituted, fallback, notice, at } | null }`

* `last_served` is what the PC's backend recorded as generating the latest reply **sent through the phone gateway**, **not** what was requested. `requested` is only what the device asked for in that turn (null when it sent no model). A turn with no `model` is compared with `expected`: the model the chat is bound to (its own seat, else its project's), read before the turn starts. `substituted` is set when another model answered than `requested`/`expected`. It is not updated by turns taken at the desk, and a failed or unrecorded turn leaves the previous value (compare `at`/the turn you just sent, not only this field).
  `substituted: true` means a different model answered than the one asked for; show `notice`/`fallback` with it.
  It is held in memory, so it is `null` after a PC restart until the next turn; the same truth rides on every reply's
  closing `message_delta.friday` block (which also carries `voice_bound` on a voice turn).
* The bound voice is re-checked against the live voice slate on every read: `still_valid: false` plus a `notice`
  means the engine went away (key removed, Local only turned on) and the PC will not use it.
* `403 owner_only`, `404 unknown_conversation` (also a malformed id here; on `/model/switch` a malformed id is `400 bad_request`), `503 chat_unavailable` (the PC's chat store did not answer: not the same as no such chat).

## Flow the phone should implement

1. Open session. `GET /tv/v1/models` and `GET /tv/v1/voices` (cache for the screen; refresh on open).
2. User picks a model: `POST /tv/v1/model/switch`. Show `facts.switch` when present, and any refusal reason. Keep `conversation_id`.
3. User picks a voice engine: `POST /tv/v1/voice/switch`. On `409 needs_choice` show `what_leaves` and wait for the PC.
4. Send turns with the same `conversation_id`. On each reply read `friday.model` and `friday.substituted`;
   label the bubble with the served model, not the requested one.
5. On reopening a chat: `GET /tv/v1/conversations/C/state` to restore the pickers and the "answered by" label.

## Where this is held

`tests/unit/test_phone_switching_contract.py` (model switch, refusals, owner-only, state), `tests/unit/test_tv_voices_contract.py`
(voice), `tests/unit/test_owner_turn_model_truth.py` (the served truth on replies). Sources: `tv_gateway/conversation_state.py`,
`tv_gateway/voices.py`, `services/model_slate.py`, `services/voice_slate.py`.
