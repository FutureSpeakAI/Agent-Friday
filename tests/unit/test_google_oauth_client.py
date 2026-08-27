"""Two ways to connect Google, and an honest hand-off between them.

Stephen, 2026-08-26: "accept the unverified app warning AND bring your own
with a walkthru. Let's do both."

So there are two clients:

  * BUNDLED — ships with Friday. One click, and a warning screen. Subject to
    Google's 100-new-users-per-project lifetime cap, which cannot be reset.
  * BYO — the user's own Google Cloud client. No cap, no warning, but they
    have to go and make one.

BYO is not a fallback for enthusiasts. It is the ESCAPE HATCH: when the
bundled client hits the cap, it is the only way anyone connects at all. That
makes its precedence load-bearing — a user who has gone to the trouble of
making their own client must never be silently routed back onto the full one.

WHAT THIS FILE DOES NOT TEST is the credential itself. Friday ships with the
bundled constants EMPTY, because only Stephen can mint them (they belong to
his Google Cloud project and name him as the publisher). Empty must therefore
behave exactly like "no bundled client" — the mechanism is complete and inert
until he pastes them in, and a half-configured client must never be offered
as if it worked.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import google_oauth_client as goc  # noqa: E402


class TestBundledIsInertUntilConfigured:
    def test_empty_constants_yield_no_config(self, monkeypatch):
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_ID", "", raising=False)
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_SECRET", "", raising=False)
        assert goc.bundled_config() is None

    def test_half_a_client_is_no_client(self, monkeypatch):
        """An id with no secret cannot complete a flow. Offering it would put
        a user through the warning screen to reach an error."""
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_ID", "123.apps.googleusercontent.com",
                            raising=False)
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_SECRET", "", raising=False)
        assert goc.bundled_config() is None

    def test_a_configured_client_is_a_desktop_client(self, monkeypatch):
        """Only the installed/Desktop flow can use a loopback redirect without
        redirect_uri_mismatch — calendar_engine._google_client_config says so."""
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_ID", "123.apps.googleusercontent.com",
                            raising=False)
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_SECRET", "shh", raising=False)
        cfg = goc.bundled_config()
        assert "installed" in cfg
        assert cfg["installed"]["client_id"].endswith(".apps.googleusercontent.com")


class TestPrecedence:
    """A client the user made themselves always wins."""

    def test_byo_beats_bundled(self, monkeypatch):
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_ID", "b.apps.googleusercontent.com",
                            raising=False)
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_SECRET", "shh", raising=False)
        byo = ({"installed": {"client_id": "mine", "client_secret": "x"}}, "~/.friday/credentials.json")
        cfg, source, kind = goc.resolve_client(byo_result=byo)
        assert kind == "byo"
        assert cfg["installed"]["client_id"] == "mine"
        assert "credentials.json" in source

    def test_bundled_is_used_when_there_is_no_byo(self, monkeypatch):
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_ID", "b.apps.googleusercontent.com",
                            raising=False)
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_SECRET", "shh", raising=False)
        cfg, source, kind = goc.resolve_client(byo_result=(None, None))
        assert kind == "bundled"
        assert cfg["installed"]["client_id"] == "b.apps.googleusercontent.com"

    def test_neither_is_reported_as_none_not_as_an_error(self, monkeypatch):
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_ID", "", raising=False)
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_SECRET", "", raising=False)
        cfg, source, kind = goc.resolve_client(byo_result=(None, None))
        assert cfg is None and kind == "none"


class TestErrorClassification:
    """The cap must land somewhere useful, not in a raw OAuth error."""

    def test_a_named_cap_is_recognised(self):
        assert goc.classify_error(
            "access_denied", "OAuth user cap reached") == goc.CAP_REACHED

    def test_a_bare_access_denied_is_not_claimed_to_be_the_cap(self):
        """Google returns access_denied both when the cap is hit and when the
        user simply clicks Cancel, and does not always say which. Guessing
        would tell someone their app is full when they just changed their
        mind."""
        assert goc.classify_error("access_denied", "") == goc.DECLINED_OR_CAPPED

    def test_a_bad_client_is_its_own_case(self):
        """A BYO client typed in wrong is a different problem with a different
        fix, and must not read as 'Friday is full'."""
        for err in ("invalid_client", "redirect_uri_mismatch"):
            assert goc.classify_error(err, "") == goc.MISCONFIGURED

    def test_a_workspace_admin_block_is_not_our_cap(self):
        assert goc.classify_error("admin_policy_enforced", "") == goc.ADMIN_BLOCKED

    def test_anything_else_is_unknown(self):
        assert goc.classify_error("weird_thing", "") == goc.UNKNOWN

    def test_no_error_at_all_is_unknown(self):
        assert goc.classify_error(None, None) == goc.UNKNOWN


class TestErrorGuidance:
    """Every classification has to end in something the user can do."""

    @pytest.mark.parametrize("code", [
        goc.CAP_REACHED, goc.DECLINED_OR_CAPPED, goc.MISCONFIGURED,
        goc.ADMIN_BLOCKED, goc.UNKNOWN,
    ])
    def test_each_has_wording(self, code):
        text = goc.explain_error(code)
        assert text and len(text) > 40, code

    def test_the_cap_sends_them_to_byo(self):
        low = goc.explain_error(goc.CAP_REACHED).lower()
        assert "your own" in low or "own google" in low

    def test_the_ambiguous_case_offers_both_readings(self):
        """It must not assert the cap, and must not assert they cancelled."""
        low = goc.explain_error(goc.DECLINED_OR_CAPPED).lower()
        assert "cancel" in low
        assert "full" in low or "cap" in low

    def test_no_guidance_tells_anyone_to_place_a_file(self):
        """The whole reason this exists. 'Place a JSON file in this directory'
        is the wall Janet hit on 2026-08-26."""
        for code in (goc.CAP_REACHED, goc.DECLINED_OR_CAPPED, goc.MISCONFIGURED,
                     goc.ADMIN_BLOCKED, goc.UNKNOWN):
            low = goc.explain_error(code).lower()
            assert ".json" not in low, code
            assert "directory" not in low, code


class TestConsentPrebrief:
    """Tell them the warning is coming, BEFORE it appears.

    A person who meets "Google hasn't verified this app" unprepared abandons.
    A person who was told it is coming, and why, clicks through.
    """

    def test_the_bundled_path_warns_about_the_warning(self):
        text = goc.consent_prebrief("bundled")
        low = text.lower()
        assert "hasn't verified" in low or "has not verified" in low
        assert "advanced" in low, "must name the button they have to click"

    def test_it_says_who_the_app_is(self):
        """The screen names the developer. Someone who does not recognise the
        name assumes phishing."""
        assert "Friday" in goc.consent_prebrief("bundled")

    def test_byo_gets_no_scare_copy(self):
        """Their own client shows no warning — promising one would be a lie
        and would make them think something is wrong."""
        low = goc.consent_prebrief("byo").lower()
        assert "hasn't verified" not in low and "has not verified" not in low

    def test_the_prebrief_says_what_friday_will_read(self):
        low = goc.consent_prebrief("bundled").lower()
        assert "calendar" in low and "mail" in low


class TestByoWalkthrough:
    """Guided, in order, ending in a paste field. Never a file drop."""

    def test_the_steps_are_ordered_and_numbered(self):
        steps = goc.byo_steps()
        assert len(steps) >= 5
        assert [s["n"] for s in steps] == list(range(1, len(steps) + 1))

    def test_every_step_says_what_to_click(self):
        for s in goc.byo_steps():
            assert s.get("do"), s
            assert len(s["do"]) > 10, s

    def test_the_console_is_linked_not_described(self):
        joined = " ".join((s.get("url") or "") for s in goc.byo_steps())
        assert "console.cloud.google.com" in joined

    def test_the_scopes_are_listed_for_copying(self):
        """Step: 'add these scopes'. Without the list the user guesses, and a
        missing scope fails later as a 403 they cannot interpret."""
        scopes = goc.byo_scopes()
        assert len(scopes) >= 6
        assert all(s.startswith("https://www.googleapis.com/auth/") for s in scopes)

    def test_it_ends_with_pasting_credentials_not_saving_a_file(self):
        last = goc.byo_steps()[-1]
        blob = (last["do"] + " " + last.get("detail", "")).lower()
        assert "paste" in blob
        assert ".json" not in blob and "directory" not in blob and "folder" not in blob

    def test_the_desktop_client_type_is_specified(self):
        """A Web client cannot use the loopback redirect. Getting this wrong
        produces redirect_uri_mismatch, which reads as Friday being broken."""
        blob = " ".join(s["do"] + " " + s.get("detail", "") for s in goc.byo_steps())
        assert "Desktop" in blob


# ── Where a pasted client goes ───────────────────────────────────────────────
#
# The walkthrough ends in "paste these two values", so they need somewhere to
# live that is NOT a file the user has to manage. Encrypted, on this machine,
# via the same credential store that holds API keys — which is also what the
# walkthrough's last step promises, and a promise the UI makes has to be true.

class TestByoStorage:
    @pytest.fixture(autouse=True)
    def _store(self, monkeypatch):
        held = {}
        import agent_friday.services.credential_store as cs
        monkeypatch.setattr(cs, "set_provider_key",
                            lambda p, k: held.__setitem__(p, k) or "test",
                            raising=False)
        monkeypatch.setattr(cs, "get_provider_key", lambda p: held.get(p),
                            raising=False)
        monkeypatch.setattr(cs, "delete_provider_key",
                            lambda p: held.pop(p, None) is not None,
                            raising=False)
        self.held = held

    def test_a_pasted_client_round_trips(self):
        goc.save_byo("abc.apps.googleusercontent.com", "shh")
        cfg = goc.stored_byo_config()
        assert cfg["installed"]["client_id"] == "abc.apps.googleusercontent.com"
        assert cfg["installed"]["client_secret"] == "shh"

    def test_it_is_stored_as_a_desktop_client(self):
        goc.save_byo("abc.apps.googleusercontent.com", "shh")
        assert "installed" in goc.stored_byo_config()

    def test_nothing_stored_is_none_not_an_empty_shell(self):
        assert goc.stored_byo_config() is None

    def test_half_a_pasted_client_is_refused_at_the_door(self):
        """Better to reject the paste than to store something that will fail
        at the consent screen, where the cause is invisible."""
        with pytest.raises(ValueError):
            goc.save_byo("abc.apps.googleusercontent.com", "   ")
        with pytest.raises(ValueError):
            goc.save_byo("", "shh")

    def test_clearing_removes_it(self):
        goc.save_byo("abc.apps.googleusercontent.com", "shh")
        assert goc.clear_byo() is True
        assert goc.stored_byo_config() is None

    def test_a_stored_client_outranks_the_bundled_one(self, monkeypatch):
        """The escape hatch again: whoever completed the walkthrough did it
        for a reason, and must not be silently put back on the shared client."""
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_ID", "b.apps.googleusercontent.com",
                            raising=False)
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_SECRET", "shh", raising=False)
        goc.save_byo("mine.apps.googleusercontent.com", "mysecret")
        cfg, source, kind = goc.active_client(discover=lambda: (None, None))
        assert kind == "byo"
        assert cfg["installed"]["client_id"] == "mine.apps.googleusercontent.com"

    def test_a_discovered_file_still_works_for_existing_installs(self, monkeypatch):
        """Stephen already has a client_secret*.json on disk. Adding a new
        storage location must not strand it."""
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_ID", "b.apps.googleusercontent.com",
                            raising=False)
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_SECRET", "shh", raising=False)
        found = ({"installed": {"client_id": "ondisk", "client_secret": "x"}},
                 "~/.friday/credentials.json")
        cfg, source, kind = goc.active_client(discover=lambda: found)
        assert kind == "byo"
        assert cfg["installed"]["client_id"] == "ondisk"

    def test_the_bundled_client_is_the_last_resort(self, monkeypatch):
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_ID", "b.apps.googleusercontent.com",
                            raising=False)
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_SECRET", "shh", raising=False)
        cfg, source, kind = goc.active_client(discover=lambda: (None, None))
        assert kind == "bundled"

    def test_a_broken_discover_does_not_take_the_flow_down(self, monkeypatch):
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_ID", "b.apps.googleusercontent.com",
                            raising=False)
        monkeypatch.setattr(goc, "BUNDLED_CLIENT_SECRET", "shh", raising=False)

        def _boom():
            raise RuntimeError("disk on fire")

        cfg, source, kind = goc.active_client(discover=_boom)
        assert kind == "bundled"
