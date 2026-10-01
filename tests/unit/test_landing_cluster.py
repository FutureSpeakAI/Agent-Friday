"""The start screen's cluster (docs/design/active/unified-shell.md §10.2-10.5):
the countdowns, the transparency layer, the chat field, the mic and Start my
day show when they are useful and fade when they are not.

  * fridayLandingJudge is one pure rule set, tested in node on a table of
    situations, with the dwell, the idle return and the two keys beside it.
  * The fade is the top bar's and the dock's own (--fr-reveal); hidden, the
    cluster takes no pointer, no focus and no screen reader's attention.
  * landing_mode (smart, always, never) is a setting, a palette command and
    show_my_day, a voice and chat tool per the voice tool contract.
  * Each change is one line in the console and one in the server's log.
Fictional names only.
"""
from __future__ import annotations

import functools
import http.server
import json
import logging
import re
import shutil
import socketserver
import subprocess
import threading
from pathlib import Path

import pytest

from agent_friday import brand
from agent_friday.services import agent, desktop_bus
from agent_friday.services import voice_engine as ve

ROOT = Path(__file__).resolve().parents[2]
PAGES = ("index.html", "ui_parts/app.html")
HEADS = ("index.html", "ui_parts/head.html")
node = shutil.which("node")
MIN = 60_000
T = 1_790_000_000_000          # a fixed moment, in ms


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8-sig")


def _block(text, name):
    m = re.search(r"/\* %s:begin \*/\n(.*?)/\* %s:end \*/" % (name, name), text, re.S)
    assert m, name
    return m.group(1)


# ── the judge, in node ───────────────────────────────────────────────────────

BASE = {"now": T, "mode": "smart", "windows": 0, "front": "", "voice": False, "chat": False,
        "typing": False, "hoverSince": 0, "summonedAt": 0, "returnedAt": 0, "awayMs": 0,
        "dayStartAt": 0, "openedAt": 0, "items": [], "lastReasonAt": 0, "lastReason": ""}

