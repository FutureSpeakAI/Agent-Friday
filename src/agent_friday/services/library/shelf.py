"""Which shelf a document goes on, and sealing for the vault shelf.

The open shelf is stored like every other Friday index. A document the
sensitivity classifier tiers as private or sensitive (TIER_2/3), or that the
owner moved, goes on the vault shelf: its text is sealed with the vault key
(AES-256-GCM, the same scheme as the vault), kept out of full-text search, and
readable only while the vault key is available. With no vault key a sensitive
document is not indexed at all and is listed as skipped, never stored in the
clear.
"""
from __future__ import annotations

import base64
from pathlib import Path

from agent_friday.services.library import grants
from agent_friday.services.library.store import Store


def _vault_key() -> bytes | None:
    try:
        from agent_friday.services import agent
        return agent._get_vault_key()
    except Exception:
        return None


def make_sealer(key: bytes):
    from agent_friday.privacy import vault_crypto as vc

    def seal(text: str) -> str:
        return base64.b64encode(vc.encrypt(text.encode("utf-8"), key)).decode("ascii")

    def unseal(blob: str) -> str:
        return vc.decrypt(base64.b64decode(blob), key).decode("utf-8")

    return seal, unseal


def attach(store: Store, key: bytes | None = None) -> bool:
    """Give `store` the vault sealer when the key exists; clear it otherwise.
    Returns whether the vault shelf is usable."""
    key = key if key is not None else _vault_key()
    if key is None:
        store.set_sealer(None, None)
        return False
    seal, unseal = make_sealer(key)
    store.set_sealer(seal, unseal)
    return True


def tier_of(title: str, sample: str) -> int:
    from agent_friday.services import sensitivity_classifier as sc
    try:
        return int(sc.classify(f"{title}\n{sample}", use_presidio=False))
    except Exception:
        # A classifier that cannot answer is not evidence the text is public.
        return sc.Tier.PRIVATE


class Shelf(str):
    """A shelf name that remembers the classifier tier it came from (None for the owner's own choice), so the
    index records the tier once and Laya's triage does not ask the classifier again."""
    tier: int | None = None


def chooser(principal: str):
    """The `classify` hook for the indexer: the owner's choice first, then the
    classifier."""
    def choose(path: Path, title: str, sample: str) -> str:
        over = grants.shelf_override(principal, path)
        if over:
            return over
        tier = tier_of(title, sample)
        out = Shelf("vault" if tier >= 2 else "open")
        out.tier = int(tier)
        return out
    return choose
