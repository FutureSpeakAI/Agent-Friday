"""Three read-only answers for the phone's Settings and avatar: ``/tv/v1/evolution``, ``/tv/v1/providers``, ``/tv/v1/connectors``.

Rules guarded here, each with a case that fails if the check is removed:
* the same door as every owner read: only an owner-bound phone or tablet holding ``pc_read`` is answered; a device without
  the right makes the PC ask on its screen; a watch, a TV, a family or an unbound device is refused and nothing reaches the
  desktop;
* ``/evolution`` is the desktop's own answer cut down to whole numbers and one short name: anything else it sends (the
  first-launch date, a path) is dropped, and an answer that is not plainly that shape is a 503, never a guess;
* ``/connectors`` carries id, label, kind and connected for each row and nothing from the desktop's setup fields, secrets,
  tools or detail text; it never claims a per-device switch (``device_control`` is false);
* ``/providers`` carries names and a yes or no: no key, no URL, no header, no environment variable name; a provider that is
  switched off is not listed;
* a desktop failure is a plain 503.
"""
import json

import pytest

from agent_friday.routing import provider_descriptors as pd
from agent_friday.services import credential_store, provider_registry
from tests.unit import test_tv_pc_read as shared
from tests.unit.test_tv_pc_read import get, paired

client = shared.client      # shared fixtures bound by name: no lint suppression (CODE_QUALITY Q3)
desk = shared.desk
_clean = shared._clean

EVOLUTION = {"day": 6, "structure": "DAY 6: VON NEUMANN TESSERACT", "structure_index": 5, "calendar_index": 1,
             "preferred_scene_index": 5, "first_launch": "2026-11-12"}


def answer(desk, payload, status=200):
    desk.status, desk.body = status, json.dumps(payload).encode()


# ── who ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

ROUTES = ["/tv/v1/evolution", "/tv/v1/providers", "/tv/v1/connectors"]


@pytest.mark.parametrize("path", ROUTES)
def test_a_session_is_needed(client, desk, path):
    assert client.get(path).status_code == 401 and desk.calls == []


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("kind,member", [("watch", "stephen"), ("tv", "stephen"), ("phone", "family"), ("phone", None)])
def test_only_an_owner_bound_phone_or_tablet_is_answered(client, desk, path, kind, member):
    answer_ = get(client, paired(kind, member, right=False), path)
    assert answer_.status_code == 403 and desk.calls == []


@pytest.mark.parametrize("path", ROUTES)
def test_without_the_read_right_the_pc_asks_and_nothing_reaches_the_desktop(client, desk, path):
    answer_ = get(client, paired(right=False), path)
    assert answer_.status_code == 403 and answer_.get_json()["code"] == "needs_right" and desk.calls == []


@pytest.mark.parametrize("kind", ["phone", "tablet"])
@pytest.mark.parametrize("path", ROUTES)
def test_a_phone_or_tablet_with_the_right_is_answered(client, desk, path, kind, monkeypatch):
    answer(desk, EVOLUTION if path.endswith("evolution") else {"connectors": []})
    monkeypatch.setattr(provider_registry, "get_provider_registry", lambda: type("R", (), {"list_providers": lambda self: []})())
    assert get(client, paired(kind), path).status_code == 200


# ── evolution ───────────────────────────────────────────────────────────────────────────────────────────────────────

def test_evolution_is_the_desktops_shape_and_nothing_more(client, desk):
    answer(desk, {**EVOLUTION, "secret_path": "D:\\Data\\x"})
    body = get(client, paired(), "/tv/v1/evolution").get_json()
    assert body == {"ok": True, "day": 6, "structure": "DAY 6: VON NEUMANN TESSERACT", "structure_index": 5,
                    "calendar_index": 1, "preferred_scene_index": 5}
    assert desk.calls == [("/api/evolution", {})]


def test_no_pin_is_null_and_an_impossible_pin_counts_as_no_pin(client, desk):
    dev = paired()
    answer(desk, {**EVOLUTION, "preferred_scene_index": None})
    assert get(client, dev, "/tv/v1/evolution").get_json()["preferred_scene_index"] is None
    answer(desk, {**EVOLUTION, "preferred_scene_index": 9999})
    assert get(client, dev, "/tv/v1/evolution").get_json()["preferred_scene_index"] is None
    answer(desk, {**EVOLUTION, "preferred_scene_index": 15})        # one past the last of the 15 shapes
    assert get(client, dev, "/tv/v1/evolution").get_json()["preferred_scene_index"] is None


