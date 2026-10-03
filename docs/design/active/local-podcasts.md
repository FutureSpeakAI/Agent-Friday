# Local podcasts: any sources, every news routine, data mode

> **Status:** implemented on branch `feat/local-podcasts` (phases 1–3); verified by real runs on the reference machine
> **Last verified:** 2026-09-29 against main `780e31fa`
> **Implementation:** `services/podcast_engine.py`, `services/podcast_render.py`,
> `services/podcast_sources.py`, `services/podcast_data.py`,
> `services/podcast_news.py`, `services/podcast_tools.py`, `routes/podcasts.py`
> **Supersedes / superseded by:** neither. Builds on `services/kokoro_voice.py`,
> `services/local_call.py`, `services/local_only_guard.py`,
> `services/scheduler.py` (`idle_work_blocked_reason`), `services/provenance.py`,
> `services/news_engine.py`, `services/voice_engine.py`, `services/desktop_bus.py`
> **Written:** 2026-09-29

The pattern (two tagged speakers who alternate, an outline written first, then
chapter-by-chapter writing that carries the conversation so far, and a cleaning
pass) is borrowed from podcastfy (Apache-2.0,
github.com/souzatharsis/podcastfy). Only the idea is borrowed; no code is.

---

## 0. Summary for the owner

1. **Every news routine comes with an episode.** The Front Page (morning and
   evening), the Briefing, the Weekly Digest and the Weekly Editorial each queue
   an episode when they finish. The routine is never held up by it; the episode
   follows a few minutes later and appears on that run in News with a transcript,
   chapters and a numbered source for every claim.
2. **All of it is written and spoken on this computer.** The script is written
   by the local model and the voices are Kokoro, running on the processor so the
   graphics card stays with the local model. The only thing that reaches the
   internet is what always did: fetching the articles themselves. Nothing about
   the episode is sent anywhere.
