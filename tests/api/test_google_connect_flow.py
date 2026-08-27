"""The connect flow tells you what is coming, and lands you somewhere on failure.

Three things Stephen called out on 2026-08-26, all of which are copy and
routing rather than cryptography:

  * "Tell the user the warning screen is coming, before it appears." A person
    who meets "Google hasn't verified this app" unprepared assumes phishing
    and abandons. So /connect returns the pre-brief along with the auth URL.
  * "Failing at the cap must be graceful." When the shared client is full the
    callback must land in the bring-your-own walkthrough with an explanation,
    not a raw OAuth error string.
  * Never "place this JSON file in this directory" — the wall Janet hit.

The old callback was literally:

    err = request.args.get("error")
    if err:
        return f"<h2>Google authorization failed</h2><p>{err}</p>", 400

which renders `access_denied` to a person who has no idea what that is, at
the exact moment they need to be told there is another way in.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.real_provider_paths


class TestConnectPrebrief:
    def _connect(self, client):
        return client.post("/api/google/accounts/connect", json={"label": "Test"})

    def test_the_response_says_which_client_is_in_play(self, client, monkeypatch):
        import agent_friday.services.google_oauth_client as goc
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_ID",
                            "b.apps.googleusercontent.com", raising=False)
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_SECRET", "shh", raising=False)
        monkeypatch.setattr(goc, "stored_byo_config", lambda: None, raising=False)

        out = self._connect(client).get_json()
        assert out.get("client_kind") in ("bundled", "byo"), out

    def test_the_bundled_path_ships_the_warning_copy(self, client, monkeypatch):
        import agent_friday.services.google_oauth_client as goc
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_ID",
                            "b.apps.googleusercontent.com", raising=False)
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_SECRET", "shh", raising=False)
        monkeypatch.setattr(goc, "stored_byo_config", lambda: None, raising=False)

        out = self._connect(client).get_json()
        if out.get("client_kind") != "bundled":
            pytest.skip("a real BYO client is configured on this machine")
        low = (out.get("prebrief") or "").lower()
        assert "advanced" in low, "must name the button before they need it"
        assert "verified" in low

    def test_with_no_client_at_all_the_error_names_an_action(self, client, monkeypatch):
        """Janet's exact message, retired."""
        import agent_friday.services.google_oauth_client as goc
        import agent_friday.services.google_accounts as ga
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_ID", "", raising=False)
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_SECRET", "", raising=False)
        monkeypatch.setattr(goc, "stored_byo_config", lambda: None, raising=False)
        monkeypatch.setattr(ga, "_google_client_config", lambda: (None, None),
                            raising=False)

        out = self._connect(client).get_json()
        blob = str(out).lower()
        assert "credentials.json" not in blob
        assert "~/.friday" not in blob
        assert "google_client_id" not in blob


class TestCallbackFailure:
    def test_a_denial_explains_itself_and_offers_the_other_path(self, client):
        r = client.get("/api/google/accounts/callback?error=access_denied")
        body = r.get_data(as_text=True).lower()
        assert "access_denied" not in body or "cancel" in body, (
            "a raw OAuth error code was rendered to a person")
        assert "your own" in body, "the escape hatch must be offered on failure"

    def test_a_named_cap_says_the_cap_is_full(self, client):
        r = client.get("/api/google/accounts/callback"
                       "?error=access_denied&error_description=OAuth+user+cap+reached")
        body = r.get_data(as_text=True).lower()
        assert "full" in body or "cap" in body
        assert "your own" in body

    def test_a_bad_client_reads_as_a_setup_problem_not_a_full_app(self, client):
        r = client.get("/api/google/accounts/callback?error=redirect_uri_mismatch")
        body = r.get_data(as_text=True).lower()
        assert "desktop" in body, "name the actual mistake"
        assert "full" not in body

    def test_no_failure_page_tells_anyone_to_place_a_file(self, client):
        for q in ("error=access_denied", "error=redirect_uri_mismatch",
                  "error=admin_policy_enforced", "error=weird"):
            body = client.get(
                "/api/google/accounts/callback?" + q).get_data(as_text=True).lower()
            assert ".json" not in body, q
            assert "directory" not in body, q


class TestByoEndpoint:
    """The walkthrough has to end somewhere that accepts a paste."""

    def test_the_steps_are_served_to_the_ui(self, client):
        out = client.get("/api/google/oauth/byo").get_json()
        assert out.get("steps") and len(out["steps"]) >= 5
        assert out.get("scopes")

    def test_pasting_a_client_stores_it(self, client, monkeypatch):
        import agent_friday.services.google_oauth_client as goc
        saved = {}
        monkeypatch.setattr(goc, "save_byo",
                            lambda i, s: saved.update(id=i, secret=s),
                            raising=False)
        r = client.post("/api/google/oauth/byo",
                        json={"client_id": "x.apps.googleusercontent.com",
                              "client_secret": "shh"})
        assert r.status_code == 200
        assert saved["id"] == "x.apps.googleusercontent.com"

    def test_half_a_client_is_refused_with_a_reason(self, client):
        r = client.post("/api/google/oauth/byo",
                        json={"client_id": "x.apps.googleusercontent.com",
                              "client_secret": ""})
        assert r.status_code == 400
        assert "secret" in (r.get_json().get("message") or "").lower()

    def test_the_endpoint_never_echoes_the_secret(self, client, monkeypatch):
        import agent_friday.services.google_oauth_client as goc
        monkeypatch.setattr(goc, "save_byo", lambda i, s: None, raising=False)
        r = client.post("/api/google/oauth/byo",
                        json={"client_id": "x.apps.googleusercontent.com",
                              "client_secret": "TOPSECRETVALUE"})
        assert "TOPSECRETVALUE" not in r.get_data(as_text=True)
