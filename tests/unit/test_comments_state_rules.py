"""AGENTS.md: comments and documentation state invariants and intent in the
present tense; the incident that taught a rule lives in git history. This
guard covers the action ledger, Doctor, bundle, schedule, weekly ask, receipt
audit and authority modules and their tests."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

FILES = [
    "src/agent_friday/services/action_ledger.py",
    "src/agent_friday/services/authority.py",
    "src/agent_friday/services/backup_schedule.py",
    "src/agent_friday/services/doctor.py",
    "src/agent_friday/services/capability_state.py",
    "src/agent_friday/services/doctor_weekly.py",
    "src/agent_friday/services/first_backup.py",
    "src/agent_friday/services/friday_bundle.py",
    "src/agent_friday/services/passphrase_strength.py",
    "src/agent_friday/services/readable_export.py",
    "src/agent_friday/services/receipt_audit.py",
    "src/agent_friday/services/safe_mode.py",
    "src/agent_friday/services/store_inventory.py",
    "src/agent_friday/services/migrations.py",
    "src/agent_friday/routes/doctor.py",
    "src/agent_friday/routes/ledger.py",
    "static/doctor.html",
    "static/ledger.html",
    "static/friday_ledger.js",
    "tests/ledger_support.py",
    "tests/unit/test_action_ledger_chain.py",
    "tests/unit/test_backup_schedule.py",
    "tests/unit/test_capability_health.py",
    "tests/unit/test_connected_service_tools_are_held.py",
    "tests/unit/test_doctor_weekly.py",
    "tests/unit/test_first_backup.py",
    "tests/unit/test_friday_bundle.py",
    "tests/clean_machine/check_first_backup.py",
    "tests/unit/test_ledger_archive.py",
    "tests/unit/test_one_action_ledger.py",
    "tests/unit/test_passphrase_strength.py",
    "tests/unit/test_readable_export.py",
    "tests/unit/test_receipt_completeness.py",
    "tests/unit/test_restore_resume_cli.py",
    "tests/unit/test_revoke_authority.py",
    "tests/unit/test_safe_mode.py",
    "tests/unit/test_store_inventory.py",
    "tests/api/test_store_inventory_route.py",
    "tests/unit/test_restore_crash_safe.py",
    "tests/unit/test_migrations.py",
    "tests/api/test_authority_routes.py",
    "tests/api/test_bundle_routes.py",
    "tests/api/test_bundle_schedule_routes.py",
    "tests/api/test_doctor_routes.py",
    "tests/api/test_doctor_weekly_routes.py",
    "tests/api/test_first_backup_routes.py",
    "tests/api/test_ledger_routes.py",
    "tests/api/test_ledger_workspace.py",
]

NARRATION = re.compile(
    r"(?i)\b(?:security\s+)?review\s+round\b|\bclosing review\b|\bre-review\b"
    r"|\bconfirmation review\b|\breview of [0-9a-f]{7,}|\bCI on #\d+|\bred on the old code\b"
    r"|\bused to (?:be|live|keep|fail|make|leave)\b")


@pytest.mark.parametrize("rel", FILES)
def test_no_comment_narrates_history(rel):
    text = (ROOT / rel).read_text(encoding="utf-8")
    hits = [(n, ln.strip()) for n, ln in enumerate(text.splitlines(), 1) if NARRATION.search(ln)]
    assert not hits, hits
