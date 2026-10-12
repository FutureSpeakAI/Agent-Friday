"""Text scanners that see user- or web-supplied strings run in linear time,
and still find what they found before.

Each timing case feeds the real code path a ~50,000-character input built to
make a backtracking pattern retry the same characters from every position,
and requires it to finish well under half a second. The same scanners are
then checked against the inputs they exist to recognise.
"""
from __future__ import annotations

import sys
import time

import pytest

N = 50_000
BUDGET = 0.5


def _fast(fn, *args, **kwargs):
    t = time.perf_counter()
    out = fn(*args, **kwargs)
    elapsed = time.perf_counter() - t
    assert elapsed < BUDGET, f"{getattr(fn, '__name__', fn)} took {elapsed:.2f}s on a {N}-char input"
    return out


# ── services/task_ledger.py ────────────────────────────────────────────────

def test_ledger_summary_sections():
    from agent_friday.services import task_ledger as tl
    ledger = tl.new("g")
    _fast(tl.absorb_summary, ledger, "GOAL:" + " " * N + "x")
    ledger = tl.new("g")
    tl.absorb_summary(ledger, "PLAN:  read the file\nfacts : a is 1\n- b is 2\nNEXT:write it")
    assert ledger["facts"] == ["a is 1", "b is 2"]


# ── services/workflow_overview.py ──────────────────────────────────────────

def test_workflow_interval_and_punctuation():
    from agent_friday.services import workflow_overview as wo
    _fast(wo.parse_when, "every" + " " * N + "!")
    _fast(wo._strip, "a" + " " * N + "b", [])
    when, rest, _ = wo.parse_when("every 5 minutes check the inbox")
    assert when == {"trigger": "interval", "spec": {"every_minutes": 5}}
    assert rest == "Check the inbox"
    assert wo.parse_when("each 2 hours ping me")[0]["spec"] == {"every_minutes": 120}
    assert wo.parse_when("every hour ping me")[0]["spec"] == {"every_minutes": 60}
    assert wo.parse_when("every fiveminutes ping me")[0]["spec"] == {"every_minutes": 5}
    assert wo._strip("send it , now  !", []) == "Send it, now!"


# ── services/style_guard.py ────────────────────────────────────────────────

def test_style_guard_keeps_bullets():
    from agent_friday.services import style_guard
    _fast(style_guard.sanitize, " " * N + "x")
    assert style_guard.sanitize("  - keep this line\n3. and this")[0] == \
        "  - keep this line\n3. and this"


# ── services/setup_research.py ─────────────────────────────────────────────

