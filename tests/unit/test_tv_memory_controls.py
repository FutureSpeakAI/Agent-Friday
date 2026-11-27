"""TV-P1 controls: the TV memory setting on owner devices (gateway), by voice (tv_memory tool), on the PC's
Settings page (owner_action click), and through whole-file writes (clamp). Widening is an owner action only."""
from pathlib import Path

import pytest

from agent_friday.services import device_pairing
from agent_friday.services import tv_memory_access as tma
from agent_friday.services import tv_memory_tools as tools
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household
from tests.unit.test_tv_gateway import FakeTv, auth, open_session

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    box = {"s": {"tv_memory_access": "none"}, "writes": [], "changes": []}
    monkeypatch.setattr(household, "_path", lambda: tmp_path / "household.json")
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    monkeypatch.setattr("agent_friday.core._load_settings", lambda: dict(box["s"]))

    def write(mode, owner_action=True):
        box.setdefault("owner", []).append(owner_action)
        box["writes"].append(mode)
        box["s"]["tv_memory_access"] = mode
        return mode
    monkeypatch.setattr(tma, "write", write)
    from agent_friday.services import setting_proposals as sp
    monkeypatch.setattr(sp, "record_change", lambda tool, **k: box["changes"].append((tool, k["old"], k["new"])))
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    household.ensure_default_members()
    yield box
    device_pairing._reset_for_tests()


@pytest.fixture
def client():
    return gateway.create_app().test_client()


def paired(kind, member):
    dev = FakeTv()
    device_pairing.add_device(dev.spki, name="A device", kind=kind)
    if member:
        household.bind_device(dev.id, member)
    return dev


# ── owner devices ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["phone", "tablet"])
def test_an_owner_device_reads_and_changes_it(client, _clean, kind):
    dev = paired(kind, household.OWNER_ID)
    h = auth(open_session(client, dev))
    body = client.get("/tv/v1/tv-memory", headers=h).get_json()
    assert body["mode"] == "none" and body["can_change"] is True
    assert [o["mode"] for o in body["options"]] == ["all", "shareable", "none"]
    r = client.post("/tv/v1/tv-memory", json={"mode": "All my memories"}, headers=h)
    assert r.status_code == 200 and r.get_json()["mode"] == "all"
    assert _clean["writes"] == ["all"]
    assert _clean["changes"] == [("tv_memory", "None", "All my memories")]


@pytest.mark.parametrize("kind,member", [("tv", household.FAMILY_ID), ("phone", household.FAMILY_ID), ("phone", None)])
def test_a_tv_or_a_family_device_reads_it_but_cannot_change_it(client, _clean, kind, member):
    dev = paired(kind, member)
    h = auth(open_session(client, dev))
    assert client.get("/tv/v1/tv-memory", headers=h).get_json()["can_change"] is False
    r = client.post("/tv/v1/tv-memory", json={"mode": "all"}, headers=h)
    assert r.status_code == 403
    assert _clean["writes"] == []


def test_a_word_that_is_not_an_option_is_a_400_and_changes_nothing(client, _clean):
    h = auth(open_session(client, paired("phone", household.OWNER_ID)))
    assert client.post("/tv/v1/tv-memory", json={"mode": "most"}, headers=h).status_code == 400
    assert _clean["writes"] == []


# ── by voice ────────────────────────────────────────────────────────────────

def test_status_says_how_it_is_set(_clean):
    assert "None" in tools.handle_tv_memory({"mode": "status"})


def test_turning_it_up_by_voice_raises_a_card_and_writes_nothing(_clean, monkeypatch):
    from agent_friday.services import setting_proposals as sp
    asked = []
    monkeypatch.setattr(sp, "hold", lambda tool, inp, **k: asked.append((tool, k["old"], k["new"])) or "SETTING_NEEDS_YES: a card")
    said = tools.handle_tv_memory({"mode": "all"})
    assert said.startswith("SETTING_NEEDS_YES")
    assert asked == [("tv_memory", "None", "All my memories")]
    assert _clean["writes"] == []


