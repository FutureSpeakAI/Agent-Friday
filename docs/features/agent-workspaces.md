# Independent agent workspaces

Status: browser workspace preview. Native desktop application control is outside
this feature.

Friday and Crew agents can use separate browser sessions with visible, named
cursors. Their input goes to those sessions. It does not move the operating
system pointer or type into the user's focused application.

## Enable and use

Enable **Independent agent workspaces** during setup or in **Privacy &
Approvals → Agent workspaces**. The setting starts off. Model-facing browser
tools, including Friday's own browser tools, require this permission. Crew
agents also need the relevant browser tools and project in their saved access
settings. Enabling workspaces does not grant additional project, file or
outward-action permission.

Open **Workspaces → Crew**, or **Crew** in a chat, to watch browser work. Each
workspace shows its agent's name, page and control state. **Watch work** opens a
live view; **Enlarge** makes it easier to follow. Views contain actual browser
frames and cursor positions recorded by browser actions. They do not animate
invented work. Friday can own a workspace through the same permission and
control path as an agent.

Up to four browser sessions can run at once. Each has separate cookies, page
state, keyboard focus and input queue. Closing a workspace ends its browser
and removes its temporary browser profile after shutdown. Sign-in state is
not copied from the user's browser or retained for the next task. A view is a
sample of a changing page; frame delivery is bounded and is not a video stream.

## Watch, pause and take over

- **Pause** prevents subsequent agent input in that workspace.
- **Take control** gives the user control inside that browser view. Click its
  page, then use the text field and key buttons to interact. Text entered into
  the control field is hidden and cleared after use.
- **Let agent continue** returns that workspace to the agent. Old queued
  actions and old page references cannot resume after the control change.
- **Close workspace** stops that workspace and prevents the same task
  authority from silently reopening it.
- **Stop all workspaces** revokes input across every agent browser. Enable a
  fresh permission period by turning workspace permission off and on before
  starting new browser work.

Control changes prevent future admitted actions. They cannot undo a click or
request that a website has already received. Taking control of one workspace
does not pause the others. A fresh displayed frame is required for each manual
input, so a delayed click cannot target a different frame.

Turning the setup permission off revokes existing browser authority and removes
live views. Changing privacy mode, agent access, room membership or task scope
can also expire a workspace. Start fresh work after correcting the underlying
setting. Stop controls remain available even when page contents can no longer
be viewed.

## Talk while work continues

Use **Ask agent** to ask about a running task. The reply appears under the
agent's identity in the chat. In an active voice room, it uses the agent's
configured voice and the room's existing speaking queue. This conversational
reply does not start another tool-using worker.

Use **Guide task** to change the worker's instructions. Guidance is queued and
marked received only when that worker reaches a checkpoint and consumes it.
Queued guidance is not a claim that the task has changed already. In voice,
Friday can use `talk_crew` for questions and `steer_crew` for task changes.
**Voice room** opens the existing room voice experience; one speaker holds the
floor at a time while other agents may keep working.

The handoff form's **Task project** can select another project assigned to the
agent. Room conversation context and task project access remain separate. An
agent invited to the room does not thereby gain access to another member's
project. Shared task context is restricted to eligible peers.

## Implementation boundaries

Model-facing browser dispatch binds an immutable actor, conversation, task,
project, profile revision, room revision, privacy period and permission
generation before governance runs. Approval replay must resolve that same
surface and control generation. A model cannot choose a different owner by
passing a browser identifier.

Owned sessions use headless browser workers with isolated profiles. The local
authenticated UI receives bounded snapshots; it does not embed arbitrary page
scripts into Friday. Manual controls and permission changes require local
screen authority. Page data and input are revalidated after blocking work and
before response publication. The explicit legacy browser service remains
available to direct internal callers; model tool dispatch never falls back to
it when workspace permission is missing.

Separate browser profiles and input do not provide a network sandbox. Friday
filters HTTP(S) URLs and WebSocket destination hosts; WebRTC traffic is outside
these URL filters.

Native application work requires a separate application integration or isolated
desktop worker. Drawing additional cursors on the host desktop alone does not
provide independent native focus or safe parallel application input.
