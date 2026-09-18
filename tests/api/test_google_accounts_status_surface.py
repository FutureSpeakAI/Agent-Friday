"""The settings surface must report what the account store actually says.

Regression origin (2026-09-09): both of Stephen's Google accounts carried
status="needs_reauth" with a last_sync of 2026-09-01, and the connectors page
showed them as "connected" for nine days. He asked Friday directly whether his
Google accounts were connected, was told yes for both, and then got an empty
schedule for a day holding two job interviews.

The failing check was structural, and it is the third instance of the same
shape in one day: a check asked whether a RECORD EXISTS and reported that as
whether the THING WORKS. has_accounts() returned true for a non-empty index so
the calendar tool reported connected:true with zero usable accounts; Kokoro's
readiness used find_spec to test that a package NAME existed and called a
broken import ready; and this surface rendered the presence of an account as a
working connection.

So these tests deliberately do NOT test the happy path only -- a happy-path
test would have passed on every one of those nine days. Each one pins the
unhappy path, and each fails if the enforcement is removed.
"""

import json

import pytest

from agent_friday.services import google_accounts as ga
from agent_friday.services import connectors as conn
from agent_friday.routes import google_accounts as ra


def _write_accounts(*records):
    """Put an exact account index on disk, bypassing the connect flow."""
    ga.ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    ga.ACCOUNTS_INDEX.write_text(
        json.dumps({"version": 1, "accounts": list(records)}, indent=2),
        encoding="utf-8")
    ga._MIGRATION_DONE = True


def _rec(**kw):
    base = {
        "id": "acct1", "email": "stephen@example.com", "label": "Work",
        "status": "connected", "services": {"gmail": True, "calendar": True},
        "color": "#00d4ff", "created": "2026-08-26T15:26:07+00:00",
        "last_sync": "2026-09-09T09:00:00+00:00", "scopes": [],
        "enc_method": "vault",
    }
    base.update(kw)
    return base


def _days_ago(n):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


@pytest.fixture(autouse=True)
def clean(tmp_path):
    import shutil
    if ga.ACCOUNTS_DIR.exists():
        shutil.rmtree(ga.ACCOUNTS_DIR, ignore_errors=True)
    ga._MIGRATION_DONE = False
    ra._RL_HITS.clear()
    ra._PENDING.clear()
    yield
    if ga.ACCOUNTS_DIR.exists():
        shutil.rmtree(ga.ACCOUNTS_DIR, ignore_errors=True)


# ── the exact production state, reproduced ──────────────────────────────────
class TestStoredStatusIsWhatShows:

    def test_needs_reauth_never_reads_as_connected(self, client):
        """The nine-day bug, pinned."""
        _write_accounts(
            _rec(id="a1", email="primary@example.com", label="Personal",
                 status="needs_reauth", last_sync=_days_ago(9)),
            _rec(id="a2", email="stephen@futurespeak.ai", label="Work",
                 status="needs_reauth", last_sync=_days_ago(9)),
        )
        d = client.get("/api/google/accounts").get_json()
        assert d["status"] == "ok"
        assert len(d["accounts"]) == 2
        for acc in d["accounts"]:
            h = acc["health"]
            assert h["healthy"] is False
            assert h["state"] == "needs_reauth"
            assert "connected" not in h["label"].lower()
        assert d["all_healthy"] is False
        assert d["healthy_count"] == 0
        assert len(d["needs_attention"]) == 2

    def test_presence_of_a_record_is_not_evidence_of_health(self):
        """The governing rule, as a unit.

        A record with no status at all exists in the index -- that is exactly
        the input the old code read as "connected". Health must fail closed.
        """
        for missing in ({}, {"status": None}, {"status": ""},
                        {"status": "something_new_we_do_not_know"}):
            rec = _rec(**missing) if missing else {k: v for k, v in _rec().items()
                                                   if k != "status"}
            h = ga.account_health(rec)
            assert h["healthy"] is False, f"{missing!r} was read as healthy"
            assert h["actionable"] is True

    def test_only_an_explicit_connected_status_is_healthy(self):
        assert ga.account_health(_rec(status="connected"))["healthy"] is True
        for bad in ("needs_reauth", "revoked", "error", "disconnected"):
            assert ga.account_health(_rec(status=bad))["healthy"] is False


