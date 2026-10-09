"""Beta CodeQL polynomial-redos alerts: the scanners stay linear on hostile
text and keep finding what they found before.

Each timing case feeds the real code path a long input built to make a pattern
retry the same characters from every start, and requires it to finish well
under half a second. The behaviour cases pin the results on ordinary input.
"""
from __future__ import annotations

import time

N = 50_000
BUDGET = 0.5


def _fast(fn, *args, **kwargs):
    t = time.perf_counter()
    out = fn(*args, **kwargs)
    elapsed = time.perf_counter() - t
    assert elapsed < BUDGET, "%s took %.2fs on a %d-char input" % (getattr(fn, "__name__", fn), elapsed, N)
    return out


# -- services/podcast_quality.py ----------------------------------------------

def test_meta_narration_is_found_sentence_by_sentence():
    from agent_friday.services.podcast_quality import META_RE
    text = "Police arrived. I did not check the time, so I am not inventing one. Then they left."
    m = META_RE.search(text)
    assert m and m.group(0).strip() == "I did not check the time, so I am not inventing one."
    assert META_RE.sub(" ", text).split() == ["Police", "arrived.", "Then", "they", "left."]
    assert META_RE.search("I checked it. We did not.") is None             # steps out of order
    assert META_RE.search("I did not check it") is None                    # no closing mark


