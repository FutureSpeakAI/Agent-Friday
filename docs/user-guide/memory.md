# Memory

Agent Friday™ keeps two kinds of memory about you:

- the **Memory card**, two short notes she reads at the start of every
  conversation;
- **your conversations**, which you can search and delete from.

Both are in **Settings > Privacy & Data**. Both are stored on this PC.

## The Memory card

Open **Settings > Privacy & Data > Memory card**. It has two notes:

| Note | What it holds | Size limit |
|---|---|---|
| **About you** | What Friday knows about you | 1,375 characters |
| **Working with you** | Friday's own lessons about working with you | 2,200 characters |

Each line can be up to 400 characters. A bar under each note shows how full
it is. When a note is full, Friday merges or drops lines to make room before
she adds a new one.

The notes are two files on this PC: `%USERPROFILE%\.friday\core_memory\ME.md`
and `FRIDAY.md`.

### Changing it yourself

Every line has **Edit** and **Delete**. Type in **Add a line** under a note
and press **Add**. Your own changes apply at once.

### When Friday saves on her own

Friday saves a line by herself only when both of these are true:

- it comes from your own words: typed by you, or spoken by you on your own
  voice call on this PC within the last 5 minutes;
- nothing she read from outside (a web page, an email, a file) in the last 6
  hours is involved.

Otherwise the line waits for you (see below). Household basics you type in the
app are saved quietly only when no household line exists yet, and only when
they match a fixed pattern; anything else waits for your review. Nothing is
saved for a child's profile.

### Waiting for your review

Under **Waiting for your review**, each suggestion says what it would do
("Add: ...", "Remove: ...", "Change ... to ..."), which note it is for, and
where it came from: nightly dreaming, the background review, Friday, or an
older note. Press **Keep** to put it on the card or **Discard** to drop it.

Nightly dreaming only ever writes suggestions here. It never changes the card
by itself.

### What Friday changed

Under **What Friday changed** you see each change she made, with **Undo**.

### The background review

**Review our conversations for the card** is on by default. Every 10 turns,
after Friday has answered, a model on this PC reads the latest exchanges and
may add a line. Change how often with the `memory_review_every_turns`
setting (2 to 100).

- It runs on a local model only. It never uses a cloud model. With no local
  model it is skipped.
- It adds only lines backed by words you typed. Anything it would change or
  remove, and anything it got from something Friday read, waits for your
  **Keep**.
- It waits its turn in the Local AI queue, so you can see it there. See
  [Scheduled jobs](scheduled-jobs.md#background-ai-work-waits-its-turn).
- Conversations held off the record are never reviewed.

Turn the switch off and nothing is reviewed.

### Fixed for the length of a conversation

Friday reads the card once, when a conversation starts, and keeps that copy
until the conversation ends. A line she saves during a conversation applies
from the next one. Your own edits in Settings apply straight away.

### Upgrading from an older version

If you used an older version, the facts you entered or approved there move
onto the **About you** note once, as far as they fit.

## Search your conversations

Open **Settings > Privacy & Data > Search your conversations**. Type words and
press **Search**. It searches everything you and Friday have said, typed or
spoken. Each result shows the date, who said it, whether it was typed or
spoken, and the conversation's title.

- The index is on this PC.
- Keys and passwords you pasted are withheld before anything is indexed.
- Anything said off the record is not there.
- When a cloud model searches your past conversations, it sees only matches
  from conversations that it took part in.

### Deleting a message everywhere

Press **Delete…** on a result. Friday asks "Delete this message everywhere? It
cannot be undone." Press **Yes, delete it** to remove the message from its
conversation and from every index Friday searches, at once, or **Keep it** to
leave it.

## Related pages

- [Privacy](privacy.md) for what may go to a cloud model.
- [Your knowledge](your-knowledge.md) for your documents and notes.
