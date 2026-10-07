"""Hosting identity, real encrypted-store compatibility and safe provider boundaries."""
import json

import pytest

from agent_friday.services import site_hosting as hosting, publish_hosting as legacy
from agent_friday.services import publish_adapters as adapters, publish_web


@pytest.fixture(autouse=True)
def public_generation(monkeypatch):
    monkeypatch.setattr(hosting.sites_privacy, "require_generation", lambda generation: None)


def connect(data):
    return hosting.connect(data, generation=0)


@pytest.fixture
def credentials(monkeypatch, tmp_path):
    from agent_friday.services import credential_store as store, keystore
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.setattr(hosting.sites_privacy, "require_generation", lambda generation: None)
    monkeypatch.setattr(store, "_PROVIDER_KEYS_DIR", tmp_path / "keys")
    monkeypatch.setattr(store, "_SECURITY_DIR", tmp_path / "security")
    monkeypatch.setattr(keystore, "KEYSTORE_PATH", tmp_path / "security" / "keystore.json")
    keystore._reset_cache_for_tests()
    yield store
    keystore._reset_cache_for_tests()


def test_actual_encrypted_legacy_key_round_trip_and_disconnect(credentials):
    token = "synthetic-hosting-token-for-tests"  # pragma: allowlist secret
    credentials.set_provider_key("publish:github_pages", json.dumps({"token": token, "repo": "example/site"}))
    assert legacy.connection("github_pages")["token"] == token
    legacy.connect_adapter("github_pages", token, repo="example/site")
    assert legacy.connection("github_pages")["repo"] == "example/site"
    ciphertext = credentials._provider_key_path("publish_github_pages").read_bytes()
    assert token.encode() not in ciphertext and credentials.looks_protected(ciphertext)
    assert credentials.get_provider_key("publish:github_pages") is None
    legacy.disconnect_adapter("github_pages")
    assert legacy.connection("github_pages") is None


def test_same_labels_do_not_alias_connections_or_leak_secrets(credentials, caplog):
    first = connect({"name": "Account", "adapter": "github_pages", "token": "synthetic-one", "repo": "example/one"})
    second = connect({"name": "Account", "adapter": "github_pages", "token": "synthetic-two", "repo": "example/two"})
    assert first["connection_id"] != second["connection_id"]
    assert "token" not in first and "token" not in second
    assert "synthetic-one" not in json.dumps(hosting.list_connections()) + caplog.text
    assert hosting.get_connection(first["connection_id"], secret=True)["token"] == "synthetic-one"
    changed = connect({key: value for key, value in dict(first, token="synthetic-three").items()  # pragma: allowlist secret -- synthetic test credential
                               if key not in ("connected", "updated_at")})
    assert changed["revision"] == 2
    assert credentials.get_provider_key(hosting._key(first["connection_id"], 1)) is None
    with pytest.raises(ValueError, match="changed"):
        hosting.disconnect(first["connection_id"], 1, generation=0)
    hosting.disconnect(first["connection_id"], changed["revision"], generation=0)
    with pytest.raises(ValueError, match="Reconnect"):
        hosting.get_connection(first["connection_id"], secret=True)
    assert hosting.get_connection(second["connection_id"], secret=True)["token"] == "synthetic-two"


def test_reconnect_cannot_retarget_a_saved_connection(credentials):
    saved = connect({"name": "Host", "adapter": "github_pages", "token": "synthetic-one", "repo": "example/one"})
    with pytest.raises(ValueError, match="different hosting target"):
        connect({key: value for key, value in dict(saved, repo="example/two", token="synthetic-two").items()  # pragma: allowlist secret -- synthetic test credential
                         if key not in ("connected", "updated_at")})
    assert hosting.get_connection(saved["connection_id"], secret=True)["token"] == "synthetic-one"


def test_metadata_write_failure_keeps_previous_credential_and_revision(credentials, monkeypatch):
    saved = connect({"name": "Host", "adapter": "github_pages", "token": "synthetic-one", "repo": "example/one"})
    monkeypatch.setattr(hosting, "_write", lambda _rows: (_ for _ in ()).throw(OSError("synthetic failure")))
    with pytest.raises(OSError):
        connect({key: value for key, value in dict(saved, token="synthetic-two").items()  # pragma: allowlist secret -- synthetic test credential
                         if key not in ("connected", "updated_at")})
    assert hosting.get_connection(saved["connection_id"], secret=True)["token"] == "synthetic-one"
    assert credentials.get_provider_key(hosting._key(saved["connection_id"], 2)) is None


