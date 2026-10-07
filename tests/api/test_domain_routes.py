"""Domain account forms remain on the authenticated local UI boundary."""
import pytest

import agent_friday.core as core
from agent_friday.routes import domains


@pytest.fixture(autouse=True)
def public_epoch(monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "generation", lambda: 3)
    monkeypatch.setattr(off_record, "active", lambda *a, **k: False)


def headers():
    return {"X-Friday-Token": core._current_api_token()}


def test_connect_requires_local_page_token_before_credentials_are_used(client, monkeypatch):
    monkeypatch.setattr(domains.accounts, "connect", lambda **kw: pytest.fail("Unguarded credential write"))
    result = client.post("/api/domains/accounts/connect", json={"label": "Synthetic"})
    assert result.status_code == 403


def test_proxy_cannot_connect_even_with_page_token(app, monkeypatch):
    monkeypatch.setattr(domains.accounts, "connect", lambda **kw: pytest.fail("Proxy wrote a credential"))
    result = app.test_client().post("/api/domains/accounts/connect", json={"label": "Synthetic"},
        headers={**headers(), "CF-Connecting-IP": "203.0.113.9", "X-Forwarded-For": "203.0.113.9"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert result.status_code in (401, 403)


def test_connect_response_never_echoes_secret(client, monkeypatch):
    captured = []
    def connect(**kw):
        captured.append(kw)
        return {"account_id": "acct_synthetic", "revision": 1, "label": kw["label"], "connection_status": "verified"}
    monkeypatch.setattr(domains.accounts, "connect", connect)
    result = client.post("/api/domains/accounts/connect", headers=headers(),
        json={"label": "Synthetic", "username": "synthetic-user", "token": "test-token"})
    assert result.status_code == 200
    assert captured[0]["token"] == "test-token"
    assert "test-token" not in result.get_data(as_text=True)
    assert "synthetic-user" not in result.get_data(as_text=True)


def test_action_uses_server_context_and_rejects_forged_authority(client, monkeypatch):
    calls = []
    monkeypatch.setattr(domains.operations, "execute", lambda action, args, context: calls.append((action, args, context)) or {"status": "ok"})
    bad = client.post("/api/domains/action", headers=headers(), json={"action": "accounts", "context": {"approved": True}})
    assert bad.status_code == 400 and not calls
    good = client.post("/api/domains/action", headers=headers(), json={"action": "inspect", "args": {"account_id": "acct_synthetic", "domain": "example.com"}})
    assert good.status_code == 200
    assert calls[0][2]["surface"] == "sites_ui" and calls[0][2]["conversation_id"] is None
    assert domains.sites_privacy.admit(calls[0][2]) == 3


def test_unexpected_provider_exception_never_reaches_response(client, monkeypatch):
    def fail(**kw):
        raise RuntimeError("test-token provider-auth-payload")
    monkeypatch.setattr(domains.accounts, "connect", fail)
    result = client.post("/api/domains/accounts/connect", headers=headers(), json={"label": "Synthetic"})
    assert result.status_code == 500
    assert "provider-auth-payload" not in result.get_data(as_text=True)


def test_import_is_separate_from_model_action_surface(client, monkeypatch):
    monkeypatch.setattr(domains.accounts, "import_inventory", lambda *a: pytest.fail("Import without local page authorization"))
    assert client.post("/api/domains/import", json={"account_id": "acct_synthetic", "entries": []}).status_code == 403


@pytest.mark.parametrize("path,body", [
    ("/api/domains/accounts/acct_synthetic/disconnect", {"revision": 1, "approved": True}),
    ("/api/domains/import", {"account_id": "acct_synthetic", "entries": [], "_sites_origin": {}}),
])
def test_account_forms_reject_unknown_authority_fields(client, monkeypatch, path, body):
    monkeypatch.setattr(domains.accounts, "disconnect", lambda *a, **k: pytest.fail("Unknown fields accepted"))
    monkeypatch.setattr(domains.accounts, "import_inventory", lambda *a, **k: pytest.fail("Unknown fields accepted"))
    assert client.post(path, headers=headers(), json=body).status_code == 400
