"""Off the record says which connected services still keep their own records
(NS-15.8-3). Synthetic connection lists only; nothing here reaches a service.

The lines come from what is connected now (the checklist the privacy map
reads): a service that is not connected is never named, local-only routing
drops the cloud-model line, the line about what off the record cannot do is
always said, and no secret, address or account id ever appears.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_friday.services import off_record_external as ext

REPO = Path(__file__).resolve().parents[2]

# Built at run time so no key-shaped literal sits in the repository.
SECRET = "sk-" + "ant-" + "SYNTHETIC" + "0123456789abcdef" + "SECRET"
SID = "A" + "C" + "0123456789abcdef" * 2


def _item(id, group, label, status="connected", **extra):
    it = {"id": id, "group": group, "label": label, "status": status,
          "detail": "Key stored encrypted on this computer.", "permissions": [],
          "connect": {"kind": "secret", "fields": [{"key": "key", "secret": True, "value": SECRET}]}}
    it.update(extra)
    return it


def _everything(status="connected"):
    return [
        _item("google", "google", "Google: Gmail, Calendar, Drive, Contacts, Tasks", status),
        _item("provider:anthropic", "ai", "Anthropic", status),
        _item("provider:openai", "ai", "OpenAI", status),
        _item("twilio", "phone", "Phone (Twilio)", status, detail="Account " + SID),
        _item("channel:telegram", "messaging", "Telegram (talk to Friday)", status),
        _item("connector:slack", "messaging", "Slack", status),
        _item("connector:github", "developer", "GitHub", status),
        _item("platform:bluesky", "social", "Bluesky", status),
        _item("provider:brave", "search", "Brave Search", status),
        _item("provider:elevenlabs", "voice", "ElevenLabs", status),
        _item("mcp:weather", "mcp", "weather", status,
              connect={"kind": "mcp_server", "remote": True, "target": "https://user:" + SECRET + "@mcp.example"}),
    ]


CLOUD = {"model_routing": {"mode": "cloud_preferred"}}
LOCAL = {"model_routing": {"mode": "local_only"}}


def _keys(records):
    return [r["key"] for r in records]


def _text(records):
    return " ".join(r["line"] + " " + " ".join(r["services"]) for r in records)


def test_nothing_connected_names_no_service_but_still_says_the_limit():
    recs = ext.external_records([], CLOUD)
    assert _keys(recs) == ["web", "always"]
    assert recs[-1]["line"] == ext.ALWAYS
    for name in ("Gmail", "Google Calendar", "Anthropic", "Twilio", "Telegram", "Slack"):
        assert name not in _text(recs)


def test_services_that_are_not_connected_are_never_named():
    items = _everything(status="not_connected") + [
        _item("google", "google", "Google", "needs_attention"),
        _item("provider:anthropic", "ai", "Anthropic", "skipped")]
    recs = ext.external_records(items, CLOUD)
    assert _keys(recs) == ["web", "always"]


def test_each_connected_service_gets_its_own_plain_line():
    recs = ext.external_records(_everything(), CLOUD)
    keys = _keys(recs)
    for k in ("gmail", "google_calendar", "cloud_ai", "twilio", "channel:telegram",
              "connector:slack", "connector:github", "platform:bluesky", "search",
              "cloud_voice", "mcp", "web", "always"):
        assert k in keys, k
    by = {r["key"]: r for r in recs}
    assert "Gmail Sent folder" in by["gmail"]["line"]
    assert "Google Calendar" in by["google_calendar"]["line"]
    assert "Anthropic" in by["cloud_ai"]["line"] and "OpenAI" in by["cloud_ai"]["line"]
    assert "retention" in by["cloud_ai"]["line"]
    assert "Twilio" in by["twilio"]["line"]
    assert by["channel:telegram"]["services"] == ["Telegram"]
    assert "Telegram" in by["channel:telegram"]["line"] and "receives" in by["channel:telegram"]["line"]
    assert keys[-1] == "always"


def test_only_the_connected_one_is_named():
    items = _everything(status="not_connected")
    items[0]["status"] = "connected"            # Google only
    recs = ext.external_records(items, CLOUD)
    assert _keys(recs) == ["gmail", "google_calendar", "google_other", "web", "always"]
    assert "Anthropic" not in _text(recs) and "Twilio" not in _text(recs)


def test_local_only_routing_removes_the_cloud_line():
    cloud = ext.external_records(_everything(), CLOUD)
    local = ext.external_records(_everything(), LOCAL)
    assert "cloud_ai" in _keys(cloud)
    assert "cloud_ai" not in _keys(local)
    assert "cloud_voice" not in _keys(local)
    assert "Anthropic" not in _text(local)
    # The services that are not models are still said under local-only routing.
    assert "gmail" in _keys(local) and "twilio" in _keys(local)


def test_the_constant_line_is_always_last_whatever_is_connected():
    for items in ([], _everything(), _everything("not_connected")):
        for settings in (CLOUD, LOCAL, {}):
            recs = ext.external_records(items, settings)
            assert recs[-1] == {"key": "always", "services": [], "line": ext.ALWAYS}
    assert "cannot delete records other services keep" in ext.ALWAYS


def test_an_unreadable_connection_list_is_said_never_as_nothing(monkeypatch):
    monkeypatch.setattr(ext, "_items", lambda: None)
    recs = ext.external_records(None, CLOUD)
    assert _keys(recs) == ["unreadable", "web", "always"]
    v = ext.view(None, CLOUD)
    assert v["readable"] is False
    assert v["lines"][-1] == ext.ALWAYS


def test_no_secret_address_or_account_id_reaches_the_words():
    items = _everything()
    items.append(_item("connector:linear", "tools", "Linear for me@example.com " + SECRET))
    items.append(_item("custom:mine", "ai", "My endpoint https://10.0.0.5:8080/v1?key=" + SECRET))
    v = ext.view(items, CLOUD)
    blob = repr(v)
    assert SECRET not in blob
    assert SID not in blob
    assert "me@example.com" not in blob
    assert "https://" not in blob and "mcp.example" not in blob
    assert not re.search(r"\b(sk|AC|SK)[-_]?[A-Za-z0-9]{20,}", blob)


def test_the_short_banner_names_the_services_in_order():
    v = ext.view(_everything()[:2], CLOUD)          # Google and Anthropic
    assert v["short"] == ("Still recorded elsewhere: Gmail, Google Calendar, Google Drive, Google Tasks, "
                          "Google Contacts, Anthropic")
    assert v["lines"][-1] == ext.ALWAYS
    assert ext.view([], CLOUD)["short"] == ""


def _scheduled(allow=True, answered=True, **models):
    blk = {"allow": allow, "answered": answered,
           "heartbeat_model": "claude-haiku-4-5-20251001", "job_model": "claude-haiku-4-5-20251001"}
    blk.update(models)
    return {"model_routing": {"mode": "local_only"}, "scheduled_cloud": blk}


def test_local_only_still_names_scheduled_jobs_allowed_onto_a_cloud_model(monkeypatch):
    monkeypatch.setattr(ext, "_provider_of", lambda model: "Anthropic")
    recs = ext.external_records(_everything(), _scheduled())
    by = {r["key"]: r for r in recs}
    assert "cloud_ai" not in by                         # chat is still not sent to the cloud
    line = by["scheduled_cloud"]["line"]
    assert line.startswith("Scheduled jobs you allowed onto Anthropic/claude-haiku-4-5-20251001 are sent there")
    assert by["scheduled_cloud"]["services"] == ["Anthropic"]
    assert recs[-1]["line"] == ext.ALWAYS
    # Two models: both named.
    recs = ext.external_records([], _scheduled(job_model="gpt-4o-mini"))
    line = {r["key"]: r for r in recs}["scheduled_cloud"]["line"]
    assert "Anthropic/gpt-4o-mini" in line and "Anthropic/claude-haiku-4-5-20251001" in line


def test_scheduled_cloud_line_needs_an_answered_allow_and_local_only(monkeypatch):
    monkeypatch.setattr(ext, "_provider_of", lambda model: "Anthropic")
    assert "scheduled_cloud" not in _keys(ext.external_records([], _scheduled(allow=False)))
    assert "scheduled_cloud" not in _keys(ext.external_records([], _scheduled(answered=False)))
    assert "scheduled_cloud" not in _keys(ext.external_records(
        [], _scheduled(heartbeat_model="", job_model="")))
    assert "scheduled_cloud" not in _keys(ext.external_records([], LOCAL))
    # Not local-only: the general cloud line covers it, no separate line.
    cloud = dict(_scheduled(), model_routing={"mode": "cloud_preferred"})
    assert "scheduled_cloud" not in _keys(ext.external_records(_everything(), cloud))


def test_custom_endpoint_on_this_pc_or_local_network_is_not_named():
    items = [_item("custom:lan", "ai", "Home box", base_url="http://192.168.1.20:8080/v1"),
             _item("custom:here", "ai", "Here", base_url="http://localhost:1234/v1"),
             _item("custom:far", "ai", "Far service", base_url="https://api.far.example/v1")]
    recs = ext.external_records(items, CLOUD, tunnel=False)
    by = {r["key"]: r for r in recs}
    assert by["cloud_ai"]["services"] == ["Far service"]
    assert "Home box" not in _text(recs) and "Here" not in by["cloud_ai"]["line"]


def test_google_names_drive_only_when_the_connection_covers_it():
    with_drive = ext.external_records([_item("google", "google", "Google: Gmail, Calendar, Drive")],
                                      CLOUD, tunnel=False)
    other = {r["key"]: r for r in with_drive}["google_other"]
    assert "Google Drive" in other["services"] and "Drive" in other["line"]
    by_scope = ext.external_records([_item("google", "google", "Google", permissions=[
        {"text": "See your files", "scope": "https://www.googleapis.com/auth/drive.file"}])],
        CLOUD, tunnel=False)
    assert "Google Drive" in {r["key"]: r for r in by_scope}["google_other"]["services"]
    without = ext.external_records([_item("google", "google", "Google: Gmail, Calendar")],
                                   CLOUD, tunnel=False)
    other = {r["key"]: r for r in without}["google_other"]
    assert "Google Drive" not in other["services"] and "Drive" not in other["line"]


def test_a_running_cloudflare_tunnel_is_named():
    assert "cloudflare_tunnel" not in _keys(ext.external_records([], CLOUD, tunnel=False))
    recs = ext.external_records([], CLOUD, tunnel=True)
    by = {r["key"]: r for r in recs}
    assert by["cloudflare_tunnel"]["services"] == ["Cloudflare"]
    assert by["cloudflare_tunnel"]["line"] == ext.TUNNEL
    assert recs[-1]["line"] == ext.ALWAYS
    # A Cloudflare item reported connected says the same.
    recs = ext.external_records([_item("cloudflare", "network", "Cloudflare (tunnels)")], CLOUD, tunnel=False)
    assert "cloudflare_tunnel" in _keys(recs)
    # Shown but not connected (set up outside Friday): not named.
    recs = ext.external_records([_item("cloudflare", "network", "Cloudflare (tunnels)", "external")],
                                CLOUD, tunnel=False)
    assert "cloudflare_tunnel" not in _keys(recs)


def test_the_privacy_map_builds_the_checklist_once(monkeypatch):
    from agent_friday.services import privacy_map, setup_connections
    calls = []

    def checklist():
        calls.append(1)
        return {"items": _everything()[:2]}
    monkeypatch.setattr(setup_connections, "checklist", checklist)
    monkeypatch.setattr(ext, "_settings", lambda: CLOUD)
    out = privacy_map.view()
    assert len(calls) == 1
    assert out["services"]["readable"] is True
    assert any("Gmail Sent folder" in line for line in out["off_record"]["external"]["lines"])


def test_the_privacy_map_carries_the_same_lines(monkeypatch):
    from agent_friday.services import privacy_map
    monkeypatch.setattr(ext, "_items", lambda: _everything()[:2])
    monkeypatch.setattr(ext, "_settings", lambda: CLOUD)
    out = privacy_map.off_record()
    assert out["external"]["lines"][-1] == ext.ALWAYS
    assert any("Gmail Sent folder" in line for line in out["external"]["lines"])


# ── the UI: identical in both copies ─────────────────────────────────────────

SOURCES = ("index.html", "ui_parts/app.html")


def _src(name):
    return (REPO / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def _component(text):
    start = text.index("const FRIDAY_OFF_RECORD_ELSEWHERE_TITLE")
    end = text.index("\n}\n", text.index("function FridayOffRecordElsewhere(")) + 3
    return text[start:end]


@pytest.mark.parametrize("source", SOURCES)
def test_the_ui_reads_the_route_and_shows_the_short_line_with_an_expand(source):
    text = _src(source)
    comp = _component(text)
    assert "const FRIDAY_OFF_RECORD_ELSEWHERE_TITLE = 'Off the record does not cover';" in comp
    assert "apiFetch('/api/off-record/external')" in comp
    assert "React.createElement('details'" in comp and "React.createElement('summary'" in comp
    assert "d.short" in comp and "d.lines" in comp
    # Beside the off-the-record notice, and under the Settings switch, only while it is on.
    assert re.search(r"fridayOffRecordFrom\(agentSettings\)\s*&&\s*React\.createElement\(FridayOffRecordElsewhere, null\)", text)
    assert re.search(r"fridayOffRecordFrom\(s\)\s*&&\s*React\.createElement\(FridayOffRecordElsewhere, null\)", text)
    # And on the privacy map, as its own section.
    body = text[text.index("function SettingsPrivacyMap("):]
    body = body[:body.index("\n}\n")]
    assert "E('h4', { className: 'pm-h' }, 'Off the record does not cover')" in body
    assert "d.off_record.external.lines" in body


def test_the_shared_component_and_map_section_are_identical_in_both_copies():
    a, b = (_src(s) for s in SOURCES)
    assert _component(a) == _component(b)

    def map_section(t):
        i = t.index("d.off_record && d.off_record.external && E('h4'")
        return t[i:t.index("\n", t.index("d.off_record && d.off_record.external && E('ul'", i))]
    assert map_section(a) == map_section(b)