def test_meta_narration_is_linear_on_hostile_text():
    from agent_friday.services.podcast_quality import META_RE
    assert _fast(META_RE.search, "a= " * (N // 3) + "!") is None
    assert _fast(META_RE.sub, " ", "I " * (N // 2)) == "I " * (N // 2)
    assert _fast(META_RE.search, "I did not " * (N // 10)) is None


def test_figures_in_a_story_ignore_an_endless_run_of_digits():
    from agent_friday.services import podcast_quality as pq
    _fast(pq._entities, "Title", "1" * N + "x")
    _fast(pq._entities, "Title", "$" + "1," * (N // 2) + "x")


# -- governance/action_gate.py ------------------------------------------------

def test_email_in_args_is_linear_and_still_finds_an_address():
    from agent_friday.governance.action_gate import _EMAIL_IN_ARGS
    assert _EMAIL_IN_ARGS.search("send to a.b+c@example.org now")
    assert not _EMAIL_IN_ARGS.search("no address here @ all")
    assert _fast(_EMAIL_IN_ARGS.search, "a" * N) is None
    assert _fast(_EMAIL_IN_ARGS.search, "a." * (N // 2)) is None


# -- brand.py -----------------------------------------------------------------

def test_spoken_mark_is_linear_and_still_drops_the_mark():
    from agent_friday import brand
    assert brand.spoken("Agent Friday™ and FutureSpeak.AI (TM)") == "Agent Friday and FutureSpeak.AI"
    assert brand.spoken("Agent Friday ™") == "Agent Friday"
    _fast(brand.spoken, "\t" * N + "(x")
    _fast(brand.spoken, " \t" * (N // 2) + "(")


# -- services/artifacts.py ----------------------------------------------------

def test_fenced_artifacts_are_replaced_once_each_and_open_fences_are_left():
    from agent_friday.services import artifacts as art
    seen = []

    def fn(header, body, whole):
        seen.append((header, body, whole))
        return "[x]"
    text = "a ```friday-artifact md\nhello\n``` b ```friday-artifact t\nx```c ```friday-artifact open\nz"
    assert art._replace_fences(text, fn) == "a [x] b [x]c ```friday-artifact open\nz"
    assert seen[0] == ("md", "hello", "```friday-artifact md\nhello\n```")
    assert seen[1][1] == "x"


def test_open_fences_do_not_cost_a_scan_each():
    from agent_friday.services import artifacts as art
    text = "```friday-artifact a\n" * (N // 21)
    assert _fast(art._replace_fences, text, lambda h, b, w: w) == text


# -- services/workspace_studio.py ---------------------------------------------

def test_css_comments_are_stripped_in_one_pass():
    from agent_friday.services import workspace_studio as ws
    assert ws._strip_comments("a/* x */b/**/c/* y */") == "abc"
    assert ws._strip_comments("a/* open") == "a/* open"
    assert ws._strip_comments("/*/ x */y") == "y"
    _fast(ws._strip_comments, "/*" * (N // 2))
    assert ws._strip_comments("/*" * 50 + "z") != ""
    assert ws._scoped_css("/* open") == ""
    assert _fast(ws._sanitize_selector, "/*" * 100) == ""


# -- services/credential_paths.py ---------------------------------------------

def test_symbolic_home_forms_resolve_left_to_right_and_linearly():
    from agent_friday.services import credential_paths as cp
    home = str(cp._home()).replace(" ", cp._SPACE)
    assert cp._resolve_symbolic("cat $HOME/x ${home}/y") == "cat %s/x %s/y" % (home, home)
    assert cp._resolve_symbolic("ls [Environment]::GetFolderPath('Desktop')") == "ls " + home
    assert cp._resolve_symbolic("$home[system.environment]::getfolderpath(x)") == home + home
    assert cp._resolve_symbolic("[Environment]::GetFolderPath( $home") == "[Environment]::GetFolderPath( " + home
    _fast(cp._resolve_symbolic, "[Environment]::GetFolderPath(" * (N // 28))


# -- services/publish_web.py --------------------------------------------------

def test_publish_web_head_and_script_scans_are_linear():
    from agent_friday.services import publish_web as pw
    out = pw._inject_into_app("<html><head lang='en'><title>t</title></head><body></body></html>", mark=False)
    assert out.index("<head lang='en'>") < out.index("Content-Security-Policy") < out.index("<title>")
    assert "Content-Security-Policy" in pw._inject_into_app("<header>x</header>", mark=False)
    _fast(pw._inject_into_app, "<head " * (N // 6), mark=False)
    _fast(pw._inject_into_app, "<html " * (N // 6), mark=False)
    assert pw.collect_imports('<script src="https://a.example/x.js"></script> import "https://b.example/m.js"') \
        == ["https://a.example/x.js", "https://b.example/m.js"]
    _fast(pw.collect_imports, "<script " * (N // 8))
    _fast(pw.collect_imports, "from" + " " * N + "x")


# -- the remaining beta alerts ------------------------------------------------

def test_artifact_fence_opener_is_linear_without_a_line_end():
    from agent_friday.services import artifacts as art
    ident = lambda h, b, w: w  # noqa: E731
    for text in ("```friday-artifact" + "\t" * N,
                 "```friday-artifact" * (N // 18),
                 "```friday-artifact a\n" + "```friday-artifact" * (N // 18)):
        assert _fast(art._replace_fences, text, ident) == text


def test_artifact_fence_header_loses_only_leading_blanks():
    from agent_friday.services import artifacts as art
    seen = []
    art._replace_fences("```friday-artifact \t {\"k\": 1} \nbody\n```", lambda h, b, w: seen.append((h, b)) or "")
    assert seen == [('{"k": 1} ', "body")]


def test_open_workspace_lead_is_linear_on_a_wall_of_spaces():
    from agent_friday.services import laya_resolver as lr
    hostile = "a" + " " * N + "x"
    assert _fast(lr._TRAIL_FILLER.sub, "", hostile) == hostile
    assert lr._TRAIL_FILLER.sub("", "the lab for me please") == "the lab"
    assert lr._TRAIL_FILLER.sub("", "the   lab  now") == "the   lab"


def test_person_markers_still_scrub_and_are_linear_on_hostile_text():
    from agent_friday.services import local_context as lc
    out, ph = lc.scrub("Ask {{person: Sam Lee | their brother}} and {{ person:Dana|a friend }}.")
    assert "Sam" not in out and "Dana" not in out
    assert "[their brother]" in out and "[a friend]" in out
    for hostile in ("{{person:" + " " * N + "x",
                    "{{person:" * (N // 9),
                    "{{person: a |" * (N // 13),
                    "{{person: a |" + " " * N + "x",
                    "{{person: " + "x " * (N // 2),
                    "{{{{person:" + " " * N):
        _fast(lc.PERSON_RE.sub, "[x]", hostile)
        _fast(lc.PERSON_RE.findall, hostile)


def test_person_marker_pattern_matches_what_it_matched_before():
    import random
    import re
    from agent_friday.services import local_context as lc
    old = re.compile(r"\{\{\s*person\s*:\s*([^|}]+?)\s*\|\s*([^}]+?)\s*\}\}", re.I)
    rng = random.Random(7)
    parts = ["{{", "}}", "person", ":", "|", " ", "Sam", "Dana Q", "brother", "a friend", "\n", "x"]
    checked = 0
    for _ in range(4000):
        text = "".join(rng.choice(parts) for _ in range(rng.randint(1, 14)))
        a = [(m.start(), m.group(1).strip(), m.group(2)) for m in old.finditer(text)]
        b = [(m.start(), m.group(1).strip(), m.group(2)) for m in lc.PERSON_RE.finditer(text)]
        # The pattern may stop at a nested "{{" or a blank name; on everything
        # else it agrees exactly.
        if "{{" in text[2:] or not all(g[1] for g in a):
            continue
        checked += 1
        assert a == b, text
    assert checked > 200


def test_figures_pattern_is_bounded_per_start():
    from agent_friday.services import podcast_quality as pq
    _fast(pq._entities, "T", ("1" * 39 + "x") * (N // 40))
    _fast(pq._entities, "T", "$" + "1," * (N // 2))
