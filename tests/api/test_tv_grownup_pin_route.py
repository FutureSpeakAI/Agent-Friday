"""Setting the TV's grown-up PIN at the PC: a press on Friday's own page, never a script, a tunnel or the LAN.

The PIN is what the PC checks before it opens a grown-up window for the household TV (services/tv_grownup_window),
and a window is what lets a TV turn use the owner's memories (TV-P1). So setting it is the owner's act on this
PC's own screen, the same proof as opening pairing. Reading whether one is set needs no press and never shows it.
"""
from pathlib import Path

import pytest

from tests.desk_press_support import desk_headers, page_headers

PIN = "4821"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _scratch_verifier(tmp_path, monkeypatch):
    from agent_friday.services import action_ledger
    from agent_friday.services import tv_grownup_window as win
    monkeypatch.setattr(win, "_path", lambda: tmp_path / "tv_grownup_pin.json")
    monkeypatch.setattr(action_ledger, "append", lambda kind, rec: None)
    win._reset_for_tests()
    yield win
    win._reset_for_tests()


def test_no_pin_reads_as_not_set(client):
    r = client.get("/api/tv-grownup-pin")
    assert r.status_code == 200 and r.get_json()["has_pin"] is False


def test_a_plain_request_cannot_set_it(client, _scratch_verifier):
    r = client.post("/api/tv-grownup-pin", json={"pin": PIN})
    assert r.status_code == 403 and r.get_json()["code"] == "tv_pin_needs_press"
    assert not _scratch_verifier.has_pin()


def test_a_page_without_this_pcs_desk_key_cannot_set_it(client, _scratch_verifier):
    r = client.post("/api/tv-grownup-pin", json={"pin": PIN}, headers=page_headers())
    assert r.status_code == 403 and not _scratch_verifier.has_pin()


def test_a_press_through_a_tunnel_or_forward_cannot_set_it(client, _scratch_verifier):
    headers = dict(desk_headers(), **{"X-Forwarded-For": "203.0.113.9"})
    r = client.post("/api/tv-grownup-pin", json={"pin": PIN}, headers=headers)
    assert r.status_code == 403 and not _scratch_verifier.has_pin()


def test_a_press_on_this_pcs_page_sets_it_and_never_echoes_it(client, _scratch_verifier):
    r = client.post("/api/tv-grownup-pin", json={"pin": PIN}, headers=desk_headers())
    assert r.status_code == 200, r.data
    assert r.get_json()["has_pin"] is True and PIN not in r.get_data(as_text=True)
    assert _scratch_verifier.has_pin() and _scratch_verifier._matches(PIN)
    got = client.get("/api/tv-grownup-pin")
    assert got.get_json()["has_pin"] is True and PIN not in got.get_data(as_text=True)


@pytest.mark.parametrize("bad", ["", "12", "abcd", "1" * 13])
def test_a_pin_that_is_not_4_to_12_digits_is_a_400_and_changes_nothing(client, _scratch_verifier, bad):
    r = client.post("/api/tv-grownup-pin", json={"pin": bad}, headers=desk_headers())
    assert r.status_code == 400 and r.get_json()["code"] == "bad_pin"
    assert not _scratch_verifier.has_pin()


def test_the_settings_control_is_in_both_ui_sources():
    for rel in ("index.html", "ui_parts/app.html"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "function TvGrownupPinPanel(" in src, rel
        assert "'/api/tv-grownup-pin'" in src, rel
        assert "React.createElement(TvGrownupPinPanel" in src, rel
