"""One weak story never sinks a podcast episode, and a cut never orphans one.

A real Front Page episode was rejected whole ("no_lede") because the script
check cut one sentence, the lede of a story about a homicide, and so made the
story's next line its first mention without an outlet. The fixture
(podcast_lede_fixture) has that structure with invented names and a neutral
city. The checks themselves are untouched; what these tests hold is the
decision about the episode, and the words the owner reads when it is refused.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from agent_friday.services import podcast_engine as pe
from agent_friday.services import podcast_quality as q
from agent_friday.services import podcast_render as render
from agent_friday.services import podcast_weather as weather

import podcast_lede_fixture as fx

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "_load_settings", lambda: {"news_local_area": "Riverton"})
    monkeypatch.setattr(weather, "current", lambda city, **k: None)
    monkeypatch.setattr(pe, "article_text", lambda url: "")
    return tmp_path


@pytest.fixture
def speaker(monkeypatch):
    np = pytest.importorskip("numpy", reason="needs numpy, which the podcast extra installs")
    spoken = []

    class S:
        def speak(self, text, voice):
            spoken.append((voice, text))
            n = int(0.05 * render.RATE * max(1, len(text.split())))
            return (0.1 * np.sin(2 * math.pi * 220 * np.arange(n) / render.RATE)).astype("float32")
    monkeypatch.setattr(render, "speaker", lambda: S())
    monkeypatch.setattr(render, "encode_mp3", lambda *a, **k: False)
    real = render.listen_back
    monkeypatch.setattr(render, "listen_back", lambda w, s, transcribe=None: real(w, s, transcribe=lambda p: s))
    return spoken


def _writer(lines):
    """A stand-in for the local model that writes `lines` chapter by chapter and
    hands the same lines back on every revision: a writer that cannot improve."""
    calls = []
    by_ch = {}
    for ln in lines:
        by_ch.setdefault(ln["chapter"], []).append(
            {"speaker": "a", "text": ln["text"], "cites": ln["cites"]})

    def llm(system, user, *, max_tokens=3000):
        calls.append(user)
        if "Plan an episode" in user:
            return {"title": "The Front Page", "chapters": [
                {"title": "Open"}, {"title": "The news"}, {"title": "Close"}]}, "bonsai2:27b"
        if "PROBLEMS FOUND" in user:
            return {"lines": [dict(x, chapter=c) for c, xs in by_ch.items() for x in xs]}, "bonsai2:27b"
        return {"lines": by_ch.get(sum("Chapter " in c for c in calls) - 1, [])}, "bonsai2:27b"
    return llm


def _episode(monkeypatch, docs, lines):
    monkeypatch.setattr(pe, "_gather", lambda ep: docs)
    monkeypatch.setattr(pe, "_llm_json", _writer(lines))
    ep = pe.create([{"kind": "news_run", "routine": "front_page", "run_id": "2031-03-14-evening"}],
                   length="short", origin="routine",
                   attached={"routine": "front_page", "run_id": "2031-03-14-evening"})
    return pe.produce(ep["id"])


def _body(done):
    return " ".join(ln["text"] for ln in done.get("lines") or [] if not ln.get("signature"))


# -- the episode that was rejected -------------------------------------------------

def test_the_front_page_whose_homicide_lede_was_cut_airs_without_the_stories_it_cannot_tell(
        monkeypatch, speaker):
    ds = fx.docs()
    lines, tip, chat = fx.writer_lines(ds)
    done = _episode(monkeypatch, ds, lines)
    assert done["status"] == "ready", done.get("error")
    said = _body(done)
    assert "homicide" not in said and "tipline" not in said          # no orphaned follow-up line
    assert "The Guardian reports" in said and "KVUE reported" in said
    left = {s["sid"]: s for s in done["left_out"]}
    assert set(left) == {tip, chat}
    assert "homicide" in left[tip]["headline"]
    assert done["script_check"]["left_out"] == done["left_out"]
    assert not [p for p in done["script_check"]["problems"] if p["code"] in q.HARD_CODES]
    # Said in the transcript, in plain words, beside the episode.
    text = pe.transcript_bytes(done).decode("utf-8-sig")
    assert "Left out: Northwind's AI gave Springfield police a fake tip" in text
    assert "Left out: What Northwind's AI Model Told Me" in text and "opening line was cut" in text
    # What was spoken is what the script says.
    assert " ".join(t for _v, t in speaker) == " ".join(ln["text"] for ln in done["lines"])


# -- a cut never orphans its story ------------------------------------------------

def test_a_story_whose_lede_is_cut_takes_its_other_lines_with_it():
    ds = fx.docs()
    lines, tip, _chat = fx.writer_lines(ds)
    out, cut = pe.edit_script(lines, ds, 3, fx.HOME)
    assert "folded into another" in " ".join(c["reason"] for c in cut)        # the lede was cut ...
    assert not any(tip in ln["cites"] for ln in out)                          # ... and so was the rest
    assert not any("tip" in ln["text"] and "department" in ln["text"] for ln in out)
    assert [c["story"] for c in cut if c.get("story")] == [tip]
    assert not [p for p in q.script_problems(out, ds, n_chapters=3, news=True, personal=True,
                                             solo=True, home=fx.HOME)
                if p["code"] == "no_lede" and p["sid"] == tip]


def test_a_story_that_keeps_its_lede_keeps_its_lines():
    ds = fx.docs()
    lines, tip, _chat = fx.writer_lines(ds)
    calm = [dict(ln, text=ln["text"].replace("Northwind AI model", "automated tool"))
            for ln in lines]
    out, cut = pe.edit_script(calm, ds, 3, fx.HOME)
    assert any(tip in ln["cites"] for ln in out)
    assert not [c for c in cut if c.get("story")]


# -- one unfixable story does not sink the episode --------------------------------

def _plain_lines(ds, *, mute):
    """Four stories, each with a proper lede, except those in `mute`, which are
    first mentioned without their outlet by every revision."""
    rolls, hill = fx.sid(ds, "Federal judge"), fx.sid(ds, "Jackson")
    rail, music = fx.sid(ds, "Owners of JD"), fx.sid(ds, "Riverton Music")
    texts = {
        rolls: ("The Guardian reports that a federal judge ruled on Friday that the US justice "
                "department's policy of collecting states' unredacted voter rolls is unlawful.",
                "A federal judge ruled on Friday that the US justice department's policy of "
                "collecting states' unredacted voter rolls is unlawful."),
        hill: ("The Hill reports that on Friday, in Maine, Jackson holds a slight lead over "
               "Collins in the Senate race survey, leading 50 percent to 46 percent.",
               "On Friday, in Maine, Jackson holds a slight lead over Collins in the Senate "
               "race survey, leading 50 percent to 46 percent."),
        rail: ("KVUE reported on Friday that owners of JD's Supermarket in southeast Riverton say "
               "a light rail project could force them to close.",
               "On Friday owners of JD's Supermarket in southeast Riverton said a light rail "
               "project could force them to close."),
        music: ("Cbsriverton reports that on Friday, in Riverton, Riverton Music Weekend Two could "
                "go from Mud Fest to Dust Fest, forecast shows.",
                "On Friday, in Riverton, Riverton Music Weekend Two could go from Mud Fest to "
                "Dust Fest, forecast shows."),
    }
    out = []
    for k, (sid_, (good, muted)) in enumerate(texts.items()):
        out.append({"speaker": "a", "chapter": 1, "cites": [sid_],
                    "text": muted if sid_ in mute else good})
    return out, rolls, hill, rail, music


def test_one_story_that_cannot_be_fixed_is_left_out_and_the_rest_airs(monkeypatch, speaker):
    ds = fx.docs()
    lines, rolls, hill, rail, music = _plain_lines(ds, mute={fx.sid(ds, "Jackson")})
    done = _episode(monkeypatch, ds, lines)
    assert done["status"] == "ready", done.get("error")
    assert done["script_check"]["revisions"] == pe.MAX_REVISIONS      # the writer was given its turns
    said = _body(done)
    assert "Jackson" not in said and "The Guardian reports" in said and "Cbsriverton" in said
    assert [s["sid"] for s in done["left_out"]] == [hill]
    assert done["left_out"][0]["headline"].startswith("Jackson holds slight lead")
    assert "outlet" in done["left_out"][0]["reason"]
    assert "Left out: Jackson holds slight lead" in pe.transcript_bytes(done).decode("utf-8-sig")
    # The check itself is as strict as ever: the same lines, unedited, still fail it.
    probs = q.script_problems(pe.with_signature(lines, {"id": "x", "hosts": pe.DEFAULTS["hosts"],
                                                        "format": "solo"}, 3, ds),
                              ds, n_chapters=3, news=True, personal=True, solo=True, home=fx.HOME)
    assert [p["sid"] for p in probs if p["code"] == "no_lede"] == [hill]


def test_an_episode_with_nothing_airable_left_is_still_rejected(monkeypatch, speaker):
    ds = fx.docs()
    every = {fx.sid(ds, s) for s in ("Federal judge", "Jackson", "Owners of JD", "Riverton Music")}
    lines, *_ = _plain_lines(ds, mute=every)
    done = _episode(monkeypatch, ds, lines)
    assert done["status"] == "failed" and done["error"]["code"] == "script_rejected"
    assert not done.get("lines") and not speaker and not done.get("left_out")
    assert done["draft_lines"]                                   # kept for review, as before


# -- the words the owner reads ----------------------------------------------------

def _no_lede(title, lacks):
    return {"code": "no_lede", "line": 1, "sid": "S2", "title": title, "lacks": lacks,
            "message": "\"%s\" is first mentioned without a spoken lede; it lacks %s."
                       % (title[:90], ", ".join(lacks))}


def test_the_refusal_names_the_story_and_what_was_missing_without_cutting_a_word():
    title = "Northwind's AI gave Springfield police a fake tip about an unsolved homicide"
    msg = q.failure_message([_no_lede(title, ["the outlet, named aloud (The Verge)"])])
    assert "The Verge" in msg and "lacks the outlet (The Verge) named aloud" in msg
    assert "unsolved…" not in msg and not re.search(r"\w…", msg)
    assert "homici\"" not in msg


def test_a_long_list_is_cut_between_problems_never_inside_one():
    probs = [_no_lede("Story number %d is about something fairly long to say aloud" % k,
                      ["the outlet, named aloud (Outlet %d)" % k, "what happened"]) for k in range(6)]
    msg = q.failure_message(probs)
    assert len(msg) <= 260 and msg.endswith("more.")
    assert msg.count("Story number") < 6 and "(Outlet 0)" in msg
    # Every item that is present is whole.
    for item in re.findall(r"\"Story number \d[^\"]*\" \([^)]*\)", msg):
        assert item.endswith(")")
    assert not re.search(r"lacks [^)]*$", msg.split("; and")[0])


@pytest.mark.parametrize("text,limit", [
    ("An unsolved homicide in a city far away from here is the story", 30),
    ("One short sentence. And a second one that runs on and on without end here", 40),
    ("short", 40),
])
def test_cut_at_boundary_never_ends_inside_a_word(text, limit):
    cut = q.cut_at_boundary(text, limit)
    assert len(cut) <= limit + 2
    body = cut.rstrip("… ").rstrip()
    assert text.startswith(body) and (len(body) == len(text) or text[len(body)] in " .")


def test_a_refused_episode_says_so_in_full_in_the_episode_and_keeps_every_problem(monkeypatch, speaker):
    ds = fx.docs()
    every = {fx.sid(ds, s) for s in ("Federal judge", "Jackson", "Owners of JD", "Riverton Music")}
    lines, *_ = _plain_lines(ds, mute=every)
    done = _episode(monkeypatch, ds, lines)
    msg = done["error"]["message"]
    # The message names whole problems while they fit and counts the rest;
    # every problem stays in the episode's script check.
    assert "lacks the outlet" in msg and msg.endswith("more.")
    assert not re.search(r"\w…", msg)
    assert len([p for p in done["script_check"]["problems"] if p["code"] == "no_lede"]) == 4


# -- the screen -------------------------------------------------------------------

@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_the_failure_line_on_screen_is_not_cut_inside_a_word(page):
    html = (ROOT / page).read_text(encoding="utf-8")
    assert "String(ep.error.message).slice(0, 140)" not in html
    assert "podcastReason(ep.error.message)" in html
    assert "function podcastReason(" in html
