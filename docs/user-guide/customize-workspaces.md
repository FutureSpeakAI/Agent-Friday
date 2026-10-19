# Customize a workspace

You can reshape any workspace by telling Agent Friday™ what you want. Friday
changes how it looks and adds shortcuts while you watch, and she keeps every
earlier version so you can go back. On screen this is called **Customize**, and
its menu is the **Vibe Coding Salon**.

## Open it

Every workspace window, and every workspace in its own tab, has a **Customize**
button in its title bar, with a pickaxe icon. It opens the **Vibe Coding Salon**
menu ("Choose what you want to shape") with three items:

- **Preview appearance** opens a form for spacing, accent colour, a pinned note
  and quick actions. Choose **Preview changes** to see a sample, then **Apply
  appearance** to save it. The preview itself saves nothing and runs no model.
- **Customize workspace** opens the Studio, a chat with Friday about this one
  workspace.
- **Open codebase Salon** edits the code behind a workspace that was installed
  from the Salon. For other workspaces it is greyed out and says "Built-in
  source editing is not available yet". The live version stays in place until
  you approve the change.

For an installed workspace it is the other way round: **Preview appearance** is
greyed out and points you to the codebase preview.

The **⋯** button (**More tools for** the workspace) holds:

- **Talk to Friday**, a voice conversation about this workspace.
- **Version history**, to review changes and restore an earlier version.
- **Open in its own tab**, to continue with more room. Settings does not offer
  this. See [Friday in your browser](browser-tabs.md).

## The Studio

The Studio is a small panel headed with the workspace's name and **Studio**, and
"Shape this workspace by talking to Friday". Type in the box ("Ask Friday to
change …") and choose **Send**. While she works, it says "Friday is working…".

A new Studio offers four starters you can click:

- "Make this layout more compact"
- "I don’t like the colors — try a warmer accent"
- "Pin a note reminding me what this workspace is for"
- "Add quick-action buttons for my common tasks here"

**clear history** empties the Studio conversation. It does not undo any change.
If a request does not come back, the Studio keeps your draft and asks you to
choose **Refresh workspace history** before you try again.

## What Friday can change

A change is a small set of settings. Anything else is dropped. Friday can set:

- **Density**: compact or comfortable.
- **Accent**: a colour for the workspace's highlights and a thin bar along its
  top.
- **Styling** that applies inside this workspace only, such as rounder cards.
- **A pinned note** at the top of the workspace, up to 1,500 characters. Its
  **×** button asks Friday to remove the note.
- **Hidden sections**: parts of the workspace she hides from view.
- **Quick actions**: up to 8 buttons. Each has a label of up to 40 characters
  and a prompt of up to 400.

A quick action is a shortcut to a request. Clicking one opens this workspace's
Studio and sends its prompt to Friday, as if you had typed it.

Friday only makes a change when you ask for a visible one. If you are just
chatting or asking a question, she replies without changing anything.

### When it needs more than a new look

Some requests need new data or a new feature, not a new look. Friday says so
plainly and offers to start a background task. She does not fake it with
styling.

## See and undo a change

When Friday applies a change, a short note says "✨ Applied" with what changed.
Under her reply, "✨ Applied" appears again with **↩ revert this**, which undoes
that one change.

A customised workspace shows the pickaxe icon beside its name. Its tooltip
reads "Friday has customized this workspace — open Customize to tweak or
revert".

## Snapshots and Reset to default

Friday saves a snapshot before every change, and the last 40 are kept. Undoing
is snapshotted too, so an undo can itself be undone.

In the Studio, the clock button shows how many snapshots there are. It opens
**Snapshots**, a list with **↩ revert** on each one, and **Reset to default**.
Reset first asks "Reset this workspace’s appearance to its original settings?
Earlier snapshots remain in history." Choose **Reset appearance** to go ahead,
or **Keep editing**.

## Version history

**⋯ > Version history** opens a panel over the workspace. It says what Friday
has changed, or "This workspace is at its baseline. Nothing has been
customized." It lists each change, newest first, with when it happened and what
it touched.

- **Undo the last change** steps back one change. Choose it again to step back
  further.
- **Back to baseline** removes every customization at once.
- **Undo this and everything after** restores the workspace to how it was just
  before that change.

Each of these is reversible, because the state before it is snapshotted.

## By voice or chat

You can also ask Friday from the main chat or by voice:

- "Make News compact" or "Give Calendar a green accent." By voice and chat she
  can set density, accent, the pinned note and quick actions. For scoped styling
  or hidden sections, use the Studio.
- "Roll that back" undoes the latest change.
- "Put my workspace back to how it was this morning" restores it as of a time.
- "Reset Calendar to default" removes every customization.
- "What did you change in News?" lists the history first, which Friday also
  does when an undo request is unclear.

These change only your own screen, so they do not ask for approval.

## In its own tab

A workspace's customization follows it. When you open it in its own browser tab,
the same look, note and quick actions appear there, and the tab has the same
**Customize** and **⋯** buttons.

## Off the record

While off the record is on, changes are paused, because the Studio saves its
history. The Studio says "Off the record: workspace changes are paused because
this Salon saves history." Preview appearance pauses too. You can continue once
off the record ends.

## Safety

- A customization changes only how the workspace looks and its shortcuts. It
  cannot touch model routing, what may leave the PC, the vault, Friday's rules
  or stored keys. A change that names any of these is refused. The Studio says
  "I did not apply that change" with the reason.
- Styling is cleaned before it is used. It stays inside its own workspace, and
  it cannot load anything from outside the PC or run scripts. Only images built
  into the styling itself are kept.
- In safe mode, workspace changes are off.

## What stays on your PC, and what can leave

Customizations, snapshots and the Studio conversation are stored in the
`.friday` folder on this PC. Preview appearance, undo, revert and reset all run
on this PC.

The Studio chat itself goes through your normal model choice, so it may use a
cloud model. When it does, your message, the recent Studio conversation and the
workspace's current customization are sent to answer it. See
[Privacy](privacy.md).

## Limits

- Friday keeps the last 40 snapshots per workspace.
- A workspace has at most 8 quick actions.
- Voice and the main chat cannot set scoped styling or hidden sections.
- Editing a workspace's code is available only for installed workspaces, through
  **Open codebase Salon**.