3. **Any sources, from anywhere.** Files, wiki pages, knowledge-graph entries,
   chat conversations, Studio creations, datasets or pasted text. It is reachable
   from Media (Studio until Media lands), from the "send to" menu, from chat ("make a podcast
   from …"), from voice, and as a typed tool.
4. **Data mode.** For a spreadsheet or CSV, Friday computes the numbers first,
   on this computer, and draws charts. The hosts may only say numbers that
   appear in that computation; a line with a number that cannot be traced is
   cut before it is spoken, and the cut is recorded.
5. **Two checks, named for what they check.** Before a word is spoken, the
   script-quality gate checks the script against the episode's story list and
   calendar (§3.11); the episode shows "script checked" or the problems it
   found. After speaking, the local speech recogniser transcribes the audio
   back and compares it with the script; that badge reads "audio matches
   script", because it checks the audio, not the writing. Either failure is
   marked, not hidden.
6. **Private stays private.** An episode made from mail, the vault, the wiki,
   files or chats is labelled "Private · made on this PC". No cloud model or
   cloud voice is ever used for it, and when voice mode (which may be a cloud
   voice) asks about it, it hears only a summary with personal details removed.

---

## 1. Why not depend on podcastfy

- **Weight.** Its requirements pin roughly 150 packages, including LangChain,
  the Google Cloud SDKs, ElevenLabs, a pydantic beta and numpy 1.26. Friday's
  venv runs numpy 2.4; the pin alone would break Kokoro and faster-whisper.
- **Every voice it ships is a cloud voice** (OpenAI, Google, ElevenLabs,
  Microsoft Edge). Edge TTS is free but still a network call. None satisfy
  "100% local".
- **Its writer goes through LiteLLM,** which can reach a local model, but it
  would bypass `local_only_guard`, the egress gate and the vault gating that
  every Friday model call goes through.
- What is worth having is the conversation pattern, which is a few hundred
  lines. It is written fresh against Friday's own local call, guard and
  provenance, and credited in `CREDITS.md`. podcastfy ships no NOTICE file, so
  Apache-2.0 §4(d) adds no obligation; the credit is courtesy.

---

## 2. Questioning it from five perspectives (STORM)

Each perspective was asked what it needs, what it fears, and what would make it
turn the feature off.

### 2.1 Public-radio producer

- **Needs:** a show that sounds produced, not read.
  - A cold open that states the one thing that matters today.
  - Chapters with a spoken signpost at each break.
  - Two voices that are clearly different.
  - A sign-off that says where the sources are.
- **Fears:**
  - Two hosts agreeing with each other for ten minutes.
  - "Great question!" filler.
  - Invented colour ("sources tell us").
- **Would turn it off if** the hosts sound like they are reading a list.

**Synthesis.**
- The script is planned as an outline of chapters first, then each chapter is
  written with the lines before it in view.
- Host B is a questioner and sceptic, not an echo. The prompt forbids praise of
  the other host's question.
- Chapter breaks carry a longer pause. The transcript is a numbered, cited
  script, not a text dump.

### 2.2 Data journalist

- **Needs:** every number traceable to a computation over the file, not to the
  model's arithmetic.
  - Denominators and time ranges stated.
  - Correlation never voiced as cause.
- **Fears:**
  - The model "rounding" 41.6% to "nearly half".
  - Inventing a year-on-year change the data cannot support.
  - Quietly averaging a column of IDs.
- **Would turn it off if** one invented number reaches air.

**Synthesis.**
- Facts are computed with pandas and numbered F1…Fn, each with the expression
  that produced it. "Which group is highest" is answered by the average per
  group with the rows it rests on (a total mixes volume with size), and the
  top group's lead is computed as a "times" figure so it is never eyeballed.
  Rows per period and the average per period are separate facts.
- The writer sees only the facts, never the raw rows.
- A validator parses every number in every line, including spelled-out numbers,
  percentages and "million". A number must match a computed fact at the
  precision the line states it (9,451 may stand for 9,450.5; 312 may not stand
  for 311); otherwise the line is cut. A small number with a unit ("six
  months", "3 times") must match that number with that unit in a fact, and a
  ratio word ("double", "half", "twice") must appear in a fact. Numbers that
  are part of names (the file name, column names, category values: "311",
  "Route 66") are not claims.
- Cuts are listed on the episode.
- ID-like columns (unique integers, names ending in `id`) are excluded from
  statistics.
- The prompt says "associated with", never "caused by".

### 2.3 Local TTS engineer

- **Needs:**
  - Kokoro on the CPU with its own pipeline, separate from voice mode's GPU
    pipeline.
  - One model shared by an American pipeline and a British pipeline (voices
    `a*` and `b*` need different g2p).
  - The espeak fallback wired.
  - Bounded synthesis per line.
- **Fears:**
  - `KPipeline()` and `load_single_voice()` call `hf_hub_download`, which makes
    a network request even when the files are cached.
  - A voice that is not cached is silently downloaded.
- **Would turn it off if** a render hangs the server.

**Synthesis.**
- The renderer resolves `config.json`, `kokoro-v1_0.pth` and each voice `.pt`
  with `huggingface_hub.try_to_load_from_cache` (no network).
- It passes file paths to `KModel(config=…, model=…)` and to the pipeline.
- A voice that is not cached is refused with its name. The episode fails
  loudly rather than downloading.
- Each line runs under `kokoro_voice.synthesis_budget_s`.
- Torch threads are capped at half the cores.

### 2.4 GPU scheduler engineer

- **Needs:**
  - Nothing new on the GPU. The local model already holds about 11 of 12 GB.
  - Script writing uses that same model through `local_call` and waits its turn
    in the single slot.
  - Speech synthesis and the listening check run on the CPU.
- **Fears:**
  - A podcast render starting while an image job holds the card.
  - A 30-minute render landing in the middle of the owner's chat.
- **Would turn it off if** chat slows because an episode is being made.

**Synthesis.** A single render worker drains a persistent queue at
`~/.friday/podcasts/queue.json`. Before each job it asks
`scheduler.idle_work_blocked_reason`:
- **Routine episodes (short or standard):** 60 s of owner inactivity and any
  hour. The routine has just used the model, and the episode "follows shortly".
- **Long episodes:** the owner's own idle window and idle threshold.
- **An episode the owner just asked for:** only stand-down and the GPU lease
  gate it.

In every case, stand-down and an exclusive GPU lease hold the queue. A job
waiting on the gate is "waiting", with the reason shown on the episode.

### 2.5 Accessibility advocate

- **Needs:**
  - A full transcript and WebVTT captions synchronised to the audio.
  - Chapter navigation by keyboard.
  - Playback speed.
  - Everything controllable by voice.
- **Fears:**
  - Audio that plays on its own.
  - A player that is only reachable by mouse.
- **Would turn it off if** there is no way to read what was said.

**Synthesis.**
- Captions are written from exact sample offsets.
- The player is a native `<audio>` with labelled buttons (previous and next
  chapter, speed, transcript) and a live-updating current-line region.
- Episodes notify; they never auto-play.
- Voice can play, pause, resume, skip chapters and ask "what's the source for
  that?", which answers from the line playing now.

---

## 3. Design

### 3.1 Episode

`~/.friday/podcasts/<episode_id>/`:

| File | What it holds |
|---|---|
| `episode.json` | Id, title, status, privacy, sources, attached run, hosts, chapters with times, lines with times and cites, verification, rejected lines |
| `audio.wav` | The master; `audio.mp3` too when ffmpeg is present |
| `captions.vtt` | Timed captions |
| `charts/*.svg` | Data mode only |

- Statuses run `queued → writing → speaking → checking → ready`, or `failed`
  with a reason, or `waiting` with the gate's reason.
- Signed provenance (`services/provenance.py`) is written for the audio with the
  tool chain (the local model, Kokoro and its voices) and the source list.

### 3.2 Sources

`podcast_sources.resolve(ref)` turns a reference into
`{id, title, kind, text, origin, private}`. The kinds:

| Kind | Reader |
|---|---|
| `file` | `file_extraction.extract_text` |
| `wiki` | `wiki_engine.wiki_read_text` |
| `kg_node` | the knowledge-graph store |
| `conversation` | `conversations.messages`; off-record messages are excluded |
| `creation` | a Studio file plus its metadata |
| `dataset` | data mode |
| `text` | pasted text |
| `news_run` | a Front Page, Briefing, Digest or Editorial |

Everything but public news articles is private.

### 3.3 Writing

- Always `local_call.call_json`, run on `scheduler._resolve_local_seat()` and
  inside `local_only_guard.local_only("Podcast")`.
- There is no cloud writer. The first call plans chapters, each naming its
  source ids; each later call writes one chapter's lines as
  `{speaker, text, cites}`.
- News prompts carry `voice_persona.VOICE_ANCHOR_RULES`, the same evidence rules
  as every news surface.
- The validator:
  - drops lines citing unknown sources;
  - requires a citation on any line with a number or a proper-noun claim;
  - merges consecutive same-speaker lines;
  - in data mode, runs the number check from §2.2.

### 3.4 Speaking and checking

- Kokoro on the CPU, one voice per host. Gaps are 0.30 s between speakers and
  0.9 s at chapter breaks.
- The check transcribes the master with `media_tools._whisper()` (base.en, CPU,
  int8) and scores word error rate after normalising numbers to words on both
  sides.
- Below 0.20 the episode is "checked". Otherwise it is "check failed", with the
  rate. It still plays; it is labelled.

### 3.5 Cloud

- A cloud voice (Gemini TTS through `voice_engine._synthesize_tts_wav_gemini`)
  exists only when the owner picks it per episode or in Settings.
- It is refused:
  - for a private episode;
  - in `local_only` mode;
  - inside a local-only run.
- It is credited on the episode. It is never the default.

### 3.6 Surfaces

- **Tools** (`services/podcast_tools.py`):
  - `make_podcast`, `podcast_list`, `podcast_play` (play, pause, resume, next
    or previous chapter, seek) and `podcast_source`.
  - All are ring 1, classified INTERNAL, and exposed to voice through
    `_VOICE_SHARED_TOOLS`.
  - Tool results for private episodes are passed through the private-summary
    seam (§3.7).
- **Routes** (`routes/podcasts.py`, owner and loopback only): list, create, get,
  audio, captions, charts, now-playing.
- **UI:** one global mini-player, the owner's own episodes in Media (Studio until
  Media lands), each News routine's episode on its News tab, an episode chip on
  each News run, and a "Make a podcast" destination in the shared send-to menu.
- **Playback control from voice:** a `podcast` action on the existing desktop
  bus.

### 3.7 Private-summary seam

`podcast_tools._private_summary(text)` is the single place a private episode's
content is turned into something a cloud voice session may hear.
- The local model writes a two-sentence summary, marking every person the way
  the voice handoff asks (`{{person: Name | relationship}}`).
- It then goes through the voice handoff's own scrub and floor,
  `local_context.prepare`: people become relationships, identifiers become
  numbered placeholders, and anything on the never-send floor withholds the
  summary entirely. Titles and source names of private episodes use the same
  path (`podcast_tools._scrub`).

### 3.8 Settings (`podcasts`)

```
enabled_for_routines: {front_page, briefing, weekly, editorial}   default all on
length:  {front_page: short, briefing: short, weekly: standard, editorial: standard}
hosts:   {a: {name: "Friday", voice: "af_heart"}, b: {name: "Emma", voice: "bf_emma"}}
on_ready: "notify"            ("notify" | "silent"; never auto-play)
cloud_voice: false
```

Lengths are about 700 words (~5 min) for short, 1,500 (~10 min) for standard
and 4,500 (~30 min) for long.

### 3.9 The news routines are local

- The Weekly Digest and Weekly Editorial join the Front Page and the Briefing in
  `LOCAL_ONLY_BY_DEFAULT`.
- A run started from the News buttons runs inside the same local-only guard as a
  scheduled one; before this, only scheduled runs were.
- If no local model is serving, the routine says so rather than going to the
  cloud.

### 3.10 Sounding and looking like Friday

- **Audio identity.** Every episode opens with a short rising motif (D5, A5,
  E6; about 1.2 s) and closes with the same notes falling, per the intro and
  outro slots in `docs/brand/BRAND.md` (under two seconds, sharing a motif). A
  designed sound placed at `assets/audio/podcast_intro.wav` or
  `podcast_outro.wav` (24 kHz mono) replaces the generated one with no code
  change.
- **Signature lines.** Fixed, not model-written: "This is <show>. I'm Friday."
  / "And I'm Emma." and "That's <show>. <where the sources are>. I'm Friday."
  The writer is told they are added and must not greet or sign off.
- **Friday's character.** The writing brief follows the brand's Voice
  section: answer first, then the evidence and how sure she is, what she did
  not check, her opinion labelled as her read, the listener addressed as
  "you". News carries `VOICE_ANCHOR_RULES`, the anchor register as traits;
  no real journalist or broadcaster is named, in prompts or copy. Generic
  two-host habits ("deep dive", "buckle up", gasps, echoing) are ruled out.
- **Steady voices.** Each host keeps one installed Kokoro voice across every
  episode (settings `podcasts.hosts`).
- **The look.** The player, the Studio view and the News chip take every
  colour, face and size from the `--fr-*` tokens, with each token's own value
  as its fallback until the token block is on the page. Amber is never used
  (it means "needs you"); "Private" is violet-soft. Charts read their colours
  from `agent_friday.brand` when it is present. `tests/unit/test_podcast_ui_brand.py`
  holds this.

### 3.11 The Briefing, and the script-quality gate

What the first live Briefing episode got wrong, and the rules that now hold
(`services/podcast_quality.py`, `tests/unit/test_podcast_quality.py`,
`tests/unit/test_podcast_briefing_episode.py`; the fixtures are a synthetic
day with the same defects).

- **Who is on the show** is a setting per routine, shown with the
  recommended value and changeable in the Podcasts view (Media, or Studio until
  Media lands), over
  `/api/podcasts/formats`, or by voice and chat (`podcast_format`). The
  Briefing, the Front Page and the Editorial are Friday alone (a newscast and
  an op-ed are one voice); the Weekly and episodes made from the owner's own
  sources are two hosts.
- **The Briefing keeps its sources.** The calendar events (title, times,
  place; never attendees or descriptions) and the news items (title, outlet,
  link, snippet) that a briefing was written from are saved beside the run
  (`briefing_runs/<date>.json`). The episode is written from those, one source
  per story and per event, with Friday's written notes as context. A run from
  before this has no story list, so its sign-off never claims links.
- **The gate**, run on every script before it is spoken:
  - *ledes*: a story's first mention says what happened, who or where, when,
    and the outlet aloud, then why it matters to the listener;
  - *safety*: violence, death or local safety is introduced plainly and
    attributed, never called background, with a practical line when it
    happened where the listener is going that day;
  - *calendar times*: every clock time is in the calendar or a source, and
    "before" / "after" agree with the calendar's order;
  - *repetition*: a word no source uses is said at most twice, no line
    restates another, and the close adds instead of re-reading;
  - *headings*: an outline heading is stripped from speech;
  - *fragments*: at most one flat fragment ("It's context.");
  - *link claim*: "linked in the transcript" only when every story heard has
    a link, which the transcript then lists under Sources;
  - *density*, for a solo newscast: at least two stories or events per spoken
    minute when the list has them.
  Lines that only restate are dropped; for the rest the writer gets up to two
  revision passes with the problems by line. What still fails is published
  with the problems shown, not passed as checked.
- **The bar.** A solo briefing is judged against a public-radio five-minute
  hourly newscast; a two-host episode against the best two-host audio
  overviews. It must still sound like Friday: evidence first and dry, with
  an anchor's authority, an explainer's build from context to consequence,
  and deadpan understatement. These are traits; no real person is named or
  imitated, in prompts or in copy.
- **Source chips** show the outlet or title, never a bare "S3", and are set
  apart from the sentence.

### 3.12 Links are attached by code, in every News routine

The saved briefings showed the fetched URLs reaching the prompt and the local
model keeping real article links on some days and none on others, and once
writing addresses of its own (a mail homepage, a jobs page). So no routine
lets the model write a link (`services/news_links.py`):

- The model sees each story as `[N3] Headline (outlet): snippet`, with no URL,
  and cites by id. Code turns each id into a link to the fetched URL, removes
  any link or address the model typed, and ends with the stories cited.
- Briefing: ids N1…; Weekly Digest: stories picked by id (W1…), title and
  link attached by code; Weekly Editorial: E1…, its cited stories saved
  beside it; Front Page: stories were already chosen by index and linked by
  code. The spoken Start My Day briefing gets the same stories without ids.
- `link_problems` fails text that cites no story or cites one without a
  working link. Google News redirect links are resolved to the publisher.
- An episode's source chips carry the same ids (`story_id`), so the written
  routine and its episode link the same story the same way.

### 3.13 A third pass on the Briefing episode

- Home is the listener's own setting (`news_local_area`): "here in <city>",
  never "where you are going". A practical line is owed only for a specific
  tie: the venue or street of one of today's events.
- Every fact in a sentence comes from the story it is about (`crossed_facts`).
- No read or opinion on violence or crime; elsewhere a read rests on the
  cited facts. The writer never narrates its own process.
- No forced relevance: a personal tie is said only when it is real.
- Stories are weighted by news value (local safety, policy, the economy
  first; gadgets last), at most eight in a briefing episode.
- `/api/podcasts/<id>/transcript.txt` is UTF-8 with a byte-order mark.

### 3.14 Blocking rules, applied before the gate

The codes in `podcast_quality.HARD_CODES` block an episode. The writer's
draft is edited by code (`podcast_engine.edit_script`) on every draft and
every revision, then revised by the model. A script that still has a
blocking problem fails as `script_rejected` and is kept for review, never
spoken.

- **A citation is a claim.** A sentence in a cited line must be supported by
  the cited story's text (`support`). Friday's commentary becomes her own
  line, with no outlet on it (`own`, the stories it is about in `about`),
  and the transcript marks it "Friday's analysis". A sentence is cut when it
  names a fact from a story it does not cite, names a fact no source holds,
  or names an outlet aloud for words that outlet never wrote.
- **Each story is told once.** A later sentence that comes back to a story
  after two lines on other stories is cut (`story_split`). The close is one
  sentence of synthesis (`close_recap`).
- **A story of violence or a threat stands alone**, with its own humane
  introduction. It is never a thread in another story, a summary or the
  close (`safety_threaded`), and it gets no read and no commentary.
- **Every story opens with a spoken lede** (`no_lede`): the outlet aloud,
  when, and what happened. Each story's source block gives the writer the
  outlet's spoken name and the date it was published.
- **The writer is told its used-up words.** Each chapter's prompt lists the
  writer's own words (ones no source uses) that it has already said twice.
- The Front Page's contrarian corner is an article and a note. The article
  is a story with its own outlet; only the note is Friday's own.

---

## 4. Not built, deliberately

- **Cloud script writer.** Not needed; the local model writes. If one is added
  later, it may only ever see the private-summary seam's output for private
  sources.
- **Auto-play.** Episodes notify.
- **Downloading voices at render time.** Refused; a voice is installed once, on
  purpose.

---

## 5. What real runs on the reference machine showed

Each of these was found by producing an episode with the real local model,
Kokoro on the CPU and faster-whisper, and is fixed with a test:

- **The local model thought itself out of room.** `local_call` sent no
  reasoning setting to a llama.cpp seat, so Bonsai 2 spent 8,000 tokens
  reasoning over a 14,000-character outline prompt and returned nothing. Seat
  calls now ask for no preamble, as the Ollama path always did; the same
  outline then took 36 s.
- **The serving local model was invisible to the scheduler.**
  `_resolve_local_seat` asked the reasoning seat (a cloud model here) and the
  Ollama daemon (not running); the llama.cpp seat named in
  `model_routing.local_model` was never asked. It is now.
- **A busy GPU or low RAM is a moment, not a failure.** Another session's test
  workers held CUDA contexts and the model stalled; the CPU voice once ran out
  of memory. Both now wait 20 minutes and retry, up to three tries.
- **Windows refuses a rename onto a file someone is reading.** Saving
  `episode.json` retries briefly.
- **Measured:** a four-minute Front Page episode took 4 min 43 s end to end
  (writing 1 min 45 s on the GPU seat, speaking about real time on 8 CPU
  threads); the listening check matched 616 of 618 words (5.2% word error). A
  data-mode episode over 1,475 rows matched at 2.8%. The only network traffic
  in either run was to 127.0.0.1.
