"""Podcasts that run about five minutes and go deeper, not wider.

* A length is spoken time (short ~2, standard ~5, long ~10 minutes), its word
  target derived from the rate the renderer achieves, with fewer stories each
  told longer; standard is the default for every routine and new episode.
* The owner sets it in Settings (one row, both UI files, validated) or by
  asking (`podcast_length`, held for their yes like every setting a tool
  writes).
* Each story the outline picks is read in full through the page reader; the
  writer gets the article's passages and the depth brief, and the script check
  takes support from what it read: a fact only in the article stands, an
  invented one is cut.
* The transcript lists the sources segment by segment.
* Writing an episode is a time-bound job in the Local AI queue.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_friday.services import background_gate as bg
from agent_friday.services import page_reader
from agent_friday.services import podcast_engine as pe
from agent_friday.services import podcast_quality as q
from agent_friday.services import podcast_render as render

ROOT = Path(__file__).resolve().parents[2]

#: The settings the code under test saved, per test.
SAVED: dict = {}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    SAVED.clear()

    def save(delta, **_k):
        cur = SAVED.setdefault("s", {})
        for k, v in delta.items():
            cur[k] = v                     # the store replaces a top-level block
    monkeypatch.setattr(core, "_load_settings", lambda: SAVED.get("s", {}))
    monkeypatch.setattr(core, "_save_settings", save)
    pe._ARTICLES.clear()
    return tmp_path


# ── lengths in spoken time ──────────────────────────────────────────────────

def test_lengths_are_spoken_minutes_with_standard_recommended():
    assert pe.LENGTHS == ("short", "standard", "long")
    assert pe.LENGTH_MINUTES == {"short": 2, "standard": 5, "long": 10}
    assert pe.RECOMMENDED_LENGTH == "standard"


def test_the_word_targets_come_from_the_rate_the_renderer_achieves():
    # The measured episode: 149 words in 70 s; the stings are fixed audio.
    wpm = render.spoken_words_per_minute()
    assert 125 <= wpm <= 145, wpm
    for k, minutes in pe.LENGTH_MINUTES.items():
        spoken = render.spoken_seconds(pe.LENGTH_WORDS[k] + pe.SIGNATURE_WORDS)
        assert abs(spoken - 60 * minutes) <= 0.05 * 60 * minutes, (k, spoken)
    assert pe.LENGTH_WORDS["short"] < pe.LENGTH_WORDS["standard"] < pe.LENGTH_WORDS["long"]
    assert 550 <= pe.LENGTH_WORDS["standard"] <= 700


def test_fewer_stories_each_told_longer():
    assert pe.LENGTH_STORIES["short"] == 2
    assert pe.LENGTH_STORIES["standard"] in (3, 4)
    assert pe.LENGTH_STORIES["long"] <= 6
    # A cold open (the top story), a chapter per further story, a one-sentence close.
    assert pe.LENGTH_CHAPTERS == {k: n + 1 for k, n in pe.LENGTH_STORIES.items()}
    shares = pe.chapter_words(pe.LENGTH_WORDS["standard"], pe.LENGTH_CHAPTERS["standard"])
    assert shares[-1] == pe.CLOSE_WORDS and min(shares[:-1]) >= 120
    assert sum(shares) <= pe.LENGTH_WORDS["standard"]
    # A solo newscast of five minutes is no longer told to cover ten stories.
    assert q.SOLO_NEWS_PER_MINUTE * 5 <= pe.LENGTH_STORIES["standard"]


def test_the_time_budgets_scale_with_the_script():
    for k in pe.LENGTHS:
        b = pe.WRITER_BUDGET[k]
        # A whole-script revision has room for the script twice over, as JSON lines.
        assert b["revise"] >= 4 * pe.LENGTH_WORDS[k]
        assert b["chapter"] >= 3 * max(pe.chapter_words(pe.LENGTH_WORDS[k], pe.LENGTH_CHAPTERS[k]))
        assert b["revisions"] >= 2
        assert pe.writer_timeout_s(b["revise"]) >= b["revise"] / pe.WRITER_SLOW_TOKENS_PER_S
        # Speaking (every line, the fixed opening and sign-off included) may
        # take three times the episode's length.
        assert pe.speak_budget_s(30, pe.LENGTH_WORDS[k] + pe.SIGNATURE_WORDS) >= 3 * 60 * pe.LENGTH_MINUTES[k]
    assert pe.WRITER_BUDGET["long"]["revisions"] > pe.WRITER_BUDGET["standard"]["revisions"]
    assert pe.writer_timeout_s(20000) > pe.writer_timeout_s(4000) >= 900


def test_the_writer_is_given_the_minutes_and_the_story_count(monkeypatch):
    llm = _writer()
    monkeypatch.setattr(pe, "_llm_json", llm)
    monkeypatch.setattr(pe, "article_text", lambda url: "")
    ep = _news_ep()
    pe.write_script(ep, _docs())
    plan = llm.calls[0]["user"]
    assert "about %d words (5 minutes spoken)" % pe.LENGTH_WORDS["standard"] in plan
    assert "Choose the 4 most important stories" in plan
    assert "Cover as many stories as fit" not in llm.calls[0]["system"]


# ── the default and the setting ─────────────────────────────────────────────

def test_standard_is_the_default_for_every_routine_and_new_episode():
    from agent_friday.core import DEFAULT_SETTINGS
    for table in (pe.DEFAULTS["length"], DEFAULT_SETTINGS["podcasts"]["length"]):
        assert set(table) == set(pe.LENGTH_SHOWS)
        assert set(table.values()) == {"standard"}
    for routine in pe.ROUTINES:
        assert pe.length_for(routine) == "standard"
    ep = pe.create([{"kind": "text", "text": "x", "title": "t"}])
    assert ep["length"] == "standard" and ep["depth"] is True
    ep = pe.create([{"kind": "text", "text": "x", "title": "t"}], origin="routine",
                   attached={"routine": "front_page", "run_id": "2026-10-10-morning"})
    assert ep["length"] == "standard"


def test_the_owners_setting_decides_when_no_length_is_asked_for():
    SAVED["s"] = {"podcasts": {"length": {"front_page": "long", "any": "short"}}}
    assert pe.create([{"kind": "text", "text": "x", "title": "t"}], origin="routine",
                     attached={"routine": "front_page", "run_id": "r1-x"})["length"] == "long"
    assert pe.create([{"kind": "text", "text": "x", "title": "t"}])["length"] == "short"
    assert pe.create([{"kind": "text", "text": "x", "title": "t"}], length="standard")["length"] == "standard"


def test_setting_one_length_keeps_every_other_podcast_setting():
    SAVED["s"] = {"podcasts": {"format": {"weekly": "solo"}, "cloud_voice": True,
                               "length": {"weekly": "short"}}}
    pe.set_length("front_page", "long")
    pod = SAVED["s"]["podcasts"]
    assert pod["length"] == {"weekly": "short", "front_page": "long"}
    assert pod["format"] == {"weekly": "solo"} and pod["cloud_voice"] is True
    pe.set_format("briefing", "duo")
    assert SAVED["s"]["podcasts"]["length"]["front_page"] == "long"
    pe.set_length("all", "short")
    assert {pe.length_for("" if k == "any" else k) for k in pe.LENGTH_SHOWS} == {"short"}


def test_minutes_map_to_the_nearest_length():
    assert pe.length_from_minutes(10) == "long"
    assert pe.length_from_minutes("5") == "standard"
    assert pe.length_from_minutes(2) == "short"
    assert pe.length_from_minutes(30) == "long"


def test_the_settings_row_is_in_both_ui_files_and_its_value_is_validated():
    for rel in ("index.html", "ui_parts/app.html"):
        html = (ROOT / rel).read_text(encoding="utf-8")
        assert 'settingKey: "settings.podcasts.length"' in html, rel
        assert "PodcastLengthRow" in html and "Standard, about 5 minutes (recommended)" in html, rel
        assert "Short (~5 min)" not in html, rel
    from agent_friday.routes import core_routes as cr
    assert cr._check_podcast_length({"podcasts": {"length": {"front_page": "long", "any": "short"}}}) is None
    assert cr._check_podcast_length({"podcasts": {"format": {"weekly": "duo"}}}) is None
    assert cr._check_podcast_length({"podcasts": {"length": {"front_page": "huge"}}})["status"] == "error"
    assert cr._check_podcast_length({"podcasts": {"length": {"the_moon": "long"}}})["status"] == "error"
    assert cr._check_podcast_length({"podcasts": {"length": "long"}})["status"] == "error"


# ── the tool ────────────────────────────────────────────────────────────────

@pytest.fixture
def cards(home, monkeypatch):
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import approvals as ap, dissent_gate as dg
    from agent_friday.services import setting_proposals as sp
    monkeypatch.setattr(ap, "APPROVALS_FILE", home / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", home / "dissent_events.jsonl")
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    monkeypatch.setattr(sp, "_store", lambda: home / "setting_changes.json")

    def approve():
        card = next(r for r in ap.list_approvals(kind="governed_action")
                    if (r.get("payload") or {}).get("handler") == sp.HANDLER and r.get("status") == "pending")
        ap.decide_with_outcome(card["approval_id"], "approve", decided_by="owner")
    return approve


def test_make_the_front_page_podcast_ten_minutes(cards):
    from agent_friday.services import podcast_tools as pt
    out = json.loads(pt._tool_podcast_length({"routine": "front_page", "minutes": 10}))
    # A setting a tool writes waits for the owner's own yes.
    assert out["status"] == "needs_yes" and pe.length_for("front_page") == "standard"
    assert "long (about 10 minutes)" in out["message"]
    cards()
    assert pe.length_for("front_page") == "long"
    assert pe.length_for("briefing") == "standard"
    read = json.loads(pt._tool_podcast_length({}))
    assert read["lengths"]["front_page"] == {"length": "long", "minutes": 10, "recommended": "standard"}


def test_make_tomorrows_longer_is_one_step_up(cards):
    from agent_friday.services import podcast_tools as pt
    out = json.loads(pt._tool_podcast_length({"routine": "briefing", "change": "longer"}))
    assert out["status"] == "needs_yes" and "long (about 10 minutes)" in out["message"]
    cards()
    assert pe.length_for("briefing") == "long"
    bad = json.loads(pt._tool_podcast_length({"routine": "briefing", "length": "epic"}))
    assert bad["status"] == "error"


def test_the_length_tool_is_governed_and_reached_through_the_text_tools():
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import agent, setting_proposals as sp, voice_engine
    from agent_friday.services import podcast_tools as pt
    assert pt.RINGS["podcast_length"] == 1
    assert "podcast_length" in ag.INTERNAL_TOOLS
    assert "podcast_length" in {t["name"] for t in agent.CLAUDE_TOOLS if isinstance(t, dict)}
    assert "podcast_length" in agent.CLAUDE_TOOL_HANDLERS
    # Not on the voice contract (no room); voice reaches it through ask_friday.
    assert "podcast_length" not in voice_engine._VOICE_SHARED_TOOLS
    assert sp.TOOL_OF["settings.podcasts.length"] == "podcast_length"
    assert sp._route("settings.podcasts.length.front_page", "10 minutes") == \
        ("podcast_length", {"routine": "front_page", "minutes": 10.0})
    assert sp._route("settings.podcasts.length.weekly", "Short") == \
        ("podcast_length", {"routine": "weekly", "length": "short"})


def test_a_new_episode_asked_for_by_voice_or_chat_says_its_real_length():
    from agent_friday.services import podcast_tools as pt
    out = json.loads(pt._tool_make_podcast({"sources": [{"kind": "text", "text": "x", "title": "t"}]}))
    assert "5-minute" in out["message"]


# ── depth: the full article ─────────────────────────────────────────────────

ARTICLE = ("Harbor council approves new seawall plan\n\n"
           "The Harbor city council voted 7-2 on Tuesday to build a seawall along the "
           "harbor front.\n\n"
           "The seawall will cost $48 million, paid over six years, the city's budget "
           "office said.\n\n"
           "Engineers said the old wall failed twice in 2024, flooding Front Street.\n\n"
           "Construction is set to begin in March, and a public hearing on the route "
           "is scheduled for November 12.\n\n"
           "Shop owners along Front Street have asked the council for flood protection "
           "since the storms.")


def _docs():
    return [
        {"sid": "S1", "title": "Harbor council approves new seawall plan", "kind": "news",
         "outlet": "examplewire.com", "url": "https://examplewire.com/seawall", "private": False,
         "text": ("Harbor council approves new seawall plan\nOutlet: examplewire.com\n"
                  "Published: Tuesday, October 6.\nThe council voted on Tuesday to approve "
                  "a seawall along the harbor front.")},
        {"sid": "S2", "title": "Riverton library reopens after flood repairs", "kind": "news",
         "outlet": "examplepost.com", "url": "https://examplepost.com/library", "private": False,
         "text": ("Riverton library reopens after flood repairs\nOutlet: examplepost.com\n"
                  "Published: Monday, October 5.\nThe Riverton library reopened on Monday "
                  "after months of flood repairs.")},
    ]


def _news_ep(**kw):
    ep = {"id": "20261010T070000-abc123", "length": "standard", "show": "The Front Page",
          "hosts": pe.DEFAULTS["hosts"], "format": "solo", "depth": True,
          "attached": {"routine": "front_page", "run_id": "2026-10-10-morning"}}
    ep.update(kw)
    return ep


def _writer(lines_for=None):
    """A stand-in for the local model, recording every prompt."""
    calls = []

    def llm(system, user, *, max_tokens=3000):
        calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        if "Plan an episode" in user:
            return {"title": "Seawall", "chapters": [
                {"title": "Seawall", "sources": ["S1"]},
                {"title": "Library", "sources": ["S2"]},
                {"title": "Close", "sources": ["S1"]}]}, "local-test"
        if "PROBLEMS FOUND" in user:
            body = user.split("sign-off are added around it):\n", 1)[1].split("\n\nPROBLEMS FOUND", 1)[0]
            return {"lines": json.loads(body)}, "local-test"
        n = sum(1 for c in calls if "Chapter " in c["user"])
        return {"lines": (lines_for(n) if lines_for else [])}, "local-test"
    llm.calls = calls
    return llm


class _Page:
    def __init__(self, text):
        self.text = text
        self.title = "Harbor council approves new seawall plan"
        self.meta = {}
        self.url = ""


def test_a_story_is_told_from_its_full_article(monkeypatch):
    fetched = []

    def fetch(url, timeout=15):
        fetched.append(url)
        if "seawall" in url:
            return _Page(ARTICLE)
        raise OSError("the page could not be read")
    monkeypatch.setattr(page_reader, "fetch", fetch)
    llm = _writer()
    monkeypatch.setattr(pe, "_llm_json", llm)
    docs = _docs()
    script = pe.write_script(_news_ep(), docs)
    assert fetched == ["https://examplewire.com/seawall", "https://examplepost.com/library"]
    seawall = next(c["user"] for c in llm.calls if 'Chapter 1 of' in c["user"])
    # The article's own facts reach the writer, with the depth brief.
    assert "FULL ARTICLE" in seawall and "$48 million" in seawall and "failed twice in 2024" in seawall
    assert "what happened before" in seawall and "say less rather than" in seawall
    library = next(c["user"] for c in llm.calls if 'Chapter 2 of' in c["user"])
    assert "FULL ARTICLE, passages read" not in library and "Only this summary could be read" in library
    assert {r["sid"]: r["read"] for r in script["depth"]} == {"S1": "article", "S2": "summary"}
    # The close is one sentence: it is asked for one, not a story's share.
    close = next(c["user"] for c in llm.calls if 'Chapter 3 of' in c["user"])
    assert "Write about %d words" % pe.CLOSE_WORDS in close and "FULL ARTICLE" not in close


def test_a_short_story_chapter_gets_one_more_pass_from_the_same_sources(monkeypatch):
    monkeypatch.setattr(pe, "article_text", lambda url: ARTICLE if "seawall" in url else "")
    extended = []

    def lines_for(n):
        return [{"speaker": "a", "cites": ["S1"],
                 "text": "Example Wire reports that on Tuesday the Harbor council voted to build a seawall."}] \
            if n == 1 else []
    llm = _writer(lines_for)

    def with_more(system, user, *, max_tokens=3000):
        if "It is short by about" in user:
            extended.append(user)
            llm.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
            return {"lines": [{"speaker": "a", "cites": ["S1"],
                               "text": "The seawall will cost $48 million, paid over six years."}]}, "local-test"
        return llm(system, user, max_tokens=max_tokens)
    monkeypatch.setattr(pe, "_llm_json", with_more)
    script = pe.write_script(_news_ep(), _docs())
    assert len(extended) == 1 and "FULL ARTICLE" in extended[0]
    assert any("$48 million" in ln["text"] for ln in script["lines"])


def test_a_fact_only_in_the_article_stands_and_an_invented_one_is_cut():
    docs = _docs()
    story_list = q.stories(docs)
    fact = "The seawall will cost $48 million, paid over six years."
    invented = "The seawall will cost $90 million, paid over six years."
    # From the summary alone, the article's figure is no one's.
    assert q.support(fact, ["S1"], story_list, docs)[0] == "cut"
    docs[0]["read"] = ARTICLE
    story_list = q.stories(docs)
    assert q.support(fact, ["S1"], story_list, docs) == ("ok", [])
    verdict, why = q.support(invented, ["S1"], story_list, docs)
    assert verdict == "cut" and "$90" in why
    # The same figure written another way is the same figure.
    assert q.support("The seawall will cost 48 million dollars.", ["S1"], story_list, docs)[0] == "ok"
    # The article widens support; it is not the story's lede or identity.
    assert story_list[0]["text"] == docs[0]["text"]


def test_the_edit_pass_keeps_the_article_fact_and_cuts_the_invented_one():
    docs = _docs()
    docs[0]["read"] = ARTICLE
    lede = ("Example Wire reports that on Tuesday, in Harbor, the city council voted to build "
            "a seawall along the harbor front.")
    lines = [{"speaker": "a", "chapter": 0, "cites": ["S1"], "text": lede},
             {"speaker": "a", "chapter": 0, "cites": ["S1"],
              "text": "Engineers said the old wall failed twice in 2024, flooding Front Street."},
             {"speaker": "a", "chapter": 0, "cites": ["S1"],
              "text": "The seawall will cost $90 million, paid over six years."}]
    kept, cut = pe.edit_script(lines, docs, 1)
    said = " ".join(ln["text"] for ln in kept)
    assert "failed twice in 2024" in said
    assert "$90 million" not in said
    assert any("$90 million" in c["text"] and "does not report" in c["reason"] for c in cut)


# ── the notes: sources per segment ──────────────────────────────────────────

def test_the_transcript_lists_the_sources_of_each_segment():
    sources = [{"id": "S1", "title": "Harbor council approves new seawall plan", "kind": "news",
                "url": "https://examplewire.com/seawall", "outlet": "Example Wire", "read": "full article"},
               {"id": "S2", "title": "Riverton library reopens after flood repairs", "kind": "news",
                "url": "https://examplepost.com/library", "outlet": "Example Post", "read": "summary only"},
               {"id": "S3", "title": "Today's front page", "kind": "news", "url": "", "role": "overview"}]
    chapters = [{"title": "The seawall"}, {"title": "The library"}]
    lines = [{"speaker": "a", "chapter": 0, "text": "This is The Front Page.", "cites": [], "signature": True},
             {"speaker": "a", "chapter": 0, "text": "Example Wire reports the seawall vote.", "cites": ["S1"]},
             {"speaker": "a", "chapter": 0, "text": "My read.", "cites": ["S3"], "own": True},
             {"speaker": "a", "chapter": 1, "text": "Example Post reports the library reopened.", "cites": ["S2"]}]
    segs = pe.segment_sources(lines, chapters, sources)
    assert [[s["id"] for s in g["sources"]] for g in segs] == [["S1"], ["S2"]]
    assert segs[0]["sources"][0] == {"id": "S1", "outlet": "Example Wire",
                                     "title": "Harbor council approves new seawall plan",
                                     "url": "https://examplewire.com/seawall", "read": "full article"}
    ep = {"title": "Seawall", "show": "The Front Page", "hosts": pe.DEFAULTS["hosts"], "format": "solo",
          "chapters": chapters, "lines": lines, "sources": sources}
    text = pe.transcript_bytes(ep).decode("utf-8-sig")
    first, second = text.split("== The library ==")
    assert "Sources for this segment:" in first and "Sources for this segment:" in second
    assert ("Example Wire: Harbor council approves new seawall plan — "
            "https://examplewire.com/seawall (full article)") in first
    assert "Riverton" not in first
    assert ("Example Post: Riverton library reopens after flood repairs — "
            "https://examplepost.com/library (summary only)") in second
    assert "Today's front page" not in text.split("TRANSCRIPT", 1)[1].split("SOURCES")[0]


# ── the Local AI queue ──────────────────────────────────────────────────────

class _Supervisor:
    def __init__(self):
        self.admitted, self.waits, self.ended = [], [], []

    def admit_inline(self, spec):
        self.admitted.append(dict(spec))
        return "job-1"

    def wait_while_blocked(self, job_id):
        self.waits.append(job_id)

    def on_task_end(self, job_id, status="completed"):
        self.ended.append((job_id, status))


def test_writing_an_episode_is_a_time_bound_job_in_the_local_ai_queue(monkeypatch):
    from agent_friday.services import local_call, scheduler
    sup = _Supervisor()
    monkeypatch.setattr(bg, "_SUPERVISOR_PROVIDER", lambda: sup)
    monkeypatch.setattr(scheduler, "_resolve_local_seat", lambda: "local-test")
    monkeypatch.setattr(local_call, "seat_endpoint", lambda model: "http://127.0.0.1:9")
    sent = []

    def transport(base, system, user, model, json_mode, max_tokens, timeout):
        sent.append(user)
        if "Plan an episode" in user:
            return json.dumps({"title": "Notes", "chapters": [{"title": "One", "sources": ["S1"]}]})
        if "Summarise" in user:
            return json.dumps({"summary": "Notes about the garden."})
        return json.dumps({"lines": [{"speaker": "a", "cites": ["S1"],
                                      "text": "The garden plan adds twelve new beds along the east fence."}]})
    monkeypatch.setattr(local_call, "_openai_style", transport)
    ep = pe.create([{"kind": "text", "title": "Garden plan",
                     "text": "The garden plan adds twelve new beds along the east fence."}],
                   title="My private garden notes")
    done = pe.produce(ep["id"], script_only=True)
    assert done["status"] == "scripted", done.get("error")
    assert sent, "the writer reached the local model"
    assert len(sup.admitted) == 1
    job = sup.admitted[0]
    assert job["kind"] == "podcast" and job["deferrable"] is False and job["seat_is_local"] is True
    assert job["job_key"] == "podcast:" + ep["id"]
    # A label, never the episode's title: a private episode's title is its content.
    assert "garden" not in job["label"].lower()
    # Every model call waited its turn at the choke point, and the job ended.
    assert sup.waits and set(sup.waits) == {"job-1"} and len(sup.waits) == len(sent)
    assert sup.ended == [("job-1", "completed")]


def test_a_podcast_job_yields_to_a_turn_but_never_waits_for_idle(monkeypatch):
    job = {"seat_is_local": True, "deferrable": pe.queue_job({"id": "x"})["deferrable"]}
    monkeypatch.setattr(bg, "idle_state", lambda: {"idle": False})
    monkeypatch.setattr(bg, "interactive_kinds", lambda: [])
    assert bg.block_reason(job) == ""                       # the owner at the keyboard
    monkeypatch.setattr(bg, "interactive_kinds", lambda: ["chat"])
    assert bg.block_reason(job) == bg.REASON_INTERACTIVE    # a chat turn goes first
    monkeypatch.setattr(bg, "interactive_kinds", lambda: ["voice"])
    assert bg.block_reason(job) == bg.REASON_INTERACTIVE    # so does a voice turn


def test_a_routine_episode_never_waits_for_idle_at_any_length(monkeypatch):
    from agent_friday.services import residency_arbiter, scheduler, stand_down
    monkeypatch.setattr(scheduler, "idle_work_blocked_reason",
                        lambda *a, **k: pytest.fail("a podcast never waits for idle"))
    monkeypatch.setattr(stand_down, "is_stood_down", lambda: False)
    monkeypatch.setattr(residency_arbiter, "exclusive_lease", lambda: None)
    for length in pe.LENGTHS:
        assert pe._gate_reason({"priority": "routine", "length": length}) == ""
    monkeypatch.setattr(stand_down, "is_stood_down", lambda: True)
    assert "stood down" in pe._gate_reason({"priority": "routine", "length": "standard"})


def test_the_queue_names_a_podcast_job():
    from agent_friday.services import seat_supervisor
    assert seat_supervisor.KIND_LABELS["podcast"] == "a podcast episode"
    for rel in ("index.html", "ui_parts/app.html"):
        assert "podcast: 'podcast'" in (ROOT / rel).read_text(encoding="utf-8"), rel
