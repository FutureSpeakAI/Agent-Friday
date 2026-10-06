# Crew conversations

Status: cloud-first implementation. Offline execution is not supported.

Crew gives a named specialist a persistent identity across conversations. Open
**Workspaces → Crew** to manage saved agents across projects, or **Crew** in a
chat to invite agents and hand off work. The hub is also available at `/w/crew`.
Create an agent, choose its reasoning provider and model,
choose its speech provider, model and voice, and set its name, caption label,
role and personality. This release requires Gemini Live for the room's host
voice; invited specialists use a separate configured speech provider.

Assign projects, skills, permitted tools, specific file or folder access, and
the agent's own memory settings. A folder write grant includes read access.
Crew grants narrow access; Friday's ordinary governance, file approvals and
cloud privacy controls still apply. Credentials and linked files are excluded.
The first tool set supports file reads and writes, web search, news search and
web browsing. Other capabilities require a future explicitly scoped adapter.

The hub shows the roster and its model, voice, project and lifecycle settings.
Its current-work list includes up to one hundred recent Crew tasks, with active
work first. The list contains task identity and status, not prompts, results or
tool logs. It reflects the runtime's current task records; it is not a separate
permanent task archive. The same editor and revision checks serve both the hub
and the chat panel.

An agent keeps its ID when its name, model or voice changes. Every edit creates
a new revision. Work using an older revision stops before its next governed
action or publication. Suspension prevents work; retirement keeps the profile
and conversation history without allowing the agent to work again. Memory
combines saved notes with bounded recent results: at most twelve results are
stored, and the latest four eligible entries can enter a request's context.
Learned results are private to the agent and project; changes to its access
scope prevent recalling results acquired under the previous scope.

Invite up to six agents to a chat, save the room, then address one by name or
send it a request from the Crew panel. Friday can use `list_crew` and `ask_crew`
in a typed or voice conversation. In voice, Friday can also propose a new
profile in the editor; the proposal remains unsaved until the user reviews it.
The user sees each agent's name, selected model, task and result in the chat.
Up to six Crew tasks run at once, with one outstanding task per agent per chat.

Requests and replies are shared with the eligible invited members. Existing
unrelated chat history and global memory are not automatically imported. A
member removed before publication is excluded from that result's shared
context. Leaving the room stops room speech but does not cancel independent
work already accepted. Use the task controls to cancel work. Interrupted Crew
work requires a fresh request; generic task resume cannot restore its scoped
authority and is refused.

The room gives one speaker the floor at a time. **Quiet** flushes current and
queued speech and discards late audio from the interrupted turn. It does not
cancel background work. Generated text is preserved with its playback state;
an interruption never claims that the entire response was heard. If a voice
provider fails, the written result remains available.

Cloud providers need configured accounts, connectivity and available quota.
An unavailable selected provider produces a visible failure; Crew does not
silently substitute a different model or voice. Local-only mode refuses cloud
Crew. Optional offline bindings may be saved for future configuration, but
neither an offline performance claim nor automatic fallback is provided.
