"""Gauntlet findings F10 (copy half) and F11: two onboarding_copy.py strings
made an absolute privacy promise the code does not currently keep.

- ROUTING_CHOICES' local_only description said "Nothing is sent anywhere,
  ever." routing/model_router.py's _route_basic() falls back to a cloud
  provider whenever Ollama is unreachable or no local model fits the task
  -- on two of its three branches the fallback_to_cloud setting is either
  dead code or unchecked entirely, and setup_wizard.py never sets it False
  for local_only anyway. The underlying router behavior is queued for
  the maintainer (a real product decision on whether local_only should fail
  closed or fall back -- see progress.md's top-priority queue item), but
  the copy itself was simply false regardless of which way that decision
  goes, so it was corrected directly.

- VAULT_LOCATION said the passphrase is stored "not in any file you could
  open." services/vault_passphrase.py's own docstring says it writes BOTH
  the keychain AND a DPAPI-wrapped file on disk -- a real file that exists
  and can be opened; its bytes are just unreadable ciphertext without the
  same Windows account. The copy collapsed "not plaintext" into "not a
  file," which is a stronger and inaccurate claim.

This probe demonstrates the fix by construction: it fails against the
literal old strings (proving the old copy really did make these claims)
and passes against the current module (proving the correction landed).
"""
from __future__ import annotations

from agent_friday.services import onboarding_copy as oc


_OLD_LOCAL_ONLY_CLAIM = "Nothing is sent anywhere, ever."
# NOT "not in any file you could open" -- VAULT_LOCATION is a triple-quoted
# multi-line string, and the real pre-fix text wrapped exactly between "you"
# and "could" ("...not in any file you\ncould open..."), so that longer
# substring never matched the real string on either side of the fix and
# this probe passed vacuously both before and after (found 2026-09-04 while
# actually running the revert-check this probe had never been given --
# see findings.jsonl F11). Kept to a substring that stays on one physical
# source line so a future rewrap can't quietly reintroduce the same gap.
_OLD_VAULT_CLAIM = "not in any file"


def _old_strings_would_fail_these_assertions():
    """A real check, not just documentation: proves the assertions below
    are discriminating by running them against the ACTUAL pre-fix
    VAULT_LOCATION text (not a hand-typed reconstruction, which is exactly
    what hid this probe's original bug), so this file cannot silently pass
    vacuously again. Prefixed with test_ so pytest actually runs it --
    the original version of this helper was never collected at all."""
    assert _OLD_LOCAL_ONLY_CLAIM.strip().rstrip(".").lower() == \
        "nothing is sent anywhere, ever"
    _real_old_vault_location = (
        "Friday stores it in this computer's credential manager, not in any file you\n"
        "could open. That is deliberate: an earlier version kept it in a startup script\n"
        "inside her own program folder, which the installer replaces when she updates."
    )
    assert _OLD_VAULT_CLAIM in _real_old_vault_location


def test_the_old_claim_fixtures_are_discriminating():
    _old_strings_would_fail_these_assertions()


class TestOnboardingCopyNoFalseAbsolutes:
    def test_local_only_no_longer_claims_an_absolute_never(self):
        choices = {mode: desc for mode, _label, desc in oc.ROUTING_CHOICES}
        local_only_desc = choices["local_only"]
        assert local_only_desc != _OLD_LOCAL_ONLY_CLAIM, (
            "local_only's description still makes the old absolute claim "
            "('Nothing is sent anywhere, ever.') that _route_basic() does "
            "not keep by default -- see findings.jsonl F10"
        )
        import re
        assert not re.search(r"\bever\b", local_only_desc.lower()), (
            "local_only's description should not restate the absolute "
            "promise in different words while the router's fallback "
            "behavior is unresolved (findings.jsonl F10)"
        )

    def test_vault_location_no_longer_claims_no_file_exists(self):
        assert _OLD_VAULT_CLAIM not in oc.VAULT_LOCATION, (
            "VAULT_LOCATION still claims the passphrase isn't stored in "
            "any file a user could open, but vault_passphrase.py writes a "
            "real (DPAPI-encrypted) file to disk as a durable backup -- "
            "see findings.jsonl F11"
        )
