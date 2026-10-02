"""The fifth pass, from a regenerated Front Page: the four rules that block an
episode until the script satisfies them. Synthetic stories; the one quoted
line is the critic's own example of the failure.

- a sentence credited to a source is supported by that source's text;
  Friday's commentary is hers, with no outlet's name on it, or it is cut
- each story is told once, and the close is one sentence of synthesis
- a story of violence or a threat to safety stands alone, never a thread
- every story opens with a spoken lede, and the writer is told which of its
  own words it has used up
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from agent_friday.services import podcast_engine as pe
from agent_friday.services import podcast_news
from agent_friday.services import podcast_quality as q
from agent_friday.services import podcast_sources
from agent_friday.services import podcast_weather as weather

#: The critic's example: Friday's comparison with the owner's own project,
#: said under two publishers' names that never wrote it.
CRITIC_LINE = ("The contrast is clear: your cLaws framework is cryptographically signed and "
               "verified before every action, while the Trump AI safety accord is a voluntary "
               "pinky-swear with no enforcement mechanism.")

TUESDAY = datetime(2031, 3, 11, 18, 0).timestamp()


def edition():
    return {
        "id": "2031-03-12-evening", "headline": "The Agent's Dirty Secret",
        "lead": {"title": "US trade regulator opens investigation into AI giants including Anthropic and OpenAI",
                 "source": "theguardian.com", "url": "https://www.theguardian.com/us-news/2031/mar/11/ftc-ai",
                 "snippet": "The Federal Trade Commission has opened an investigation into AI companies "
                            "including Anthropic and OpenAI over the dangers their agents pose.",
                 "ts": TUESDAY},
        "sections": [{"articles": [
            {"title": "FTC opens probe into AI giants including Anthropic and OpenAI",
             "source": "reuters.com", "url": "https://www.reuters.com/technology/ftc-probe-ai",
             "snippet": "The U.S. Federal Trade Commission opened a probe into Anthropic and OpenAI, "
                        "its first enforcement action on rogue AI agents.", "ts": TUESDAY},
            {"title": "Here's what AI leaders are saying about Trump's new safety plan",
             "source": "theverge.com", "url": "https://www.theverge.com/ai/trump-safety-plan",
             "snippet": "Executives responded on Tuesday to President Trump's voluntary AI safety plan.",
             "ts": TUESDAY},
            {"title": "Democrats block data center bill in Senate",
             "source": "thehill.com", "url": "https://thehill.com/data-center-bill",
             "snippet": "Senate Democrats blocked a bill on data center electricity costs on Tuesday, "
                        "calling it toothless.", "ts": TUESDAY},
            {"title": "Suspect arrested in 'credible' plot to attack the Springfield Capitol, police say",
             "source": "springfieldcourier.com", "url": "https://springfieldcourier.com/capitol-plot",
             "snippet": "State police said Tuesday they arrested a suspect in a credible plot to attack "
                        "the state Capitol in Springfield. They have not named a motive.", "ts": TUESDAY},
            {"title": "House committee presses Springfield officials on grants and contracts",
             "source": "thetribune.example", "url": "https://thetribune.example/grants",
             "snippet": "A state House committee on Tuesday pressed Springfield officials over "
                        "third-party grants and contracts.", "ts": TUESDAY},
        ]}],
        "contrarian_corner": {
            "note": "Your cLaws framework is cryptographically signed and verified before every action; "
                    "the Trump AI safety accord is a voluntary pinky-swear with no enforcement mechanism. "
                    "The contrast is the difference between a safety framework you can verify and one "
                    "you can only hope is honored.",
            "title": "Trump's AI Safety 'Accord' Is a Fancy Pinky-Swear",
            "url": "https://www.wired.com/story/ai-safety-accord", "source": "wired.com"},
    }


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "_load_settings", lambda: {"news_local_area": "Springfield, IL"})
    monkeypatch.setattr(weather, "current", lambda city, **k: None)
    return tmp_path


@pytest.fixture
def ds():
    return podcast_sources.number(podcast_news._front_page_docs(edition()))


def sid(ds, start):
    return next(d["sid"] for d in ds if d["title"].startswith(start))


def L(chapter, text, cites):
    return {"speaker": "a", "chapter": chapter, "text": text, "cites": list(cites)}


def check(lines, ds):
    return q.script_problems(lines, ds, n_chapters=3, news=True, personal=True, solo=True,
                             home="Springfield, IL")


def codes(problems):
    return [p["code"] for p in problems]


def ftc_lede(ds):
    return L(0, "The Guardian reports that on Tuesday the Federal Trade Commission opened an "
                "investigation into AI companies including Anthropic and OpenAI, and Reuters "
                "confirms the probe.", [sid(ds, "US trade"), sid(ds, "FTC opens")])


# 1 ── a citation is a claim about the source ────────────────────────────────

def test_the_critics_line_under_two_publishers_names_is_misattributed(ds):
    g, r = sid(ds, "US trade"), sid(ds, "FTC opens")
    lines = [ftc_lede(ds), L(0, CRITIC_LINE, [g, r])]
    hard = [p for p in check(lines, ds) if p["code"] in q.HARD_CODES]
    assert any(p["code"] == "misattributed" and p["line"] == 1 for p in hard), hard


def test_the_critics_line_never_ships_under_a_publishers_name(ds):
    g, r = sid(ds, "US trade"), sid(ds, "FTC opens")
    lines, cut = pe.edit_script([ftc_lede(ds), L(0, CRITIC_LINE, [g, r])], ds, n_chapters=3)
    for ln in lines:
        if {g, r} & set(ln["cites"]):
            assert "cLaws" not in ln["text"] and "pinky" not in ln["text"].lower(), ln
    # It names the Trump accord, a story the line does not cite: cut, not relabelled.
    assert not any("cLaws" in ln["text"] for ln in lines)
    assert any("cLaws" in c["text"] for c in cut)
    assert "misattributed" not in codes(check(lines, ds))


def test_commentary_in_a_cited_line_becomes_fridays_own(ds):
    g, r = sid(ds, "US trade"), sid(ds, "FTC opens")
    own = "The difference is between a safety framework you can verify and one you can only hope is honored."
    lines, _cut = pe.edit_script([L(0, ftc_lede(ds)["text"] + " " + own, [g, r])], ds, n_chapters=3)
    assert [ln["text"] for ln in lines] == [ftc_lede(ds)["text"], own]
    assert lines[0]["cites"] == [g, r] and not lines[0].get("own")
    assert lines[1]["cites"] == [] and lines[1]["own"] is True
    assert "misattributed" not in codes(check(lines, ds))


def test_a_read_is_never_credited_to_the_outlet(ds):
    g, r = sid(ds, "US trade"), sid(ds, "FTC opens")
    read = "My read is that the probe marks a shift from voluntary promises to enforcement."
    lines, _cut = pe.edit_script([L(0, ftc_lede(ds)["text"] + " " + read, [g, r])], ds, n_chapters=3)
    mine = [ln for ln in lines if ln["text"] == read]
    assert mine and mine[0]["own"] and mine[0]["cites"] == []
    # Still a grounded read on the story it follows.
    assert "ungrounded_read" not in codes(check(lines, ds))


def test_the_transcript_marks_fridays_own_lines():
    ep = {"title": "T", "show": "The Front Page", "format": "solo", "hosts": {"a": {"name": "Friday"}},
          "chapters": [{"title": "Open"}], "sources": [],
          "lines": [{"speaker": "a", "chapter": 0, "text": "A framework you can verify.", "cites": [],
                     "own": True, "start": 1}]}
    text = pe.transcript_bytes(ep).decode("utf-8-sig")
    assert "A framework you can verify.   (Friday's analysis)" in text


def test_the_contrarian_corner_is_a_story_and_fridays_note(ds):
    wired = [d for d in ds if d.get("outlet") == "wired.com"]
    assert wired and wired[0]["url"] == "https://www.wired.com/story/ai-safety-accord"
    overview = next(d for d in ds if d.get("role") == "overview")
    assert "cLaws" in overview["text"] and "Fancy Pinky-Swear" not in overview["text"]
    assert "wired.com" not in overview["text"]


def test_merging_never_folds_fridays_own_line_into_a_cited_one():
    a = {"speaker": "a", "chapter": 0, "text": "The FTC opened a probe.", "cites": ["S2"]}
    b = {"speaker": "a", "chapter": 0, "text": "My read: it matters.", "cites": [], "own": True, "about": ["S2"]}
    assert len(pe.merge_turns([a, b])) == 2


# 2 ── each story once; the close is one sentence ────────────────────────────

def test_a_story_told_twice_blocks_and_is_edited_to_once(ds):
    g, h = sid(ds, "US trade"), sid(ds, "Democrats block")
    lines = [ftc_lede(ds),
             L(1, "The Hill reports that on Tuesday Senate Democrats blocked a bill on data center "
                  "electricity costs, calling it toothless.", [h]),
             L(1, "The bill had been pitched as a fix for electricity costs.", [h]),
             L(1, "The House committee story aside, the bill stays stalled.", [h]),
             L(1, "The FTC probe into Anthropic and OpenAI is the most significant development, "
                  "as it signals active enforcement.", [g])]
    assert "story_split" in q.HARD_CODES
    assert "story_split" in codes(check(lines, ds))
    fixed, cut = pe.edit_script(lines, ds, n_chapters=3)
    assert "story_split" not in codes(check(fixed, ds))
    assert any("most significant" in c["text"] for c in cut)
    assert fixed[0]["text"] == ftc_lede(ds)["text"]


def test_the_close_is_one_sentence_of_synthesis(ds):
    g, h = sid(ds, "US trade"), sid(ds, "Democrats block")
    lines = [ftc_lede(ds),
             L(1, "The Hill reports that on Tuesday Senate Democrats blocked a bill on data center "
                  "electricity costs, calling it toothless.", [h]),
             L(2, "The FTC is moving faster than the Senate this week. The FTC action suggests "
                  "voluntary commitments may not be enough. The FTC probe will define what comes next.", [g, h])]
    assert "close_recap" in q.HARD_CODES
    assert "close_recap" in codes(check(lines, ds))
    fixed, _cut = pe.edit_script(lines, ds, n_chapters=3)
    close = [ln for ln in fixed if ln["chapter"] == 2]
    assert [ln["text"] for ln in close] == ["The FTC is moving faster than the Senate this week."]
    assert "close_recap" not in codes(check(fixed, ds))


# 3 ── a safety story stands alone ───────────────────────────────────────────

def test_a_plot_to_attack_is_a_safety_story(ds):
    plot = next(s for s in q.stories(ds) if "Capitol" in s["title"])
    assert plot["safety"]


def test_a_safety_story_is_never_a_thread_in_another(ds):
    g, c, t = sid(ds, "US trade"), sid(ds, "Suspect arrested"), sid(ds, "House committee")
    lines = [L(0, "The Springfield Courier reports that on Tuesday state police arrested a suspect in "
                  "a credible plot to attack the state Capitol in Springfield. Police have not named "
                  "a motive.", [c]),
             ftc_lede(ds),
             L(1, "The Tribune reports that on Tuesday a state House committee pressed Springfield "
                  "officials over third-party grants and contracts.", [t]),
             L(2, "The Capitol plot arrest is a serious local matter, but the FTC probe is the story "
                  "that will define what comes next.", [c, g])]
    assert "safety_threaded" in q.HARD_CODES
    probs = check(lines, ds)
    assert any(p["code"] == "safety_threaded" and p["line"] == 3 for p in probs), probs
    fixed, cut = pe.edit_script(lines, ds, n_chapters=3)
    assert "safety_threaded" not in codes(check(fixed, ds))
    assert any("Capitol plot arrest" in x["text"] for x in cut)
    assert fixed[0]["text"].startswith("The Springfield Courier reports")


def test_no_read_on_a_plot_to_attack(ds):
    c = sid(ds, "Suspect arrested")
    lines = [L(0, "The Springfield Courier reports that on Tuesday state police arrested a suspect in "
                  "a credible plot to attack the state Capitol in Springfield.", [c]),
             L(0, "My read is that the word credible matters here.", [c])]
    assert "opinion_on_violence" in codes(check(lines, ds))
    fixed, _cut = pe.edit_script(lines, ds, n_chapters=3)
    assert not any("My read" in ln["text"] for ln in fixed)


def test_the_prompt_keeps_safety_stories_apart():
    assert "never a thread" in pe.NEWS_RULES


# 4 ── ledes block; the writer is told its own repeated words ───────────────

def test_a_missing_lede_blocks():
    assert "no_lede" in q.HARD_CODES


def test_a_the_domain_is_said_with_its_article():
    assert q.spoken_outlet({"outlet": "thetribune.example"}) == "The Tribune"


def test_each_story_brings_its_lede_kit(ds):
    g = next(d for d in ds if d["sid"] == sid(ds, "US trade"))
    assert "Published: Tuesday, March 11" in g["text"]
    block = pe._source_block(ds, {g["sid"]})
    assert 'Say the outlet as "The Guardian"' in block


def _fake_writer(monkeypatch, chapters, prompts):
    def llm(system, user, *, max_tokens=3000):
        prompts.append(user)
        if "Plan an episode" in user:
            return {"title": "T", "chapters": [{"title": "A"}, {"title": "B"}, {"title": "C"}]}, "m"
        if "PROBLEMS FOUND" in user:
            body = user.split("sign-off are added around it):\n", 1)[1].split("\n\nPROBLEMS FOUND", 1)[0]
            return {"lines": json.loads(body)}, "m"
        ch = int(user.split("Chapter ", 1)[1].split(" ", 1)[0]) - 1
        return {"lines": [{"speaker": "a", "text": t, "cites": c} for t, c in chapters[ch]]}, "m"
    monkeypatch.setattr(pe, "_llm_json", llm)


def test_the_writer_is_told_which_of_its_own_words_are_used_up(ds, monkeypatch):
    g, h = sid(ds, "US trade"), sid(ds, "Democrats block")
    prompts: list = []
    _fake_writer(monkeypatch, {
        0: [(ftc_lede(ds)["text"], [g]),
            ("It is a local story, a local test and a local question for the local industry.", [g])],
        1: [("The Hill reports that on Tuesday Senate Democrats blocked a bill on data center "
             "electricity costs, calling it toothless.", [h])],
        2: [("Regulators are moving faster than lawmakers this week.", [g, h])]}, prompts)
    ep = pe.create([{"kind": "text", "text": "x", "title": "t"}], origin="routine",
                   attached={"routine": "front_page", "run_id": "2031-03-12-evening"})
    pe.write_script(ep, ds)
    chapter_two = next(p for p in prompts if "Chapter 2 of" in p)
    assert "already said twice" in chapter_two and "local" in chapter_two.split("already said twice", 1)[1]
    assert "at most twice" in pe.NEWS_RULES
    close = next(p for p in prompts if "Chapter 3 of" in p)
    assert "one sentence" in close


def test_blocking_rules_are_satisfied_before_the_script_ships(ds, monkeypatch):
    """Every draft re-tells a story and recaps in the close; the script that
    comes back has neither: it was edited until it passed."""
    g, h = sid(ds, "US trade"), sid(ds, "Democrats block")
    prompts: list = []
    _fake_writer(monkeypatch, {
        0: [(ftc_lede(ds)["text"], [g, sid(ds, "FTC opens")])],
        1: [("The Hill reports that on Tuesday Senate Democrats blocked a bill on data center "
             "electricity costs, calling it toothless.", [h]),
            ("The bill had been pitched as a fix for electricity costs.", [h]),
            ("Senate Democrats called the data center bill toothless.", [h]),
            ("The FTC probe into Anthropic and OpenAI is the most significant development.", [g])],
        2: [("Regulators are moving faster than lawmakers. The FTC probe will define what comes "
             "next. " + CRITIC_LINE, [g])]}, prompts)
    ep = pe.create([{"kind": "text", "text": "x", "title": "t"}], origin="routine",
                   attached={"routine": "front_page", "run_id": "2031-03-12-evening"})
    got = pe.write_script(ep, ds)
    left = codes(got["script_check"]["problems"])
    for code in ("story_split", "close_recap", "misattributed", "safety_threaded"):
        assert code not in left, got["script_check"]["problems"]
    assert not any("cLaws" in ln["text"] for ln in got["lines"])


# 5 ── a lede keys on the story, not its topic's vocabulary ─────────────────

def test_a_story_first_said_without_its_outlet_or_what_happened_blocks(ds):
    """Modelled on the real misses: the bill is referred to as if already known."""
    h = sid(ds, "Democrats block")
    lines = [ftc_lede(ds),
             L(1, "Senate Democrats and their data center bill are next, and it is a setback.", [h])]
    probs = [p for p in check(lines, ds) if p["code"] == "no_lede" and p["sid"] == h]
    assert probs and "no_lede" in q.HARD_CODES
    assert "outlet" in probs[0]["message"] and "what happened" in probs[0]["message"]


def test_a_well_introduced_story_that_says_ai_passes(ds):
    lines = [ftc_lede(ds),
             L(0, "The FTC says it is the first enforcement action on rogue AI agents.",
               [sid(ds, "FTC opens")])]
    assert "no_lede" not in codes(check(lines, ds))


def test_topic_vocabulary_is_not_a_mention_of_a_story(ds):
    """AI, the state, a bill: words of the day's topics, not any one story."""
    vocab = L(0, "AI is in every headline this week, and the state and a new bill are in most of them.", [])
    stories_named = [s["sid"] for s in q.stories(ds) if q._mentions(vocab, s)]
    assert stories_named == []
    lines = [vocab, ftc_lede(ds)]
    assert not [p for p in check(lines, ds) if p["code"] == "no_lede" and p["line"] == 0]


