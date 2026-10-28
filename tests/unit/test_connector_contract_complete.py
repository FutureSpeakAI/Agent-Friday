"""The Google connector's contract, complete (BD-06: NS-23.1-3, NS-23.1-4, NS-23.1-6, NS-23.1-7,
NS-8.9-2).

C2 (#319) declared each operation: read or change, the permission, the action class. A
connector's contract also says how fast it calls the provider and what it does when told to
slow down, where its calls go, what Google receives, what is kept on this PC and for how long,
how access is removed and what record is kept, and what the provider cannot do; and the
owner can read all of it on the connector's card. Before, none of that was declared and the
card showed buttons only.

Each declared number is tied to the constant the code runs on, each host to the code that
calls it, so the declaration fails here when the code changes under it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_friday.services import connector_contracts as cc

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "agent_friday"


def _contract():
    return cc.google_contract()


def _part(name):
    got = _contract().get(name)
    assert got, "the Google connector's contract does not declare its %s" % name
    return got


def test_the_contract_declares_every_part():
    for name in ("rate_limits", "destinations", "receives", "retention", "revocation", "audit",
                 "limitations", "card"):
        _part(name)


# ── NS-23.1-3 rate limits: the numbers are the code's ──────────────────────────

def test_the_declared_rate_limits_are_the_ones_the_code_runs_on():
    from agent_friday.services import gmail_api, item_actions, scheduling, tasks_inbox
    rl = _part("rate_limits")
    g = rl["gmail_retry"]
    assert (g["tries"], g["first_delay_s"], g["max_delay_s"], g["batch_size"]) == \
        (gmail_api.MAX_TRIES, gmail_api.BASE_DELAY_S, gmail_api.MAX_DELAY_S, gmail_api.BATCH_SIZE)
    caps = rl["friday_caps"]
    assert caps["calendar_holds_per_request"] == scheduling.MAX_HOLDS
    assert caps["scheduling_window_days"] == scheduling.MAX_WINDOW_DAYS
    assert caps["items_per_organize_card"] == item_actions.MAX_ITEMS
    assert caps["tasks_inbox_posts_per_day"] == tasks_inbox.MAX_POSTS_PER_DAY
    assert caps["tasks_inbox_reposts_per_hour"] == tasks_inbox.MAX_REPOSTS_PER_HOUR
    routes = (SRC / "routes" / "google_accounts.py").read_text(encoding="utf-8")
    m = re.search(r"rate_limited\(max_calls=(\d+), window_s=300, bucket=\"connect\"\)", routes)
    assert m and int(m.group(1)) == caps["connect_attempts_per_5_minutes"]


def test_what_is_said_about_retries_is_true():
    # A send is never retried, and the other services' changes are not (so nothing runs twice);
    # a draft save is, and the declaration says so.
    import inspect
    from agent_friday.services import gmail_send
    assert "gmail_api.execute" not in inspect.getsource(gmail_send.send)
    assert "gmail_api.execute" in inspect.getsource(gmail_send.save_draft)
    on = _part("rate_limits")["gmail_retry"]["on"]
    assert "draft" in on and "mailbox changes" in on
    assert "gmail_api.execute" in (SRC / "services" / "gmail_mailbox.py").read_text(encoding="utf-8")
    for mod in ("calendar_write.py", "google_contacts_write.py", "scheduling.py", "tasks_inbox.py"):
        assert "gmail_api" not in (SRC / "services" / mod).read_text(encoding="utf-8"), mod


# ── NS-23.1-4 destinations and what is kept ───────────────────────────────────

CONNECTOR_MODULES = {"google_accounts.py", "google_oauth_client.py", "calendar_engine.py",
                     "calendar_views.py", "calendar_write.py", "gmail_send.py", "gmail_read.py",
                     "gmail_api.py", "gmail_mailbox.py", "scheduling.py", "google_contacts_write.py",
                     "tasks_inbox.py", "relationship_memory.py", "google.py", "connectors.py",
                     "egress_record.py"}


def _py_sources():
    for p in SRC.rglob("*.py"):
        yield p, p.read_text(encoding="utf-8", errors="replace")


def test_every_google_host_the_code_calls_is_declared():
    declared = {d["host"] for d in _part("destinations")}
    seen = set()
    for p, text in _py_sources():
        if p.name not in CONNECTOR_MODULES:
            continue                       # Gemini, YouTube, news and fonts are other connections
        for host in re.findall(r"https://([a-z0-9.-]*\.google(?:apis)?\.com)/", text):
            seen.add(host)
    seen -= {"myaccount.google.com", "console.cloud.google.com", "support.google.com",
             "developers.google.com", "calendar.google.com", "mail.google.com", "docs.google.com",
             "drive.google.com"}          # links shown to the owner, not called
    assert seen <= declared, "called but not declared: %s" % sorted(seen - declared)


def test_every_google_api_client_built_is_declared():
    declared = getattr(cc, "GOOGLE_API_CLIENTS", None)
    assert declared, "the contract does not declare which Google API clients Friday builds"
    for p, text in _py_sources():
        for svc, ver in re.findall(r"\bbuild\(\s*[\"']([a-z0-9]+)[\"']\s*,\s*[\"'](v\d+)[\"']", text):
            assert declared.get(svc) == ver, "%s %s built in %s but not declared" % (svc, ver, p.name)


def test_what_is_kept_names_modules_that_exist():
    kept = _part("retention")
    for r in kept:
        assert (SRC / r["module"]).exists(), r
        assert r["what"] and r["kept"] and r["where"]
    where = " ".join(r["where"] for r in kept)
    for stem in ("google_accounts/tokens", "relationships/", "tasks_inbox/", "google_token.json",
                 "gmail-cache.json", "mail_outbox/"):
        assert stem in where, "kept on this PC but not declared: %s" % stem
    eng = (SRC / "services" / "calendar_engine.py").read_text(encoding="utf-8")
    assert "google_token.json" in eng      # the older connect page still writes it: declared


def test_the_kept_and_limit_numbers_are_the_codes():
    from agent_friday.services import action_journal, action_ledger, gmail_send
    text = " ".join(_part("limitations")) + " " + " ".join(r["kept"] for r in _part("retention"))
    assert "%d MB" % (gmail_send.MAX_ATTACHMENT_BYTES // (1024 * 1024)) in text
    assert "%d days ahead" % (gmail_send.MAX_SCHEDULE_S // 86400) in text
    assert "%d seconds" % gmail_send.UNDO_SECONDS in text
    assert "last %d" % action_journal.MAX_KEPT in text
    assert "%d days" % action_ledger.DEFAULT_ARCHIVE_AFTER_DAYS in text
    ga = (SRC / "services" / "google_accounts.py").read_text(encoding="utf-8")
    assert "[:20000]" in ga and "20,000 characters" in text
    assert "A1:Z200" in ga and "A1 to Z200" in text
    assert "[:300]" in ga and "300 characters" in text


# ── NS-23.1-6 revocation and audit ────────────────────────────────────────────

@pytest.fixture
def one_account(tmp_path, monkeypatch):
    from agent_friday.services import google_accounts as ga
    index = {"accounts": [{"id": "acct0001", "email": "owner@example.invalid"}]}
    monkeypatch.setattr(ga, "_load_index", lambda: {"accounts": list(index["accounts"])})
    monkeypatch.setattr(ga, "_save_index", lambda new: index.update(new))
    tok = tmp_path / "acct0001.token.enc"
    tok.write_bytes(b"synthetic")
    monkeypatch.setattr(ga, "_token_path", lambda aid: tok)
    state = tmp_path / "service_state.json"
    state.write_text('{"drive": {"blocked": true}}', encoding="utf-8")
    monkeypatch.setattr(ga, "_SERVICE_STATE_FILE", state)
    monkeypatch.setattr(ga.cs, "audit_event", lambda *a, **k: None)
    return ga, tok, state


def test_removing_the_last_account_leaves_nothing_behind_and_says_if_google_did_not_confirm(
        client, one_account, monkeypatch):
    ga, tok, state = one_account
    monkeypatch.setattr(ga, "_revoke_remote", lambda aid: False)
    r = client.post("/api/google/accounts/acct0001/remove")
    body = r.get_json()
    assert r.status_code == 200 and body.get("revoked_at_google") is False, body
    assert "did not confirm" in body.get("message", "")
    assert not tok.exists() and not state.exists(), "Google data was left after the last account went"


def test_the_older_plaintext_sign_in_file_goes_with_the_last_account(client, one_account, monkeypatch,
                                                                    tmp_path):
    ga, _tok, _state = one_account
    from agent_friday.services import calendar_engine
    legacy = tmp_path / "google_token.json"
    legacy.write_text('{"refresh_token": "synthetic"}', encoding="utf-8")   # pragma: allowlist secret
    monkeypatch.setattr(calendar_engine, "GOOGLE_TOKEN_PATH", legacy)
    monkeypatch.setattr(ga, "_revoke_remote", lambda aid: True)
    assert client.post("/api/google/accounts/acct0001/remove").status_code == 200
    assert not legacy.exists(), "a plaintext Google sign-in file was left after the last account"


def test_a_token_that_cannot_be_deleted_keeps_the_account_listed(client, one_account, monkeypatch):
    ga, tok, _state = one_account
    monkeypatch.setattr(ga, "_revoke_remote", lambda aid: False)

    class Stuck:
        def unlink(self, missing_ok=False):
            raise PermissionError("in use")
    monkeypatch.setattr(ga, "_token_path", lambda aid: Stuck())
    r = client.post("/api/google/accounts/acct0001/remove")
    assert r.status_code >= 400 and "still listed" in (r.get_json() or {}).get("message", "")
    assert any(a["id"] == "acct0001" for a in ga._load_index()["accounts"])


def test_a_confirmed_revoke_is_said_too(client, one_account, monkeypatch):
    ga, _tok, _state = one_account
    monkeypatch.setattr(ga, "_revoke_remote", lambda aid: True)
    body = client.post("/api/google/accounts/acct0001/remove").get_json()
    assert body.get("revoked_at_google") is True and "confirmed" in body.get("message", "")


def test_the_revocation_and_audit_parts_say_how_and_what_is_kept():
    rv = _part("revocation")
    assert "Remove" in rv["how"] and rv["does"] and rv["keeps"] and rv["if_google_revokes"]
    assert any("ledger" in a for a in _part("audit"))


# ── NS-8.9-2 the card ─────────────────────────────────────────────────────────

def test_the_card_states_everything_the_owner_needs():
    card = _part("card")
    heads = [s["heading"] for s in card]
    for need in ("What it lets Friday do", "Permissions", "Runs without a card", "What Google receives",
                 "Kept on this PC", "Limits", "Removing it"):
        assert need in heads, need
    services = {o["service"] for o in cc.GOOGLE_OPERATIONS}
    assert services <= set(cc.GOOGLE_SERVICE_SUMMARY), "a service with no line on the card"
    no_card = next(s for s in card if s["heading"] == "Runs without a card")["lines"][0]
    for tool in _contract()["changes_without_a_card"]:
        assert tool in no_card
    assert "Google Tasks" in no_card, "the phone list's own changes run without a card and must say so"
    perms = " ".join(next(s for s in card if s["heading"] == "Permissions")["lines"])
    assert "reading" in perms and "changing" in perms


@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_the_settings_panel_shows_the_card_and_the_removal_answer(page):
    text = (ROOT / page).read_text(encoding="utf-8")
    assert "GoogleConnectorCard" in text and "/api/google/accounts/contract" in text, \
        "the connector card is not on the Google settings panel"
    i = text.find("removeAccount")
    assert "d.message" in text[i:i + 800], "the panel does not say whether Google confirmed the revoke"
    assert "r.ok" in text[i:i + 800], "the panel drops an account the server did not remove"
