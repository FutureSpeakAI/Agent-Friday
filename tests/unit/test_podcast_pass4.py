"""The fourth pass, from a live Front Page episode: every News routine is
covered, not only the Briefing. Synthetic stories, city and weather only.

- the show format per routine (Front Page, Briefing, Editorial solo; Weekly two hosts)
- an anchor's open: city, day and date, local time, weather, the show
- no reasoning read aloud ("I did not check", "the evidence is thin", host meta talk)
- no echoed lines: the writer repeating the continuity tail it was shown
- one story in one place, one headline, at the top
- a clause attributed to an outlet comes from that outlet's item
"""
from __future__ import annotations

import json
import math
from datetime import datetime

import pytest

from agent_friday.services import podcast_engine as pe
from agent_friday.services import podcast_news
from agent_friday.services import podcast_quality as q
from agent_friday.services import podcast_render as render
from agent_friday.services import podcast_weather as weather

from podcast_briefing_fixture import docs, good_lines

SAVED: dict = {}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    SAVED.clear()
    SAVED["s"] = {"news_local_area": "Springfield, IL"}
    monkeypatch.setattr(core, "_load_settings", lambda: SAVED["s"])
    monkeypatch.setattr(weather, "current", lambda city, **k: None)
    return tmp_path


@pytest.fixture
def ds():
    return docs()


def _codes(problems):
    return [p["code"] for p in problems]


def _check(lines, ds):
    return q.script_problems(lines, ds, n_chapters=3, news=True, personal=True, solo=True,
                             home="Springfield, IL")


# 1 ── the format, for every routine ─────────────────────────────────────────

@pytest.mark.parametrize("routine,fmt", [("front_page", "solo"), ("briefing", "solo"),
                                         ("editorial", "solo"), ("weekly", "duo")])
def test_every_news_routine_has_its_format(routine, fmt):
    ep = pe.create([{"kind": "text", "text": "x", "title": "t"}], origin="routine",
                   attached={"routine": routine, "run_id": "2031-03-12"})
    assert ep["format"] == fmt
    opening, _ = pe.signature_lines(ep)
    assert [ln["speaker"] for ln in opening] == (["a"] if fmt == "solo" else ["a", "b"])


# 2 ── the anchor's open ─────────────────────────────────────────────────────

def _news_ep(routine="front_page", when=(2031, 3, 12, 18, 4)):
    ep = pe.create([{"kind": "text", "text": "x", "title": "t"}], origin="routine",
                   attached={"routine": routine, "run_id": "2031-03-12"})
    ep["created_at"] = datetime(*when).timestamp()
    return ep


def test_the_open_is_an_anchor_s_from_home_with_date_time_and_weather(monkeypatch):
    monkeypatch.setattr(weather, "current", lambda city, **k: {
        "text": "71 degrees and clear", "source": "Open-Meteo", "url": "https://open-meteo.com/"})
    ep = _news_ep()
    pe.stamp_open(ep)
    opening, _ = pe.signature_lines(ep)
    assert opening[0]["text"] == ("Good evening from Springfield, Illinois. It's Wednesday, "
                                  "March 12, 6:04 PM, and 71 degrees and clear. "
                                  "This is Friday's Front Page. I'm Friday.")
    assert ep["open"]["weather_source"] == "Open-Meteo"


def test_without_weather_the_open_simply_leaves_it_out():
    ep = _news_ep(when=(2031, 3, 12, 7, 30))
    pe.stamp_open(ep)
    assert pe.signature_lines(ep)[0][0]["text"] == (
        "Good morning from Springfield, Illinois. It's Wednesday, March 12, 7:30 AM. "
        "This is Friday's Front Page. I'm Friday.")


def test_the_transcript_names_the_weather_source(monkeypatch):
    monkeypatch.setattr(weather, "current", lambda city, **k: {
        "text": "71 degrees and clear", "source": "Open-Meteo", "url": "https://open-meteo.com/"})
    ep = _news_ep()
    pe.stamp_open(ep)
    ep.update(lines=pe.signature_lines(ep)[0], sources=[], duration_s=10, chapters=[{"title": "Open"}])
    text = pe.transcript_bytes(ep).decode("utf-8-sig")
    assert "Weather: Open-Meteo (https://open-meteo.com/), city level" in text


def test_weather_is_looked_up_by_city_only_and_fails_quietly():
    calls = []

    def fetch(url):
        calls.append(url)
        if "geocoding" in url:
            return {"results": [{"latitude": 39.8, "longitude": -89.6, "country_code": "US",
                                 "admin1": "Illinois", "name": "Springfield"}]}
        return {"current": {"temperature_2m": 70.6, "weather_code": 0}}
    got = weather.lookup("Springfield, IL", fetch=fetch)
    assert got == {"text": "71 degrees and clear", "source": "Open-Meteo", "url": "https://open-meteo.com/"}
    assert "name=Springfield" in calls[0] and "temperature_unit=fahrenheit" in calls[1]

    def down(url):
        raise OSError("offline")
    assert weather.lookup("Springfield, IL", fetch=down) is None


