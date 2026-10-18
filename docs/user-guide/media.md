# Media

Media is the workspace for things you and Agent Friday™ have made: images,
videos, audio, podcast episodes, decks, documents, pages, articles, drafts and
posts. It gives every piece of work one card, with a preview, a status and a
record of how it was made.

## Open it

Choose **Media** in the **Work** group of the dock, or ask Friday ("show my
drafts", "what needs me", "show me September's videos"). The header offers
**Library**, **Pipeline** and **Calendar** views, **Insights**, **Own tab** (opens
Media in its own browser tab) and **+ New**.

## What Media lists

Friday builds the index itself, without you asking. It looks only at Friday's own
places, and never at your whole home folder:

- the creations folder (`friday-creations` on your Desktop),
- Friday's documents and podcast episodes you made yourself,
- Draft workspace copies, the Ideas board, and posts in the content store,
- cards made inside Media (ideas, drafts and articles), stored as Markdown files.

Nothing it reads is moved or rewritten. Changes you make on a card (status,
project, title, date) are kept separately and survive every re-index. News
editions and their shows stay in News, and your wiki stays in Knowledge. Search
finds them and links across.

While the first index builds, Media says "Indexing your library" and how many
cards it has so far.

## Statuses and views

Every card has one of Idea, Draft, In review, Scheduled, Published or Kept.
The left side offers default views (In progress, Needs you, Published, Kept here,
Everything), types (Drafts, Articles, Posts, Episodes, Images, Video, Audio and
music, Decks and documents, Charts and data, Pages, Codebases), projects, and a
privacy filter (private to this PC, shared or published, unsigned). You can group
by project, date or type, sort by what matters next, newest, title or status, and
switch between grid, list and a 3D layout.

You can save the current filters as a **collection**. A collection is evaluated
fresh each time you open it, and deleting one never deletes cards.

## Previews and Quick Look

Friday makes a preview for each card on this PC, lazily, in the background:

- images: a thumbnail with dimensions; image sets: a 2x2 mosaic,
- decks, sheets and office documents: the first page, or a text card of its words,
- pages (HTML): a screenshot of the first view, made with a headless browser that
  is refused every network request except the file itself,
- video: a poster frame, a frame strip for hover scrubbing, and the duration,
- audio and episodes: a waveform and the duration,
- anything else: no image, with the type and size in the details.

Select a card and press **Space**, or choose **Quick look**, for a large view. Use
the arrow keys to move to the next card and **Escape** to close it. Quick look
offers **Open card**, **Open in app** and **Show in folder**. The previews work
only when the PC has memory to spare, and they pause otherwise, so a busy PC may
show a card without a preview for a while. Nothing here leaves the PC.

## Search and transcripts

Press **/** to search titles, text, sources and transcripts. Friday transcribes
every audio and video card on this PC, one file at a time, after the preview
pass, using the speech recogniser (faster-whisper, English base model) on the CPU.
This needs that model on disk. Friday never downloads it on its own: if it is
missing, **Settings > Voice > Local voice models** shows "Transcription needs
whisper base.en (141 MB)" with a **Download** button (see
[Local voice models](local-voice-gpu-tier.md)).
Files longer than three hours are skipped with a note, and transcription waits
while the PC is short of memory. A search hit carries the time the words were
said. Transcripts are stored on this PC and nothing is sent.

## Ask and play

By voice or chat:

- "Show my drafts", "show the videos from this week" changes the view on screen.
- "What did I make this week?" lists cards without moving your screen.
- "Play the last podcast about AI policy" finds the newest matching audio or
  video card, opens it in Quick look and plays it, starting from where the words
  were said when a transcript exists.
- "Turn the ferry story into a podcast" makes a new card from an existing one.

You can also ask about a card from its editor with **Ask Friday**.

## Organize and convert

Select cards with Ctrl-click or **X**, or Shift-click for a run. The bar offers
**Move to project**, **Add a tag**, **Favourite** and **Unfavourite**. Friday can
do the same when you ask. One card changes at once. Two or more asked of Friday
wait for one approval card that lists them (your own bar in Media does not ask).

**Turn this into...** makes a new Draft card, related to the original: for
example a podcast, a post, a page, an article, slides, or read aloud. For audio
and video it can also make a transcript, captions (.srt and .vtt), a waveform
video, the sound track, a still, or, for a picture, the words it carries (text
recognition, not a description). Media offers only what this PC can make, and
names what it cannot, with the reason. Music is transcribed only when it is
Friday's own, because the words of other people's songs are not reproduced.

## What can become what

Open a card and use **Turn this into…** in its side rail, or select a card in
the Library and press **T**. Each choice makes a new Draft card. The new card
lists the original under **Related** as "made from".

| A card of this kind | Can become |
|---|---|
| Drafts, articles and documents you edit in Media | a podcast, slides, a post, a page, read aloud |
| Other documents | an article, read aloud, slides |
| Podcast episodes | an article, a post, captions, a video (a waveform, the captions burned in) |
| Images and image sets | a video (a slow push on the keeper), a post, a page (a gallery), the words in it |
| Decks and sheets | a page, an article, a podcast, narration, a narrated video |
| Posts | an article |
| Charts | a data podcast, slides, a post |
| Codebases | a page, a podcast |
| Audio | a transcript, captions, a video |
| Music | a transcript and captions (only for Friday's own music), a video |
| Video | a transcript, captions, the sound track, a still, a post |

A choice this PC cannot make is listed as "Not ... here", with the reason.
Other kinds, such as pages, show "Not offered for this kind: an episode about
an episode is noise. Edit it instead."

Turning a card into something new does not ask: it writes only on this PC.
Publishing does ask (see [Publishing](#publishing)).

## Making new things

**+ New** in the header (or the **N** key) makes an empty Draft card called
"Untitled" and opens it. You can also ask Friday to make something:

- **Images.** When a local image model is installed, the picture is made on
  this PC and nothing is sent. Otherwise Friday uses the cloud image service
  you set up, which receives the prompt after the privacy check. With Local
  only on and no local image model, Friday refuses: "Image generation is not
  done: Local only is on and no on-device image model is installed, so the
  prompt was not sent to a cloud provider."
- **Video.** Video normally uses a cloud service, such as Google Veo. The prompt
  leaves the PC, and so does the starting picture when you give one. With
  Local only on, Friday refuses and sends nothing.
- **Edits of your clips.** Stitching clips, adding music and exporting
  versions (for example 16:9 or 9:16) run on this PC with FFmpeg.
- **Decks and websites.** Friday writes the content, and a fixed template on
  this PC turns it into one self-contained HTML file.
- **Office files.** Friday makes and edits real .docx, .xlsx and .pptx files on
  this PC. No Office install is needed, and nothing is sent.

New files land in the creations folder and show up in Media.

**Daily creation** is a built-in scheduled job, and it is on by default. Once a
day, between 09:00 and 23:00, after 10 minutes without activity, Friday makes
one piece of her own choosing. Most days it is writing or code art. Some days
it is an image, a music clip or a short film, which use the same services as
when you ask, so their prompts can go to a cloud service unless Local only is
on. To stop it, switch off **Daily creation** under **Friday's built-in
routines** in **Workflows**. See [Scheduled jobs](scheduled-jobs.md).

## Provenance

A card's side rail has a **Provenance** box:

- **Made by**: who made it.
- **From**: the sources it was made from.
- **Credentials**: **Signed**, with the start of its fingerprint, or
  **Unsigned**, "signed when it leaves".
- **Versions**: the other versions of this card, or "This one".

A **Related** box lists linked cards, such as the card this one was "made
from".

The credential is a record of who made the file and from what. It is signed
with a key kept on this PC, so a later change to the file can be detected. It
is not issued by an outside authority.

## Cards in 3D

The **3D** layout in the Library shows your creations folder in the 3D file
browser. Documents and episodes are not in that folder; find them in the grid.

**🧊 View in 3D**, on the bar above Media, shows every card in 3D. Choose an
arrangement: **Clusters** (one ball per group), **Wall** (a curved wall, one
section per group), **Carousel** (a ring you scroll through) or **Timeline**
(newest nearest). Group by status, kind or project. **✕ Close 3D** goes back.

## Publishing

**Publish...** always raises one approval card, and nothing leaves the PC until
you approve it. The card names where it goes, what leaves (the file and its
credential, and nothing else), the sources, and how to undo. **Unpublish...**
puts it back. See [Approvals and receipts](approvals-and-receipts.md). Connected
publishing accounts are managed under **Settings > Connections**.

A post card goes through its own approval card for each platform. See
[Posting to your accounts](posting.md) for the composer, scheduling, the
Pipeline board and the Calendar.

## Tidy and the recoverable trash

Under **Housekeeping**, **Tidy up...** looks for near-duplicate images, video
posters and documents, and for stale drafts (Media's own drafts and ideas
untouched for 30 days with little text). It offers the result as **one approval
card**. Nothing moves until you approve it. In each group of duplicates, the
suggested keeper is the favourite, then the larger file, then the newer one.

Anything removed, by a tidy or by **Delete** on a card, moves to Friday's own trash
in `media/trash` with a record of where it came from. Delete asks you to confirm
first. Open **Housekeeping > Trash** to **Restore** an entry. Friday never empties
this trash for you. Posts that went out are never touched: you take those down on
the platform.

## What stays on your PC, and what can leave

Indexing, previews, transcripts, search, tidy and conversions that use local
tools all run on this PC. What can leave: a card you publish (after you approve),
and anything a conversion hands to a model or service you have set up for that
job.

## Limits

- The index covers Friday's own folders, not arbitrary files; use the
  [Library](library.md) for documents from elsewhere on the PC.
- Transcription is English and needs the speech model already on disk.
- Previews and transcripts wait while the PC is short of memory.
- Friday can tick, point at and filter cards on screen; see
  [See and Touch](see-and-touch.md).
