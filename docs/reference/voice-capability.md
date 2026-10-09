# What voice can do

Voice can do anything chat can, unless the user has restricted Friday.

> Adding a capability to voice? See
> [voice-tool-contract.md](voice-tool-contract.md) — how a typed tool becomes
> voice-callable, spoken confirmation, reading an approval card back, and the
> rule for anything that touches private data.

## How

- **Direct tools** (`services/voice_engine.py`, `_VOICE_LIVE_TOOLS` and
  `_VOICE_SHARED_TOOLS`) answer quick things inside the conversation: the
  calendar, email, news, the wiki, files, the screen.
- **Organizing** (`organize_email`, `organize_files`, `organize_wiki`,
  `undo_action`, `answer_card`) raises one card per batch and reads it back in
  a sentence; the user answers yes, no, or change it, and `answer_card`
  decides it through the voice path's own rule. The cloud voice model hears
  counts, never a subject, sender, file or page name; the card lists them on
  screen, and an ambiguous page name is numbered there to choose from.
- **`delegate_to_friday`** hands any other request to the full Friday agent.
  It runs as a background task with the full tool registry, on the
  conversation's bound seat or else the background seat
  (`settings.subagent_model`), and reports to the voice call's conversation.
  Friday acknowledges it in a sentence and keeps talking.
- **Results come back.** A finished task posts its outcome into its
  conversation (`agent._post_task_result_to_conversation`). If a voice call is
  still open on that conversation, the outcome is queued for the live model
  (`services/voice_live_channel.py`). It is handed over at the next quiet
  moment, through the same egress gate as any tool result.
- **Governance is unchanged.** Every voice tool call, delegated work included,
  goes through `agent._execute_tool` with the call's conversation and the
  user's latest spoken words in its context. Outward actions raise approval
  cards, and the loop guard applies.

## What remains, and why

`voice_engine.voice_restrictions()` returns this list with each entry's
current state. The Voice tab in Settings shows it.

| Limit | Kind | Why |
|---|---|---|
| Local-only mode | user setting | Nothing may reach a cloud model, so the cloud voice session does not start. Local voice still works. |
| Vault kept on this PC | user setting | Private vault notes reach the cloud voice model only as an answer from the local model, shown on a card first. |
| Tools off in voice | user setting | `voice_tools: false`: voice talks but calls no tools. |
| Computer control off | user setting | Screenshots and mouse and keyboard control are refused, as in chat. |
| Approval cards for outward actions | governance | Sending, buying, posting or changing anything outside the PC waits for an OK, as in chat. |
| Never-send list | privacy | Withheld from every cloud model. |
| 20-second limit on direct tools | responsiveness | A quick tool that runs longer is stopped so the conversation never goes silent. Longer work goes through `delegate_to_friday`. |
| Spoken approvals in a room of several people | known limit | In "Several people" mode, a spoken yes to a card counts only when it names Friday. Voices are not told apart until Household Identity lands. |

## What was removed

- **The 20-tool ceiling.** Anything outside the direct tools now goes through
  the delegate.
- **The voice-only `confirmed=true` flag** on `open_url` and
  `navigate_workspace`. Governance decides, as it does for a typed request.
- **The voice-only null context.** Voice calls used to carry no conversation
  id, so voice-started tasks and cards reported to Main.

## Context only this PC has

`ask_local_for_context` lets the cloud voice model ask the local model a
question that needs private context (notes, calendar, memory, the people in
the user's life, preferences). See `services/local_context.py`.

- **The local model writes the answer**, marking every person it mentions.
  Each name is replaced by a relationship placeholder such as
  `[their partner]`, and identifiers become numbered placeholders such as
  `[phone number 1]`.
- **The egress floor applies:** never-send material and hard identifiers stop
  the share whatever the user decides.
- **A payload card shows exactly what the cloud model would receive.** It also
  lists what each placeholder stands for (the category, never the value),
  which local model wrote it, and which cloud model would get it. The options
  are Send, Edit, Don't send, and "Allow for this conversation", which uses
  the scoped, expiring governance grants and expires in 4 hours.
- **Edits are sent exactly as saved.** If the privacy check would change an
  edit, both versions are shown and the user chooses.
- **By voice**, "send it" / "don't send it" decides, and "change X to Y" /
  "leave out the part about Z" edits on this machine. The cloud model never
  sees the draft before it is approved. A spoken decision counts only when
  the user's own latest words say so. In "Several people" room mode it must
  also name Friday.
- **One approval path:** the card is decided once and disappears from every
  tab. The approved text reaches the live call byte for byte. A declined card
  sends nothing.

## Remembering earlier conversations

- **Every turn records who heard it.** Voice and chat turns store
  `meta.provider`, `meta.sent_to` and `meta.off_record`
  (`services/conversation_provenance.py`).
- **Older turns** count as heard by Gemini only where the evidence is solid: a
  voice turn stored while a Gemini Live session was open, according to the
  egress audit log's "live voice session opened/closed" entries. Everything
  else counts as local-only.
- **`search_past_conversations`** searches voice and chat history with dates
  (`services/conversation_recall.py`).
  - Matches the same cloud provider already heard come back directly, through
    the normal egress gate.
  - Other matches are summarised by the local model and shown on the payload
    card first.
  - Off-record turns are never returned.
- **A Gemini Live call starts with a short pin** of recent voice calls, built
  only from turns Gemini already heard. The locally written daily summary
  covers every conversation, so it reaches a cloud call only through the
  search and the card.
- **Off the record, nothing about the call is written to disk.** Turns live
  in memory for the session and are dropped when off-record ends; a call is
  never distilled from turns spoken off the record. See
  [off-the-record.md](off-the-record.md).

## How much to say

The bridge keeps a small running picture of the conversation
(`services/voice_conversation_state.py`):
- **What the user cares about right now:** topic weights decay each turn, so
  priorities shift as the conversation moves.
- **How much detail he wants:** depth rises with explicit asks, why and how
  questions, follow-ups, and a topic he keeps returning to. Brevity cues win
  until he asks for more.
- **What is still open:** questions no reply has covered yet.

When the picture changes, the bridge shows it to the model as a note joined
to his next turn. The model can refine it with `note_conversation_state`.
`voice_persona.VOICE_LENGTH_RULE` forbids the reflexive "one, two or three
things" list: when depth is wanted the answer is connected paragraphs, and a
list comes only when he asks for options or steps.

`tools/voice_bench/depth_bench.py` measures this against Gemini Live on
scripted general-knowledge exchanges, comparing the previous rule with the
current rule plus the per-turn note.