# 3 ── no reasoning read aloud ───────────────────────────────────────────────

@pytest.mark.parametrize("leak", [
    "I did not check the full text of the pledge.",
    "The evidence is thin on what the pledge requires.",
    "That's a gap I need to flag.",
    "You're right to push back.",
    "That missing detail is what a sharp listener would want to know.",
    "I only have the framing from the interview, not the data.",
    "That's a fair read, but it leaves the question hanging.",
])
def test_reasoning_read_aloud_fails_the_script(ds, leak):
    lines = good_lines(ds)
    lines[4] = dict(lines[4], text=lines[4]["text"] + " " + leak)
    probs = _check(lines, ds)
    assert "reasoning_leak" in _codes(probs)
    assert "reasoning_leak" in q.HARD_CODES


def test_reasoning_sentences_are_cut_before_speech():
    kept, _ = pe.clean_lines([{"speaker": "a", "cites": ["S1"], "text":
                               "The pledge has no enforcement. The evidence is thin on its terms. "
                               "You're right to push back. The six firms signed on Tuesday."}], {"S1"})
    assert kept[0]["text"] == "The pledge has no enforcement. The six firms signed on Tuesday."


def test_an_unknown_said_as_a_fact_about_the_reporting_is_fine(ds):
    lines = good_lines(ds)
    lines[4] = dict(lines[4], text=lines[4]["text"] + " Brightline Bank hasn't published its method.")
    assert "reasoning_leak" not in _codes(_check(lines, ds))


def test_one_read_per_story(ds):
    lines = good_lines(ds)
    capex = next(d["sid"] for d in ds if d["title"].startswith("AI data"))
    lines[5] = dict(lines[5], text="My read: $400B of spending outruns any pledge. My read is that "
                                   "the spending of $400B next year is the real story.", cites=[capex])
    assert "read_cap" in _codes(_check(lines, ds))


# 4 ── no echoed lines ───────────────────────────────────────────────────────

def test_a_line_repeated_word_for_word_fails_and_is_hard(ds):
    lines = good_lines(ds)
    echo = dict(lines[4], text=lines[4]["text"].replace("that ", "that ").replace("up from", "up from"),
                chapter=2, cites=[])
    lines.insert(11, echo)
    assert "duplicate_line" in _codes(_check(lines, ds)) and "duplicate_line" in q.HARD_CODES


def test_the_writer_echoing_its_continuity_tail_is_cut_at_the_stitch(home, monkeypatch):
    """The live bug: shown the last lines as continuity, the writer repeated
    them (contractions expanded, cites dropped) as new output."""
    import numpy as np

    def llm(system, user, *, max_tokens=3000):
        if "Plan an episode" in user:
            return {"title": "T", "chapters": [{"title": "A"}, {"title": "B"}, {"title": "C"}]}, "m"
        if "PROBLEMS FOUND" in user:
            body = user.split("sign-off are added around it):\n", 1)[1].split("\n\nPROBLEMS FOUND", 1)[0]
            return {"lines": json.loads(body)}, "m"
        n = user.count("Chapter ")
        ch = int(user.split("Chapter ", 1)[1].split(" ", 1)[0])
        own = {1: ["The council passed the transit budget on Tuesday, seven votes to two.",
                   "It's the first split vote on transit since the new council was seated."],
               2: ["The storm closed the river road overnight, and crews expect it open on Friday.",
                   "Commuters are being sent over the north bridge until then."],
               3: ["Watch the council's next vote on bus routes, due in April."]}[ch]
        out = [{"speaker": "a", "text": t, "cites": ["S1" if ch == 1 else "S2"]} for t in own]
        if ch == 3:                                 # the echo, as the live writer did it
            tail = user.split("ALREADY WRITTEN", 1)[1].split("\n\n", 1)[0] if "ALREADY WRITTEN" in user \
                else user.split("ended with:\n", 1)[1].split("\n\n", 1)[0]
            for t in [x.split(": ", 1)[1] for x in tail.strip().splitlines() if ": " in x]:
                out.append({"speaker": "a", "text": t.replace("It's", "It is"), "cites": []})
        return {"lines": out}, "m"

    class S:
        def speak(self, text, voice):
            k = int(0.05 * render.RATE * max(1, len(text.split())))
            return (0.1 * np.sin(2 * math.pi * 220 * np.arange(k) / render.RATE)).astype("float32")
    monkeypatch.setattr(pe, "_llm_json", llm)
    monkeypatch.setattr(render, "speaker", lambda: S())
    monkeypatch.setattr(render, "encode_mp3", lambda *a, **k: False)
    real = render.listen_back
    monkeypatch.setattr(render, "listen_back", lambda w, s, transcribe=None: real(w, s, transcribe=lambda p: s))
    ep = pe.create([{"kind": "text", "title": "Council", "text": "The council passed the transit budget "
                     "on Tuesday, seven votes to two, the first split vote since the council was seated."},
                    {"kind": "text", "title": "Storm", "text": "A storm closed the river road overnight; "
                     "crews expect it to reopen Friday; traffic uses the north bridge."}], length="short")
    done = pe.produce(ep["id"])
    texts = [ln["text"] for ln in done["lines"] if not ln.get("signature")]
    assert len(texts) == len(set(texts))
    assert not any(t.startswith("It is the first split vote") for t in texts)
    assert any("echo" in r["reason"] for r in done["rejected"])


