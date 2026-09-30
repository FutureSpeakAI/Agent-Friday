"""A data export never ships the vault re-encrypt backup.

The re-encrypt backup was a whole-vault copytree: the default export used to include
backups/.../vault/.vault_config.json and backups/.../vault/context-log/*.jsonl
(a nested plaintext copy), even though the live versions of those are gated.
The skip_reason assertions below fail on today's main, where backups/ paths
return None for a full export.
"""
from __future__ import annotations

from pathlib import PurePath

import pytest

from agent_friday.services import data_export as de

_STAMP = "vault-reencrypt-20260930T000000Z"

_BACKUP_PATHS = [
    f"backups/{_STAMP}/providers/keys/openrouter.key",
    f"backups/{_STAMP}/vault/.vault_config.json",
    f"backups/{_STAMP}/vault/.governance-key",
    f"backups/{_STAMP}/vault/context-log/2026-09-28.jsonl",
]


@pytest.mark.parametrize("rel", _BACKUP_PATHS)
@pytest.mark.parametrize("full", [False, True])
def test_backups_excluded_from_every_export(rel, full):
    assert de.skip_reason(PurePath(rel), full=full) == "backup"


@pytest.mark.parametrize("rel", [
    "vault/finances/notes.md",     # owner content: stays in
    "chat_history.json",
    "conversations/c1/messages.jsonl",
])
def test_live_owner_data_still_included(rel):
    assert de.skip_reason(PurePath(rel), full=True) is None
