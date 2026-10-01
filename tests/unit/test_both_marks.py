"""Both marks (docs/design/active/unified-shell.md §9): the product is Agent
Friday™ and its maker FutureSpeak.AI™, on every brand surface and in written
podcast material, never with ® (neither is registered), and plain in audio.

  * The lockup's maker carries its mark, and the About card carries the
    trademark line, whose last words name the owner, plain.
  * Her hint lines are shown text, through the display filter.
  * Podcasts: written material credits the show "… from Agent Friday™" (the
    captions, the episode card and detail, the notice, the audio's tags); the
    hosts say it in plain words and she calls herself by her own name; show
    names carry no one's name.
  * The README names the product with the lockup and carries the line.
"""
from __future__ import annotations

import functools
import http.server
import json
import re
import socketserver
import threading
from pathlib import Path

import pytest

from agent_friday import brand

ROOT = Path(__file__).resolve().parents[2]
PAGES = ("index.html", "ui_parts/app.html")
TM = "™"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8-sig")


def _fn(text: str, name: str) -> str:
    m = re.search(r"\nfunction %s\(" % name, text)
    assert m, name
    return text[m.start():text.index("\n}\n", m.start() + 1) + 2]


# ── the page ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", PAGES)
def test_the_lockup_marks_the_maker_as_it_marks_the_product(rel):
    lockup = _fn(_read(rel), "FridayLockup")
    maker = lockup[lockup.index("fr-lockup-maker"):]
    assert "b.maker" in maker and "fr-lockup-tm" in maker and "b.mark" in maker, rel


@pytest.mark.parametrize("rel", PAGES)
def test_the_about_card_carries_the_trademark_line(rel):
    about = _fn(_read(rel), "SettingsTabAbout")
    assert "window.FRIDAY_BRAND.notice" in about and "trademark-notice" in about, rel


@pytest.mark.parametrize("rel", ("index.html",))
def test_her_hint_lines_are_shown_through_the_filter(rel):
    assert "text = fridayTM(text);" in _fn(_read(rel), "FridaySays")


def test_the_sign_in_page_names_the_maker_with_its_mark():
    from agent_friday import core
    assert "<b>%s</b>" % brand.MAKER_NAME in core.LOGIN_HTML


# ── podcasts ─────────────────────────────────────────────────────────────────

def _pe():
    from agent_friday.services import podcast_engine
    return podcast_engine


def test_a_show_name_carries_no_ones_name():
    pe = _pe()
    for routine, show in pe.SHOW_NAMES.items():
        assert "Friday" not in show, (routine, show)


def test_written_material_credits_the_show_from_the_product():
    pe = _pe()
    assert pe.show_credit({"show": "The Briefing"}) == "The Briefing from Agent Friday" + TM
    assert pe.show_credit({"show": ""}) == "A podcast from Agent Friday" + TM
    # episodes saved under the old names read through the new ones
    assert pe.show_credit({"show": "Friday's Front Page"}) == "The Front Page from Agent Friday" + TM
    assert pe.show_credit({"show": "Friday Podcast"}) == "A podcast from Agent Friday" + TM
    # stored text (a notice) keeps the words plain; the page adds the mark
    assert pe.show_credit({"show": "The Week"}, marked=False) == "The Week from Agent Friday"


def test_the_hosts_say_the_credit_plainly_and_she_says_her_own_name():
    pe = _pe()
    ep = {"show": "The Week", "hosts": pe.DEFAULTS["hosts"], "attached": {"routine": "weekly"}}
    opening, closing = pe.signature_lines(ep)
    spoken = " ".join(x["text"] for x in opening + closing)
    assert "This is The Week from Agent Friday." in spoken and TM not in spoken
    assert "I'm %s." % pe.DEFAULTS["hosts"]["a"]["name"] in spoken
    alone = pe.signature_lines(dict(ep, show="", attached=None))[0][0]["text"]
    assert alone.startswith("This is a podcast from Agent Friday.")