def test_two_everyday_words_a_story_happens_to_capitalise_are_not_its_identity():
    """"Officials" and "Budget" capitalised in one item, written in lower case
    by another: vocabulary, so "officials want a budget" names neither story."""
    docs_ = podcast_sources.number([
        {"title": "Transit chiefs meet on fares", "kind": "news", "outlet": "examplewire.com",
         "url": "https://examplewire.com/fares", "private": False,
         "text": "Transit chiefs meet on fares\nThe chiefs met Tuesday. Then Officials and Budget "
                 "writers argued over Riverton fares."},
        {"title": "Library hours cut", "kind": "news", "outlet": "exampleledger.com",
         "url": "https://exampleledger.com/library", "private": False,
         "text": "Library hours cut\nThe officials set the budget for Elmford libraries."}])
    line = L(0, "Everyone's officials want a budget this year.", [])
    assert [s["title"] for s in q.stories(docs_) if q._mentions(line, s)] == []
    assert [s["title"] for s in q.stories(docs_) if q._mentions(L(0, "Riverton fares rise.", []), s)] \
        == ["Transit chiefs meet on fares"]


def test_a_briefing_run_that_gathered_nothing_speaks_from_its_written_sections(home):
    """A run's sidecar with no stories and no calendar is no structured source:
    the episode is made from the written briefing, as for a run from before
    sources were kept."""
    (home / "wiki" / "briefings").mkdir(parents=True)
    (home / "wiki" / "briefings" / "2031-03-12.md").write_text(
        "# Briefing\nIntro\n## Calendar\nThe 3pm review moved.\n## News\nThe budget passed 7-2.\n",
        encoding="utf-8")
    p = podcast_news.sidecar_path("2031-03-12")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 1, "date": "2031-03-12", "calendar": [], "news": []}), encoding="utf-8")
    said = " ".join(d["text"] for d in podcast_news.run_documents("briefing", "2031-03-12"))
    assert "The budget passed 7-2." in said and "The 3pm review moved." in said


