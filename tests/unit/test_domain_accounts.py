import json

import pytest

from agent_friday.services import credential_store, domain_accounts as accounts, namecom, off_record


@pytest.fixture
def world(tmp_path, monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "generation", lambda: 3)
    monkeypatch.setattr(off_record, "active", lambda *a, **k: False)
    monkeypatch.setattr(accounts, "_path", lambda: tmp_path / "domains.json")
    monkeypatch.setattr(accounts, "require_recording", lambda: None)
    monkeypatch.setattr(credential_store, "_PROVIDER_KEYS_DIR", tmp_path / "keys")
    # Exercise the actual credential store's encrypted file writer and reader.
    from cryptography.fernet import Fernet
    box = Fernet(Fernet.generate_key())
    monkeypatch.setattr(credential_store, "protect", lambda raw: (box.encrypt(raw), "test-encrypted"))
    monkeypatch.setattr(credential_store, "unprotect", box.decrypt)
    monkeypatch.setattr(credential_store, "audit_event", lambda *a, **k: None)
    monkeypatch.setattr(namecom.Client, "hello", lambda self: None)
    return tmp_path


def test_two_named_accounts_have_distinct_encrypted_credentials(world):
    first = accounts.connect(label="Same label", username="first-test", token="test-token-one", environment="sandbox")  # pragma: allowlist secret -- synthetic test credential
    second = accounts.connect(label="Same label", username="second-test", token="test-token-two", environment="sandbox")  # pragma: allowlist secret -- synthetic test credential
    assert first["account_id"] != second["account_id"]
    assert accounts.client(accounts.account(first["account_id"]), generation=off_record.generation()).username == "first-test"
    assert accounts.client(accounts.account(second["account_id"]), generation=off_record.generation()).username == "second-test"
    for path in world.rglob("*"):
        if path.is_file():
            assert b"test-token" not in path.read_bytes()
            assert b"first-test" not in path.read_bytes()
    assert "username" not in first and "token" not in first


def test_account_identity_cannot_be_silently_retargeted(world):
    first = accounts.connect(label="Account", username="first-test", token="test-token", environment="sandbox")  # pragma: allowlist secret -- synthetic test credential
    with pytest.raises(ValueError, match="new named account"):
        accounts.connect(label="Account", username="different-test", token="other-token", environment="sandbox",  # pragma: allowlist secret -- synthetic test credential
                         account_id=first["account_id"], revision=first["revision"])
    assert accounts.account(first["account_id"])["revision"] == 1


def test_reconnect_and_disconnect_invalidate_old_revision(world):
    first = accounts.connect(label="Account", username="first-test", token="test-token", environment="sandbox")  # pragma: allowlist secret -- synthetic test credential
    aid = first["account_id"]
    next_rec = accounts.connect(label="New label", username="first-test", token="changed-token", environment="sandbox", account_id=aid, revision=1)  # pragma: allowlist secret -- synthetic test credential
    assert next_rec["revision"] == 2
    assert credential_store.get_provider_key(accounts._key(aid, 1)) is None
    with pytest.raises(ValueError, match="changed"):
        accounts.disconnect(aid, 1)
    assert accounts.disconnect(aid, 2)["revision"] == 3
    assert credential_store.get_provider_key(accounts._key(aid, 2)) is None


def test_import_preserves_renews_expires_without_claiming_verification(world):
    rec = accounts.connect(label="Imported")
    rows = accounts.import_inventory(rec["account_id"], [
        {"domain": "one.example", "next_date_kind": "renews", "next_date": "2030-10-26"},
        {"domain": "two.example", "next_date_kind": "expires", "next_date": "2030-10-29"},
    ])
    assert [r["imported"]["next_date_kind"] for r in rows] == ["renews", "expires"]
    assert all(not r["verified"] and r["last_synced_at"] is None for r in rows)
    assert all("autorenew_enabled" not in r for r in rows)


