"""Settings > Laya, Slice 2: Teach her a phrase, the reset-bars button, and the Laya panels moved from Privacy & Data.

Teaching goes through the real personal layer (the World fixture of tests/unit/test_laya_personal.py: a synthetic
vault key, embedder, seeds and sensitivity probe), so a taught phrase is stored exactly as a spoken correction is.
"""
from __future__ import annotations

import pathlib
import re

import pytest
from flask import Flask, g

from agent_friday.services import laya_personal as lp
from agent_friday.services import laya_settings as ls
from tests.unit.test_laya_personal import World

ROOT = pathlib.Path(__file__).resolve().parents[2]
PAGES = ["index.html", "ui_parts/app.html"]
MOVED = ["What Laya learns about you", "Instant answers (no model)", "Laya chat pilot",
         "Safe writes (off until you turn them on)"]
PANELS = ["LayaLearningPanel", "LayaReflexPanel", "LayaPilotPanel", "LayaSafeWritesPanel"]


@pytest.fixture
def w(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch)
    yield world
    world.close()


# ── teach her a phrase ───────────────────────────────────────────────────────

def test_a_taught_phrase_is_kept_as_the_owners_own_gold_correction(w):
    res = ls.teach("pull up my week", "query_calendar")
    assert res["ok"] and "pull up my week" not in res["text"]
    (row,) = w.rows()
    assert (row["kind"], row["weight"], row["source"], row["label"], row["text"]) == (
        "correction", "gold", "owner", "query_calendar", "pull up my week")


def test_just_talk_to_me_is_a_label_too(w):
    assert ls.teach("tell me about your day", "conversation")["ok"]
    assert w.rows()[0]["label"] == "conversation"


def test_a_sensitive_phrase_is_not_kept(w):
    res = ls.teach("check my salary review", "check_email")
    assert not res["ok"] and "sensitive" in res["why"] and w.rows() == []


def test_only_her_own_actions_can_be_taught(w):
    for label in ("send_email", "answer_card", "undo_action", "", "act"):
        assert not ls.teach("do the thing", label)["ok"], label
    assert w.rows() == []


def test_a_phrase_is_a_few_words(w):
    assert not ls.teach("hi", "query_calendar")["ok"]
    assert not ls.teach("x" * 201, "query_calendar")["ok"]
    assert w.rows() == []


def test_nothing_is_taught_while_learning_is_off_or_off_the_record(w, monkeypatch):
    lp.use_settings({"laya_personal_learning": False})
    lp._ls_cache.clear()                                 # the learning state is cached for a few seconds
    res = ls.teach("pull up my week", "query_calendar")
    assert not res["ok"] and "switched off" in res["why"] and w.rows() == []
    lp.use_settings({})
    lp._ls_cache.clear()
    monkeypatch.setattr(lp, "_off_record", lambda: True)
    assert not ls.teach("pull up my week", "query_calendar")["ok"] and w.rows() == []


def test_a_locked_vault_keeps_it_sealed_until_it_opens_and_says_so(w):
    w.key[0] = None                                      # locked: the staging key seals it
    res = ls.teach("pull up my week", "query_calendar")
    assert res["ok"] and "locked" in res["text"]
    w.key[0] = __import__("tests.unit.test_laya_personal", fromlist=["KEY"]).KEY
    assert [r["label"] for r in w.rows()] == ["query_calendar"]


def test_with_no_vault_at_all_nothing_is_claimed_kept(w, monkeypatch):
    monkeypatch.setattr(lp, "vault_state", lambda: "no_key")
    res = ls.teach("pull up my week", "query_calendar")
    assert not res["ok"] and "vault" in res["why"] and w.rows() == []


def test_the_labels_offered_are_the_ones_she_can_learn():
    ids = [l["id"] for l in ls.teach_labels()]
    assert ids[-1] == "conversation" and "query_calendar" in ids
    assert not {"answer_card", "undo_action", "task_control"} & set(ids)


