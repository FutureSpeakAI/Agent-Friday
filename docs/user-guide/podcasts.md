# Podcasts

Agent Friday™ can turn your material into a podcast episode. An episode has
chapters, a transcript with timed captions, and a source for every claim. Friday
writes the script with your local model and speaks it on your PC. (One
exception: see [What stays on your PC](#what-stays-on-your-pc-and-what-can-leave).)
The same
engine makes the audio versions of the News shows.

## What you get

- **Episodes from any sources.** A file, a wiki page, a knowledge-graph entry, a
  conversation, something Friday made, a dataset, pasted text, a web page, or a
  News run.
- **Short, standard or long.** About 2, 5 or 10 minutes spoken. Standard is
  recommended and is the default for every show and for the episodes you ask
  for. See [How long an episode runs](#how-long-an-episode-runs).
- **Fewer stories, told in depth.** A News episode tells a few stories, each
  from its full article where the page can be read, not a list of headlines.
- **Solo or two hosts.** A solo episode is Friday alone, like a newscast. Solo
  is recommended, and is the default, for every show and for the episodes you
  make yourself. You can choose two hosts for a show, for all your own
  episodes, or for one episode. The second host is called Nova until you name
  them (or "Agent Friday" if you have renamed Friday).
- **Chapters, captions and a transcript.** Open the transcript or captions
  alongside the audio. Each line is timed.
- **A source for every claim.** Ask "what's the source for that?" while an
  episode plays and Friday names the outlet and link, or the computed fact. If a
  line cites nothing, Friday tells you it was the hosts' own connective talk.
- **Data mode.** Give it a CSV, TSV, Excel or Parquet file. Friday computes the
  numbers first, then the hosts talk only about what was computed. A line with a
  number that is not in the computed facts is cut before it is spoken, and small
  charts are drawn with the plotted numbers in their description.

The News shows are The Front Page, The Briefing, The Week and The Editorial.
Each is credited as "from Agent Friday™". Each finished News run queues its own
episode, so the routine is never held up, and a notice tells you when the
episode is ready (the default).

## Make an episode

1. Open the **Podcasts** page, add one or more sources, choose a length, and
   optionally add an angle or emphasis. Choose to make it. You can also use:
   - **Make a podcast** on a conversation, a wiki page or an item in another
     workspace, which makes an episode of your usual length from that item;
   - **Turn this into... a podcast** on a Media card (see [Media](media.md));
   - **Listen** on a News edition;
   - or ask in chat or by voice, for example "make a podcast from my notes on the
     ferry project" or "make a 10 minute episode about this spreadsheet".
2. Friday queues the episode and says so. You get a notice when it is ready.
3. Play it from the Podcasts page or News, or say "play the Briefing". By voice
   you can pause, resume, stop, skip a chapter, go back a chapter, or seek.

Writing an episode is a job in the **Local AI queue** (the chip that shows what
your local model is doing). It runs one job at a time, after the job ahead of
it, and it pauses before each step while you are chatting or talking to Friday
by voice. It never waits for you to be away from the computer: you asked for
it, or its News show is due. It waits otherwise only if Friday is stood down at
your request, or if another job holds the GPU exclusively. You can cancel it
from the queue like any other job. A failure caused by the moment (no local
model serving, low memory, the voice busy) is retried up to three times, 20
minutes apart. **Cancel**, **Retry** and **Delete** are on each episode. Delete
asks you to confirm and removes the audio from this PC.

## How long an episode runs

| Length | Spoken | Stories in a News episode |
|---|---|---|
| Short | about 2 minutes | 2 |
| Standard (recommended) | about 5 minutes | 3 or 4 |
| Long | about 10 minutes | up to 6 |

The length is measured in spoken time. The word count Friday aims for comes from
how fast the local voice actually speaks, after the fixed opening, sign-off and
music. A script can come out a little shorter than its target when the check
cuts lines it could not trace to a source; it is never padded to make up the
time.

Each show has its own length, and the episodes you ask for have one too. Set
them in **Settings > Voice > Podcasts > Episode length**, or ask: "make the
Front Page podcast 10 minutes", "make tomorrow's Briefing longer". A change
asked for in chat or by voice shows the old and new length and waits for your
yes, like every setting Friday changes for you. A length you choose when making
one episode applies to that episode only.

## Depth: each story from its full article

For each story the episode's plan picks, Friday reads the full article through
its page reader (the same guarded reader the News deep dive uses) and gives the
writer the article's own passages, not only the feed's two-line summary. The
writer is asked, for each story, for what happened with at least one concrete
fact or number from the source, why it matters, what happened before, and what
to watch next, and to leave out any of these the sources do not give. When two
outlets report the same event, both are named aloud and the episode says what
each adds.

A page that cannot be read (a paywall, an error, a page that is not an article)
leaves that story on its summary: it is told briefly, and the transcript marks
it "summary only". Reading a page sends nothing about you or the episode; it is
fetched as any reader would fetch it.

## How an episode is checked

- Each spoken line carries the ids of the sources it rests on. A line citing a
  source that does not exist is cut, and a line with a number and no citation is
  cut. Every cut is recorded on the episode.
- A quality check reads the script before it is spoken. It looks at ledes (what
  happened, who or where, when, and the outlet), times against your calendar,
  repetition, and whether a sentence is supported by the story it cites. A
  sentence is checked against everything the writer was given for that story:
  its summary and the article passages it read. A fact that is only in the
  article stands; a fact or figure that is in no source is cut. The writer gets
  up to two revision passes (three for a long episode). A script that still
  fails is not spoken.
- The transcript lists the sources of each segment (the outlet, the headline
  and the link, and whether the full article or only the summary was read),
  so every claim can be traced to the report it rests on.
- After speaking, a listening check transcribes the audio and compares it with
  the script. An episode counts as checked when the word error rate is 20 percent
  or lower.
- The episode carries signed provenance.

## What always asks

Making an episode from your own material needs no approval card, because it
writes only on your PC. A web page source is fetched from the internet to be read
(the page says so; nothing about the episode is sent anywhere). Playing an episode
moves your own screen and does not ask. Anything you then do with an episode
outside Friday (sending, posting or publishing it) asks as usual; see
[Approvals and receipts](approvals-and-receipts.md).

An episode is a file, so you cannot make one while you are off the record.

## What stays on your PC, and what can leave

- **Where the script is written.** A private episode (see below) is always
  written by your local model on this PC. Other episodes are written locally
  too, with one exception: if you chose cloud for everything (the routing mode
  "cloud only", in the setup chat or in Settings) and a cloud model is set for
  that job (**Settings > Spending**), the script for a public episode, such as a News show, is written by
  that cloud model. If you never chose that mode yourself, it stays local. If no
  local model is serving when one is needed, the episode fails with that reason.
- **Speech runs on this PC's processor** with a local voice (Kokoro). The
  listening check runs locally too. Friday never downloads a voice on its own:
  if the Kokoro voice is missing, **Settings > Voice > Local voice models**
  offers it with its size and a **Download** button (see
  [Local voice models](local-voice-gpu-tier.md)).
- **Private episodes.** An episode built from any of your own material (files,
  wiki, graph entries, conversations, creations, datasets, pasted text, and the
  Briefing, which carries mail and calendar) is marked private. A private episode
  may not use a cloud voice. A voice session on a cloud model hears only a short
  summary written by the local model with personal details removed, and titles
  and source names are scrubbed the same way.
- **Cloud voices.** They are off for podcasts by default, are refused whenever
  Local-only mode is on, and are never used for a private episode.
- **Weather.** A News episode's opening can include the weather where you live.
  Only the city name (from your Local beat setting) is sent, to Open-Meteo, a
  free public service. If the lookup fails, the weather is left out.
- **Web page sources** and the **articles of the stories an episode tells**
  reach the internet when fetched: a plain page request to the publisher.
  Nothing else does.

## Limits

- At most 60,000 characters of source text go to the writer for an episode, and
  at most 8,000 from one document. Longer sources are cut.
- A spoken episode needs about 4 GB of memory headroom for the voice. When the PC
  cannot make room, Friday says there is not room to speak it right now and tries
  again in a few minutes.
- A data episode loads up to two million rows. An old .xls file needs an extra
  package, and Friday asks you to save it as .xlsx or CSV instead.
- Voices come from the voices installed on this PC.

Related: [Voice](voice.md), [Privacy](privacy.md),
[Scheduled jobs](scheduled-jobs.md).
