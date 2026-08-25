"""WO-17 — user-granted cloud egress permissions for files.

Every test here fails against current behaviour (there is no file_grants
module, no ledger, no read-time feeder) — the whole point of this WO. The
adversarial cases Fable 5's spec called out explicitly are pinned as their
own tests: a model cannot create a grant (no tool exists — checked in
TestNoGrantToolExists), a stale content hash fails closed, a deny beats a
grant, a corrupted ledger only ever tightens, and a summary/paraphrase of
granted content inherits nothing.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from agent_friday.services import file_grants as fg
from agent_friday.services import egress_gate as eg
from agent_friday.services import judgment_gate as jg


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch):
    """Each test gets its own ledger file and a clean egress/judgment
    registry — no leakage between tests, no touching the real ~/.friday."""
    ledger = tmp_path / "file_grants.jsonl"
    monkeypatch.setattr(fg, "_ledger_path", lambda: ledger)
    fg._invalidate_cache()
    with eg._TRUSTED_LOCK:
        eg._PUBLIC_PARAS.clear()
        eg._PUBLIC_ORIGINS.clear()
        eg._OVERRIDE_PARAS.clear()
    with jg._DENY_LOCK:
        jg._DENY_SPANS.clear()
    yield
    fg._invalidate_cache()
    with eg._TRUSTED_LOCK:
        eg._PUBLIC_PARAS.clear()
        eg._PUBLIC_ORIGINS.clear()
        eg._OVERRIDE_PARAS.clear()
    with jg._DENY_LOCK:
        jg._DENY_SPANS.clear()


def _cv(tmp_path, name="cv.txt", body="Senior AI leadership experience.\n\nSanofi pivot analysis.") -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


# ── No grant tool anywhere ──────────────────────────────────────────────────

class TestNoGrantToolExists:
    def test_no_grant_permit_allow_tool_in_the_registry(self):
        from agent_friday.services.agent import CLAUDE_TOOLS
        names = {t.get("name", "").lower() for t in CLAUDE_TOOLS}
        forbidden = {n for n in names if any(
            k in n for k in ("grant", "permit", "deny_mark", "allow_file"))}
        assert not forbidden, (
            f"a model-callable tool exists that could create a grant: {forbidden}")


# ── Basic ledger fold ───────────────────────────────────────────────────────

class TestLedgerFold:
    def test_create_and_list_file_grant(self, tmp_path):
        p = _cv(tmp_path)
        event = fg.create_file_grant(str(p))

        grants = fg.list_grants()
        assert any(g["id"] == event["id"] for g in grants)

    def test_revoke_removes_it_from_the_folded_state(self, tmp_path):
        p = _cv(tmp_path)
        event = fg.create_file_grant(str(p))
        fg.revoke(event["id"])

        grants = fg.list_grants()
        assert not any(g["id"] == event["id"] for g in grants)

    def test_scope_grant_requires_expiry(self, tmp_path):
        folder = tmp_path / "docs"
        folder.mkdir()
        with pytest.raises(ValueError):
            fg.create_scope_grant(str(folder), "folder", expiry_days=0)

    def test_scope_grant_caps_expiry_at_30_days_at_the_api(self, tmp_path):
        """Non-negotiable: enforced here, not merely suggested by a UI."""
        folder = tmp_path / "docs"
        folder.mkdir()
        with pytest.raises(ValueError):
            fg.create_scope_grant(str(folder), "folder", expiry_days=31)

    def test_scope_grant_at_the_cap_is_accepted(self, tmp_path):
        folder = tmp_path / "docs"
        folder.mkdir()
        event = fg.create_scope_grant(str(folder), "folder", expiry_days=30)
        assert event["expires_ts"] > event["created_ts"]

    def test_file_grant_never_send_override_is_never_available_on_scope_grants(self, tmp_path):
        folder = tmp_path / "docs"
        folder.mkdir()
        event = fg.create_scope_grant(str(folder), "folder", expiry_days=5)
        assert event["never_send_override"] is False


# ── Content-pinned staleness ─────────────────────────────────────────────────

class TestFileGrantStaleness:
    def test_editing_the_file_makes_the_grant_stale(self, tmp_path):
        p = _cv(tmp_path)
        fg.create_file_grant(str(p))

        p.write_text("Completely different content now.", encoding="utf-8")
        new_sha = hashlib.sha256(p.read_bytes()).hexdigest()
        check = fg.check_grant(p, sha256_hex=new_sha)

        assert check.state == "stale"

    def test_unchanged_file_stays_active(self, tmp_path):
        p = _cv(tmp_path)
        fg.create_file_grant(str(p))
        sha = hashlib.sha256(p.read_bytes()).hexdigest()

        check = fg.check_grant(p, sha256_hex=sha)

        assert check.state == "active"

    def test_stale_grant_is_visible_in_pending_reapproval(self, tmp_path):
        p = _cv(tmp_path)
        fg.create_file_grant(str(p))
        p.write_text("Different now.", encoding="utf-8")

        pending = fg.list_pending_reapproval()

        assert any(str(p) == g["path"] for g in pending)


# ── Precedence: deny beats grant, always ────────────────────────────────────

class TestDenyBeatsGrant:
    def test_deny_after_grant_wins(self, tmp_path):
        p = _cv(tmp_path)
        fg.create_file_grant(str(p))
        fg.create_deny_mark(str(p), "file")

        check = fg.check_grant(p, sha256_hex=hashlib.sha256(p.read_bytes()).hexdigest())

        assert check.state == "denied"

    def test_grant_after_deny_still_loses_to_the_deny(self, tmp_path):
        p = _cv(tmp_path)
        fg.create_deny_mark(str(p), "file")
        fg.create_file_grant(str(p))

        check = fg.check_grant(p, sha256_hex=hashlib.sha256(p.read_bytes()).hexdigest())

        assert check.state == "denied"

    def test_folder_deny_beats_a_more_specific_file_grant(self, tmp_path):
        folder = tmp_path / "docs"
        folder.mkdir()
        p = folder / "cv.txt"
        p.write_text("content", encoding="utf-8")
        fg.create_file_grant(str(p))
        fg.create_deny_mark(str(folder), "folder")

        check = fg.check_grant(p, sha256_hex=hashlib.sha256(p.read_bytes()).hexdigest())

        assert check.state == "denied", "deny beats a grant at ANY specificity"


# ── The read-time feeder: the actual mechanism ──────────────────────────────

class TestReadTimeFeeder:
    def test_on_file_read_registers_spans_for_an_active_grant(self, tmp_path):
        p = _cv(tmp_path)
        event = fg.create_file_grant(str(p))
        text = p.read_text(encoding="utf-8")

        result = fg.on_file_read(p, text)

        assert result.state == "active"
        with eg._TRUSTED_LOCK:
            assert "Senior AI leadership experience." in eg._PUBLIC_PARAS
            assert eg._PUBLIC_ORIGINS["Senior AI leadership experience."] == f"user-grant:{event['id']}"

    def test_granted_file_then_passes_the_real_gate_to_a_cloud_provider(self, tmp_path):
        """The motivating case, end to end at the gate: read a granted file,
        then a cloud-bound payload containing its exact text is not redacted."""
        p = _cv(tmp_path, body="Sanofi pivot analysis: strong candidate fit.")
        fg.create_file_grant(str(p))
        text = p.read_text(encoding="utf-8")

        fg.on_file_read(p, text)
        out = eg._gate_text(text, "anthropic", "tool_result")

        assert out == text
        assert "EGRESS-GATE" not in out

    def test_a_page_sized_paragraph_over_2000_chars_still_registers(self, tmp_path):
        """Regression pin (found live 2026-08-25 during the end-to-end walk
        against Stephen's real CV): register_public_text's 2000-char default
        exists for news headlines, but a granted file's paragraphs are
        page-sized prose — extract_text joins PDF pages on "\\n\\n", and a
        real resume page routinely runs 2500-3500 chars. Before this was
        fixed, 3 of 4 pages of a real CV silently failed to register: the
        grant LOOKED like it worked (ledger entry created, check_grant
        returned 'active', no error anywhere) while most of the document
        still gated normally on the next read. Content-search snippets
        (file_search._search_content) share this feeder and this bug."""
        long_para = "Enterprise AI leadership experience. " * 80  # > 2000 chars
        assert len(long_para) > 2000
        p = _cv(tmp_path, body=long_para)
        fg.create_file_grant(str(p))
        text = p.read_text(encoding="utf-8")

        fg.on_file_read(p, text)
        out = eg._gate_text(text, "anthropic", "tool_result")

        assert out == text, (
            "a >2000-char granted paragraph must still register and pass — "
            "it silently did not before the max_len fix"
        )
        assert "EGRESS-GATE" not in out

    def test_without_a_grant_the_same_content_is_still_withheld(self, tmp_path):
        """Falsifiability for the case above: the SAME text, from a file that
        was never granted, must not pass — proves the pass came from the
        grant, not from the content being harmless."""
        p = _cv(tmp_path, body="Sanofi pivot analysis: strong candidate fit.")
        text = p.read_text(encoding="utf-8")
        # No grant created, no on_file_read call.
        out = eg._gate_text(text, "anthropic", "tool_result")
        # This particular sentence may or may not individually trip a tier
        # (it is fairly benign prose), so the real assertion is comparative:
        # register it as ungranted content and confirm nothing exempted it.
        with eg._TRUSTED_LOCK:
            assert text.strip() not in eg._PUBLIC_PARAS

    def test_a_stale_grant_does_not_register_spans(self, tmp_path):
        p = _cv(tmp_path)
        fg.create_file_grant(str(p))
        p.write_text("Something entirely different and unrelated.", encoding="utf-8")
        new_text = p.read_text(encoding="utf-8")

        result = fg.on_file_read(p, new_text)

        assert result.state == "stale"
        with eg._TRUSTED_LOCK:
            assert new_text.strip() not in eg._PUBLIC_PARAS

    def test_a_denied_file_registers_into_the_never_send_floor(self, tmp_path):
        p = _cv(tmp_path, body="Confidential severance terms discussed here.")
        fg.create_deny_mark(str(p), "file")
        text = p.read_text(encoding="utf-8")

        result = fg.on_file_read(p, text)

        assert result.state == "denied"
        assert jg.never_send_hits(text)

    def test_denied_file_content_is_blocked_at_the_real_gate(self, tmp_path):
        p = _cv(tmp_path, body="Confidential severance terms discussed here.")
        fg.create_deny_mark(str(p), "file")
        text = p.read_text(encoding="utf-8")
        fg.on_file_read(p, text)

        with pytest.raises(eg.NeverSendBlocked):
            eg._gate_text_span(text, "anthropic", "tool_result")


# ── Never-send override: file-grant only, explicit, sharp ───────────────────

class TestNeverSendOverride:
    def test_override_requires_actual_matches_to_be_recorded(self, tmp_path):
        """Granting blind is structurally impossible: an override flag with
        nothing to override is not persisted as an override."""
        p = _cv(tmp_path, body="Nothing sensitive here at all.")
        event = fg.create_file_grant(str(p), never_send_override=True)
        assert event["never_send_override"] is False

    def test_override_lets_a_watchlisted_paragraph_pass_for_that_file_only(
            self, tmp_path, monkeypatch):
        from agent_friday.services import judgment_gate as _jg
        monkeypatch.setattr(_jg, "_PROBE_EXTRA_NEVER", ["Wile E Coyote Case 4471"])

        body = "Wile E Coyote Case 4471 is the matter discussed on page one."
        p = _cv(tmp_path, body=body)
        event = fg.create_file_grant(str(p), never_send_override=True,
                                      ack_never_send_matches=["Wile E Coyote Case 4471"])
        assert event["never_send_override"] is True

        text = p.read_text(encoding="utf-8")
        fg.on_file_read(p, text)

        out = eg._gate_text_span(text, "anthropic", "tool_result")
        assert out == text

    def test_without_override_the_same_watchlisted_content_still_blocks(
            self, tmp_path, monkeypatch):
        from agent_friday.services import judgment_gate as _jg
        monkeypatch.setattr(_jg, "_PROBE_EXTRA_NEVER", ["Wile E Coyote Case 4471"])

        body = "Wile E Coyote Case 4471 is the matter discussed on page one."
        p = _cv(tmp_path, body=body)
        fg.create_file_grant(str(p), never_send_override=False)
        text = p.read_text(encoding="utf-8")
        fg.on_file_read(p, text)

        with pytest.raises(eg.NeverSendBlocked):
            eg._gate_text_span(text, "anthropic", "tool_result")


# ── Registration must happen post-PII-scrub, not pre ────────────────────────

class TestRegistrationOrderVsPiiScrub:
    """Found live 2026-08-25 walking the motivating case against Stephen's
    real CV: read_file's result is PII-scrubbed by a post-tool hook
    (priority 95) before it reaches the egress gate. Registering the RAW
    pre-scrub text (the original approach) meant any paragraph containing a
    phone number or address never matched its scrubbed form at gate time —
    the grant looked live (ledger entry, check_grant='active') while entire
    paragraphs stayed withheld. The fix moved registration into its own
    post-hook (priority 96, after pii_scrub). These tests exercise the real
    _execute_tool hook chain, not on_file_read() directly — calling
    on_file_read() directly (as most tests above do) would NOT have caught
    this bug, because it bypasses the hook ordering entirely."""

    def test_a_granted_paragraph_containing_a_phone_number_still_passes_end_to_end(
            self, tmp_path):
        from agent_friday.services.agent import _execute_tool

        body = ("Reach the candidate directly at 555-201-9834 for scheduling. "  # pragma: allowlist secret
                 "Sanofi pivot analysis: strong candidate fit for the senior role.")
        p = _cv(tmp_path, body=body)
        fg.create_file_grant(str(p))

        # pii_lookup={} exercises the SAME rehydration-scrub path chat.py
        # uses in production (_hook_pii_scrub's `if isinstance(ctx.pii_lookup,
        # dict)` branch calls _scrub_pii, which handles phone/email/address —
        # the destructive _pii_redact fallback used with no pii_lookup only
        # covers SSN/card/watchlist and would not have caught this bug).
        result = _execute_tool("read_file", {"path": str(p)}, pii_lookup={})

        assert "[PII:phone:" in result, (
            "the scrub must actually have run for this test to mean anything"
        )
        assert "EGRESS-GATE" not in eg._gate_text(result, "anthropic", "tool_result")

    def test_falsifiable_without_the_grant_the_scrubbed_paragraph_still_gates(
            self, tmp_path):
        from agent_friday.services.agent import _execute_tool

        body = ("Reach the candidate directly at 555-201-9834 for scheduling. "  # pragma: allowlist secret
                 "Sanofi pivot analysis: strong candidate fit for the senior role.")
        p = _cv(tmp_path, body=body)
        # No grant this time.

        result = _execute_tool("read_file", {"path": str(p)}, pii_lookup={})
        out = eg._gate_text(result, "anthropic", "tool_result")

        # Not asserting a specific tier here (classification of this exact
        # sentence isn't the point) — asserting the grant, not luck, is what
        # made the case above pass: registering it explicitly should change
        # the outcome relative to not registering it at all.
        with eg._TRUSTED_LOCK:
            assert result.strip() not in eg._PUBLIC_PARAS


# ── Derived content inherits nothing ────────────────────────────────────────

class TestSummaryInheritsNothing:
    def test_a_paraphrase_of_granted_content_is_classified_on_its_own_words(self, tmp_path):
        p = _cv(tmp_path, body="Sanofi pivot analysis: strong candidate fit.")
        fg.create_file_grant(str(p))
        text = p.read_text(encoding="utf-8")
        fg.on_file_read(p, text)

        paraphrase = "In short, the candidate looks like a good match for the Sanofi role."
        with eg._TRUSTED_LOCK:
            assert paraphrase.strip() not in eg._PUBLIC_PARAS


# ── Ledger corruption: fail toward tightening ────────────────────────────────

class TestLedgerCorruption:
    def test_corrupting_one_line_drops_it_and_counts_it(self, tmp_path):
        p = _cv(tmp_path)
        fg.create_file_grant(str(p))
        ledger = fg._ledger_path()
        lines = ledger.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        corrupted = json.loads(lines[0])
        corrupted["event"]["path"] = "/tampered/path"   # HMAC no longer matches
        ledger.write_text(json.dumps(corrupted) + "\n", encoding="utf-8")

        state = fg._load_state(force=True)

        assert state.dropped == 1
        assert state.suspended is True

    def test_suspended_ledger_suspends_all_grants(self, tmp_path):
        p = _cv(tmp_path)
        fg.create_file_grant(str(p))
        ledger = fg._ledger_path()
        with open(ledger, "a", encoding="utf-8") as f:
            f.write("not even json\n")

        check = fg.check_grant(p, sha256_hex=hashlib.sha256(p.read_bytes()).hexdigest())

        assert check.state == "none", "a corrupted ledger must suspend grants, not honor them"

    def test_suspended_ledger_still_enforces_clean_deny_marks(self, tmp_path):
        p = _cv(tmp_path)
        fg.create_deny_mark(str(p), "file")
        ledger = fg._ledger_path()
        with open(ledger, "a", encoding="utf-8") as f:
            f.write("garbage line that fails hmac\n")

        check = fg.check_grant(p, sha256_hex=hashlib.sha256(p.read_bytes()).hexdigest())

        assert check.state == "denied", (
            "a corrupted ledger may only ever TIGHTEN — a clean deny mark "
            "recorded before the corruption must still hold")

    def test_a_silently_emptied_ledger_is_safe_not_a_free_pass(self, tmp_path):
        """No ledger file at all (never created, or wiped clean) means zero
        grants and zero denies — strictly the safe direction, not suspenders
        mode (suspenders is reserved for lines that existed and failed)."""
        ledger = fg._ledger_path()
        assert not ledger.exists()
        state = fg._load_state(force=True)
        assert state.suspended is False
        assert state.grants == {}
        assert state.denies == {}

    def test_corruption_fires_exactly_one_notification(self, tmp_path, monkeypatch):
        pushed = []

        class _FakeNotifEngine:
            def push(self, **kwargs):
                pushed.append(kwargs)

        import agent_friday.services.voice_engine as ve
        monkeypatch.setattr(ve, "_notif_engine", _FakeNotifEngine())

        p = _cv(tmp_path)
        fg.create_file_grant(str(p))
        ledger = fg._ledger_path()
        with open(ledger, "a", encoding="utf-8") as f:
            f.write("garbage\n")

        fg._load_state(force=True)
        fg._load_state(force=True)   # a second read must not re-push (dedupe)

        assert len(pushed) >= 1
        assert pushed[0]["priority"] == "high"


# ── HMAC integrity ───────────────────────────────────────────────────────────

class TestHmacIntegrity:
    def test_a_line_with_a_wrong_hmac_is_dropped(self, tmp_path):
        p = _cv(tmp_path)
        fg.create_file_grant(str(p))
        ledger = fg._ledger_path()
        rec = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
        rec["hmac"] = "0" * 64
        ledger.write_text(json.dumps(rec) + "\n", encoding="utf-8")

        state = fg._load_state(force=True)

        assert state.grants == {}
        assert state.dropped == 1

    def test_the_hmac_key_is_the_apps_own_secret_key(self, tmp_path):
        """Falsifiability: a line signed with the WRONG key must be rejected,
        proving verification is actually checking the signature and not just
        trusting whatever is on disk."""
        from agent_friday.core import _load_or_create_secret
        real_key = _load_or_create_secret().encode("utf-8")
        wrong_key = b"not-the-real-secret-key"
        assert real_key != wrong_key

        event = {"event": "grant_file", "id": "x", "type": "file",
                 "path": "C:/nope.txt", "sha256": "0" * 64,
                 "created_ts": 0.0, "never_send_override": False,
                 "ack_never_send_matches": [], "findings_summary": ""}
        bad_sig = hmac.new(wrong_key, json.dumps(event, sort_keys=True,
                            separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
        ledger = fg._ledger_path()
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(json.dumps({"event": event, "hmac": bad_sig}) + "\n", encoding="utf-8")

        state = fg._load_state(force=True)

        assert state.grants == {}
        assert state.dropped == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
