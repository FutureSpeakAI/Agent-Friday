"""
agent_friday.core.vault — vault encryption state and passphrase config.

The canonical vault logic lives in services/vault_crypto.py; this module
re-exports the shared mutable state dict so route handlers and services can
import from a focused namespace::

    from agent_friday.core.vault import _VAULT_ENCRYPTION_STATE
"""

from agent_friday.core import (  # noqa: F401
    FRIDAY_VAULT_PASSPHRASE,
    _VAULT_ENCRYPTION_STATE,
)