JUDGE_CASES = [
    # (name, changes, show, reason)
    ("just opened", {"openedAt": T - 10_000}, True, "just opened"),
    ("the dwell after it", {"openedAt": T - 70_000, "lastReasonAt": T - 10_000,
                            "lastReason": "just opened"}, True, "just opened"),
    ("then gone", {"openedAt": T - 2 * MIN, "lastReasonAt": T - 40_000}, False,
     "nothing needs you here"),
    ("back after idle", {"returnedAt": T - 5_000, "awayMs": 14 * MIN}, True, "back after 14 min"),
    ("back after hours", {"returnedAt": T - 5_000, "awayMs": 185 * MIN}, True, "back after 3 h"),
    ("first look today", {"dayStartAt": T - 5_000, "returnedAt": T - 5_000,
                          "awayMs": 600 * MIN}, True, "first look today"),
    ("working in a workspace", {"windows": 1, "front": "News", "returnedAt": T - 5_000,
                                "awayMs": 9 * MIN}, False, "working in News"),
    ("talking", {"voice": True, "returnedAt": T - 5_000, "awayMs": 9 * MIN}, False, "talking"),
    ("in the chat", {"chat": True, "openedAt": T - 5_000}, False, "in the chat"),
    ("never, on its own", {"mode": "never", "returnedAt": T - 5_000, "awayMs": 9 * MIN}, False,
     "set to show only when asked"),
    ("never, when asked", {"mode": "never", "summonedAt": T - 5_000}, True, "you asked"),
    ("asked while talking", {"voice": True, "summonedAt": T - 5_000}, True, "you asked"),
    ("asked over a window", {"windows": 2, "front": "Calendar", "summonedAt": T - 5_000}, False,
     "working in Calendar"),
    ("always", {"mode": "always", "voice": True}, True, "set to always show"),
    ("always, under a window", {"mode": "always", "windows": 1, "front": "Mail"}, False,
     "working in Mail"),
    ("a glance is not a rest", {"hoverSince": T - 300}, False, "nothing needs you here"),
    ("the pointer rests on it", {"hoverSince": T - 600}, True, "the pointer is on it"),
    ("typing in it", {"typing": True}, True, "typing in it"),
    ("the pointer rests on it while talking", {"voice": True, "hoverSince": T - 600}, True,
     "the pointer is on it"),
    ("typing in it with the chat open", {"chat": True, "typing": True}, True, "typing in it"),
    ("a glance while talking", {"voice": True, "hoverSince": T - 300}, False, "talking"),
    ("within the hour", {"items": [{"label": "Design review", "at": (T + 58 * MIN) // 1000}]},
     True, "Design review in 58 min"),
    ("between the marks", {"items": [{"label": "Design review", "at": (T + 30 * MIN) // 1000}]},
     False, "nothing needs you here"),
    ("and at ten minutes", {"items": [{"label": "Design review", "at": (T + 9 * MIN) // 1000}]},
     True, "Design review in 9 min"),
    ("an all-day item is no alarm", {"items": [{"label": "Dana's birthday", "at": None,
                                                "date": "2026-10-12"}]}, False,
     "nothing needs you here"),
]


def _node(script: str, tmp_path, name="run.js"):
    p = tmp_path / name
    p.write_text(script, encoding="utf-8")
    cp = subprocess.run([node, str(p)], capture_output=True, text=True, timeout=60)
    assert cp.returncode == 0, cp.stderr
    return json.loads(cp.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def judged(tmp_path_factory):
    if not node:
        pytest.skip("node is not on PATH")
    judge = _block(_read("index.html"), "fridayLandingJudge")
    cases = [dict(BASE, **changes) for _, changes, _, _ in JUDGE_CASES]
    script = judge + "\nconst cases = %s;\n" % json.dumps(cases) + \
        "console.log(JSON.stringify(cases.map(fridayLandingJudge)));\n"
    return _node(script, tmp_path_factory.mktemp("judge"))


@pytest.mark.parametrize("i", range(len(JUDGE_CASES)), ids=[c[0] for c in JUDGE_CASES])
def test_the_judge(judged, i):
    name, changes, show, reason = JUDGE_CASES[i]
    r = judged[i]
    assert (r["show"], r["reason"]) == (show, reason), name
    assert T < r["next"] <= T + MIN, name        # it looks again within the minute


def test_the_judge_looks_again_when_its_answer_can_change(judged):
    by = {c[0]: r for c, r in zip(JUDGE_CASES, judged)}
    assert by["just opened"]["next"] == T - 10_000 + MIN
    assert by["just opened"]["live"] and by["just opened"]["until"] == T - 10_000 + MIN
    assert by["the dwell after it"]["next"] == T - 10_000 + 30_000 and not by["the dwell after it"]["live"]
    assert by["a glance is not a rest"]["next"] == T - 300 + 500
    assert by["between the marks"]["next"] == T + MIN          # the ten-minute mark is further off
    assert by["the pointer rests on it"]["until"] is None      # Infinity: while it rests there


@pytest.fixture(scope="module")
def stepped(tmp_path_factory):
    if not node:
        pytest.skip("node is not on PATH")
    judge = _block(_read("index.html"), "fridayLandingJudge")
    script = judge + r"""
const T = %d, MIN = 60000;
const sees = {mode: 'smart', windows: 0, front: '', voice: false, chat: false, typing: false, items: []};
const out = {};
// opened: news for a minute, then the dwell's thirty seconds, then gone
let st = fridayLandingClock(T);
out.opened = [0, 59000, 60000, 89000, 90000].map(dt => {
  const r = fridayLandingStep(st, sees, T + dt);
  return [dt, r.show, r.reason, r.live];
});
// the pointer rests for twenty seconds: the dwell counts from when it leaves
st = fridayLandingClock(T - 10 * MIN);
st.hoverSince = T - 600;
const rest = [];
let r = fridayLandingStep(st, sees, T);
rest.push([0, r.show, r.reason]);
st.hoverSince = 0;
for (const dt of [20000, 49000, 50000]) { r = fridayLandingStep(st, sees, T + dt); rest.push([dt, r.show, r.reason]); }
out.rest = rest;
// input: a return only after the idle time; the first input of a new day
st = fridayLandingClock(T);
out.input = [fridayLandingInput(st, T + 4 * MIN), fridayLandingInput(st, T + 9 * MIN + 1), st.awayMs, st.returnedAt === T + 9 * MIN + 1];
st.day = 'a day before';
out.newDay = [fridayLandingInput(st, T + 9 * MIN + 2), st.dayStartAt === T + 9 * MIN + 2];
// the page hidden and shown again
st = fridayLandingClock(T);
fridayLandingVisible(st, false, T);
out.shortAway = fridayLandingVisible(st, true, T + MIN);
fridayLandingVisible(st, false, T + MIN);
out.longAway = [fridayLandingVisible(st, true, T + 7 * MIN), st.awayMs];
// the keys
out.keys = [
  {ctrlKey: true, key: '/'}, {metaKey: true, key: '/', code: 'Slash'}, {ctrlKey: true, shiftKey: true, key: ' ', code: 'Space'},
  {key: '/'}, {ctrlKey: true, altKey: true, key: '/'}, {ctrlKey: true, key: 'k'}, {ctrlKey: true, shiftKey: true, key: '?', code: 'Slash'}
].map(fridayLandingKey);
console.log(JSON.stringify(out));
""" % T
    return _node(script, tmp_path_factory.mktemp("step"))


def test_it_stays_for_the_dwell_after_its_last_reason(stepped):
    assert stepped["opened"] == [
        [0, True, "just opened", True], [59000, True, "just opened", True],
        [60000, True, "just opened", False], [89000, True, "just opened", False],
        [90000, False, "nothing needs you here", False]]


def test_a_rest_holds_until_the_pointer_leaves_and_the_dwell_counts_from_then(stepped):
    assert stepped["rest"] == [[0, True, "the pointer is on it"], [20000, True, "the pointer is on it"],
                               [49000, True, "the pointer is on it"], [50000, False, "nothing needs you here"]]


def test_a_return_is_input_after_five_idle_minutes_or_the_first_of_a_day(stepped):
    assert stepped["input"] == [False, True, 5 * MIN + 1, True]
    assert stepped["newDay"] == [True, True]
    assert stepped["shortAway"] is False and stepped["longAway"] == [True, 6 * MIN]


def test_the_two_keys(stepped):
    assert stepped["keys"] == ["chat", "chat", "voice", None, None, None, None]


def test_both_pages_carry_the_same_judge():
    assert _block(_read(PAGES[0]), "fridayLandingJudge") == _block(_read(PAGES[1]), "fridayLandingJudge")


# ── the page ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", PAGES)
def test_the_cluster_is_on_the_desktop_and_the_judge_fades_it(rel):
    text = _read(rel)
    assert "noWindows&&<div style={{position:'fixed'" not in text and \
        'noWindows && /*#__PURE__*/React.createElement("div", {\n    style: {\n      position: \'fixed\'' not in text
    assert re.search(r"'landing-cluster' ?\+ ?\(landing\.show ?\? ?'' ?: ?' hidden'\)", text), rel
    assert re.search(r"aria-hidden\"?:? ?=?\{?landing\.show ?\? ?undefined ?: ?'true'", text), rel
    assert "el.setAttribute('inert', '')" in text, rel
    assert "fridayLandingStep(st, " in text and "console.info('[landing] ' + (r.show ? 'shown' : 'hidden') + ': ' + r.reason)" in text, rel
    assert "landingJudge.current();" in text and "[landingMode, openWins, focusWin, voiceOn, chatOpen, countdowns]" in text, rel


@pytest.mark.parametrize("rel", PAGES)
def test_the_page_reports_the_cluster_and_answers_show_my_day(rel):
    text = _read(rel)
    assert "landing: kind === 'desktop' && d.landing ? { shown: !!d.landing.show, reason: d.landing.reason } : undefined" in text, rel
    assert "answer({ opened: true, matched: null, landing: { show: !!l.show, reason: l.reason || '' } })" in text, rel
    assert re.search(r"a\.type ?=== ?'landing'", text) and "new CustomEvent('friday:landing'" in text, rel


@pytest.mark.parametrize("rel", PAGES)
def test_the_palette_the_keys_and_the_setting(rel):
    text = _read(rel)
    assert re.search(r"layoutCommands\.concat\(landingCommands, ?barCommands\b", text), rel
    for cmd in ("'show-my-day'", "'chat-field'", "meta: 'Ctrl+/'", "meta: 'Ctrl+Shift+Space'"):
        assert cmd in text, (rel, cmd)
    assert "const k = fridayLandingKey(e);" in text and "landingDo.current.toggleVoice()" in text, rel
    about = text[text.index("function SettingsTabAppearance("):]
    about = about[:about.index("\n}\n")]
    assert "title: \"Start screen\"" in about and "save({\n        landing_mode: v\n      })" in about, rel


def test_one_reveal_token_moves_the_top_bar_the_dock_and_the_cluster():
    assert brand.TOKENS["--fr-reveal"] == "0.35s cubic-bezier(0.2, 0.8, 0.3, 1)"
    assert brand.TOKENS["--fr-reveal-time"] == "0.35s"
    for rel in HEADS:
        css = _read(rel)
        assert "transition: transform 0.35s" not in css, rel
        assert re.search(r"\.top-bar \{[^}]*transition: transform var\(--fr-reveal\)", css), rel
        assert re.search(r"\.dock \{[^}]*transition: transform var\(--fr-reveal\)", css), rel
        hidden = re.search(r"\.landing-cluster\.hidden \{([^}]*)\}", css).group(1)
        assert "opacity: 0" in hidden and "visibility: hidden" in hidden and "var(--fr-reveal)" in hidden, rel


def test_the_setting_defaults_to_smart():
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["landing_mode"] == "smart"


# ── show_my_day ──────────────────────────────────────────────────────────────

@pytest.fixture
def pages():
    desktop_bus.reset()
    yield
    desktop_bus.reset()


def test_show_my_day_is_a_voice_tool_with_its_argument():
    spec = next(t for t in ve._VOICE_LIVE_TOOLS if t[0] == "show_my_day")
    assert spec[3] == [] and spec[2]["mode"][0] == "string"
    assert ve._voice_tool_names().count("show_my_day") == 1
    for word in ("DAY_SHOWN", "DAY_NOT_SHOWN", "DAY_MODE", "do not guess"):
        assert word in spec[1], word


def test_show_my_day_is_a_chat_tool_on_the_owners_own_screen():
    from agent_friday.governance import action_gate
    from agent_friday.services import taint
    assert agent.CLAUDE_TOOL_HANDLERS["show_my_day"] is agent._tool_show_my_day
    tool = next(t for t in agent.CLAUDE_TOOLS if t["name"] == "show_my_day")
    assert tool["input_schema"]["properties"]["mode"]["enum"] == ["smart", "always", "never"]
    assert agent.TOOL_RINGS["show_my_day"] == 1
    assert "show_my_day" in action_gate.INTERNAL_TOOLS
    assert taint.TOOL_ROLES["show_my_day"] == {}


def test_voice_routes_show_my_day_through_the_checkpoint(monkeypatch):
    called, governed = [], []
    monkeypatch.setattr(agent, "_tool_show_my_day", lambda inp: called.append(inp) or "DAY_SHOWN")

    def execute(t, a, handler=None, session_ctx=None):
        governed.append(t)
        return handler(a)

    monkeypatch.setattr(agent, "_execute_tool", execute)
    out = ve._voice_tool_run("show_my_day", {}, lambda *a, **k: None, {"conversation_id": "c1"})
    assert out == "DAY_SHOWN" and called == [{}] and governed == ["show_my_day"]


def _answering(q, reply):
    """A desktop page that runs the command and answers as `reply` says."""
    seen = []

    def page():
        msg = q.get(timeout=5)
        seen.append(msg)
        desktop_bus.ack(msg["id"], reply)

    t = threading.Thread(target=page, daemon=True)
    t.start()
    return seen, t


def test_show_my_day_reports_what_the_page_did(pages, monkeypatch):
    monkeypatch.setattr(agent, "LANDING_ACK_S", 5.0)
    q = desktop_bus.subscribe("desk", "desktop")
    desktop_bus.report_state("desk", {"kind": "desktop", "focused": True})
    seen, t = _answering(q, {"opened": True, "landing": {"show": True, "reason": "you asked"}})
    out = agent._tool_show_my_day({})
    t.join(5)
    assert out.startswith("DAY_SHOWN"), out
    assert seen[0]["actions"] == [{"type": "landing", "summon": True, "via": "friday"}]
    seen, t = _answering(q, {"opened": True, "landing": {"show": False, "reason": "working in News"}})
    out = agent._tool_show_my_day({})
    t.join(5)
    assert out == "DAY_NOT_SHOWN — working in News, so the start screen is covered.", out


def test_show_my_day_says_plainly_when_no_page_can_show_it(pages):
    assert agent._tool_show_my_day({}) == "DAY_NOT_SHOWN — no Friday desktop page is open to show it on."


def test_a_mode_is_kept_and_sent(pages, monkeypatch):
    from agent_friday.core import _load_settings
    monkeypatch.setattr(agent, "LANDING_ACK_S", 5.0)
    out = agent._tool_show_my_day({"mode": "Never"})
    assert out.startswith("DAY_MODE:never") and "only when you ask" in out, out
    assert _load_settings()["landing_mode"] == "never"
    q = desktop_bus.subscribe("desk", "desktop")
    desktop_bus.report_state("desk", {"kind": "desktop", "focused": True})
    seen, t = _answering(q, {"opened": True, "landing": {"show": True, "reason": "set to always show"}})
    out = agent._tool_show_my_day({"mode": "always"})
    t.join(5)
    assert out == ("DAY_MODE:always — the start screen shows your day whenever no workspace is "
                   "open. It is showing now."), out
    assert seen[0]["actions"] == [{"type": "landing", "summon": False, "via": "friday", "mode": "always"}]
    assert agent._tool_show_my_day({"mode": "sometimes"}).startswith("DAY_FAIL")
    from agent_friday.core import _save_settings
    _save_settings({"landing_mode": "smart"})


def test_the_countdowns_never_reach_the_model(pages, monkeypatch):
    """The page shows them; the result names nothing from the owner's day."""
    monkeypatch.setattr(agent, "LANDING_ACK_S", 5.0)
    q = desktop_bus.subscribe("desk", "desktop")
    desktop_bus.report_state("desk", {"kind": "desktop", "focused": True})
    seen, t = _answering(q, {"opened": True, "landing": {"show": True, "reason": "you asked"},
                             "countdowns": [{"label": "Dana's birthday"}]})
    out = agent._tool_show_my_day({})
    t.join(5)
    assert "Dana" not in out and "birthday" not in out


# ── the server's log ─────────────────────────────────────────────────────────

def test_each_change_is_one_line_in_the_servers_log(pages, caplog):
    caplog.set_level(logging.INFO, logger="agent_friday.services.desktop_bus")
    for shown, reason in ((True, "just opened"), (True, "back after 14 min"),
                          (False, "working in News"), (False, "talking"), (True, "you asked")):
        desktop_bus.report_state("desk", {"kind": "desktop", "landing": {"shown": shown, "reason": reason}})
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("landing ")]
    assert lines == ["landing shown: just opened", "landing hidden: working in News",
                     "landing shown: you asked"]


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
            logs = []
            page.on("console", lambda m: logs.append(m.text))
            page.route("**/api/desktop/events**", lambda r: r.fulfill(status=204, body=""))
            page.route("**/api/**", lambda r: r.fulfill(status=200, content_type="application/json",
                                                        body=json.dumps({"status": "ok"})))
            page.clock.install()
            yield page, base, logs
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def _cluster(page):
    return page.evaluate("""() => {
      const el = document.querySelector('[data-testid="landing-cluster"]');
      const cs = el && getComputedStyle(el);
      return el && {state: el.dataset.landing, inert: el.hasAttribute('inert'), hidden: el.getAttribute('aria-hidden'),
                    visibility: cs.visibility, transition: cs.transition,
                    focusInside: el.contains(document.activeElement)};
    }""")


def test_the_cluster_fades_away_and_a_key_brings_it_back(browser_page):
    page, base, logs = browser_page
    page.goto(base + "/index.html", wait_until="domcontentloaded")
    page.wait_for_selector('[data-testid="landing-cluster"]', timeout=60000)
    c = _cluster(page)
    assert c["state"] == "shown" and not c["inert"] and c["hidden"] is None
    assert "opacity 0.35s cubic-bezier(0.2, 0.8, 0.3, 1)" in c["transition"]
    for sel in (".top-bar", ".dock"):
        tr = page.evaluate("s => getComputedStyle(document.querySelector(s)).transition", sel)
        assert tr.startswith("transform 0.35s cubic-bezier(0.2, 0.8, 0.3, 1)"), (sel, tr)
    page.mouse.move(40, 500)                     # away from the cluster's area
    # Jump the page's clock (due timers fire once; the scene's frames are not
    # played one by one): past "just opened", then past the dwell.
    page.clock.fast_forward(61_000)
    page.clock.fast_forward(31_000)
    page.wait_for_function("document.querySelector('[data-testid=\"landing-cluster\"]').dataset.landing === 'hidden'",
                           timeout=10000)
    page.wait_for_timeout(700)                   # the fade itself runs in real time
    c = _cluster(page)
    assert c["inert"] and c["hidden"] == "true" and c["visibility"] == "hidden"
    assert any(line == "[landing] hidden: nothing needs you here" for line in logs), logs[-5:]
    page.keyboard.press("Control+/")
    page.wait_for_function("document.querySelector('[data-testid=\"landing-cluster\"]').dataset.landing === 'shown'",
                           timeout=10000)
    # the cursor lands a moment after it shows
    page.wait_for_function("document.querySelector('[data-testid=\"landing-cluster\"]').contains(document.activeElement)",
                           timeout=10000)
    c = _cluster(page)
    assert c["focusInside"] and not c["inert"], c
    assert "[landing] shown: you asked" in logs and "[keys] chat field" in logs
    page.keyboard.press("Control+Shift+Space")
    page.wait_for_timeout(300)
    assert "[keys] voice" in logs


def test_the_key_gives_the_field_the_cursor_when_the_cluster_shows(browser_page):
    """Ctrl+/ on a hidden cluster: the field can take the cursor only once the
    cluster is shown and no longer inert, which is a render away. Focusing
    after a fixed delay lost that race under load; with the page's timers
    stopped, nothing but the showing itself can give the field the cursor."""
    page, base, logs = browser_page
    page.goto(base + "/index.html", wait_until="domcontentloaded")
    page.wait_for_selector('[data-testid="landing-cluster"]', timeout=60000)
    page.mouse.move(40, 500)
    page.clock.fast_forward(61_000)
    page.clock.fast_forward(31_000)
    page.wait_for_function("document.querySelector('[data-testid=\"landing-cluster\"]').dataset.landing === 'hidden'",
                           timeout=10000)
    page.wait_for_timeout(700)
    page.clock.pause_at(page.evaluate("Date.now()") + 1)
    try:
        page.keyboard.press("Control+/")
        page.wait_for_function("document.querySelector('[data-testid=\"landing-cluster\"]').dataset.landing === 'shown'",
                               timeout=10000)
        page.wait_for_function(
            "document.querySelector('[data-testid=\"landing-cluster\"]').contains(document.activeElement)",
            timeout=5000)
    finally:
        page.clock.resume()