# 6 ── her name is the one the owner gave her ────────────────────────────────

def test_her_name_reads_like_the_page_does():
    from agent_friday import brand
    assert brand.her_name("AGENT FRIDAY") == "Friday"
    assert brand.her_name("NOVA") == "Nova" and brand.her_name("Jarvis") == "Jarvis"
    assert brand.her_name("") == "Friday" and brand.her_name(None) == "Friday"


def test_the_show_speaks_and_shows_her_chosen_name(monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda: {"agent_name": "NOVA"})
    assert pe.settings()["hosts"]["a"]["name"] == "Nova"
    ep = pe.create([{"kind": "text", "text": "x", "title": "t"}], origin="routine",
                   attached={"routine": "front_page", "run_id": "2031-03-12-evening"})
    opening, closing = pe.signature_lines(ep)
    assert opening[0]["text"].endswith("I'm Nova.") and closing[0]["text"].endswith("I'm Nova.")
    text = pe.transcript_bytes(dict(ep, chapters=[{"title": "Open"}], sources=[], lines=[
        {"speaker": "a", "chapter": 0, "text": "A framework you can verify.", "cites": [], "own": True, "start": 1}
    ])).decode("utf-8-sig")
    assert "Nova alone" in text and "(Nova's analysis)" in text and "Friday" not in text.replace("Agent Friday", "")


