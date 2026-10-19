# News

Agent Friday™ reads the news for you and writes it up as shows. This page
covers the **News** workspace: what each show carries, how stories are chosen,
how sources are scored, and how to talk a story through with Friday.

## The shows

News has these tabs: **Front Page**, **Feed**, **Read Later**, **My Notes**,
**Briefings**, **Weekly**, **Editorial** and **Trust**.

- **The Front Page** comes out twice a day, morning and evening. It has a lead
  story, sections by subject, a **Contrarian Corner** and **Competitor Watch**.
- **The Briefing** comes out in the afternoon. It carries your calendar, your
  mail and the news.
- **The Week** (the **Weekly** tab) sums up the week's editions on Sunday.
- **The Editorial** comes out on Friday. It is Friday's own opinion piece. See
  [The Editorial](#the-editorial).

Each show is a built-in routine. When it runs, which model writes it and what
it costs are in [Scheduled jobs](scheduled-jobs.md). Each show also gets a
spoken episode; see [Podcasts](podcasts.md).

## Notices when a show is ready

Each show has its own switch. Open **Workflows**, then **Friday's built-in
routines**. A routine that announces its finish has a switch, **Tell me when
<routine> finishes**. Turn it off and that show runs quietly. The setup chat
offers the same choice, **Tell me when it finishes**, for the routines you tick.

## Today's news only

The Front Page and the Briefing use stories from the last 36 hours. A story
older than that is held back. A story with no date is kept, because nothing
says it is old.

The window has no control in the app. It is the settings.json key
`news_edition_window_hours`, with one number of hours for each show:
`{"front_page": 36, "briefing": 36}`. See [Configuration](configuration.md).

The Front Page also remembers what it already ran, across its last 14 editions.
A story counts as the same story when its link is the same, or its headline is
nearly the same.

- A story that already ran comes back only with a new development. That is a
  new fact (a name, a figure, a day) or an outlet that had not reported it.
- It is marked **UPDATE**. The lead shows **What's new today:** and other
  stories show **New:**, with what changed.
- A story that ran before with nothing new is held back.
- A story new since the last edition is marked **NEW**.

## Pages that aren't articles

Some links in a feed are not stories. Friday drops them before an edition is
written:

- a page with no headline
- a weather forecast, or a list of headlines ("Top stories", "Headlines")
- an advert for an app or a newsletter ("download", "sign up", "subscribe")
- sponsored or partner content, and advertorials
- an outlet promoting its own event, such as a conference or awards
- obituaries (headlines with "obituary")
- a cut-off post of three words or fewer that ends in "…"
- a site's home page, or a page that says it is a section front or a list of
  links

## One story, every outlet

When several outlets report the same event, the Front Page shows it once. The
freshest report leads. The card lists the others: **Also reported by** and each
outlet's name, linked to its own report. An outlet is listed once, even if it
ran two reports.

## How the Front Page is ordered

Stories are sorted by kind, in this order:

1. **Hard news**: safety, government, courts, elections and the economy.
2. **Analysis**: everything else that reports or explains.
3. **Service pieces**: guides, tips, lists and things to do.

Promotional pieces are dropped, as above. Inside each kind, the order stays as
it was. A story goes in the section for its subject, not its feed: a markets
story is **Business**. A section shows at most six stories, and at most two
from one outlet.

## The OPINION badge

On the Front Page, a column or opinion piece carries an **OPINION** badge. Its
hover text says "Opinion: the writer's view, not news reporting".

The badge is set by fixed rules, not a model. A story gets it when:

- its link sits under an opinion part of the site (`/opinion/`, `/op-ed/`,
  `/column/`, `/commentary/` and similar), or
- its headline is written in the first person ("I", "my"), or starts with
  "Opinion" or "Column".

## Trust badges

Each story card has a coloured dot and a score from 0.00 to 1.00 for its
outlet. Green is 0.70 or more, yellow is 0.40 or more, red is below that.

Hover over it to see **Source trust** and the six measures below. A measure
with no evidence says **not measured**. An outlet with no observations yet says
"No observations yet — score is a seed estimate." If you banned or boosted the
outlet, the hover says so: "Your preference: banned (score held low)" or
"Your preference: boosted (score raised)".

To boost or ban an outlet, use the ⭐ (**Boost <outlet>**) or 🚫 (**Ban
<outlet>**) button on one of its cards. A banned outlet no longer appears in
your feeds.

## The Trust tab

The **Trust** tab shows the **Trust leaderboard**: every scored outlet, its
rank, its score, its article count ("art") and its observation count ("obs").
"banned" or "boosted" appears next to an outlet you set.

Choose an outlet to see its breakdown. It shows the **Evidence score**, your
preference (or **No owner preference**), a six-sided chart, one bar for each
measure, and the **Observation history** (the newest 40). Hover over a bar to
see what that measure is based on and how many observations it rests on.

### The six measures

The names say what each one actually measures:

| Measure | What it is based on |
|---|---|
| **Primary-source corroboration** | Stories this outlet covered with one or two others, where the coverage cites a primary document (a filing, ruling, official statement or study). It does not check whether a claim is true. |
| **Corrections issued** | Headlines that correct or retract an earlier article by this outlet. At most two a week count. |
| **Attribution (writing style)** | Words like "said" and "according to", or links, in the headline and summary. A long summary that names no source counts against. |
| **Predictions** | Not measured. Nothing on this PC scores forecasts. Only a verified peer attestation can supply it. |
| **Opinion labelling (writing style)** | Opinion filed under an opinion or analysis section counts for. Words like "should" and "must" in a news headline count against. |
| **Independence** | Not measured. Only a verified peer attestation can supply it. |

### How the score is worked out

- The **Evidence score** is a weighted average of the measures that have a
  score. A measure that is not measured is left out, not counted as 0.5.
- Recent evidence counts more. An observation loses weight by a factor of 0.95
  for each week of its age.
- Your **Ban** holds the score at 0.15 or below, whatever the evidence says.
- Your **Boost** adds 0.15 to the evidence score, and lifts it to at least
  0.70 (green).

You can change these numbers in **Settings > Advanced > News source trust**,
or in News under **⚙ Customize**, **How trust scores move**. The fields are
**Learning rate: evidence needed to move a score**, **Boost adds**, **A boosted
outlet is at least** and **A banned outlet is at most**.

## Who said what

Friday keeps her own words apart from the outlets' reporting.

- In a spoken News episode, the first mention of a story names the outlet
  aloud. A script that misses this is not spoken.
- Friday's own analysis is never credited to an outlet. A line that puts her
  view in a publisher's mouth stops the episode until it is fixed.
- On the Front Page, her comment on the lead is labelled **<name>'s note:**.
- In Discuss, her analysis sits under **<name>'s read**, with no citations.

Here and below, <name> is Friday's name, or the name you gave her.

## 💬 Discuss with Friday

Story cards on the Front Page and in the Feed have a **💬 Discuss with <name>**
button. It opens a side panel,
**💬 DISCUSS WITH <NAME>**, and starts with **Compare coverage**. While it works,
the panel says "Reading the sources on this computer…".

Each answer has two parts:

- **What the sources say**: findings, each with numbered links to the sources
  it rests on. Friday's code adds the links from the pages it fetched; the
  model never types a link.
- **<name>'s read**: her own view, with no citations.

A finding that its source does not support moves to her read, or is cut. A
**Sources:** line at the bottom links every source used.

### The buttons

| Button | What you get |
|---|---|
| **Compare coverage** | **How each outlet framed it**: each outlet's framing, and **Left out:** what it omits that others report. Uses other reports from the last four days. |
| **Primary source** | **Primary source:** with a link to the document the story rests on (a court, government, filing or research page linked from the article), and what it says. If the article links none, Friday says "Primary source not found in the article; I won't guess one." |
| **Background** | **Timeline**, with dated events, and **Who's who**. Uses the last 30 days of news. |
| **Claim check** | **Claims**, each with a badge: **confirmed** (two independent outlets or a primary document), **disputed** (sources disagree) or **one side only** (only the party making the claim). |
| **Local angle** | What the story means where you live. |
| **Follow** | Adds the story to your follow list. |
| **Make something** | Three choices, below. |

**Local angle** needs your city. Set it in News under **⚙ Customize**, **Local
beat**. Without it, Friday says "No local angle: set your city in News, Local
beat, and ask again."

**Follow** replies "Following it: the next Front Page and Briefing say what
changed, or that nothing did." Each new Front Page records whether a followed
story had a development. The News screen does not show that record.

**Make something** offers:

- **🎧 A two-host podcast**: a deep-dive episode on this one story, spoken with
  the local voice. The panel says "A two-host deep dive is being made on this
  computer; it will be in Media."
- **📝 Notes**: a page in your wiki under `news-notes`, with the headline, the
  link and the date. The panel shows **Notes saved:** and the page.
- **✍️ A draft in Media**: a draft called "On: <headline>" with the findings and
  their links. Media opens on it.

### A story about violence

A story about violence or a threat gets no read. The panel says "A story of
violence or a threat: what is confirmed, and no read."

### The local model only

Discuss runs on the model on this PC, never a cloud model. If the local model
is not serving, the panel says "The local model is not answering (<reason>).
Nothing was sent anywhere else; try again when it is serving."

In chat in the News workspace, Friday can do the same for a story you name.
Give her its link, or open it first.

## 📊 Your Media Diet

**📊 Your Media Diet** in the News toolbar opens a panel about your outlets.

### Rules from conversation

Tell Friday which outlets to avoid or prefer, in your own words: "never cite
this outlet", "I prefer that one". She does not change anything. She proposes
a rule, and says "I've put that in your Media diet for you to approve".

The proposal waits in the panel under **Waiting for you**. It shows the rule,
what you said, and where it applies. Choose **Approve** or **Reject**. Nothing
changes until you approve; a rejected proposal is dropped.

There are two kinds of rule: **Never cite** an outlet, and **Prefer** an
outlet. Only a **Never cite** rule removes stories.

### The panel

- **Your rules**: each rule in force, with **Remove**. Removing a rule needs no
  approval.
- **Waiting for you**: proposals to approve or reject.
- **What your rules removed**: the latest removals, with the time, the place
  and how many stories each rule removed.
- **Articles read by category**, **⭐ Most engaged sources** and **🚫 Least
  engaged sources**: what you read, save, boost, ban and ignore.

With no rules yet, it says "No rules yet. Tell <name> which outlets to avoid or
prefer; she proposes a rule here, and nothing applies until you approve it."

### Where the rules apply

A **Never cite** rule removes the outlet's stories from the Front Page, the
Briefing, the Week, News podcasts and Discuss. Each removal is recorded. In
Discuss, the panel says "N source(s) left out: your media diet".

The rules do not apply to the Editorial. See below.

## The Editorial

The Editorial is the one place where Friday writes as herself. It draws on the
whole week's saved news, including outlets your media diet leaves out. Banned
outlets are not saved after you ban them, so it only has their earlier
stories. Every editorial opens with "This editorial draws from my full source
index, not your curated feed. My job here is to tell you what I actually
think." Its foot lists your whole ban list.

The header shows an **independence** score. See
[How Friday checks her own answers](self-review.md#the-editorials-independence-score).

## What leaves your PC

- **Feeds and articles.** Friday fetches feeds and article pages from the
  publishers, as any reader would. Discuss fetches the story's page, and
  **Primary source** fetches the documents it links. Nothing about you is sent
  with them.
- **Your Local beat.** The city or area you set is sent to Google News as a
  search, to find local stories, and to Brave Search if you set a Brave key.
- **Weather.** A News episode's opening can include your weather. The city
  name, then its map position, is sent to Open-Meteo. See
  [Podcasts](podcasts.md).
- **Writing.** Which model writes each show is in
  [Scheduled jobs](scheduled-jobs.md). If a show is written by a cloud model,
  that model gets the stories; for the Briefing it also gets your mail
  senders, subjects and snippets and your calendar. Discuss answers are always
  written on this PC; a two-host podcast from **Make something** is scripted
  by the podcast writer, which can be a cloud model.
- **Brave Search.** If you set a Brave Search key and a topic's feeds have
  nothing, the Front Page, the Feed and the Briefing search Brave for news by
  topic.

Related: [Podcasts](podcasts.md), [Scheduled jobs](scheduled-jobs.md),
[Configuration](configuration.md), [How Friday checks her own
answers](self-review.md).