# 5 ── one story, one place; one headline at the top ─────────────────────────

def test_a_story_split_across_the_episode_is_named(ds):
    lines = good_lines(ds)
    pledge = next(d["sid"] for d in ds if d["title"].startswith("Tech chiefs"))
    lines.insert(10, {"speaker": "a", "chapter": 2, "cites": [pledge],
                      "text": "Back to the voluntary pledge the six chief executives signed in Washington."})
    assert "story_split" in _codes(_check(lines, ds))


def test_the_headline_is_named_once(ds):
    lines = good_lines(ds)
    lines[10] = dict(lines[10], text="So the pledge is the headline tonight. " + lines[10]["text"])
    lines[1] = dict(lines[1], text="The most important thing today is the 9:00 AM interview with Northwind Labs.")
    assert "two_headlines" in _codes(_check(lines, ds))


# 6 ── an outlet's clause comes from that outlet ─────────────────────────────

def _with_insight(ds):
    return ds + [{"sid": "S99", "kind": "digest", "role": "digest", "title": "Friday's written briefing: Insight",
                  "heading": "4. Proactive Insight",
                  "text": "Your Sentinel framework is cryptographically signed and verified.", "url": ""}]


def test_friday_s_own_analysis_attributed_to_an_outlet_is_named_and_hard(ds):
    dd = _with_insight(ds)
    lines = good_lines(dd)
    lines[2] = dict(lines[2], text=lines[2]["text"] + " Example Wire reports that the pledge is "
                    "voluntary, while the Sentinel framework is cryptographically signed.")
    probs = [p for p in _check(lines, dd) if p["code"] == "misattributed"]
    assert probs and "Sentinel" in probs[0]["message"] and "misattributed" in q.HARD_CODES


def test_friday_s_analysis_labelled_as_hers_is_fine(ds):
    dd = _with_insight(ds)
    lines = good_lines(dd)
    lines[3] = dict(lines[3], text="My read: a pledge is a promise; a signed, verified constraint like "
                    "your Sentinel framework is a control.", cites=[lines[3]["cites"][0], "S99"])
    assert "misattributed" not in _codes(_check(lines, dd))


def test_a_hard_problem_that_survives_revision_fails_the_episode(home, monkeypatch):
    import podcast_briefing_fixture as fx
    (home / "wiki" / "briefings").mkdir(parents=True)
    (home / "wiki" / "briefings" / (fx.DATE + ".md")).write_text(fx.digest_markdown(), encoding="utf-8")
    (home / "briefing_runs").mkdir()
    (home / "briefing_runs" / (fx.DATE + ".json")).write_text(json.dumps(fx.sidecar()), encoding="utf-8")
    ds = fx.docs()
    bad = good_lines(ds)
    bad[2] = dict(bad[2], text=bad[2]["text"] + " Example Wire reports that Brightline Bank "
                  "estimates $400B of spending next year.")

    def llm(system, user, *, max_tokens=3000):
        if "Plan an episode" in user:
            return {"title": "T", "chapters": [{"title": "A"}, {"title": "B"}, {"title": "C"}]}, "m"
        if "PROBLEMS FOUND" in user:
            return {"lines": [dict(chapter=ln["chapter"], **{k: ln[k] for k in ("speaker", "text", "cites")})
                              for ln in bad if not ln.get("signature")]}, "m"
        ch = int(user.split("Chapter ", 1)[1].split(" ", 1)[0]) - 1
        return {"lines": [{k: ln[k] for k in ("speaker", "text", "cites")} for ln in bad
                          if not ln.get("signature") and ln["chapter"] == ch]}, "m"
    monkeypatch.setattr(pe, "_llm_json", llm)
    spoken = []
    monkeypatch.setattr(render, "render_lines", lambda *a, **k: spoken.append(1) or (b"", []))
    done = pe.produce(podcast_news.queue_for_run("briefing", fx.DATE)["id"])
    assert done["status"] == "failed" and done["error"]["code"] == "script_rejected"
    assert "misattributed" in done["error"]["message"] and not spoken
    assert any(p["code"] == "misattributed" for p in done["script_check"]["problems"])


def test_a_single_what_to_watch_line_in_the_close_is_not_a_split(ds):
    assert "story_split" not in _codes(_check(good_lines(ds), ds))


def test_retelling_a_story_in_the_close_is_a_split(ds):
    """The live case: the close covered the regulator's probe a second time."""
    lines = good_lines(ds)
    pledge = next(d["sid"] for d in ds if d["title"].startswith("Tech chiefs"))
    lines.insert(11, {"speaker": "a", "chapter": 2, "cites": [pledge],
                      "text": "So the pledge is back: six chief executives signed it in Washington on Tuesday."})
    assert "story_split" in _codes(_check(lines, ds))