def test_a_host_name_the_owner_set_for_the_show_wins(monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda: {"agent_name": "NOVA",
                                                         "podcasts": {"hosts": {"a": {"name": "Ada"}}}})
    assert pe.settings()["hosts"]["a"]["name"] == "Ada"


# 7 ── a script for review, before any audio ────────────────────────────────

def test_a_script_only_run_writes_and_checks_the_script_and_speaks_nothing(ds, monkeypatch):
    """The same sources and gates; no voice is loaded. The audio is made by a
    later produce() that resumes at speaking."""
    from agent_friday.services import podcast_render as render
    g, h = sid(ds, "US trade"), sid(ds, "Democrats block")
    _fake_writer(monkeypatch, {
        0: [(ftc_lede(ds)["text"], [g, sid(ds, "FTC opens")])],
        1: [("The Hill reports that on Tuesday Senate Democrats blocked a bill on data center "
             "electricity costs, calling it toothless.", [h])],
        2: [("Regulators are moving faster than lawmakers this week.", [g, h])]}, [])
    monkeypatch.setattr(pe, "_gather", lambda ep: [dict(d) for d in ds])
    spoke = []
    monkeypatch.setattr(render, "render_lines", lambda *a, **k: spoke.append(1) or (b"", []))
    monkeypatch.setattr(render, "speaker", lambda: spoke.append("voice"))
    ep = pe.create([{"kind": "text", "text": "x", "title": "t"}], origin="routine",
                   attached={"routine": "front_page", "run_id": "2031-03-12-evening"})
    done = pe.produce(ep["id"], script_only=True)
    assert done["status"] == "scripted" and done["lines"] and not spoke
    assert done["script_check"]["ok"] in (True, False)
    text = (pe._dir(ep["id"]) / "transcript.txt").read_bytes().decode("utf-8-sig")
    assert "The Hill reports" in text and "SOURCES" in text


