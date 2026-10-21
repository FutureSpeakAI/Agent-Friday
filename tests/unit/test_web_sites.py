"""The owner's site list for website tasks (governance/web_sites.py).

Spec: Friday operates websites, section C1 (permission slips, not keys).
"""
import json

import pytest

from agent_friday.governance import web_sites as ws


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "_dir", lambda: tmp_path)
    monkeypatch.setattr(ws, "_key", lambda: b"test-governance-key-not-real")
    return tmp_path


@pytest.mark.parametrize("given,site", [
    ("https://www.shop.example.com/basket", "example.com"),
    ("https://shop.example.co.uk/x", "example.co.uk"),
    ("www.example.co.uk", "example.co.uk"),
    ("https://EXAMPLE.org.", "example.org"),
    ("https://user.github.io/page", "user.github.io"),
])
def test_site_is_the_registrable_domain(given, site):
    assert ws.site_of(given) == site


@pytest.mark.parametrize("given", [
    "", "https://localhost/", "http://10.0.0.1/admin", "https://[::1]/", "co.uk",
    "https://co.uk/", "intranet", "file:///C:/x",
])
def test_no_site_for_addresses_that_are_not_sites(given):
    assert ws.site_of(given) == ""


@pytest.mark.parametrize("host", ["alice.myshopify.com", "bob.wordpress.com", "x.s3.amazonaws.com"])
def test_a_shared_host_slip_covers_one_tenant_only(store, host):
    ws.set_site(host, "may_act", made_by="owner:ui")
    assert ws.level_for(host) == "may_act"
    assert ws.level_for("mallory." + host.split(".", 1)[1]) is None


def test_lookalike_name_is_matched_only_as_punycode(store):
    ws.set_site("example.com", "may_act", made_by="owner:ui")
    # Cyrillic "е" in place of Latin "e": a different site, not the owner's slip.
    assert ws.level_for("https://\u0435xample.com/") is None


def test_nothing_is_allowed_by_default(store):
    assert ws.level_for("https://example.com/") is None
    assert ws.all_sites() == {}


@pytest.mark.parametrize("maker", ["friday", "model", "page", "owner:voice", "", "owner"])
def test_only_the_owners_own_action_can_add_or_widen(store, maker):
    with pytest.raises(ws.SiteRefused):
        ws.set_site("example.com", "may_act", made_by=maker)
    assert ws.level_for("example.com") is None


def test_owner_press_adds_a_signed_slip(store):
    row = ws.set_site("https://www.example.com/x", "may_read", made_by="owner:ui")
    assert row["site"] == "example.com" and row["level"] == "may_read"
    assert ws.level_for("https://shop.example.com/") == "may_read"
    assert row["pictures"] == "never" and row["keep_sign_in"] is False


def test_a_tampered_row_is_absent_and_shown(store):
    ws.set_site("example.com", "may_read", made_by="owner:ui")
    path = store / "web_sites.json"
    data = json.loads(path.read_text())
    data["example.com"]["level"] = "may_act"          # widened by hand, not re-signed
    path.write_text(json.dumps(data))
    assert ws.level_for("example.com") is None
    assert ws.all_sites()["example.com"]["verified"] is False


def test_a_row_moved_to_another_site_is_absent(store):
    ws.set_site("example.com", "may_act", made_by="owner:ui")
    path = store / "web_sites.json"
    data = json.loads(path.read_text())
    data["evil.com"] = data["example.com"]            # valid signature, wrong key
    path.write_text(json.dumps(data))
    assert ws.level_for("evil.com") is None


def test_history_is_append_only(store):
    ws.set_site("example.com", "may_read", made_by="owner:ui")
    ws.set_site("example.com", "may_act", made_by="owner:voice-confirmed")
    ws.remove("example.com", made_by="owner:voice")
    from tests import ledger_support               # history lives in the action ledger
    rows = ledger_support.rows("web_site")
    assert [r["event"] for r in rows] == ["set", "set", "remove"]
    assert rows[1]["previous"]["level"] == "may_read"
    assert ws.level_for("example.com") is None


def test_narrowing_and_removing_may_be_spoken(store):
    ws.set_site("example.com", "may_act", made_by="owner:ui")
    assert ws.narrow("example.com", made_by="owner:voice")["level"] == "may_read"
    assert ws.level_for("example.com") == "may_read"
    with pytest.raises(ws.SiteRefused):
        ws.narrow("example.com", made_by="friday")


def test_an_unreadable_list_changes_nothing(store):
    (store / "web_sites.json").write_text("{not json")
    with pytest.raises(ws.SiteRefused):
        ws.set_site("example.com", "may_act", made_by="owner:ui")
    assert ws.level_for("example.com") is None


def test_the_list_holds_no_secrets(store):
    row = ws.set_site("example.com", "may_act", made_by="owner:ui", note="groceries")
    assert set(row) == {"site", "level", "keep_sign_in", "pictures", "readers", "note",
                        "made_by", "made_at", "revision", "sig"}
