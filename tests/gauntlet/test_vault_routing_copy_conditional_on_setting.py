"""Gauntlet finding Q19 (3 extra loci, copy-only -- the main _MODE_MEANING
local_only routing-mode text and its own behavioral fix are tracked
separately and NOT touched here): a false absolute claim -- "vault-touching
turns always go local" / "never leaves this machine" / "no matter what mode
is configured" -- was independently hardcoded in three more places besides
the original finding. All three describe `routing/model_router.py`'s
`_route_vault`, which explicitly returns None (does NOT force local)
whenever `vault_local_only` is off -- confirmed by reading `_route_vault`
directly (it returns None at the `if not policy.force_local_routing: return
None` line before ever picking a local model).

The three loci fixed here:
  (a) ui_parts/app.html's own independently-hardcoded MODE_MEANING dict,
      smart mode's entry ("vault-touching turns always go local").
  (b) The Work Proposal list's touches_vault badge tooltip, in BOTH
      index.html and ui_parts/app.html ("which never leaves this
      machine").
  (c) services/work_queue.py's enqueue() docstring and its ValueError
      message, both of which described _route_vault as forcing local "no
      matter what mode is configured."

This probe does NOT touch (and does not assert about) seat_transparency.py's
own _MODE_MEANING dict or its local_only entry -- that is Q19's main finding,
handled separately, and tests/unit/test_seat_transparency.py already pins
its current (still-being-worked) text.

Red -> green -> red-on-revert proof: each assertion fails against the old
literal claim (proving the file really made it) and passes against the
corrected, conditional wording.
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_INDEX_HTML = _REPO_ROOT / "index.html"
_APP_HTML = _REPO_ROOT / "ui_parts" / "app.html"
_WORK_QUEUE_PY = (_REPO_ROOT / "src" / "agent_friday" / "services" /
                  "work_queue.py")

_OLD_SMART_MODE_CLAIM = "vault-touching turns always go local."
_OLD_BADGE_CLAIM = "reads vault-tier material, which never leaves this machine"
_OLD_DOCSTRING_CLAIM = "forces a local route no\n    matter what mode is configured"


def _old_claims_would_fail_these_assertions():
    """Not a real test -- documents that the assertions below are
    discriminating (fail against the pre-fix text)."""
    assert _OLD_SMART_MODE_CLAIM in (
        "The router picks the seat per turn — vault-touching turns always go local."
    )
    assert _OLD_BADGE_CLAIM == (
        "reads vault-tier material, which never leaves this machine"
    )


class TestVaultRoutingCopyConditionalOnSetting:
    def test_app_html_smart_mode_meaning_is_now_conditional(self):
        text = _APP_HTML.read_text(encoding="utf-8")
        assert _OLD_SMART_MODE_CLAIM not in text, (
            "ui_parts/app.html's smart-mode MODE_MEANING entry still "
            "claims vault-touching turns ALWAYS go local -- _route_vault "
            "returns None (does not force local) whenever vault_local_only "
            "is off; see findings.jsonl Q19"
        )
        assert "smart:" in text
        idx = text.index("smart:")
        window = text[idx:idx + 200]
        assert "Vault Local-Only" in window, (
            "the corrected smart-mode description should condition the "
            "vault-forcing claim on the Vault Local-Only setting"
        )

    def test_work_proposal_badge_conditional_in_both_html_files(self):
        for path in (_INDEX_HTML, _APP_HTML):
            text = path.read_text(encoding="utf-8")
            assert _OLD_BADGE_CLAIM not in text, (
                f"{path.name}'s Work Proposal touches_vault badge still "
                "claims vault-tier material never leaves this machine, "
                "unconditionally; see findings.jsonl Q19"
            )
            assert "vault-tier material" in text
            idx = text.index("vault-tier material")
            window = text[max(0, idx - 20):idx + 150]
            assert "Vault Local-Only" in window, (
                f"{path.name}'s corrected badge tooltip should condition "
                "the never-leaves-this-machine claim on Vault Local-Only"
            )

    def test_work_queue_docstring_no_longer_claims_unconditional_forcing(self):
        text = _WORK_QUEUE_PY.read_text(encoding="utf-8")
        assert "no\n    matter what mode is configured" not in text and \
            "no matter what mode is configured" not in text, (
                "work_queue.py's enqueue() docstring still describes "
                "_route_vault as forcing local 'no matter what mode is "
                "configured' -- it returns None whenever vault_local_only "
                "is off; see findings.jsonl Q19"
            )
        assert "vault_local_only" in text, (
            "the corrected docstring should name the setting _route_vault "
            "actually keys off"
        )

    def test_work_queue_valueerror_still_pins_pre_existing_test_substring(self):
        """Grounding check: the pre-existing test
        tests/unit/test_work_queue.py::test_vault_work_cannot_be_queued_for_the_cloud
        asserts 'never leaves the machine' in the raised ValueError's
        message. That test file is off-limits to edit, so the corrected
        message must keep this substring at runtime -- checked here by
        actually calling enqueue(), not by grepping the raw .py source
        (the phrase is written as two adjacent string literals split
        across lines with 'the ' ending one line and 'machine' starting
        the next, which Python concatenates at runtime but which a raw
        source-text grep would miss)."""
        import pytest as _pytest
        from agent_friday.services import work_queue as _wq

        with _pytest.raises(ValueError) as exc_info:
            _wq.enqueue("read the vault", "x", cls="heavy",
                        disposition="now_cloud", touches_vault=True)
        message = str(exc_info.value)
        assert "never leaves the machine" in message, (
            "the ValueError message lost the 'never leaves the machine' "
            "substring that tests/unit/test_work_queue.py (off-limits to "
            "edit) asserts on"
        )
        assert "Vault Local-Only" in message, (
            "the ValueError message still frames _route_vault's forcing as "
            "unconditional -- it should name Vault Local-Only as the "
            "condition, matching _route_vault's real behavior; see "
            "findings.jsonl Q19"
        )

    def test_route_vault_still_returns_none_when_vault_local_only_is_off(self):
        """Grounding check: confirms the corrected 'conditional' copy is
        actually true right now by reading _route_vault's own early-exit,
        without calling into it (no live settings/model state needed)."""
        model_router_py = (_REPO_ROOT / "src" / "agent_friday" / "routing" /
                            "model_router.py").read_text(encoding="utf-8")
        start = model_router_py.index("def _route_vault(")
        next_def = model_router_py.index("\n    def ", start + 1)
        body = model_router_py[start:next_def]
        assert "if not policy.force_local_routing:" in body
        assert "return None" in body, (
            "_route_vault no longer appears to return None when vault "
            "gating is off -- if it now always forces local, Q19's "
            "corrected 'conditional' copy needs revisiting"
        )