def test_sync_strips_contacts_and_keeps_imported_provenance(world, monkeypatch):
    rec = accounts.connect(label="Account", username="first-test", token="test-token", environment="sandbox")  # pragma: allowlist secret -- synthetic test credential
    accounts.import_inventory(rec["account_id"], [{"domain": "one.example", "next_date_kind": "renews", "next_date": "2030-10-26"}])
    monkeypatch.setattr(namecom.Client, "domains", lambda self: [{"domainName": "one.example", "nameservers": ["ns1.name.com"],
        "expireDate": "2030-11-26T00:00:00Z", "autorenewEnabled": False, "contacts": {"private": "not for the model"}}])
    row = accounts.sync(rec["account_id"], _generation=3)[0]
    assert row["verified"] and row["autorenew_enabled"] is False
    assert row["imported"]["next_date_kind"] == "renews"
    assert "contacts" not in row and "not for the model" not in json.dumps(accounts.read())


def test_partial_sync_failure_retains_previous_inventory(world, monkeypatch):
    rec = accounts.connect(label="Account", username="first-test", token="test-token", environment="sandbox")  # pragma: allowlist secret -- synthetic test credential
    accounts.import_inventory(rec["account_id"], [{"domain": "one.example", "next_date_kind": "expires", "next_date": "2030-10-26"}])
    before = accounts.inventory(rec["account_id"])
    def fail(self):
        raise namecom.ProviderError("Name.com pagination did not complete.")
    monkeypatch.setattr(namecom.Client, "domains", fail)
    with pytest.raises(namecom.ProviderError):
        accounts.sync(rec["account_id"], _generation=3)
    assert accounts.inventory(rec["account_id"]) == before
    assert accounts.account(rec["account_id"])["sync_status"] == "failed"


def test_corrupt_store_is_not_silently_replaced(world):
    accounts._path().write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="Nothing was replaced"):
        accounts.connect(label="Imported")
    assert accounts._path().read_text(encoding="utf-8") == "not json"


@pytest.mark.parametrize("nameservers,expected", [
    (["ns1.name.com", "ns2.name.com"], "namecom"),
    (["ns1dns.name.com", "ns2nsy.name.com", "ns3sxz.name.com", "ns4cfn.name.com"], "namecom"),
    (["ns1.name.com.attacker.example"], "external"),
    (["ns1.other.name.com"], "external"),
    (["ns1.name.com", "ns2.other.example"], "external"),
])
def test_dns_authority_accepts_documented_account_nameserver_variants(nameservers, expected):
    assert accounts.domain_view({"domainName": "one.example", "nameservers": nameservers})["dns_authority"] == expected


def test_connect_privacy_cycle_during_provider_check_saves_no_secret_or_account(world, monkeypatch):
    from agent_friday.services import off_record
    epoch = {"value": 3}
    monkeypatch.setattr(off_record, "generation", lambda: epoch["value"])
    monkeypatch.setattr(namecom.Client, "hello", lambda self: epoch.update(value=5))
    with pytest.raises(ValueError, match="privacy context"):
        accounts.connect(label="Account", username="synthetic", token="synthetic-token", environment="sandbox")  # pragma: allowlist secret -- synthetic test credential
    assert accounts.list_accounts() == []
    assert credential_store.list_provider_keys() == []


def test_sync_privacy_cycle_retains_previous_inventory(world, monkeypatch):
    from agent_friday.services import off_record
    rec = accounts.connect(label="Account", username="synthetic", token="synthetic-token", environment="sandbox")  # pragma: allowlist secret -- synthetic test credential
    accounts.import_inventory(rec["account_id"], [{"domain": "one.example", "next_date_kind": "expires", "next_date": "2030-10-26"}])
    before = accounts._path().read_bytes()
    epoch = {"value": 3}
    monkeypatch.setattr(off_record, "generation", lambda: epoch["value"])
    def response(self):
        epoch["value"] = 5
        return [{"domainName": "one.example", "nameservers": ["ns1.name.com"]}]
    monkeypatch.setattr(namecom.Client, "domains", response)
    with pytest.raises(ValueError, match="privacy context"):
        accounts.sync(rec["account_id"], _generation=3)
    assert accounts._path().read_bytes() == before