def test_setup_research_domain():
    from agent_friday.services import setup_research as sr
    _fast(sr._domain, "a" + ".a" * (N // 2) + "-")
    assert sr._domain("example.com") == "example.com"
    assert sr._domain("https://Sub.Example.co.uk/path") == "sub.example.co.uk"
    assert sr._domain("my-site.io") == "my-site.io"
    for bad in ("localhost", "10.0.0.1", "example.c", ".com", "example.com1", ""):
        assert sr._domain(bad) == "", bad


# ── services/relationship_memory.py and services/gmail_send.py ─────────────

def test_address_validators():
    from agent_friday.services import gmail_send, relationship_memory as rm
    evil = "!@!." + "!." * (N // 2) + "@"
    _fast(rm._EMAIL.match, evil)
    with pytest.raises(gmail_send.SendRefused):
        _fast(gmail_send._addresses, evil)
    for good in ("a@b.co", "first.last@mail.example.com", "x@.b.c", "x@b..c"):
        assert rm._EMAIL.match(good), good
        assert gmail_send._addresses(good) == [good]
    for bad in ("a@b", "a@b.", "a@.b", "a b@c.d", "a@b@c.d", "@b.co"):
        assert not rm._EMAIL.match(bad), bad
        with pytest.raises(gmail_send.SendRefused):
            gmail_send._addresses(bad)
    assert not rm._EMAIL.match("<a@b.co>")
    assert gmail_send._addresses("a@b.co; c@d.org") == ["a@b.co", "c@d.org"]


# ── services/message_triage.py ─────────────────────────────────────────────

def test_triage_sender_address():
    from agent_friday.services import message_triage as mt
    _fast(mt._email_of, {"sender_email": "'" * N})
    assert mt._email_of({"sender": "Jo <jo.o'neil+x@mail.example.com>"}) == \
        "jo.o'neil+x@mail.example.com"
    assert mt._email_of({"sender_email": "no address here"}) == "no address here"


# ── governance/behavioral_monitor.py ───────────────────────────────────────

def test_remit_paths(tmp_path):
    from agent_friday.governance.behavioral_monitor import BehavioralMonitor
    mon = BehavioralMonitor(base_dir=tmp_path / "bmon")
    _fast(mon.extract_remit, "-" * N)
    remit = mon.extract_remit("edit src/app/main.py, ./notes.txt and C:\\x\\report.final.docx")
    assert set(remit.referenced_paths) == {"main.py", "notes.txt", "report.final.docx"}


# ── services/extension_security.py ─────────────────────────────────────────

def test_placeholder_env_values():
    from agent_friday.services import extension_security as es
    _fast(es._is_inline_secret, "API_TOKEN", "<" * N)
    assert es._is_inline_secret("API_TOKEN", "<your token here>") is False
    assert es._is_inline_secret("API_TOKEN", "abc<d>efgh") is False
    assert es._is_inline_secret("API_TOKEN", "a<<>bcdefgh") is False       # "<<>" holds "<" then ">"
    assert es._is_inline_secret("API_TOKEN", "a<>b<cdefgh") is True        # "<>" is empty, no ">" after
    assert es._is_inline_secret("API_TOKEN", "abcdefgh1234") is True
    assert es._is_inline_secret("PATH", "C:/tools/bin") is False


# ── services/content_composer.py ───────────────────────────────────────────

def test_title_from_heading():
    from agent_friday.services import content_composer as cc
    _fast(cc._extract_title, "", "#" + " " * N + "\n", "reddit", {})
    assert cc._extract_title("", "intro\n## Big News\nbody", "reddit", {}) == "Big News"
    assert cc._extract_title("", "#\n## Next line\nbody", "reddit", {}) == "## Next line"
    assert cc._extract_title("", "#   \n", "reddit", {}) == "#"


# ── services/agent.py: open / navigate intents ─────────────────────────────

def test_open_and_navigate_intents():
    from agent_friday.services import agent
    _fast(agent._maybe_handle_navigate_intent, "open a" + "\t" * N + "\nx")
    _fast(agent._maybe_handle_navigate_intent, "open " + " " * N + "x" + " " * N + "\ny")
    _fast(agent._resolve_workspace, "news" + " " * N + "tab")
    # One trailing phrase stripped per pass used to rescan the whole string
    # each time: quadratic in the number of phrases.
    _fast(agent._resolve_workspace, "news" + " please" * (N // 7))
    _fast(agent._resolve_workspace, "news" + " right now" * (N // 10) + " tab")
    _fast(agent._resolve_open_target, "zz-no-such-place" + " dir" * (N // 4) + " folder")
    rx = agent._OPEN_VERB_RE
    assert rx.match("switch to settings!!").group(2) == "settings"
    assert rx.match("Friday, please open the news tab?").group(2) == "the news tab"
    assert rx.match("open up").group(2) == "up"
    assert rx.match("show me what's new ?!").group(1, 2) == ("show me", "what's new")
    assert rx.match("open ???").group(2) == "?"
    assert rx.match("open\nfoo").group(2) == "foo"
    assert rx.match("open foo\nbar") is None
    assert agent._maybe_handle_navigate_intent("open workflows please") == \
        ("Opening the **Workflows** workspace for you.", "workflows")
    assert agent._maybe_handle_navigate_intent("switch to the news tab")[1] == "news"
    assert agent._resolve_workspace("front page") == "news"
    assert agent._resolve_workspace("my settings  for me  thanks") == "settings"


# ── services/sensitivity_classifier.py ─────────────────────────────────────

def test_account_and_issued_id_shapes():
    from agent_friday.services import sensitivity_classifier as sc
    _fast(sc._ISSUED_ID_RE.search, "account number" + " " * N + "x")
    _fast(sc._ACCT_TAIL_RE.search, "card ending" + " " * N + "x")
    for text, span in (("policy number: AB-12345 today", "policy number: AB-12345"),
                       ("Member ID 12345678", "Member ID 12345678"),
                       ("account #: 99887766", "account #: 99887766"),
                       ("claim no. X 55554444", "claim no. X 55554444"),
                       ("case#-12345", "case#-12345")):
        m = sc._ISSUED_ID_RE.search(text)
        assert m and m.group(0) == span, text
    assert not sc._ISSUED_ID_RE.search("account number ABCDE12345")
    assert not sc._ISSUED_ID_RE.search("invoice id 123")
    for text in ("Chase account ending 4417", "card ending in  0042", "acct x ENDING IN 123456"):
        assert sc._ACCT_TAIL_RE.search(text), text
    assert not sc._ACCT_TAIL_RE.search("card endingin 4417")
    assert not sc._ACCT_TAIL_RE.search("card ending in4417")


# ── services/retrieval_ledger.py ───────────────────────────────────────────

def test_section_header_names():
    from agent_friday.services import retrieval_ledger as rl
    _fast(rl._section_header, "==" + " " * N)
    assert rl.derive_section_name("== RECENT MEMORY ==\nstuff", 4) == "recent_memory"
    assert rl.derive_section_name("=== Calendar (today) ===", 1) == "calendar_today"
    # "== bad! ==" is not a name; the next "==" pair, " then ", is.
    assert rl.derive_section_name("== bad! == then == Good ==", 2) == "then"
    assert rl.derive_section_name("== bad! ==\n== Good ==", 2) == "good"
    assert rl.derive_section_name("==   == then == Good ==", 3) == "section_3"
    assert rl.derive_section_name("no header here", 5) == "section_5"


# ── services/knowledge_graph/structural_query.py ───────────────────────────

def test_path_questions():
    from agent_friday.services.knowledge_graph import structural_query as sq
    _fast(sq.classify_query, "how is " * (N // 7))
    _fast(sq.classify_query, "how is a linked to " + "a connected to a" * (N // 16) + "\nx")
    _fast(sq.classify_query, "what connects a to " + "a and a" * (N // 7) + "\nx")
    assert sq.classify_query("How is Alice connected to Bob?") == ("path", ["Alice", "Bob"])
    assert sq.classify_query("trace the path from X to Y to Z") == ("path", ["X", "Y to Z"])
    assert sq.classify_query("what connects rust and go??") == ("path", ["rust", "go?"])
    assert sq.classify_query("notes\nhow does A relate... how is A linked to B\n") == \
        ("path", ["A relate... how is A", "B"])
    assert sq.classify_query("how is A linked to B\nmore")[0] != "path"
    assert sq.classify_query("how is A linked to ?")[1] == ["A", "?"]
    assert sq.classify_query("how is A linked to ")[0] != "path"


# ── services/html_text.py and its callers ──────────────────────────────────

PAGE = ("<html><head><title>T</title><STYLE type='text/css'>p{x:1}</STYLE\n>"
        "<script src=a.js></script ><SCRIPT>alert('<p>no</p>')</SCRIPT foo>"
        "</head><body><!-- hidden --><p>Fish &amp; chips</p><p>a &lt; b</p>"
        "<div>x<br/>y</div>3 < 4</body></html>")


def test_html_to_text_drops_script_style_comments():
    from agent_friday.services.html_text import html_to_text
    assert html_to_text(PAGE) == "T Fish & chips a < b x y 3 < 4"
    assert html_to_text("a<script>never closed") == "a"
    assert html_to_text("a<scripts>b</scripts>c") == "a b c"
    assert html_to_text("a<style>x</styles></style>b") == "a b"
    assert html_to_text("<p>a&nbsp;b</p>") == "a b"
    assert html_to_text("") == ""


_HTML_EVIL = {
    "open-tags": "<a" * (N // 2), "comments": "<!--" * (N // 4), "end-tags": "</" * (N // 2),
    "decls": "<!" * (N // 2), "pis": "<?" * (N // 2), "script-lt": "<script>" + "<" * N,
    "script-ends": "<script>" + "</script" * (N // 8), "lt": "<" * N, "amp": "&" * N,
}


@pytest.mark.parametrize("name", sorted(_HTML_EVIL))
def test_html_to_text_is_linear(name):
    from agent_friday.services.html_text import html_to_text
    _fast(html_to_text, _HTML_EVIL[name])


def test_html_callers_share_the_scanner(monkeypatch):
    from agent_friday.services import agent, voice_engine, web_fetch
    assert voice_engine._strip_html(PAGE) == "T Fish & chips a < b x y 3 < 4"
    monkeypatch.setitem(sys.modules, "bs4", None)       # the no-BeautifulSoup fallback
    assert web_fetch._html_to_text(PAGE) == "T Fish & chips a < b x y 3 < 4"
    assert agent._html_to_text(PAGE) == "T Fish & chips a < b x y 3 < 4"


# ── services/laya_router.py: a yes or no to a card, and small talk ─────────

def test_card_answers_and_small_talk():
    from agent_friday.services import laya_router as lr
    _fast(lr._YES.match, "yes" + "\t" * N + "x")
    _fast(lr._NO.match, "no" + "\t" * N + "x")
    _fast(lr._SMALL_TALK.match, "hi" + "\t" * N + "x")
    # The owner's words reach them through the router, which keeps runs of dots and commas.
    _fast(lr.rules_route, "yes" + " ." * (N // 2) + " x", pending_card="c1")
    _fast(lr.rule_route, "hi" + " ," * (N // 2) + " x")
    for yes in ("yes", "Friday, yes please", "ok, go ahead!", "sounds good. do it.", "okay", "yes... please!!"):
        assert lr._YES.match(yes), yes
    for other in ("yes but change it", "yes, go ahead and send it to Sam", "yesterday", "yes please please"):
        assert not lr._YES.match(other), other
    for no in ("no", "nope, thanks.", "cancel that", "never mind thank you", "stop!"):
        assert lr._NO.match(no), no
    for other in ("no, send it to Sam", "nobody", "no thanks thanks"):
        assert not lr._NO.match(other), other
    for st in ("hi", "hello, how are you doing today?", "thanks!", "good morning friday", "hi? ?!",
               "hey there, nice weather"):
        assert lr._SMALL_TALK.match(st), st
    for other in ("hi, can you check my mail? now", "his notes", "hi? x", "great question?x"):
        assert not lr._SMALL_TALK.match(other), other


def test_clause_split_and_query_fillers_read_collapsed_whitespace():
    # _CLAUSE_SPLIT, _FILLER_TAIL and live_laya._CLAUSES start with \s*, which retries a run of
    # spaces from each of its characters; every caller reaches them through route, rules_route,
    # rule_route or live_laya.write_intent, which collapse whitespace to single spaces first.
    from agent_friday.services import laya_router as lr, live_laya as ll
    _fast(lr.rule_route, "archive it" + " " * N + "and" + "\t" * N + "x")
    _fast(lr.rules_route, "find news about" + " " * N + "x" + " " * N + "online")
    _fast(ll.write_intent, "a" + " " * N + "and" + " " * N + "x")
    assert lr._clauses("archive it, then star the rest") == ["archive it", "star the rest"]


# ── services/point_act.py: courtesy words at the end of a request ──────────

def test_point_act_trailing_courtesy_words():
    from agent_friday.services import point_act as pa
    _fast(pa.normalize, "open it" + " please now" * (N // 11))
    _fast(pa.normalize, "a" + " " * N + "x")
    assert pa.normalize("open the second one now please") == "open the second one"
    assert pa.normalize("archive them right now thank you") == "archive them"
    assert pa.normalize("open the snow report okay") == "open the snow report"
    assert pa._strip_tail("please") == "please"                  # a word only counts after a space


# ── services/calendar_quickadd.py: attendee addresses ──────────────────────

def test_calendar_quickadd_addresses():
    from agent_friday.services import calendar_quickadd as cq
    _fast(cq.parse, "+" * N)
    _fast(cq.parse, "a" + "." * N)
    out = cq.parse("lunch with sam@example.com and lee.k+cal@mail.example.org tomorrow at 1pm")
    assert out["fields"]["attendees"] == ["sam@example.com", "lee.k+cal@mail.example.org"]
    assert cq.parse("call (ana-b@x.co) at 3pm")["fields"]["attendees"] == ["ana-b@x.co"]


# ── services/live_session_plan.py: the voice instruction's headers ─────────

def test_live_instruction_headers():
    from agent_friday.services import live_session_plan as lsp
    _fast(lsp.split_blocks, "==x" + "\t" * N + "y")
    _fast(lsp.split_blocks, ("==x" + "\t" * 190 + "y\n") * (N // 194))
    _fast(lsp.safety_ok, "==ASIMOV" + "\t" * N + "y", "")
    text = "intro\n=== Memory ===\nm\n==  Tools \t==  \nt\n== not = closed\n====x====\n"
    assert [(b.name, b.text) for b in lsp.split_blocks(text)][:3] == [
        ("preamble", "intro\n"), ("memory", "=== Memory ===\nm\n"),
        ("tools", "==  Tools \t==  \nt\n== not = closed\n====x====\n")]
    assert lsp._header_marks("== a == b ==") == [(0, "a == b")]
    assert lsp._header_marks("== " + "a" * 300 + " ==") == []        # a long line is body text
    before = "x\n== ASIMOV Laws \t==\n=== asimov two ===\n==ASIMOV open\n"
    assert lsp._asimov_headers(before) == ["ASIMOV Laws", "asimov two"]
    assert lsp.safety_ok(before, "ASIMOV Laws and asimov two")
    assert not lsp.safety_ok(before, "ASIMOV Laws only")
    assert lsp._asimov_headers("== ASIMOV " + "a " * N + "==") == ["ASIMOV " + "a " * (N - 1) + "a"]


# ── index.html and ui_parts/app.html: read-aloud text ──────────────────────

def test_read_aloud_text_uses_the_dom_parser_in_both_ui_files():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    for rel in ("index.html", "ui_parts/app.html"):
        src = (root / rel).read_text(encoding="utf-8", errors="replace")
        assert "(?!<\\/script>)" not in src, rel
        compact = "".join(src.split())
        assert ("const_doc=newDOMParser().parseFromString(htmlContent,'text/html');"
                "_doc.querySelectorAll('script,style').forEach(n=>n.remove());") in compact, rel
