# Podcasts

Agent Friday™ can turn your material into a podcast episode. An episode has
chapters, a transcript with timed captions, and a source for every claim. Friday
writes the script with your local model and speaks it on your PC. The same
engine makes the audio versions of the News shows.

## What you get

- **Episodes from any sources.** A file, a wiki page, a knowledge-graph entry, a
  conversation, something Friday made, a dataset, pasted text, a web page, or a
  News run.
- **Short, standard or long.** About 5, 10 or 30 minutes.
- **Solo or two hosts.** A solo episode is Friday alone, like a newscast. With
  two hosts the default voices are Friday and Emma. Each show has a recommended
  setting: the Briefing, the Front Page and the Editorial are recommended solo,
  the Weekly and episodes you make yourself are recommended with two hosts.
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
     workspace, which makes a short episode from that item;
   - **Turn this into... a podcast** on a Media card (see [Media](media.md));
   - **Listen** on a News edition;
   - or ask in chat or by voice, for example "make a podcast from my notes on the
     ferry project" or "make a 30 minute episode about this spreadsheet".
2. Friday queues the episode and says so. You get a notice when it is ready.
3. Play it from the Podcasts page or News, or say "play the Briefing". By voice
   you can pause, resume, stop, skip a chapter, go back a chapter, or seek.

An episode you ask for starts as soon as the PC allows: it waits only if Friday
is stood down at your request, or if another job holds the GPU exclusively.
Episodes from routines wait until you have been idle for 60 seconds. A long
episode waits for your idle window. A failure caused by the moment (no local
model serving, low memory, the voice busy) is retried up to three times, 20
minutes apart. **Cancel**, **Retry** and **Delete** are on each episode. Delete
asks you to confirm and removes the audio from this PC.

## How an episode is checked

- Each spoken line carries the ids of the sources it rests on. A line citing a
  source that does not exist is cut, and a line with a number and no citation is
  cut. Every cut is recorded on the episode.
- A quality check reads the script before it is spoken. It looks at ledes (what
  happened, who or where, when, and the outlet), times against your calendar,
  repetition, and whether a sentence is supported by the story it cites. The
  writer gets up to two revision passes. A script that still fails is not spoken.
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

- **The script is written locally and never by a cloud model.** If no local model
  is serving, the episode fails with that reason. It does not fall back to the
  cloud.
- **Speech runs on this PC's processor** with a local voice (Kokoro). The
  listening check runs locally too. A voice that is not installed is refused by
  name and never downloaded for this.
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
- **Web page sources** reach the internet when fetched. Nothing else does.

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
