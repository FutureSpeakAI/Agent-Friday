"""Calendar quick add reads the model's event when the reply carries spilled
thinking (a local seat splits a reply at a literal `<tool_call>`).

The model call is stubbed with `patch_app`; nothing reaches a model or Google.
"""
from __future__ import annotations

import json


def test_quick_add_reads_the_answer_after_spilled_thinking(client, patch_app):
    answer = {"title": "Dinner with Sam", "start_time": "2031-03-04T19:00",
              "end_time": "2031-03-04T21:00", "location": ""}
    draft = {"title": "draft", "start_time": "2031-01-01T08:00"}
    reply = ("<tool_call>{\"name\": \"x\", ...}` came up while thinking.\n"
             "First guess: " + json.dumps(draft) + "\nNo.\n</think>\n\n" + json.dumps(answer))
    patch_app("_generate_text", lambda *a, **k: reply)
    resp = client.post("/api/calendar/quick-add", json={"text": "dinner with Sam sometime"})
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["status"] == "ok", body
    assert body["preview"]["start_time"].startswith("2031-03-04T19:00")
