"""The AI-history import route (routes/ai_history_import.py): preview, stage, then answer.

A synthetic Claude export is uploaded the way the page does (multipart). Staging saves no
conversation; only the owner's press on this PC accepts one (routes/import_staging). On the
old tree the route does not exist, so these fail by assertion on the status code.
"""
from __future__ import annotations

import io
import json

from tests.desk_press_support import desk_headers, page_headers

EXPORT = [{"uuid": "route-k-1", "name": "Depot story", "created_at": "2026-03-01T09:00:00Z",
           "chat_messages": [{"uuid": "a", "sender": "human", "text": "When did the depot close?",
                              "created_at": "2026-03-01T09:00:00Z"}]}]


def _upload(client, preview=False, headers=None):
    data = {"file": (io.BytesIO(json.dumps(EXPORT).encode("utf-8")), "conversations.json")}
    if preview:
        data["preview"] = "1"
    return client.post("/api/import/ai-history", data=data, content_type="multipart/form-data",
                       headers=headers or page_headers())


def _clear(home):
    d = home / "wiki" / "_staging"
    if d.exists():
        for p in d.glob("imp-claude-*"):
            p.unlink()


def test_preview_counts_and_stages_nothing(client):
    import agent_friday.core as core
    from pathlib import Path
    _clear(Path(core.FRIDAY_DIR))
    r = _upload(client, preview=True)
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d["preview"] is True and d["conversations"] == 1 and d["service"] == "Claude"
    listed = client.get("/api/import-staging?source=claude", headers=page_headers()).get_json()
    assert listed["items"] == []


def test_stage_then_only_a_press_on_this_pc_saves_it(client):
    import agent_friday.core as core
    from pathlib import Path
    from agent_friday.services import conversations as cv
    _clear(Path(core.FRIDAY_DIR))
    r = _upload(client)
    assert r.status_code == 200 and r.get_json()["staged"] == 1
    assert not [c for c in cv.list_all() if (c.get("imported") or {}).get("conversation_id") == "route-k-1"]
    item = client.get("/api/import-staging?source=claude", headers=page_headers()).get_json()["items"][0]
    refused = client.post("/api/import-staging/review", json={"id": item["id"], "answer": "accept"},
                          headers=page_headers())
    assert refused.status_code == 403
    ok = client.post("/api/import-staging/review", json={"id": item["id"], "answer": "accept"},
                     headers=desk_headers())
    assert ok.status_code == 200, ok.get_json()
    saved = [c for c in cv.list_all() if (c.get("imported") or {}).get("conversation_id") == "route-k-1"]
    assert len(saved) == 1 and saved[0]["title"] == "Claude: Depot story"
    import shutil
    shutil.rmtree(cv._dir(saved[0]["id"]), ignore_errors=True)
    _clear(Path(core.FRIDAY_DIR))


def test_a_second_upload_while_one_is_read_is_refused_before_reading(client):
    from agent_friday.services import ai_history_import as aih
    assert aih._ONE_AT_A_TIME.acquire(blocking=False)
    try:
        r = _upload(client, preview=True)
    finally:
        aih._ONE_AT_A_TIME.release()
    assert r.status_code == 409 and r.get_json()["error"] == aih.BUSY


def test_an_upload_over_the_limit_is_refused_in_words(client, monkeypatch):
    from agent_friday.services import ai_history_import as aih
    monkeypatch.setattr(aih, "MAX_FILE_BYTES", 16)
    r = _upload(client, preview=True)
    assert r.status_code in (400, 413)
    body = r.get_json(silent=True) or {}
    assert body.get("ok") is False and body.get("error")


def test_a_file_that_is_not_an_export_is_a_400_with_words(client):
    r = client.post("/api/import/ai-history",
                    data={"file": (io.BytesIO(b"hello"), "notes.txt")},
                    content_type="multipart/form-data", headers=page_headers())
    assert r.status_code == 400 and r.get_json()["error"]
