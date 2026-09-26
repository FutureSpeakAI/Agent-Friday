# What voice can do

Voice can do anything chat can, unless the user has restricted Friday.

## How

- **Direct tools** (`services/voice_engine.py`, `_VOICE_LIVE_TOOLS` and
  `_VOICE_SHARED_TOOLS`) answer quick things inside the conversation: the
  calendar, email, news, the wiki, files, the screen.
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
current state. The Voice & Tracking settings tab shows it.

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
