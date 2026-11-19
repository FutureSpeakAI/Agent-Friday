"""The Settings panel for accepted issuers (NS-49.1-1). TEST CREDITS ONLY.

The server half (#915) already refuses to accept an issuer without a press on this PC, declines an event that names an
unaccepted issuer before reading it, and lets withdrawal through without a press. This panel is the owner's way to use
that: it lists the accepted issuers (none by default), accepts one only when the owner clicks, and withdraws on a click.

A UI change edits both `index.html` and `ui_parts/app.html` (CONTRIBUTING). What these tests hold:
  - the component is the same text in both files, and Settings > Federation shows it under "Accepted issuers";
  - it talks to the three real routes through apiFetch (which carries the desk key the press check reads), and the only
    POST sits inside the accept click handler: nothing accepts on load, on typing or on a timer;
  - it carries no approve, pay or send control and no reserved card wording (the shell's own wording rules);
  - the component parses as JavaScript (node), so a typo cannot ship green.
The limit, stated plainly: this is the static half. A Playwright walk of the panel needs a held-feature setting turned on
in a scratch server; the Settings walk treats the Federation tab as guarded and does not click in it.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FILES = {"index.html": ROOT / "index.html", "ui_parts/app.html": ROOT / "ui_parts" / "app.html"}
START = "function AcceptedIssuersPanel() {"


def component(text):
    start = text.index(START)
    end = text.index("\nfunction ", start + 10)
    return text[start:end]


@pytest.fixture(scope="module")
def sources():
    return {name: path.read_text(encoding="utf-8") for name, path in FILES.items()}


def test_both_ui_files_carry_the_same_component(sources):
    bodies = {name: component(text) for name, text in sources.items()}
    assert bodies["index.html"] == bodies["ui_parts/app.html"]
    assert all(text.count(START) == 1 for text in sources.values())


def test_settings_federation_shows_it_under_its_own_heading(sources):
    for name, text in sources.items():
        tab = text[text.index("function SettingsTabFederation()"):text.index("function SettingsTabEconomy()")]
        assert "AcceptedIssuersPanel" in tab and "Accepted issuers" in tab, name
        assert tab.index("Accepted issuers") < tab.index("Blocking instances"), name


def test_it_uses_the_three_real_routes_through_apifetch_and_never_bare_fetch(sources):
    body = component(sources["index.html"])
    assert body.count("apiFetch('/api/federation/accepted-issuers'") == 2      # list and accept
    assert "apiFetch('/api/federation/accepted-issuers/' + encodeURIComponent(id), { method: 'DELETE' })" in body
    assert not re.search(r"(?<![A-Za-z])fetch\(", body)


def test_nothing_accepts_on_load_typing_or_a_timer(sources):
    body = component(sources["index.html"])
    assert body.count("method: 'POST'") == 1
    accept = body[body.index("const accept = async"):body.index("const withdraw = async")]
    assert "method: 'POST'" in accept
    outside = body.replace(accept, "")
    assert "POST" not in outside
    assert "setInterval" not in body and "setTimeout" not in body
    effect = body[body.index("React.useEffect"):body.index("\n", body.index("React.useEffect"))]
    assert "load()" in effect and "accept" not in effect
    assert "onChange: e => setCandidate(e.target.value)" in body, "typing only sets the text"


def test_the_only_buttons_are_accept_and_withdraw_and_the_wording_is_the_plain_one(sources):
    body = component(sources["index.html"])
    labels = re.findall(r"createElement\('button'[^\n]*, '([A-Z][^']*)'\)", body)
    assert sorted(labels) == ["Accept this issuer", "Withdraw"], labels
    assert "Test credits only" in body
    for word in ("Approve", "Pay ", "Buy", "Top up", "Send money", "Allow"):
        assert word not in body, word


def test_the_component_parses_as_javascript(sources):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    wrapped = "const React = {useState(){}, useEffect(){}}; const apiFetch = () => {};\n" + component(sources["index.html"])
    done = subprocess.run([node, "--check", "-"], input=wrapped, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr


def test_the_check_can_fail_a_load_time_post_would_be_seen(sources):
    body = component(sources["index.html"])
    broken = body.replace("React.useEffect(() => { load(); }, []);",
                          "React.useEffect(() => { load(); apiFetch('/x', { method: 'POST' }); }, []);")
    accept = broken[broken.index("const accept = async"):broken.index("const withdraw = async")]
    assert "POST" in broken.replace(accept, ""), "the load-time POST would be seen"
    assert "POST" not in body.replace(body[body.index("const accept = async"):body.index("const withdraw = async")], "")