def bundle():
    return publish_web.Bundle({"index.html": b"<h1>Site</h1>"}, "Site", "site-" + "a" * 32,
                              "site", "site-" + "a" * 32, "chat-test", 1)


def test_cloudflare_is_refused_before_credentials_or_provider_actions(credentials, monkeypatch):
    conn = {"adapter": "cloudflare_pages", "account_id": "a" * 32, "project": "example-site", "token": "synthetic"}
    monkeypatch.setattr(adapters, "_http", lambda *a, **kw: pytest.fail("Unsupported upload must not contact Cloudflare"))
    monkeypatch.setattr(credentials, "set_provider_key", lambda *a: pytest.fail("Unsupported adapter must not retain a token"))
    with pytest.raises(ValueError, match="asset-upload protocol"):
        connect(conn)
    with pytest.raises(ValueError, match="asset-upload protocol"):
        adapters.publish_site(bundle(), conn, hostname="www.example.test", privacy_generation=0)


def test_github_configuration_uses_update_for_cname_and_full_tree(monkeypatch):
    conn = {"adapter": "github_pages", "repo": "example/site", "branch": "gh-pages", "token": "synthetic"}
    commits, calls = [], []
    def commit(*args, **kwargs):
        commits.append((args, kwargs))
        return "a" * 40
    def http(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return (404 if method == "GET" else 201 if method == "POST" else 204), {}, ""
    monkeypatch.setattr(adapters, "_gh_commit", commit)
    monkeypatch.setattr(adapters, "_http", http)
    result = adapters.publish_site(bundle(), conn, hostname="www.example.test", privacy_generation=0)
    assert commits[0][1]["replace_tree"] is True
    assert commits[0][0][1]["CNAME"] == b"www.example.test\n"
    assert "cname" not in next(row for row in calls if row[0] == "POST")[2]["json_body"]
    assert next(row for row in calls if row[0] == "PUT")[2]["json_body"]["cname"] == "www.example.test"
    assert result["status"] == "provider_accepted" and not result["verified"]


def test_provider_ready_requires_matching_commit_and_is_not_live(monkeypatch):
    conn = {"adapter": "github_pages", "repo": "example/site", "token": "synthetic"}
    monkeypatch.setattr(adapters, "_http", lambda *a, **kw: (200, {"commit": "b" * 40, "status": "built"}, ""))
    assert adapters.site_deployment_status(conn, {"provider_deployment_id": "a" * 40}, privacy_generation=0)["status"] == "provider_pending"
    result = adapters.site_deployment_status(conn, {"provider_deployment_id": "b" * 40}, privacy_generation=0)
    assert result["status"] == "provider_ready" and result["verified"] is False


def test_custom_domain_requirements_preserve_mail_and_refuse_unsupported_hosts():
    with pytest.raises(ValueError, match="asset-upload protocol"):
        adapters.site_dns_requirements({"adapter": "cloudflare_pages"}, "www.example.test", "example.test")
    conn = {"adapter": "github_pages", "repo": "example/site"}
    result = adapters.site_dns_requirements(conn, "www.example.test", "example.test")
    assert result["records"] == [{"host": "www", "type": "CNAME", "answer": "example.github.io", "ttl": 300}]
    assert result["host_association_required"] is True
    apex = adapters.site_dns_requirements(conn, "example.test", "example.test")
    assert len(apex["records"]) == 4 and {row["type"] for row in apex["records"]} == {"A"}


def test_provider_transport_does_not_follow_credential_bearing_redirects(monkeypatch):
    import requests
    captured = []
    class Reply:
        status_code = 302
        text = ""
        def json(self):
            return {}
        def close(self):
            pass
    monkeypatch.setattr(requests, "request", lambda *args, **kwargs: captured.append(kwargs) or Reply())
    adapters._http("GET", "https://api.github.com/repos/example/site/pages", headers={"Authorization": "Bearer synthetic"})
    assert captured[0]["allow_redirects"] is False


def test_failed_remote_takedown_preserves_both_mirror_and_publication_index(monkeypatch, tmp_path):
    monkeypatch.setattr(adapters, "_mirror_root", lambda: tmp_path / "mirrors")
    monkeypatch.setattr(publish_web, "_index_path", lambda: tmp_path / "index.json")
    page = publish_web.Bundle({"index.html": b"still published"}, "Page", "page", "html", "art", "chat", 1)
    adapters._write_mirror("cloudflare_pages", page)
    publish_web._write_index([{"slug": "page", "adapter": "cloudflare_pages"}])
    monkeypatch.setattr(adapters, "_cf_ensure_project", lambda conn: None)
    monkeypatch.setattr(adapters, "_cf_deploy", lambda *a: (_ for _ in ()).throw(RuntimeError("synthetic timeout")))
    monkeypatch.setattr(legacy, "connection", lambda adapter: {"token": "synthetic", "account_id": "a", "project": "site"})
    with pytest.raises(ValueError, match="not confirmed"):
        publish_web.unpublish("page")
    assert publish_web.list_published()[0]["slug"] == "page"
    assert (adapters._mirror("cloudflare_pages") / "page" / "index.html").read_bytes() == b"still published"


def test_expired_origin_never_begins_host_publication(monkeypatch):
    def require(generation):
        raise ValueError("privacy ended")
    monkeypatch.setattr(hosting.sites_privacy, "require_generation", require)
    monkeypatch.setattr(adapters, "_http", lambda *a, **kw: pytest.fail("No provider request after privacy ends"))
    with pytest.raises(ValueError, match="privacy ended"):
        adapters.publish_site(bundle(), {"adapter": "cloudflare_pages"}, privacy_generation=0)


def test_transition_during_provider_read_prevents_the_next_write(monkeypatch):
    current = {"valid": True}
    calls = []
    def require(generation):
        assert generation == 4
        if not current["valid"]:
            raise ValueError("privacy ended")
    def request(*args, **kwargs):
        calls.append((args, kwargs))
        current["valid"] = False
        return 200, {"sha": "a" * 40}, ""
    monkeypatch.setattr(hosting.sites_privacy, "require_generation", require)
    monkeypatch.setattr(adapters, "_site_http", request)
    with pytest.raises(ValueError, match="privacy ended"):
        adapters.publish_site(bundle(), {"adapter": "github_pages",
            "repo": "example/site", "token": "synthetic"}, privacy_generation=4)
    assert len(calls) == 1 and calls[0][0][0] == "GET"
    assert adapters._SITE_GENERATION.get() is None


def test_status_read_carries_fresh_call_generation_and_drops_late_result(monkeypatch):
    current = {"valid": True}
    def require(generation):
        assert generation == 9
        if not current["valid"]:
            raise ValueError("privacy ended")
    def read(*args, **kwargs):
        assert adapters._SITE_GENERATION.get() == 9
        current["valid"] = False
        return {"status": "provider_ready"}
    monkeypatch.setattr(hosting.sites_privacy, "require_generation", require)
    monkeypatch.setattr(adapters, "_site_deployment_status", read)
    with pytest.raises(ValueError, match="privacy ended"):
        adapters.site_deployment_status({"adapter": "github_pages"}, {"privacy_generation": 0}, privacy_generation=9)
    assert adapters._SITE_GENERATION.get() is None


def test_credential_wait_that_loses_generation_keeps_the_old_connection(credentials, monkeypatch):
    saved = connect({"name": "Host", "adapter": "github_pages", "token": "synthetic-one", "repo": "example/one"})
    current = {"valid": True}
    original = credentials.set_provider_key
    def require(generation):
        if not current["valid"]:
            raise ValueError("privacy ended")
    def write(*args):
        original(*args)
        current["valid"] = False
    monkeypatch.setattr(hosting.sites_privacy, "require_generation", require)
    monkeypatch.setattr(credentials, "set_provider_key", write)
    with pytest.raises(ValueError, match="privacy ended"):
        connect({key: value for key, value in dict(saved, token="synthetic-two").items()  # pragma: allowlist secret -- synthetic test credential
                 if key not in ("connected", "updated_at")})
    assert hosting.get_connection(saved["connection_id"], secret=True)["token"] == "synthetic-one"
    assert credentials.get_provider_key(hosting._key(saved["connection_id"], 2)) is None


def test_credential_writer_failure_cleans_up_only_new_hosting_revision(credentials, monkeypatch):
    saved = connect({"name": "Host", "adapter": "github_pages", "token": "synthetic-one", "repo": "example/one"})
    before = hosting._path().read_bytes()
    original = credentials.set_provider_key
    def write_then_fail(*args):
        original(*args)
        raise OSError("Synthetic interruption after secret persistence")
    monkeypatch.setattr(credentials, "set_provider_key", write_then_fail)
    with pytest.raises(OSError, match="Synthetic interruption"):
        connect({key: value for key, value in dict(saved, token="synthetic-two").items()  # pragma: allowlist secret -- synthetic test credential
                 if key not in ("connected", "updated_at")})
    assert hosting._path().read_bytes() == before
    assert hosting.get_connection(saved["connection_id"], secret=True)["token"] == "synthetic-one"
    assert credentials.get_provider_key(hosting._key(saved["connection_id"], 2)) is None