# 8 ── what the first script-only Front Page showed ──────────────────────────

def test_one_event_from_several_outlets_needs_one_lede_with_its_outlet(ds):
    """Two outlets, one event: the first mention introduces it, naming either."""
    g, r = sid(ds, "US trade"), sid(ds, "FTC opens")
    lines = [L(0, "The Guardian reports that on Tuesday the Federal Trade Commission opened an "
                  "investigation into AI companies including Anthropic and OpenAI.", [g]),
             L(0, "The commission called it its first enforcement action on rogue AI agents.", [g]),
             L(0, "Reuters confirms the probe into Anthropic and OpenAI.", [r])]
    assert "no_lede" not in codes(check(lines, ds))


def test_a_month_is_not_a_place():
    assert q._places("incidents first reported in July, and in Austin on Monday") == {"austin"}


def test_fridays_own_notes_cited_show_as_her_analysis():
    ep = {"title": "T", "show": "The Front Page", "format": "solo", "hosts": {"a": {"name": "Friday"}},
          "chapters": [{"title": "Close"}],
          "sources": [{"id": "S1", "title": "Today's front page: X", "kind": "news", "role": "overview", "url": ""}],
          "lines": [{"speaker": "a", "chapter": 0, "text": "It adds up to a tense week.", "cites": ["S1"], "start": 1}]}
    text = pe.transcript_bytes(ep).decode("utf-8-sig")
    assert "It adds up to a tense week.   (Friday's analysis)" in text


