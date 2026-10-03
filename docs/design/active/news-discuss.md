# Discuss with Friday: a story, evidence first

Status: spec, approved for build (2026-10-02). Owner: the News workspace.
Related: `docs/design/active/local-podcasts.md` (the podcast engine and its
script gate), the Media diet (News → Your Media Diet).

## What it is

Every story in News (Front Page, Feed, Briefing, Weekly, a deep link) has a
**Discuss** button. Voice offers the same thing ("Friday, discuss the FTC
story", "compare the coverage of that"). It opens a panel beside the story
with one of seven ways to go deeper. Each answer is evidence first: what the
sources say, each source linked, then Friday's own analysis, labelled as
hers. The owner's media diet rules hold in every answer.

## Hard rules

- **Local only, $0.** The model is the local brain seat (`arbiter-local`),
  the same path the podcast writer uses (`podcast_engine._llm_json`). If the
  seat is not serving, the panel says so and offers to try again; there is
  no cloud fallback. Web fetches (articles, primary documents, other
  outlets' coverage) are allowed, through the SSRF-guarded fetcher.
- **Links come from code.** Sources are numbered `[D1]`... in the prompt;
  the model cites ids; links are attached from the fetched URLs, never typed
  by the model (the News links rule, `news_links`).
- **Her analysis is hers.** Every answer has two parts: *What the sources
  say* (each claim cited) and *My read* (no citations, labelled). The
  podcast gate's support check (`podcast_quality.support`) runs on the first
  part: a sentence its cited source does not support moves to the second, or
  is cut.
- **The media diet holds.** Blocked outlets are never fetched, cited or
  suggested. The answer carries a receipt line when a rule removed something
  ("Fox News left out: your media diet").
- **No opinion on violence.** A story of violence or a threat gets the
  confirmed facts and who confirmed them, no read (the podcast rule).

## The seven modes

| Mode | What it does | Sources it uses |
|---|---|---|
| Compare coverage | How other outlets framed the story, and what each left out. A table: outlet, headline, framing in one line, what it omits relative to the others. | The story's event cluster (`podcast_quality.stories` clusters) from today's pool and archive; a news search for the headline's names when the cluster has fewer than three outlets. |
| Primary source | Finds the bill, filing, study, court record or transcript the story is about, links it, and quotes the passage the story rests on. | Links in the article body to .gov, courts, journals, SEC/EDGAR, congress.gov and arXiv, and a search on the names plus "bill", "filing" or "study". It says "not found" rather than guess. |
| Background | What led here: a dated timeline, who's who (people and organisations, one line each, from the sources), and the open questions. | The archive (stories that ran before, `news_seen`), the article, and the wiki's notes on the names. |
| Claim check | Each checkable claim in the story, marked *confirmed* (two or more independent outlets or a primary source), *disputed* (sources disagree; both cited), or *one side only* (only the party making it). | The cluster and the primary source. |
| Follow | Tracks the story: its names and headline fingerprint go on a follow list; the next Front Page and Briefing surface any development as an Update with what changed (`news_seen` marks it), and say when nothing changed. | `~/.friday/news/follows.json`. |
| Local angle | What the story means where the owner lives (the Local beat setting, `news_local_area`), from local outlets and the owner's calendar. It says "no local angle found" rather than invent one. | Local outlets (the Local beat sources) and the calendar. |
| Make something | Turns the story and the panel's answer into: a two-host deep-dive podcast (the podcast engine, any-source show, local voice); notes in the wiki; or a draft in Media. | The story, the panel's sources. |

## Voice

One tool, `discuss_story(story, mode)`. The story is a URL, a headline, or
"that story" (the one on screen or last mentioned). The mode is one of
the seven. It is a ring-1 tool (reads the web, writes only the owner's own
files), shared by text and voice. A spoken answer is the first part's top
two findings and the read, with "the links are on screen".

## Build order

1. The engine: `services/news_discuss.py`, holding the sources (cluster,
   fetch, primary-source finder), the prompt per mode, the local call, the
   support check and the diet filter. Unit tests per mode use synthetic
   stories and a fake model.
2. The route `POST /api/news/discuss {url, title, mode}` and the follow list.
3. The UI: a Discuss button on every story card and a panel (both pages),
   with rendered frames looked at.
4. The voice tool, and its registration.
5. Make something: wiring to the podcast engine, the wiki and Media.

## Not in scope

Discuss never posts, emails or shares anything. Following a story never
pulls from a blocked outlet. A cloud model is never used, even when the
local seat is down.
