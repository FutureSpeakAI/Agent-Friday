# Off the record

Status: implemented (`services/off_record.py`); every store listed below has
a test in `tests/unit/test_off_record_writes_nothing.py`.

While **Go off the record** is on, a conversation (typed or spoken) writes
nothing about itself to disk. `off_record_stops_storage` defaults to on; it
exists only so the old behaviour ("pause the context log") can be chosen
deliberately.

## What happens to the conversation

- Messages are kept in this process's memory, per conversation, so the model
  still sees the conversation while it lasts (`conversations.messages` returns
  them after the stored ones).
- Switching off the record off drops them. A restart drops them too, because
  nothing was written.
- Every chat box shows "Off the record: nothing is being saved" as its
  placeholder and tooltip, with a pink border; the Settings and Quick Settings
  switches say the same.

## Stores and what each does while off the record

| Store | While off the record |
|---|---|
| Context log (`vault/context-log/*.jsonl`) | Nothing written, including tool-call and file-read entries. |
| `chat_history.json` | Off-record rows stay in memory and are filtered out of every save. |
| `conversations/<id>/messages.jsonl` and `conversation.json` | Nothing written; no title is taken from an off-record message. |
| Trajectories (`trajectories.jsonl`, SkillOpt metrics) | Nothing captured. |
| Cognitive memory (`memory/*.json`, `memory_ledger.jsonl`) | Nothing written, not even the ledger line. |
| Conversation index (ChromaDB) and emotional arc | Nothing indexed. |
| Behavioral monitor (`behavioral_monitor/*`) | Still scores and alerts. What it stores holds tool names, rings, outcome classes, scores and times; no request text, arguments or targets. |
| Reasoning traces (`traces/ledger.jsonl`) | Shown live, never archived, even if the trace finishes after off-record ends. |
| Task journal and task ledger (`tasks/*`) | A task started off the record writes nothing for its whole life. |
| Voice call summary (distill) | Turns spoken off the record never enter the call's summary. |
| `decisions.jsonl` | Nothing written. |
| `approvals.json` | The card works in memory; the file keeps only its receipt fields (id, kind, class, status, times). After a restart a pending off-record card reads as expired. |
| Notifications (`notifications.json`) | The file keeps a stub titled "Off the record"; the words live in memory. |
| Web page cache (`research/sources`) | Fetched pages are not cached. |
| Boot-guard known-good snapshot | Deferred; the previous snapshot stays in place. |

## What is still written

The signed action receipts (`decision-bom.jsonl`) and governance logs are
still written, but hold only what a receipt needs:

- receipts: tool, class, decision, time, and the ids that link them to a card,
  grant or task; no reason text, target, or hash of the arguments;
- dissent events: the verdict fields, with the action summary, statement and
  conflicting text removed;
- the egress audit log: provider, field, tier and verdict, with the reason
  replaced by "off the record";
- the activity ledger: its metadata fields, without free text.

Settings, credentials and model downloads are not conversation content and
are unaffected.