def test_the_cards_yes_writes_it(_clean, monkeypatch):
    from agent_friday.services import setting_proposals as sp
    monkeypatch.setattr(sp, "hold", lambda tool, inp, **k: None)        # the approved card being carried out
    assert tools.handle_tv_memory({"mode": "all"}).startswith("Done")
    assert _clean["writes"] == ["all"]


def test_turning_it_down_by_voice_needs_no_card(_clean, monkeypatch):
    from agent_friday.services import setting_proposals as sp
    _clean["s"]["tv_memory_access"] = "all"
    monkeypatch.setattr(sp, "hold", lambda *a, **k: pytest.fail("narrowing raised a card"))
    assert tools.handle_tv_memory({"mode": "none"}).startswith("Done")
    assert _clean["writes"] == ["none"]
    assert _clean["owner"] == [False], "a narrowing is written without the owner flag, so core re-checks it"


def test_set_setting_routes_the_row_to_the_tool():
    from agent_friday.services import setting_proposals as sp
    assert sp._route("settings.tv.memory_access", "none") == ("tv_memory", {"mode": "none"})
    assert "tv_memory" in sp.SETTINGS_TOOLS
    assert sp.match_row("tv memory")[0] == "settings.tv.memory_access"


def test_the_tool_is_registered_and_internal():
    from agent_friday.governance import action_gate
    from agent_friday.services import agent
    assert "tv_memory" in agent.CLAUDE_TOOL_HANDLERS
    assert action_gate.classify("tv_memory", {"mode": "none"})[0] == action_gate.INTERNAL


# ── the PC's Settings page and whole-file writes ────────────────────────────

def _post(monkeypatch, body, click):
    from flask import Flask
    from agent_friday.routes import core_routes as cr
    from agent_friday.services import screen_click
    saved = {}
    monkeypatch.setattr(cr, "_save_settings", lambda data, **kw: saved.update(data=data, kw=kw) or dict(data))
    monkeypatch.setattr(screen_click, "screen_session", lambda req: click)
    with Flask(__name__).test_request_context("/api/settings", method="POST", json=body):
        resp = cr.api_settings()
    resp, status = (resp if isinstance(resp, tuple) else (resp, 200))
    return status, saved


def test_the_settings_route_needs_the_click_for_the_owner_action(monkeypatch):
    body = {"owner_action": "tv_memory_access", "settings": {"tv_memory_access": "all"}}
    status, saved = _post(monkeypatch, body, click=True)
    assert status == 200 and saved["kw"]["owner_tv_memory_change"] is True
    status, saved = _post(monkeypatch, body, click=False)
    assert status == 403 and not saved
    status, saved = _post(monkeypatch, {"settings": {"tv_memory_access": "all"}}, click=True)
    assert status == 200 and saved["kw"]["owner_tv_memory_change"] is False


def test_a_whole_file_write_cannot_turn_it_up():
    from agent_friday.services import guarded_settings as gs
    assert gs.clamp({"tv_memory_access": "all"}, {"tv_memory_access": "none"})["tv_memory_access"] == "none"
    assert gs.clamp({"tv_memory_access": "none"}, {"tv_memory_access": "all"})["tv_memory_access"] == "none"
    assert gs.clamp({"tv_memory_access": "all"}, {})["tv_memory_access"] == "none", "no current value: not above none"
    assert gs.clamp({}, {"tv_memory_access": "none"})["tv_memory_access"] == "none", "dropping the key keeps it"
    assert "tv_memory_access" not in gs.clamp({}, {}), "a key neither side carries stays absent"
    assert gs.clamp({"tv_memory_access": "junk"}, {})["tv_memory_access"] == "none"


def test_the_settings_control_is_in_both_ui_sources_and_sends_its_owner_action():
    for name in ("index.html", "ui_parts/app.html"):
        src = (REPO / name).read_text(encoding="utf-8")
        assert "owner_action: 'tv_memory_access'" in src, name
        assert 'settingKey: "settings.tv.memory_access"' in src, name
        for label in ("All my memories", "Only ones I mark shareable with the TV"):
            assert label in src, name
        assert "React.createElement(TvMemoryPanel" in src, name