@pytest.fixture
def client(monkeypatch):
    from agent_friday.routes import laya_settings as route
    monkeypatch.setattr("agent_friday.core._is_local_request", lambda: True)
    screen = [False]
    monkeypatch.setattr("agent_friday.services.screen_click.screen_session", lambda request: screen[0])
    seen = []
    monkeypatch.setattr(ls, "teach", lambda text, label: seen.append((text, label)) or {"ok": True, "text": "Taken."})
    app = Flask("t")

    @app.before_request
    def _who():
        g.friday_principal = "user"
    app.register_blueprint(route.laya_settings_bp)
    c = app.test_client()
    c.screen, c.seen = screen, seen
    return c


def test_teaching_takes_a_click_on_the_page(client):
    r = client.post("/api/laya/settings/teach", json={"text": "pull up my week", "label": "query_calendar"})
    assert r.status_code == 403 and client.seen == []
    client.screen[0] = True
    r = client.post("/api/laya/settings/teach", json={"text": "pull up my week", "label": "query_calendar"})
    assert r.status_code == 200 and client.seen == [("pull up my week", "query_calendar")]
    assert "pull up my week" not in r.get_data(as_text=True)
    assert client.post("/api/laya/settings/teach", json={"text": 3, "label": "x"}).status_code == 400


# ── the page ─────────────────────────────────────────────────────────────────

def _fn(page: str, name: str) -> str:
    text = (ROOT / page).read_text(encoding="utf-8")
    a = text.index("function %s(" % name)
    end = text.index("// End Laya tab", a) if name == "SettingsTabLaya" else text.index("\nfunction ", a + 10)
    return text[a:end]


@pytest.mark.parametrize("page", PAGES)
def test_the_laya_panels_moved_from_privacy_into_the_laya_tab(page):
    privacy, laya = _fn(page, "SettingsTabPrivacy"), _fn(page, "SettingsTabLaya")
    for title, panel in zip(MOVED, PANELS):
        assert title not in privacy and panel not in privacy, (page, title)
        assert ('title: "%s"' % title) in laya and ("E(%s" % panel) in laya, (page, title)
    assert "What needs your sign-off" in privacy, "the approval check stays with the other sign-off rules"


@pytest.mark.parametrize("page", PAGES)
def test_the_laya_tab_receives_the_settings_the_moved_panels_use(page):
    text = (ROOT / page).read_text(encoding="utf-8")
    assert "tab === 'laya' && /*#__PURE__*/React.createElement(SettingsTabLaya, { s: s, save: save })" in text


def test_reset_asks_first_and_sends_every_bar_back_to_shipped_in_shadow():
    p = _fn("index.html", "SettingsTabLaya")
    assert "'Reset my bars to how she shipped'" in p and "'Yes, reset my bars'" in p and "setResetAsk(true)" in p
    assert re.search(r"b\[r\.tool\] = null; \}\); post\('bars', \{bars: b, mode: 'shadow'\}, 'reset'\)", p)


def test_teach_picks_nothing_for_you_and_posts_to_its_own_route():
    p = _fn("index.html", "SettingsTabLaya")
    assert "useState('')" in p and "E('option', {value:''}, 'Pick what you mean')" in p
    assert "post('teach', {text: teachText, label: teachLabel}, 'teach')" in p
    assert "key: 'learn' + taught" in p, "the report card reloads after a phrase is taught"


def test_modes_point_at_the_section_that_changes_them():
    rows = {r["id"]: r for r in ls.modes({})}
    assert (rows["personal_learning"]["change_in"], rows["personal_learning"]["change_section"]) == (
        "laya", "What Laya learns about you")
    assert rows["safe_writes"]["change_section"] == "Safe writes (off until you turn them on)"
    assert (rows["approval_check"]["change_in"], rows["approval_check"]["change_section"]) == (
        "privacy", "What needs your sign-off")


def test_messages_name_the_new_place():
    src = (ROOT / "src/agent_friday/services/laya_personal.py").read_text(encoding="utf-8")
    assert "Privacy & Data > What Laya learns about you" not in src
    assert "Settings > Laya > What Laya learns about you" in src