@pytest.mark.parametrize("change", [
    {"structure_index": 15}, {"structure_index": 64}, {"structure_index": -1}, {"calendar_index": 15}, {"structure_index": "5"}, {"structure_index": True},
    {"structure_index": 1.5}, {"calendar_index": None}, {"day": 0}, {"day": "6"}, {"structure": ""},
    {"structure": "D:\\Data\\x"}, {"structure": "DAY 6\nNEXT"}, {"structure": "x" * 81}, {"structure": 5},
    {"structure": "see https://example.com"}, {"structure": "file://share/x"}, {"structure": "mail me@example.test"},
    {"structure": "~/notes"}, {"structure": "/srv/data/x"}])
def test_an_evolution_answer_that_is_not_plainly_the_shape_is_a_503(client, desk, change):
    answer(desk, {**EVOLUTION, **change})
    assert get(client, paired(), "/tv/v1/evolution").status_code == 503


@pytest.mark.parametrize("status,body", [(500, b"{}"), (404, b"{}"), (200, b"<html>"), (200, b"[1]"), (200, b'"x"')])
def test_a_desktop_that_fails_or_answers_oddly_is_a_503_for_evolution(client, desk, status, body):
    desk.status, desk.body = status, body
    assert get(client, paired(), "/tv/v1/evolution").status_code == 503


def test_a_desktop_that_raises_is_a_503(client, desk):
    desk.boom = RuntimeError("down")
    assert get(client, paired(), "/tv/v1/evolution").status_code == 503


# ── connectors ──────────────────────────────────────────────────────────────────────────────────────────────────────

def connector(key="gmail", kind="oauth", connected=True, **more):
    return {"key": key, "name": key.title(), "icon": "mail", "category": "mail", "kind": kind, "blurb": "Reads your mail",
            "capabilities": ["read"], "workspaces": ["messages"], "setup_hint": "paste it", "docs_url": "https://docs.example/x",
            "fields": [{"key": "GMAIL_TOKEN", "label": "Token", "secret": True, "set": True}], "status": "connected",
            "color": "#00ff00", "detail": "signed in as stephen@example.com", "connected": connected, "tool_count": 4,
            "tools": ["send_mail"], **more}


def test_connectors_carry_id_label_kind_and_connected_only(client, desk):
    answer(desk, {"status": "ok", "connectors": [connector("gmail"), connector("higgsfield", kind="mcp", connected=False)],
                  "health": {"summary": "ok"}})
    body = get(client, paired(), "/tv/v1/connectors").get_json()
    assert body == {"ok": True, "device_control": False, "connectors": [
        {"id": "gmail", "label": "Gmail", "kind": "connector", "connected": True},
        {"id": "higgsfield", "label": "Higgsfield", "kind": "mcp", "connected": False}]}
    text = json.dumps(body)
    for forbidden in ("GMAIL_TOKEN", "stephen@example.com", "docs.example", "send_mail", "paste it", "#00ff00", "Reads your mail"):
        assert forbidden not in text
    assert desk.calls == [("/api/connectors", {})]


def test_one_odd_connector_row_is_skipped_and_the_others_are_kept(client, desk):
    odd = [5, connector(key="../x"), connector(key=""), connector(connected="yes"), {**connector(), "name": "see https://x.y"},
           {**connector(), "name": "D:/Data/x"}, {**connector(), "name": ""}]
    answer(desk, {"connectors": [connector("gmail"), *odd, connector("spotify")]})
    ids = [row["id"] for row in get(client, paired(), "/tv/v1/connectors").get_json()["connectors"]]
    assert ids == ["gmail", "spotify"]


@pytest.mark.parametrize("rows", [[connector("a%d" % n) for n in range(201)], "not a list", None, {"a": 1}])
def test_a_connectors_answer_that_is_not_plainly_a_list_is_a_503(client, desk, rows):
    answer(desk, {"connectors": rows})
    assert get(client, paired(), "/tv/v1/connectors").status_code == 503


