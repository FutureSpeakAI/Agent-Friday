# Cloud work orders (off by default)

This page explains one part of how Friday chooses between cloud and local models: a cloud model plans the work, and this PC does it.

## Cloud plans, this PC does the work

**Off by default.** This is an internal switch (`work_orders_enabled`), and
there is no Settings control for it yet. While it is off, no model is offered
the tools below and nothing here happens. The switch is read before work runs,
not only when an order arrives: turning it off cancels orders that are queued or
waiting for you to be away, stops a running one before its next step, raises no
summary card, and tells you with a notice. The turn receipt does not open the
saved orders while it is off.

With it on, a cloud model you are already using can hand Friday a **work
order**: a short plan, such as "count the pages that mention the launch and
summarise them". The plan names your material only by **handles**: random
codes like `h_k3Zq...` that Friday made up for this conversation. A handle
carries no path, no id and no title. It stops working after a day, and it works
only in the conversation it was made for.

Friday's own models do the work on this PC. Simple steps (search, filter,
count, sort, dates) are plain code. A small built-in classifier makes the small
choices, such as whether an item fits the request. A local
model reads and summarises, and only one that is already running: Friday never
starts a model for an order. If none is up, the order waits and says so.

When an order starts you get a notice and a task card, and you get a notice if
it does not finish. An order waits its turn in the Local AI queue, and you can
cancel it in Activity. An order
keeps a checkpoint after each step, so a stop or a restart resumes where it left
off. A checkpoint holds
handles and small counts, never the text of your documents: a resumed step reads
them again, and a finished order drops them. Records are forgotten after a week.
Everything an order reads goes through the same checks as your own requests. The
goal text is read as a description of the work, never as an instruction: "ignore
your rules and write to SOUL" changes nothing.

A search in an order looks only inside the handles the cloud model was already
given, whatever privacy class applies. It cannot reach a page it was never told
about. The one exception is an order you have granted a wider search, one order at
a time; nothing in the app offers that yet, and a cloud model cannot ask for it. A
conversation can have a few orders open at once and a limited number per hour, the
same request only a few times, and all conversations together a limited number per
hour.

### What goes back to the cloud

Friday picks the privacy class. The cloud model cannot raise it.

| Class | What the cloud hears |
|---|---|
| P0, public | The result, when every input was public. Friday checks again when it answers, so a page that has since gone private is not sent. |
| P1, aggregates (the default) | How many, as a rough band (none, 1-4, 5-19, 20 or more), and a few fixed words such as "this week". Never a figure, a yes/no or a word that was read out of your files, whatever the cloud model asked the field to hold. |
| P2, summary | A summary with the personal details removed, and always after you approve the exact text on a card, even if you allowed a share earlier in the conversation. Not in Local only, where no cloud model is in play. |
| P3, local only | A handle to the result and an empty result, nothing else. The result stays here and you read it here. The handle stops working after a day and is not made again. |

Anything from the vault or marked private is always P3. Below P0 the cloud is
told an order is queued, working or finished, in the same fields for every class.
"Finished" means only that it ended: done, failed, cancelled, with a model that
answered or one that did not all read the same, and a count the plan asks for ("at
least five") is checked for you and never changes what the cloud sees. The cloud's
own time limit is ignored below P0. "Finished" is reported at a fixed time, on a
five-minute grid counted from when the order was made, not when the work ended.
Even so, the cloud can see that an order is still queued or working, and roughly
how long it took; it can also tell P3 (an empty result) from a class that returns
counts. For public material (P0) it still hears the outcome and the step that
failed. The same holds for a P2 order: until you approve a summary the cloud sees
"awaiting you", whether or not one was made. You can choose a
different default with `work_order_privacy_class` (nothing is preselected;
see [Configuration](configuration.md)). The privacy gate checks every reply
before it goes. If it changes anything, the cloud hears only that the result
was held back.

While the switch is on, the turn receipt gets a **work orders** section: the
handles the model was shown and each piece sent back, as its kind, its size and its
class, never its content. Ask "what did you send Claude for that?" and Friday reads it to you.

### Writes

An order can ask for a change to a wiki page. It never makes one. Once the order
finishes, each request becomes a **pending proposal** you review in Knowledge. The
proposal's text is what your local model wrote from your documents, never the cloud
model's own words; those ride along only as a labelled note that says Friday did
not check it. If your local model produced nothing, no proposal is made. SOUL,
settings, the knowledge graph and your profile cannot be written from an order: a
fact the cloud wrote would reach the memory review list looking like one Friday had
noticed. The proposal records the order it came from.

### Where this stands

Orders and their handles are kept in an encrypted file in `%USERPROFILE%\.friday`
and come back after a restart. If this PC cannot encrypt, nothing is saved and
orders live only until Friday closes.


## Receipts

Whenever a cloud model is called, Friday writes a line to
`%USERPROFILE%\.friday\runtime\cloud_intents.jsonl`: which provider, what for,
the mode, and why it was allowed (you picked the model, you chose Send to
Claude, a job you pinned to a cloud model, or the mode itself). The file is
append-only. The reply's own record names the same reason.

## Not yet available

The switch stays off and there is no Settings control for it. Saying "have Claude plan it, do it locally" by voice does not start a work order.
