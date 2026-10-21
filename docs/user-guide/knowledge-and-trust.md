# Knowledge, Trust and deep research

Agent Friday™ keeps a wiki of pages and a knowledge graph that links them. The
**Knowledge** workspace shows both. The **Trust** workspace shows how far
Friday trusts each person she knows about. Deep research is a request you make
in chat: Friday reads the web for you and writes a cited report.

## Knowledge

### Open it

Choose **Knowledge** in the **System** group of the dock. You can also ask
Friday to open it ("open my wiki", "show the knowledge graph").

### Three views

The buttons at the top right choose what is on screen. Hover over one to see
its tooltip.

| Button | Tooltip | What you see |
|---|---|---|
| **Graph** | The galaxy only | The 3D graph. Each page is a node, each link between pages a line. |
| **Split** | The galaxy and the open page side by side | The graph and the open page together. |
| **Pages** | The pages only | The wiki's sections and the open page. |

**Split** is the default. Friday remembers the view you chose last time. When
the window is narrow or tall, **Split** stacks the graph above the page.

### Opening a page

- Click a node to open its page. In **Graph** view, opening a page switches to
  **Split** so you can read it.
- Open a page from the list, a link or a citation, and the graph flies to its
  node and lights the links in and out of it.
- In **Pages** view, **Show in graph** switches to **Split** and shows where the
  page sits.
- The bar at the bottom names what is open and counts its links, for example
  "3 linked from · 5 links to". Index pages, drafts and .txt notes are not drawn
  in the graph; the bar says "not in the galaxy".
- **✕** in the bar clears what is open.

### One search for both

The box "Search pages and the graph…" filters both sides at once. The graph
dims everything that does not match. The pages side lists the pages whose text
matches, with the matching lines.

If a citation names a page that does not exist, Friday searches for the name
instead and says so.

### What stays on your PC

The wiki and the graph are stored on this PC. Browsing, opening and searching
them sends nothing anywhere.

## Trust

### Open it

**Trust** is in the **System** group of the dock. On a new install it is not
shown. Turn on **Show all workspaces** in **Settings > General > Workspaces**,
or ask Friday to open Trust.

### What it shows

The header reads, for example, "Trust Graph — 12 Entities". Above it, Friday
says how many are tracked: "Click any person to see dimension breakdown."

Each person is a card, highest trust first. A card shows:

- the person's name, and any other names they go by in brackets,
- a trust percentage with a bar: green above 70%, amber above 40%, red at 40%
  or below.

### The breakdown

Click a card to open it. You see five measures, each with its own bar and
percentage:

- **reliability**
- **information quality**
- **emotional trust**
- **timeliness**
- **domain expertise**

Below them is a line such as "Evidence: 12 records". It counts the records
behind the scores; the records themselves are not listed here. A measure with no
score yet shows 50%.

### News sources

How far Friday trusts each news source is not here. It is in **News > Trust**.
See [News](news.md).

## Deep research

Deep research is for a question that would take a person hours of reading. You
ask for it in chat. Friday runs it in the background and reports back in the
same conversation.

### Asking for it

Ask in plain words, for example: "Do deep research on the history of the town
library, and give me a cited report."

You can also tell Friday:

- **specific questions to answer**, up to 10. Without them, Friday plans her
  own.
- **how many pages to read at most**. The most is 80, and that is the default.
- **some background** that shapes the research.

For a quick fact, ask a normal question instead. Friday then does a single web
search.

### How long it takes

A run can take up to about 45 minutes. When that time is up, Friday stops
reading and writes up what she has.

### Which models run it

Friday asks her local models on this PC to plan the questions, write the
searches, pull out passages and write the report. A cloud model is not used for
the research itself. The report's last line names each model that did the work.
With no local model, the research cannot run, and Friday tells you it failed.

### What leaves your PC

- **Search words** go to the web search service Friday uses. Each search first
  passes Friday's privacy check. A search holding something that stays on this
  PC is not sent.
- **Page requests** go to the websites that host the pages.

Nothing else is sent. If your chat runs on a cloud model, that model sees your
request as it sees any chat message. If that request repeats your private words
word for word, a card asks first: **Start deep research with this text?** Only
**Send** on the card starts the job; **Don't send** starts nothing.

### Every quote is checked

Each finding in the report pairs a claim with a quote from a page. Before the
report is shown, Friday checks every quote word for word against the page she
read. The check is done by code, not by a model.

- A quote that is not on the page is struck. Its sentence shows
  "[unverified — removed]".
- Struck claims are listed under **What I could not confirm**, with what was
  tried. This section is always there, even when it is empty.

### Sealed copies

Friday keeps a sealed copy of every page a finding cites. It holds the page
text, its address, when it was read and a fingerprint (a hash) of the text. The
copy is stored on this PC and encrypted where Friday's keystore allows.

If the saved page text is gone, Friday checks the quote against the sealed copy.
Each time a copy is read, its fingerprint is checked again. A copy that has
changed is reported as not intact, and a quote checked against it does not
count.

### The report

When the research is done, Friday posts a message in the conversation. It says
how many claims were confirmed, how many were struck and what she could not
confirm. The report has an answer, sections with a **source** link on each
claim, **What I could not confirm**, and a list of **Sources**.

The report is also saved as a page in the **Research** section of your wiki, so
you can open it in **Knowledge**. A formatted copy opens in a new tab.

If the sources had no answer, the report begins "**I could not answer this.**"
It then lists what Friday searched and read, so you can judge whether the gap
is in the sources or in the search. If something broke, such as the search
service or the models, Friday says the research **failed** and why. She does
not call that a finding.

### Watching progress

The run is a background task named "Research: " and your question. It appears
in **Activity**, with its status and a link to its reasoning trace.

### After a restart

The research survives a restart. When Friday starts again, she picks it up.
Questions already answered are not redone.
