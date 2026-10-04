"""The calendar's one-line day note leaves room for a thinking model.

On a thinking model the reasoning counts toward max_tokens. A budget sized
for the sentence alone comes back with no text, and that empty note was then
cached for the whole day.
"""
import datetime

import agent_friday.services.calendar_engine as cal
import agent_friday.services.model_router as mr

EVENTS = [{"title": "Dentist", "type": "normal"}]


def _run(monkeypatch, reply):
    seen, saved = {}, {}

    def fake_generate(messages, system=None, **kw):
        seen.update(kw)
        return reply

    monkeypatch.setattr(mr, "_generate_text", fake_generate)
    monkeypatch.setattr(mr, "_get_friday_system_prompt", lambda **kw: "")
    monkeypatch.setattr(mr, "_predict_route_provider", lambda **kw: "cloud")
    monkeypatch.setattr(mr, "_gated_vault_control", lambda: None)
    monkeypatch.setattr(cal, "_load_json_dict", lambda path: {})
    monkeypatch.setattr(cal, "_save_json_dict", lambda path, data: saved.update(data))
    note = cal._day_annotation(datetime.date(2031, 3, 12), EVENTS)
    return note, seen, saved


def test_the_budget_leaves_room_for_thinking(monkeypatch):
    note, seen, _ = _run(monkeypatch, "A quick dentist stop, then the day is yours.")
    assert seen["max_tokens"] >= 1024
    assert note.startswith("A quick dentist stop")


def test_an_empty_reply_is_never_cached_as_the_note(monkeypatch):
    note, _, saved = _run(monkeypatch, "")
    assert note, "an empty reply became the day's note"
    assert all(saved.values()), saved
