"""The Front Page's failure path — the part the user was never shown.

Between 2026-09-19 and 2026-09-22 every edition written to
`~/.friday/front_pages/` was the un-curated fallback: nine consecutive, the
last genuinely curated one being 2026-09-18 morning. Each was written to
disk, each pushed the same cheerful "📰 Friday's Front Page — Morning
edition" notification, and the page rendered identically to a curated one.
Stephen found out by refreshing localhost.

`_editorialize_front_page` had two exits — `if not isinstance(data, dict):
return fallback` and a bare `except Exception: return fallback` — and neither
logged, recorded, or said anything. These tests pin that both now return the
fallback MARKED, that the mark reaches the stored edition and the
notification, and that a failed re-run cannot overwrite a good edition.

The model is stubbed at `_generate_text`, which is the seam the real failure
came through: the loop handed back the string
"[Agent hit max tool iterations without completing.]" and `_extract_json_block`
could not parse it.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import news_engine as ne


_POOL = [
    {"category": "AI/Tech", "source": "example.com", "score": 9,
     "title": "A thing happened", "snippet": "details", "url": "https://a/1",
     "color": "tech"},
    {"category": "Politics", "source": "example.org", "score": 7,
     "title": "Another thing", "snippet": "more", "url": "https://a/2",
     "color": "politics"},
]

# The literal reply the live run produced, from orb openai-e8f20a1d.
_THE_REPLY = "[Agent hit max tool iterations without completing.]"


@pytest.fixture
def quiet_prompt(monkeypatch):
    """Keep the vault/persona prompt out of these tests."""
    monkeypatch.setattr(ne, "_get_friday_system_prompt", lambda **kw: "sys")
    monkeypatch.setattr(ne, "_predict_route_provider", lambda **kw: "local")
    monkeypatch.setattr(ne, "_gated_vault_control", lambda: None)
    monkeypatch.setattr(ne, "_answering_model", lambda: "bonsai2:27b")


def test_an_unparsable_reply_is_marked_degraded_and_quoted(quiet_prompt, monkeypatch, caplog):
    monkeypatch.setattr(ne, "_generate_text", lambda *a, **k: _THE_REPLY)

    with caplog.at_level("WARNING"):
        ed = ne._editorialize_front_page(_POOL, slot="morning")

    deg = ed["degraded"]
    assert deg["model"] == "bonsai2:27b"
    assert isinstance(deg["seconds"], float)
    # The evidence survives: whoever reads this can see WHAT came back.
    assert _THE_REPLY in deg["detail"]
    assert "JSON" in deg["reason"]
    # And it was said out loud exactly once.
    assert sum("front page" in r.message for r in caplog.records) == 1


def test_a_provider_that_refused_is_marked_degraded_with_its_reason(quiet_prompt, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("No model provider could generate text (tried local: down)")
    monkeypatch.setattr(ne, "_generate_text", _boom)

    ed = ne._editorialize_front_page(_POOL, slot="morning")

    assert ed["degraded"]["reason"] == "the editorial call failed"
    assert "No model provider" in ed["degraded"]["detail"]


def test_an_empty_pool_is_degraded_too_not_quietly_normal(quiet_prompt):
    ed = ne._editorialize_front_page([], slot="morning")
    assert ed["degraded"]["reason"] == "no candidate stories"


def test_a_good_reply_carries_no_mark_at_all(quiet_prompt, monkeypatch):
    monkeypatch.setattr(ne, "_generate_text", lambda *a, **k: json.dumps({
        "lead_index": 1, "lead_note": "because it matters to you",
        "headline": "Two Things Happened",
        "section_context": {"AI/Tech": "the week in models"},
    }))

    ed = ne._editorialize_front_page(_POOL, slot="morning")

    assert "degraded" not in ed
    assert ed["headline"] == "Two Things Happened"
    assert ed["lead_index"] == 1


def test_the_output_budget_now_exceeds_the_1800_that_truncated_it(quiet_prompt, monkeypatch):
    """1,800 was the ceiling the live seat spent entirely on reasoning."""
    seen = {}

    def _capture(*a, **k):
        seen.update(k)
        return _THE_REPLY
    monkeypatch.setattr(ne, "_generate_text", _capture)

    ne._editorialize_front_page(_POOL, slot="morning")
    assert seen["max_tokens"] > 1800


# ── The mark has to reach the things a person looks at ──────────────────────

def test_the_stored_edition_says_it_is_not_curated(quiet_prompt, monkeypatch, tmp_path):
    monkeypatch.setattr(ne, "FRONT_PAGES_DIR", tmp_path)
    monkeypatch.setattr(ne, "_gather_front_page_pool", lambda: (list(_POOL), {}))
    monkeypatch.setattr(ne, "_generate_text", lambda *a, **k: _THE_REPLY)

    edition = ne._generate_front_page("morning")

    assert edition["editorial_status"]["state"] == "degraded"
    on_disk = json.loads((tmp_path / f"{edition['id']}.json").read_text(encoding="utf-8"))
    assert on_disk["editorial_status"]["model"] == "bonsai2:27b"


def test_a_failed_rerun_does_not_eat_the_curated_edition(quiet_prompt, monkeypatch, tmp_path):
    """Click Generate on a good edition, have the seat fail, keep the good one."""
    monkeypatch.setattr(ne, "FRONT_PAGES_DIR", tmp_path)
    monkeypatch.setattr(ne, "_gather_front_page_pool", lambda: (list(_POOL), {}))

    monkeypatch.setattr(ne, "_generate_text", lambda *a, **k: json.dumps({
        "lead_index": 0, "lead_note": "the note worth keeping",
        "headline": "Kept", "section_context": {"AI/Tech": "context"}}))
    good = ne._generate_front_page("morning")
    assert good["editorial_status"]["state"] == "curated"

    monkeypatch.setattr(ne, "_generate_text", lambda *a, **k: _THE_REPLY)
    after = ne._generate_front_page("morning")

    assert after["headline"] == "Kept"
    assert after["lead"]["editorial_note"] == "the note worth keeping"
    on_disk = json.loads((tmp_path / f"{good['id']}.json").read_text(encoding="utf-8"))
    assert on_disk["headline"] == "Kept"
    # …and it is honest about the re-run that did not land.
    assert on_disk["editorial_status"]["kept_through_failed_rerun"]["reason"]


def test_a_better_rerun_still_replaces_a_degraded_edition(quiet_prompt, monkeypatch, tmp_path):
    """The guard must not freeze a bad edition in place."""
    monkeypatch.setattr(ne, "FRONT_PAGES_DIR", tmp_path)
    monkeypatch.setattr(ne, "_gather_front_page_pool", lambda: (list(_POOL), {}))

    monkeypatch.setattr(ne, "_generate_text", lambda *a, **k: _THE_REPLY)
    ne._generate_front_page("morning")

    monkeypatch.setattr(ne, "_generate_text", lambda *a, **k: json.dumps({
        "lead_index": 0, "lead_note": "now with an editor", "headline": "Fixed",
        "section_context": {"AI/Tech": "context"}}))
    after = ne._generate_front_page("morning")

    assert after["headline"] == "Fixed"
    assert after["editorial_status"] == {"state": "curated"}


class _Notif:
    def __init__(self):
        self.pushed = []

    def push(self, **kw):
        self.pushed.append(kw)


def test_the_notification_says_the_editor_did_not_answer(monkeypatch):
    n = _Notif()
    monkeypatch.setattr(ne, "_notif_engine", n)

    ne._notify_front_page({
        "id": "2026-09-22-morning", "headline": "Your Front Page",
        "lead": {"title": "A thing happened"},
        "editorial_status": {"state": "degraded", "reason": "the editor's reply "
                             "was not the JSON it was asked for",
                             "model": "bonsai2:27b", "seconds": 1741.0},
    }, "morning")

    got = n.pushed[0]
    assert "without the editor" in got["title"]
    assert "bonsai2:27b" in got["body"]
    assert "1741s" in got["body"]
    assert got["priority"] == "high"
    # Still links somewhere real, and only to things the panel can do.
    assert got["actions"] == [{"label": "View Front Page", "workspace": "news",
                               "tab": "frontpage"}]


def test_a_curated_edition_still_gets_the_ordinary_notice(monkeypatch):
    n = _Notif()
    monkeypatch.setattr(ne, "_notif_engine", n)

    ne._notify_front_page({
        "id": "2026-09-22-morning", "headline": "Two Things Happened",
        "lead": {"title": "A thing happened"},
        "editorial_status": {"state": "curated"},
    }, "morning")

    got = n.pushed[0]
    assert got["title"] == "📰 Friday's Front Page — Morning edition"
    assert got["priority"] == "medium"
