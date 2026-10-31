"""A suppression rule that cannot be saved, read or removed answers with an error
id, never with the exception text, which can carry a folder path (CodeQL
py/stack-trace-exposure 1262-1266)."""
from __future__ import annotations

PRIVATE = r"C:\Users\someone\private-folder\suppress_rules.json"


def test_the_suppressed_list_that_cannot_be_read_shows_an_error_id(client, monkeypatch):
    from agent_friday.services import memory_suppress as ms

    def broken():
        raise OSError("cannot open %s" % PRIVATE)

    monkeypatch.setattr(ms, "summary", broken)
    r = client.get("/api/core-memory/suppressed")
    body = r.get_data(as_text=True)
    assert "private-folder" not in body, body
    assert "error_id" in r.get_json(), body


def test_a_rule_that_cannot_be_saved_is_reported_by_id_not_by_path(monkeypatch):
    from agent_friday.routes._errors import public_result
    from agent_friday.services import memory_suppress as ms

    def broken(_data):
        raise OSError("disk full writing %s" % PRIVATE)

    monkeypatch.setattr(ms, "_save", broken)
    out = ms.add_rule("weekly pottery class on thursdays", kind="remove", origin="card.test")
    assert out["ok"] is False
    safe = public_result(out, "Couldn't record that")
    assert "private-folder" not in str(safe), safe
    assert safe.get("error_id"), safe