# ── staleness: "connected on September 1st" is not "connected" ──────────────
class TestStalenessIsVisible:

    def test_old_sync_is_flagged_and_dated(self):
        h = ga.account_health(_rec(status="connected", last_sync=_days_ago(9)))
        assert h["stale"] is True
        assert "9 days ago" in h["sync_phrase"]
        # The age has to reach the reader, not just the flag.
        assert "9 days ago" in h["summary"]

    def test_fresh_sync_is_not_flagged_stale(self):
        h = ga.account_health(_rec(status="connected", last_sync=_days_ago(0)))
        assert h["stale"] is False
        assert h["healthy"] is True

    def test_never_synced_is_said_plainly(self):
        h = ga.account_health(_rec(status="connected", last_sync=None))
        assert "never synced" in h["summary"]


# ── an account that needs fixing must offer the fix ─────────────────────────
class TestRecoveryPathIsOffered:

    def test_broken_account_carries_a_reconnect_action(self):
        assert ga.account_health(_rec(status="needs_reauth"))["action"] == "reconnect"
        assert ga.account_health(_rec(status="connected"))["action"] is None

    def test_connect_targets_the_named_account(self, client, monkeypatch):
        """Reconnect must preselect the broken address at Google.

        Without login_hint the user has to pick the right address out of an
        account chooser -- the moment a non-technical user abandons the fix.
        """
        _write_accounts(_rec(id="a1", email="stephen@futurespeak.ai",
                             status="needs_reauth"))
        seen = {}

        class FakeFlow:
            code_verifier = "v"

            def authorization_url(self, **kw):
                seen.update(kw)
                return "https://accounts.google.com/o/oauth2/auth?x=1", "state123"

        monkeypatch.setattr(ga, "build_auth_flow",
                            lambda state=None: (FakeFlow(), "http://127.0.0.1:3000/cb", "installed"))
        r = client.post("/api/google/accounts/connect", json={"account_id": "a1"})
        d = r.get_json()
        assert d["status"] == "ok"
        assert seen.get("login_hint") == "stephen@futurespeak.ai"
        assert d["reconnecting"] is True


# ── the other direction: recovery must not be masked by a cached verdict ────
class TestRecoveryIsReflected:

    def test_surface_updates_when_the_store_recovers(self, client):
        _write_accounts(_rec(id="a1", status="needs_reauth",
                             last_sync=_days_ago(9)))
        first = client.get("/api/google/accounts").get_json()
        assert first["accounts"][0]["health"]["healthy"] is False

        # what a successful reconnect writes
        ga._mark_status("a1", "connected", touch_sync=True)

        second = client.get("/api/google/accounts").get_json()
        assert second["accounts"][0]["health"]["healthy"] is True
        assert second["accounts"][0]["health"]["stale"] is False
        assert second["all_healthy"] is True
        assert second["needs_attention"] == []


