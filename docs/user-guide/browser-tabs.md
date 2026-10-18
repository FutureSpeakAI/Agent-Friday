# Friday in your browser

Agent Friday™ runs as a page in your web browser. Beyond the main desktop page,
you can give any workspace a tab of its own, link straight to one item, and ask
Friday to open things for you. This page also covers the separate browser
Friday uses for web work.

## A workspace in its own tab

Every workspace except Settings can open in its own browser tab:

- In the workspace's title bar, choose **⋯ > Open in its own tab**. The desktop
  window closes and the tab opens where you were, scrolled to the same place.
- Ctrl-click (Cmd-click on a Mac) or middle-click its icon in the dock. This
  opens the tab in the background, like a link, and you stay where you are.
- Go to `/w/` followed by the workspace's name, for example `/w/news`, on
  Friday's address.

From the **⋯** menu, asking again for a workspace that already has a tab brings
that tab forward rather than making a copy. A tab has a **Desktop** link that opens the main desktop with
this workspace open.

The tab's title names the workspace, then Friday, for example "News · Agent
Friday™". A tab opened for one item names the item first: "Q3 plan · Messages ·
Agent Friday™". If you have renamed Friday, her name appears instead.

Settings always opens on the desktop. The address `/w/settings` takes you there.

### If the browser blocks the tab

Browsers may block a new tab as a pop-up. From the **⋯** menu, the window then
says: "The browser blocked the new tab. Allow pop-ups for" Friday's address
"(the blocked pop-up icon at the right end of the address bar), then choose Open
in its own tab again. This window stays open until then." From the dock, a
short message asks you to allow pop-ups and try again.

## Fullscreen with chat

Press **Ctrl+Shift+F** to make the front workspace fill the screen, with the
chat tray docked beside it and the dock hidden. Press it again to leave. It is
also in the command palette as **Fullscreen with chat**, and Friday can set it
when you ask.

Friday remembers this per workspace. The next time that workspace comes to the
front, or opens in its own tab, it opens the same way. With no workspace open,
Friday says "Open a workspace first: fullscreen with chat is set per
workspace."

## Links to one item

A tab can open straight to one thing inside a workspace. Friday builds these
addresses when she opens something in a tab. They can point at:

- a workspace, or a tab or section inside it,
- an email thread, or a mail search,
- a file,
- a Knowledge page, or a node on the knowledge map,
- a day or a meeting in Calendar,
- a contact,
- a post,
- a news story,
- a creation,
- a Media card,
- a meeting.

A Settings section opens on the desktop, not in a tab.

### Old addresses

Some workspaces were merged into others. Their old addresses still work:

- `/w/wiki` opens Knowledge on its Pages view.
- `/w/draft` and `/w/content` open Media on its board.
- `/w/studio` opens Media on its library.
- `/w/edition` and `/w/home` open the desktop.

## Notifications from News

News notifications for the Front Page, the Weekly Digest, the weekly editorial
and the daily briefing each lead to their own page in News. Open the
notification and choose **Open News**. Friday opens News on that page and clears
the notification.

## Citations

When Friday cites a web page, the citation is a link. It opens the source in a
new tab. Where your browser supports it, the page scrolls to the cited passage
and highlights it.

## Ask Friday to open something

By voice or chat, ask Friday to open a workspace, an email, a file, a page, a
day, a contact or anything in the list above. For example: "open the Harbor
Legal email", or "show my budget spreadsheet in its own tab".

- On the desktop, she opens it in the main Friday page. She can also make it
  fill the screen.
- "In its own tab" opens a new tab in Chrome, filled with the item. Without
  Chrome, she uses your default browser.

Friday tells you what happened. She says when the tab or desktop shows the
item. If it opened but has not confirmed in time, she says it was sent but not
confirmed. If it opened something else, she says that too. When a name matches
several things, she opens the newest best match and names the others.

To show things on the desktop, keep the main Friday page open in a browser. If
it is closed, she says "no Friday desktop page is open to show it in". If the
page is minimized or covered, she says it is not on screen yet.

Opening something only moves your own screen, so it does not ask for approval.

## Several tabs at once

- **Approvals.** An approval card shows in every open Friday page. Decide it in
  any tab, by voice or by a text reply, and it leaves every page at once. See
  [Approvals and receipts](approvals-and-receipts.md).
- **Layouts.** Fullscreen with chat is saved per workspace, so a workspace keeps
  its layout whichever page shows it.
- **Customizations.** A workspace's look, note and quick actions follow it into
  its own tab. See [Customize a workspace](customize-workspaces.md).
- **Voice.** **Ctrl+Shift+Space** starts talking to Friday, or stops, from any
  Friday page.

## Friday's own browser

For web work, such as filling a form or working through a portal, Friday uses
her own browser sessions. They never use your normal browser or its profile.
These rules always hold:

- Friday never types into a password field. When a site needs you to sign in,
  she stops and asks you to.
- Typing into a payment, card, bank or identity-number field waits for an
  approval card.
- A click that submits, sends, pays, buys, books, signs, deletes, publishes or
  confirms waits for an approval card. The card shows the page, the button, and
  every field and value the form will send. If the form changes before you
  approve, nothing is sent.
- Friday never ticks a signature or attestation box.
- A browser closes itself after 15 minutes without use.

### Turn it on

Friday's browser work is off until you allow it. Go to **Settings > Privacy &
Data > Agent workspaces** and choose **Allow agent workspaces**. Turning it off
again stops the work and removes the live views.

### Watch it

Open **Crew** to watch each browser session. **Watch work** shows a live view.
**Pause** stops Friday's input, **Take control** lets you use the page yourself,
and **Let agent continue** hands it back. **Close workspace** ends a session,
and **Stop all workspaces** stops every one. Each session has its own cookies,
and sign-ins are not kept for the next task. See
[Independent agent workspaces](../features/agent-workspaces.md).

### Settings > Connections > Friday's browser

This section manages a separate, older browser profile kept on this PC. It
shows whether that window is open, with **Close**, and how much the profile
holds. **Clear Friday's browser profile** deletes it, which signs that profile
out of every site. Browser work that Friday does from a chat does not use this
profile.

## What stays on your PC, and what can leave

Workspace tabs, layouts and links are all served from this PC. The pages Friday
opens in her own browser are on the web, so visiting them reaches those sites.
Anything that submits, sends or pays waits for your approval card first.

## Limits

- Settings cannot open in a tab of its own.
- A new tab needs pop-ups allowed for Friday's address.
- Showing something on the desktop needs the main Friday page open.
- Friday's browser work needs **Allow agent workspaces** to be on.