def test_privacy_change_during_credential_write_removes_new_key_and_preserves_account(world, monkeypatch):
    from agent_friday.services import off_record
    original_account = accounts.connect(label="Account", username="synthetic", token="original-token", environment="sandbox")  # pragma: allowlist secret -- synthetic test credential
    before = accounts._path().read_bytes()
    keys = credential_store.list_provider_keys()
    epoch = {"value": 3}
    monkeypatch.setattr(off_record, "generation", lambda: epoch["value"])
    original = credential_store.set_provider_key
    def delayed(*args):
        result = original(*args)
        epoch["value"] = 5
        return result
    monkeypatch.setattr(credential_store, "set_provider_key", delayed)
    with pytest.raises(ValueError, match="privacy context"):
        accounts.connect(label="Account", username="synthetic", token="replacement-token", environment="sandbox",  # pragma: allowlist secret -- synthetic test credential
                         account_id=original_account["account_id"], revision=1)
    assert accounts._path().read_bytes() == before
    assert credential_store.list_provider_keys() == keys
    assert accounts.client(accounts.account(original_account["account_id"]), generation=off_record.generation())._token == "original-token"


def test_credential_writer_failure_cleans_up_only_the_new_revision(world, monkeypatch):
    original_account = accounts.connect(label="Account", username="synthetic", token="original-token", environment="sandbox")  # pragma: allowlist secret -- synthetic test credential
    before = accounts._path().read_bytes()
    keys = credential_store.list_provider_keys()
    original = credential_store.set_provider_key
    def interrupted(*args):
        original(*args)
        raise OSError("Synthetic interruption after encrypted write.")
    monkeypatch.setattr(credential_store, "set_provider_key", interrupted)
    with pytest.raises(OSError, match="Synthetic interruption"):
        accounts.connect(label="Account", username="synthetic", token="replacement-token", environment="sandbox",  # pragma: allowlist secret -- synthetic test credential
                         account_id=original_account["account_id"], revision=1)
    assert accounts._path().read_bytes() == before
    assert credential_store.list_provider_keys() == keys
    assert accounts.client(accounts.account(original_account["account_id"]), generation=off_record.generation())._token == "original-token"


def test_sync_first_page_privacy_transition_closes_response_and_stops_pagination(world, monkeypatch):
    rec = accounts.connect(label="Account", username="synthetic", token="synthetic-token", environment="sandbox")  # pragma: allowlist secret -- synthetic test credential
    accounts.import_inventory(rec["account_id"], [{"domain": "one.example", "next_date_kind": "expires", "next_date": "2030-10-26"}])
    before = accounts._path().read_bytes()
    epoch, calls, closed = {"value": 3}, [], []
    monkeypatch.setattr(off_record, "generation", lambda: epoch["value"])
    class Response:
        status_code, headers, content = 200, {}, b"{}"
        def json(self):
            epoch["value"] = 5
            return {"domains": [{"domainName": "one.example", "nameservers": ["ns1.name.com"]}], "nextPage": 2}
        def close(self):
            closed.append(True)
    def request(*args, **kwargs):
        calls.append(kwargs["params"]["page"])
        return Response()
    monkeypatch.setattr("requests.request", request)
    with pytest.raises(ValueError, match="privacy context"):
        accounts.sync(rec["account_id"], _generation=3)
    assert calls == [1] and closed == [True]
    assert accounts._path().read_bytes() == before


def test_saved_client_does_not_recapture_origin_for_a_later_request(world, monkeypatch):
    rec = accounts.connect(label="Account", username="synthetic", token="synthetic-token", environment="sandbox")  # pragma: allowlist secret -- synthetic test credential
    client = accounts.client(accounts.account(rec["account_id"]), generation=3)
    monkeypatch.setattr(off_record, "generation", lambda: 5)
    monkeypatch.setattr("requests.request", lambda *a, **kw: pytest.fail("A saved client cannot adopt a new public generation"))
    with pytest.raises(ValueError, match="privacy context"):
        client.domain("one.example")
