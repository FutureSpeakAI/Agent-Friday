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
             L(2, "Regulators are moving faster than lawmakers this week. The FTC action suggests "
                  "voluntary commitments may not be enough. The FTC probe will define what comes next.", [g, h])]
    assert "close_recap" in q.HARD_CODES
    assert "close_recap" in codes(check(lines, ds))
    fixed, _cut = pe.edit_script(lines, ds, n_chapters=3)
    close = [ln for ln in fixed if ln["chapter"] == 2]
    assert [ln["text"] for ln in close] == ["Regulators are moving faster than lawmakers this week."]
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