# ── the aggregate badge must not let one good account hide a bad one ────────
class TestConnectorAggregate:

    def _patch_probe(self, monkeypatch, healthy_ids):
        monkeypatch.setattr(ga, "credentials_for",
                            lambda aid: object() if aid in healthy_ids else None)

    def test_all_broken_is_not_connected(self, monkeypatch):
        _write_accounts(_rec(id="a1", status="needs_reauth"),
                        _rec(id="a2", status="needs_reauth"))
        self._patch_probe(monkeypatch, set())
        st = conn._status_for_google({})
        assert st["status"] != "connected"
        assert st["accounts_healthy"] == 0

    def test_one_broken_of_two_is_not_connected(self, monkeypatch):
        """`any account works` is the wrong question.

        A green badge here tells the user their Google is fine while half
        their calendar is silently missing.
        """
        _write_accounts(_rec(id="a1", status="connected"),
                        _rec(id="a2", email="b@x.com", status="needs_reauth"))
        self._patch_probe(monkeypatch, {"a1"})
        st = conn._status_for_google({})
        assert st["status"] != "connected"
        assert st["accounts_healthy"] == 1
        assert st["accounts_total"] == 2
        assert "b@x.com" in st["detail"]

    def test_all_healthy_is_connected(self, monkeypatch):
        _write_accounts(_rec(id="a1", status="connected"),
                        _rec(id="a2", email="b@x.com", status="connected"))
        self._patch_probe(monkeypatch, {"a1", "a2"})
        assert conn._status_for_google({})["status"] == "connected"


# ── the surface itself, not just the API behind it ──────────────────────────
class TestRenderedSurface:
    """index.html is the served page (docs/development/ui-build.md).

    These fail if someone removes the status rendering from the UI while
    leaving the API correct -- which is exactly the state the bug was in.
    """

    def _served(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        return (root / "index.html").read_text(encoding="utf-8")

    def test_panel_reads_the_health_verdict(self):
        s = self._served()
        assert "function gaHealth(" in s
        assert "GA_STATE_COLORS" in s
        assert 'data-testid": "ga-status-pill' in s

    def test_panel_offers_reconnect(self):
        assert 'data-testid": "ga-reconnect' in self._served()

    def test_unknown_health_is_not_rendered_as_connected(self):
        """gaHealth's fallback branch must be the broken one."""
        s = self._served()
        i = s.find("function gaHealth(")
        body = s[i:i + 800]
        assert "healthy: false" in body
        assert "healthy: true" not in body

    def test_sync_line_is_not_a_bare_clock_time(self):
        """`synced 12:25:16 AM` for a nine-day-old sync reads as this morning."""
        s = self._served()
        assert "new Date(acc.last_sync).toLocaleTimeString()" not in s


# ── the helper the agent-facing tools still need to adopt ───────────────────
class TestWorkingAccountsHelper:
    """agent.py still answers "is Google connected?" with has_accounts().

    Four call sites (agent.py 1096/1158/1249/1310) gate on a record existing
    and then emit "connected": True -- agent.py:1123 says so in a comment:
    `# accounts exist and are linked`. That boolean is what told Stephen his
    accounts were fine. These pin the honest replacement so adopting it is a
    one-line change per site.
    """

    def test_has_accounts_and_has_working_accounts_disagree(self):
        _write_accounts(_rec(id="a1", status="needs_reauth"),
                        _rec(id="a2", email="b@x.com", status="needs_reauth"))
        assert ga.has_accounts() is True      # records exist
        assert ga.has_working_accounts() is False   # nothing works

    def test_summary_refuses_to_call_a_dead_store_connected(self):
        _write_accounts(_rec(id="a1", email="primary@example.com",
                             status="needs_reauth", last_sync=_days_ago(9)))
        s = ga.accounts_summary()
        assert s["connected"] is False
        assert s["healthy"] == 0
        assert "primary@example.com" in s["note"]
        assert "9 days ago" in s["note"]

    def test_summary_marks_the_partial_case_a_boolean_cannot_express(self):
        _write_accounts(_rec(id="a1", status="connected"),
                        _rec(id="a2", email="b@x.com", status="needs_reauth"))
        s = ga.accounts_summary()
        assert s["connected"] is True
        assert s["degraded"] is True
        assert "INCOMPLETE" in s["note"]
        assert "b@x.com" in s["note"]

    def test_all_healthy_has_nothing_to_warn_about(self):
        _write_accounts(_rec(id="a1", status="connected"))
        s = ga.accounts_summary()
        assert s["connected"] is True and s["degraded"] is False
        assert s["note"] == ""
        assert s["needs_attention"] == []