def test_the_captions_are_the_written_transcript():
    from agent_friday.services import podcast_render as render
    vtt = render.captions_vtt([{"speaker": "a", "text": "This is The Week from Agent Friday.",
                                "start": 0.0, "end": 1.0}], {"a": "Dana"},
                              header=brand.from_product("The Week"))
    assert vtt.startswith("WEBVTT - The Week from Agent Friday" + TM + "\n")
    assert "<v Dana>This is The Week from Agent Friday" + TM + "." in vtt


def test_the_audio_files_tags_credit_the_show(monkeypatch, tmp_path):
    from agent_friday.services import podcast_render as render
    from agent_friday.services import timeline_engine
    seen = []

    class Done:
        returncode = 0

    def run(cmd, **kw):
        seen.append(cmd)
        Path(cmd[-1]).write_bytes(b"ID3")
        return Done()

    monkeypatch.setattr(timeline_engine, "ffmpeg_exe", lambda: "ffmpeg")
    monkeypatch.setattr(render.subprocess, "run", run)
    assert render.encode_mp3(tmp_path / "a.wav", tmp_path / "a.mp3", "Rates, explained",
                             album=brand.from_product("The Briefing"), artist=brand.PRODUCT_NAME)
    cmd = seen[0]
    assert "album=The Briefing from Agent Friday" + TM in cmd
    assert "artist=Agent Friday" + TM in cmd and "title=Rates, explained" in cmd


def test_the_ready_notice_credits_the_show_in_plain_words(monkeypatch):
    pe = _pe()
    pushed = []
    from agent_friday import notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: pushed.append(kw))
    monkeypatch.setattr(pe, "settings", lambda: {"on_ready": "notify"})
    pe._announce({"id": "20260930T100000-abcdef", "title": "Rates", "privacy": "public",
                  "show": "The Briefing", "duration_s": 300})
    assert pushed[0]["body"].startswith("The Briefing from Agent Friday · 5 min")


def test_the_page_and_the_api_show_the_credit():
    pe = _pe()
    s = pe.summary({"id": "x", "show": "The Briefing", "chapters": [], "sources": []})
    assert s["credit"] == "The Briefing from Agent Friday" + TM
    src = _read("src/agent_friday/routes/podcasts.py")
    assert 'ep["credit"] = pe.show_credit(ep)' in src
    for rel in PAGES:
        s = _read(rel)
        assert s.count("ep.credit || ep.show") >= 2, rel
        assert "fridayTM(ln.text)" in s and "fridayTM(line.text)" in s, rel


# ── the README ───────────────────────────────────────────────────────────────

def test_the_readme_names_the_product_with_the_lockup_and_carries_the_line():
    readme = _read("README.md")
    assert readme.splitlines()[0] == "# " + brand.PRODUCT_LOCKUP
    assert brand.TRADEMARK_NOTICE in readme
    assert "®" not in readme
    for old in ("Agent Friday Desktop", "Open Friday Desktop"):
        assert old not in readme, old


def test_brand_md_records_both_marks():
    doc = _read("docs/brand/BRAND.md")
    for phrase in ("`MAKER_NAME`", "`TRADEMARK_NOTICE`", "FutureSpeak.AI" + TM, "never ®"):
        assert phrase in doc, phrase


# ── in a real browser ────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def browser_page():
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(ROOT)))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        with sync_api.sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:                           # pragma: no cover
                pytest.skip("no chromium for playwright: %s" % exc)
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.route("**/api/desktop/events**", lambda r: r.fulfill(status=204, body=""))
            page.route("**/api/**", lambda r: r.fulfill(status=200, content_type="application/json",
                                                        body=json.dumps({"status": "ok"})))
            yield page, base
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_lockup_and_the_about_card_read_both_marks(browser_page):
    page, base = browser_page
    page.goto(base + "/index.html?workspace=settings", wait_until="domcontentloaded")
    page.wait_for_selector(".st-root", timeout=60000)
    page.evaluate("window.dispatchEvent(new CustomEvent('friday:settings-tab', {detail: {tab: 'about'}}))")
    page.wait_for_selector('[data-testid="trademark-notice"]', timeout=15000)
    assert page.locator('[data-testid="trademark-notice"]').first.inner_text() == brand.TRADEMARK_NOTICE
    maker = page.locator(".top-bar .fr-lockup-maker").first.inner_text().replace("\n", "")
    assert maker == brand.MAKER_NAME, maker