def test_an_empty_connector_list_is_a_200_with_no_rows(client, desk):
    answer(desk, {"connectors": []})
    body = get(client, paired(), "/tv/v1/connectors").get_json()
    assert body["connectors"] == [] and body["device_control"] is False


def test_a_desktop_failure_for_connectors_is_a_503(client, desk):
    desk.status = 500
    assert get(client, paired(), "/tv/v1/connectors").status_code == 503


# ── providers ───────────────────────────────────────────────────────────────────────────────────────────────────────

def provider(name, **more):
    return {"name": name, "label": name.title(), "base_url": "https://secret-host.example/v1",
            "auth": {"type": "env_var", "key": "SECRET_ENV_NAME"}, "extra_headers": {"X-Token": "SECRET-HEADER-VALUE"}, **more}


@pytest.fixture
def providers(monkeypatch):
    rows = [provider("anthropic"), provider("openrouter"), provider("llama-server", auth={"type": "none"}),
            provider("off", enabled=False), provider("../bad")]
    monkeypatch.setattr(provider_registry, "get_provider_registry", lambda: type("R", (), {"list_providers": lambda self: rows})())
    monkeypatch.setattr(credential_store, "provider_key_status", lambda name: "connected" if name == "anthropic" else "missing")
    monkeypatch.setattr(pd, "_env_key", lambda prov: None)
    monkeypatch.setattr(pd, "classification_of", lambda prov: "local" if prov["name"] == "llama-server" else "cloud")
    return rows


def test_providers_are_names_and_a_yes_or_no_only(client, desk, providers):
    body = get(client, paired(), "/tv/v1/providers").get_json()
    assert body == {"ok": True, "providers": [
        {"id": "anthropic", "label": "Anthropic", "configured": True, "local": False},
        {"id": "openrouter", "label": "Openrouter", "configured": False, "local": False},
        {"id": "llama-server", "label": "Llama-Server", "configured": True, "local": True}]}
    text = json.dumps(body)
    for forbidden in ("secret-host", "SECRET_ENV_NAME", "SECRET-HEADER-VALUE", "Authorization"):
        assert forbidden not in text
    assert desk.calls == []                     # answered from the PC's own registry, no desktop route


def test_a_key_in_the_environment_alone_counts_as_configured(client, desk, providers, monkeypatch):
    monkeypatch.setattr(pd, "_env_key", lambda prov: "x" if prov["name"] == "openrouter" else None)
    by_id = {p["id"]: p["configured"] for p in get(client, paired(), "/tv/v1/providers").get_json()["providers"]}
    assert by_id["openrouter"] is True


def test_reading_the_providers_never_reads_or_stamps_a_key_as_used(client, desk, providers, monkeypatch):
    """credential_store.get_provider_key (and read_secret) record a USE of the key; a Settings read is not one."""
    def forbidden(*args, **kwargs):
        raise AssertionError("a Settings read must not read or use a stored key")
    monkeypatch.setattr(credential_store, "get_provider_key", forbidden)
    monkeypatch.setattr(credential_store, "read_secret", forbidden)
    assert get(client, paired(), "/tv/v1/providers").status_code == 200


def test_a_provider_with_a_broken_auth_block_is_listed_without_failing_the_others(client, desk, providers):
    providers.append({"name": "oddball", "label": "Oddball", "auth": "not a dict"})
    ids = [p["id"] for p in get(client, paired(), "/tv/v1/providers").get_json()["providers"]]
    assert "oddball" in ids and "anthropic" in ids


def test_a_provider_that_is_switched_off_or_badly_named_is_not_listed(client, desk, providers):
    ids = [p["id"] for p in get(client, paired(), "/tv/v1/providers").get_json()["providers"]]
    assert "off" not in ids and "../bad" not in ids


def test_a_registry_that_fails_is_a_503_for_providers(client, desk, monkeypatch):
    def boom():
        raise RuntimeError("registry down")
    monkeypatch.setattr(provider_registry, "get_provider_registry", boom)
    assert get(client, paired(), "/tv/v1/providers").status_code == 503
