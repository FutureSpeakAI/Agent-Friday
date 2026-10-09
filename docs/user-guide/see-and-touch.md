# See and Touch

Agent Friday™ can see what the workspace in front of you is showing, point at
rows with numbered outlines, tick the rows you mean, set a filter, and write
into a field you can see. You say what you mean ("the newsletters", "these",
"the second one"), and the screen shows what Friday understood before anything
happens to your mail, files or calendar.

Looking, pointing, ticking, filtering and writing into a field change nothing
by themselves. Acting on what is ticked still goes through the usual
[approvals](approvals-and-receipts.md).

## Where it works

Open the workspace first. Friday sees the one on your screen.

| Workspace | Friday can point at rows | Friday can tick rows | Friday can set a filter |
|---|---|---|---|
| Message Center | yes | yes | yes (view, unread, search, folder, account) |
| Media | yes | yes | yes (status, kind, project, search) |
| Library | yes | yes | yes (folder) |
| News | yes | no | yes (category, sort) |
| Files | yes | yes | no |
| Calendar | yes | yes | no |
| Chat conversation list | yes | yes | no |
| Workflows, People, Career, Trust, Sites, System | yes | no | no |
| Health, Finance, Family | counts only | no | no |

Friday can write into these fields, and only these:

- the Message Center reply and compose boxes (To, Subject, message)
- the Calendar quick-add line and its follow-up message
- the Workflows editor (name, description, each step) and the "Describe it" box
- the box that steers a running task in System

A field that no open screen offers is refused, and Friday says what it can
write in instead.

## What you say

Ordinary sentences work, typed or spoken.

- "Select the newsletters." Friday ticks them. A word Friday does not know as
  a kind of item is not guessed at; it asks you to use a search instead.
- "Show me the unread ones from this week." Friday outlines them.
- "Point at the second one." The numbers stay valid for two minutes, so "the
  second one" and "those" mean the rows Friday just pointed at.
- "Only show drafts." Friday sets a filter chip.
- "Add a reply saying I'll call tomorrow." Friday writes into the reply box.
- "Archive these." "These" means, in this order, the rows you have ticked, the
  row your hand cursor is on (for three seconds after it moved off), then what
  Friday just pointed at. If none of those exists, Friday asks which ones you
  mean instead of guessing.

## What you see

- **Ticks.** Friday's ticks and yours are the same ticks. A chip names the
  group and says who made it, for example "Friday selected", and "(edited)"
  once you change it. You can untick rows, add rows or clear the whole set.
  Ctrl-click (Cmd-click on a Mac) or a pinch ticks a row yourself.
- **Rows you cannot see.** Ticked rows below the loaded list are counted
  ("and 12 more below the list"), not dropped.
- **Outlines and numbers.** Friday outlines up to 12 rows at once and numbers
  them in the order it mentions them. More than 12 are counted ("and 8 more
  not marked"). The outlines fade on your next input, or after about 12 seconds.
- **Filter chips.** A filter Friday sets shows as a chip marked "by Friday"
  with an x to remove it. If you change that filter yourself, it stops being
  marked as Friday's.
- **Written text.** A field Friday filled shows "Friday wrote this" with an
  **Undo** button. If you edit the text, it becomes yours.
- **Honest reports.** Friday reports what the page confirmed. If the page did
  not confirm the ticks, Friday says so and does not claim that anything is
  selected.

With reduced motion on, the outlines and ticks use a short fade instead of
movement.

## What still asks

Ticking is not approval. When you ask Friday to act on the ticked rows, the
normal rule for that action applies.

- **Mail.** Archive, trash, spam, restore and move wait for one approval card
  that lists every conversation they will touch. The list is fixed when the
  card is raised; approving it changes exactly those conversations. Rows
  turn amber while a card is waiting, leave the list when the change is
  done (with an Undo), and keep their ticks if you decline. Mark read or unread,
  star and label run at once and write a receipt you can undo.
- **Files and wiki pages.** Trash and archive always ask, even for one item.
  Two or more items ask on one card. A single local rename or move that can be
  undone runs at once.
- **Media cards.** Favourite, tag and move to a project run at once for one
  card; two or more wait for one card.
- **Calendar.** Moving events always waits for one card, because it reaches
  Google.
- **Sending and saving.** A filled field is never sent, saved or created by
  Friday. You press Send, Save or Enter yourself. The send card says the text
  was written by Friday on your screen.

You can answer a card by pressing its button or by saying yes or no in words.
See [Approvals and receipts](approvals-and-receipts.md).

## What is never shared

- **Health, Finance and Family** show Friday a kind of row (such as
  "medication" or "position") and its place in the list. No title, name,
  amount, diagnosis, date of birth or filter text leaves those pages.
- **Voice.** When you talk to a cloud voice, or the room is shared, Friday's
  view of your screen is reduced to counts and categories: no sender, subject,
  file name or filter words. Results for a cloud voice name counts and kinds,
  never what Friday found.
- **Typed chat.** For a typed turn, Friday's model reads a short block
  headed "ON SCREEN": the counts, your ticks, your filters and up to 30 rows
  as sender and title. It is marked as data, not instructions, and text in a
  title that tries to give Friday orders is stripped. The block is not saved
  in the conversation history. It is only built from a screen report no
  older than two minutes.
- **Nothing is stored.** The page's report is held in memory only and goes to
  your own server. The only log line is a count (workspace, action, number of
  rows), never a title, sender or search.
- **Text Friday read elsewhere.** If text Friday writes into a field contains a
  link or address it read in an email or page, the field and the send card
  flag it with "Check:" so you can look before you send.

## Limits

- Friday works from the rows that are loaded. At most 120 rows and 500 ticks
  are held at once; a larger request is asked to narrow.
- A report older than two seconds is requested again before Friday acts on it.
  If the page does not answer, Friday says it cannot see the list.
- Only the Message Center can search its whole inbox; everywhere else Friday
  picks among the rows shown.
- One fill is at most 8,000 characters; longer text is written in parts.
- Ticking and pointing are not available in a workspace that is not open.

## Hand cursor

If you use the camera-driven hand cursor, "this" can mean the row the cursor
rests on. A quick pinch ticks a row; a pinch held for about 0.7 seconds opens
it. Guarded actions (send, delete, spend, publish, approve) need the hold to
finish. See the [hand cursor guidelines](../design/hig/hand-cursor.md).

Related pages: [Library](library.md), [Media](media.md), [Voice](voice.md),
[Privacy](privacy.md).
