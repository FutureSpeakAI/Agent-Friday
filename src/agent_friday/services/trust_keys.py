"""The public keys Friday trusts to sign what it downloads.

Two separate purposes, two separate lists, so a key that signs one can never
pass for the other:

* RELEASE_KEYS sign each release's ``release-manifest.json`` (what the update
  check verifies before it recommends a release);
* PUBLIC_LIST_KEYS sign the public lists FutureSpeak maintains (export steps,
  cancellation pages, retailer return windows, state data-broker registries).

Each entry is the text of a minisign public key (``minisign -G`` writes it to
``minisign.pub``). The private halves are made and kept on an offline machine
and are never placed in CI or on a server (docs/development/release-signing.md).

Both lists are empty until those keys exist. Empty means "nothing verifies":
the update check says a release's manifest could not be checked, and the
public lists stay unfetched. Nothing is ever accepted because no key was set.
"""
from __future__ import annotations

RELEASE_KEYS: tuple = ()

PUBLIC_LIST_KEYS: tuple = ()