# 9 ── polish from the editorial read ────────────────────────────────────────

def test_an_outlet_is_named_as_it_publishes_itself():
    """A news-feed snippet ends with the publisher's own name: "Click2Houston",
    not the domain capitalised ("Click2houston")."""
    story = {"outlet": "click2houston.com", "title": "Suspect arrested after alleged plot, police say",
             "text": "Suspect arrested after alleged plot, police say\nOutlet: click2houston.com\n"
                     "Suspect arrested after alleged plot, police say Click2Houston"}
    assert q.spoken_outlet(story) == "Click2Houston"
    assert q.spoken_outlet({"outlet": "click2houston.com", "title": "x"}) == "Click2houston"


def test_several_outlets_on_one_event_become_one_lede(ds):
    """KUT, Click2Houston and KVUE each said the same arrest: one lede names
    the outlets, and only a line with new facts stays."""
    g, r = sid(ds, "US trade"), sid(ds, "FTC opens")
    lines = [L(0, "The Guardian reports that on Tuesday the Federal Trade Commission opened an "
                  "investigation into AI companies including Anthropic and OpenAI.", [g]),
             L(0, "Reuters reports that the Federal Trade Commission opened a probe into Anthropic "
                  "and OpenAI on Tuesday.", [r]),
             L(0, "Reuters adds that it is the first enforcement action on rogue AI agents.", [r])]
    fixed, cut = pe.edit_script(lines, ds, n_chapters=3)
    assert fixed[0]["text"].startswith("The Guardian and Reuters report that on Tuesday the Federal")
    assert sorted(fixed[0]["cites"]) == sorted([g, r])
    assert any(c["text"].startswith("Reuters reports that the Federal") for c in cut)
    assert any("first enforcement action" in ln["text"] for ln in fixed)


def test_fridays_own_notes_are_never_cited_and_an_empty_wrap_is_cut(ds):
    g, h = sid(ds, "US trade"), sid(ds, "Democrats block")
    overview = next(d["sid"] for d in ds if d.get("role") == "overview")
    lines = [ftc_lede(ds),
             L(1, "The Hill reports that on Tuesday Senate Democrats blocked a bill on data center "
                  "electricity costs, calling it toothless.", [h]),
             L(1, "A promise is not a control you can check.", [overview]),
             L(2, "The news adds up to a moment of tension between enforcement and inaction.", [overview])]
    fixed, cut = pe.edit_script(lines, ds, n_chapters=3)
    assert not any(overview in ln["cites"] for ln in fixed)
    mine = next(ln for ln in fixed if ln["text"].startswith("A promise"))
    assert mine.get("own") and mine["cites"] == []
    assert not any(ln["chapter"] == 2 for ln in fixed)
    assert any("moment of tension" in c["text"] for c in cut)
    assert "names the stories it connects" in pe.NEWS_RULES and "one lede that names" in pe.NEWS_RULES
