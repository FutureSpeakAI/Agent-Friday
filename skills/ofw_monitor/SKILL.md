# Skill: ofw_monitor

## Identity

Daily Our Family Wizard (OFW) monitoring skill for Agent Friday. Runs
browser automation against `ourfamilywizard.com` via the Claude-in-Chrome
MCP, tracks messages / calendar / expenses, performs **local-only**
sentiment analysis, and surfaces priority notifications.

> **Privacy posture:** OFW content is court-discoverable for one purpose
> only. This skill enforces that nothing leaves the local machine.
> Screenshots, message bodies, and the running sentiment model all stay
> in the Sovereign Vault under HMAC-SHA256 audit. No third-party
> processing — sentiment runs against a local classifier; LLM
> summarization is gated and explicitly opt-in.

## When this skill fires

- **Daily message scan** — 08:00 CT every day.
- **Deadline check** — every 12 hours, even outside active hours.
- **Weekly summary** — Sunday 19:00 CT.
- **Ad-hoc** — Stephen says "any new OFW", "anything from co-parent",
  "check the OFW calendar".

## What this skill does

1. **Authenticated session.** Picks up the active OFW Chrome session via
   the Claude-in-Chrome MCP. Never asks for credentials in chat.
2. **Inbox check.** Walks the OFW message list; pulls subject, sender,
   timestamp, and the first 300 characters for unread items only.
3. **Calendar tracker.** Diffs the live custody calendar against the last
   stored snapshot. Highlights schedule changes, swap requests, and any
   block flagged "needs action".
4. **Expense monitor.** Lists new expense submissions and any pending
   approval requests.
5. **Sentiment analysis.** Runs each new message through the local
   `nltk` / lightweight classifier — never via any LLM by default. Tags:
   `cooperative`, `neutral`, `passive-aggressive`, `hostile`, `urgent`.
6. **Response deadline tracker.** Surfaces any inbound message older than
   72 hours without a sent response.
7. **Pattern detection.** Rolling 30-day window — message volume,
   weekday distribution, average sentiment, response latency.
8. **Auto-archive to vault.** Every scanned message + screenshot is
   HMAC-SHA256 sealed and appended to `~/.friday/vault/ofw-archive.jsonl`.
9. **Notify.** Surfaces a typed payload per rule below.

## Inputs

- `mode` — `"full" | "messages" | "calendar" | "expenses"` (default `"full"`)
- `since` — ISO timestamp (default: last successful run)
- `notify` — bool (default `True`)

## Outputs

```json
{
  "scanned_messages": 3,
  "new_messages": 1,
  "calendar_changes": 0,
  "new_expenses": 1,
  "overdue_responses": 2,
  "tone_shifts": [
    {"from": "neutral", "to": "passive-aggressive", "msg_id": "ofw_..."}
  ],
  "archive_path": "~/.friday/vault/ofw-archive.jsonl",
  "duration_ms": 14210
}
```

## Notification rules

| Trigger              | Priority  | Template                           |
|----------------------|-----------|------------------------------------|
| `new_message`        | high      | "OFW · new from {{sender}}: {{subject}}" |
| `calendar_change`    | high      | "OFW · custody schedule changed: {{delta}}" |
| `expense_submitted`  | medium    | "OFW · expense {{amount}} submitted by {{party}}" |
| `response_overdue`   | critical  | "OFW · {{n}} message(s) overdue > 72h" |
| `tone_shift`         | low       | "OFW · {{from}} → {{to}}: review when convenient" |

## Privacy guarantees

1. **No content leaves the machine.** Sentiment uses local lexicon-based
   classifier; LLM summarization is gated behind explicit consent and
   logged as an opt-in event.
2. **Screenshots encrypted locally.** Stored AES-encrypted in
   `~/.friday/vault/ofw-screenshots/` with the key in Sovereign Vault.
3. **Archive is HMAC-sealed.** Every record carries a SHA-256 chain
   pointer to the prior record, like the cLaws decision-BOM. Tamper
   evidence on the entire stream.
4. **Personal info redacted before any logging surface.** Phone numbers,
   addresses, and minor's full name are scrubbed.

## Failure modes guarded

- OFW DOM changes — the skill captures structured snapshots before
  attempting field extraction; on extractor miss, archives the raw HTML
  and surfaces an "extractor degraded" maintenance notification.
- Login expired — surface a `consent_required` notification, do not
  attempt credential entry.
- Timezone drift — the skill normalizes everything to America/Chicago
  (CT) and tags raw timestamps with their captured TZ.
- Co-parent name leak — minor's name and co-parent's full name pass
  through the privacy_shield before being included in any notification.

## How this skill improves itself

Every run is recorded to SkillOpt with:
- `accuracy` — fraction of priority alerts Stephen marked relevant
- `user_satisfaction` — explicit feedback ("good catch" / "noise")
- `completeness` — did we capture all three streams (messages, calendar, expenses)?
- `latency_ms` — total scan time including browser automation
- `cost_usd` — should remain $0; local classifier only

If the rolling outcome drops, autoresearch proposes:
- New sentiment lexicon entries learned from feedback,
- Tighter / looser tone-shift thresholds,
- Switching to a deeper local classifier when the lightweight one is
  insufficient (still without ever calling a remote model).

## Success criteria

A run is successful if it:

1. Completes within 90 seconds end-to-end,
2. Archives every new message + a screenshot before any notification fires,
3. Never surfaces co-parent or minor identifying details in non-vault logs,
4. Hits the `response_overdue` rule for every message past 72 hours with
   no false positives.
