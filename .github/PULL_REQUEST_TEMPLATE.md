## Summary

<!-- What does this change do, and why? One paragraph is fine. Link issues with "Closes #N". -->

## Changes

-

## How you verified it

<!-- A bug fix includes a test that fails before the change and passes after it. -->

- [ ] A test covers the change (for a bug fix, it fails without the fix)
- [ ] `pytest tests/unit tests/api -q`
- [ ] `pytest tests/security tests/test_egress_adversarial.py tests/test_judgment_gate.py -q` (when the change touches privacy, governance or egress)
- [ ] `python scripts/check_imports.py`
- [ ] `ruff check --select E9,F63,F7,F82 .`
- [ ] `python scripts/check_gated_prompt_callers.py`
- [ ] `python scripts/check_settings_readers.py`
- [ ] `python scripts/check_stale_model_names.py`
- [ ] `python scripts/check_brand_tokens.py`
- [ ] `python scripts/check_trust_in_governance.py`
- [ ] `python scripts/check_doc_links.py` (when the change touches documentation)
- [ ] `python .githooks/security_scan.py --tree`
- [ ] A UI change edits both `index.html` and `ui_parts/app.html`

## Repository hygiene

- [ ] No credentials, personal identifiers or local paths that contain a username
- [ ] The commit messages describe the change and the rule it protects
- [ ] No new dependency (or one was agreed in an issue first)

## Sensitive subsystems

Does this change touch any of these? If so, describe the security impact and how
you tested it.

- [ ] `src/agent_friday/privacy/`
- [ ] `src/agent_friday/governance/`
- [ ] `src/agent_friday/services/egress_gate.py` or `sensitivity_classifier.py`
- [ ] `src/agent_friday/services/credential_store.py` or `vault_passphrase.py`
- [ ] Authentication, session or cookie handling

## Notes for reviewers

<!-- Anything unusual or worth extra attention. -->
