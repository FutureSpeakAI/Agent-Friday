"""Call-time key injection for the providers Friday's own code calls (Compressor eval A3).

A module that needs a provider asks this gateway for a loopback URL and sends a stand-in key; the gateway
(``salon_proxy.KeyProxy``, one listener per provider) puts the real key on the outgoing request only. The module never
reads the key. Rules, each guarded by tests/unit/test_key_gateway.py:

* loopback only, one upstream per provider, only the provider's own paths and verbs (the proxy's own invariants);
* the key is looked up on every request (the owner can change it without a restart) and appears nowhere but the
  outgoing header;
* a provider that is not in ``PROVIDERS`` has no URL (``UnknownProvider``): the table is the only way in;
* ``available()`` says whether a key exists without returning it, so status screens never hold a key.

This module is the one place that reads a Brave key (it is listed in scripts/raw_key_readers.allow.json until the
store itself moves); modules that used to read it are off that list.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Optional

from agent_friday.services.salon_proxy import DUMMY_KEY, KeyProxy


class UnknownProvider(KeyError):
    """The provider has no row in PROVIDERS."""


@dataclass(frozen=True)
class Provider:
    name: str
    upstream: str
    prefixes: tuple
    methods: tuple
    key_header: str
    key_format: str
    env_names: tuple
    store_names: tuple
    settings_names: tuple = ()


PROVIDERS = {
    "brave": Provider(
        name="brave", upstream="https://api.search.brave.com", prefixes=("/res/v1/",), methods=("GET",),
        key_header="X-Subscription-Token", key_format="{key}", env_names=("BRAVE_SEARCH_API_KEY",),
        # "brave-search" is the name the first-run key screen used to store it under.
        store_names=("brave", "brave-search"), settings_names=("brave_search_api_key", "brave_api_key")),
    "firecrawl": Provider(
        name="firecrawl", upstream="https://api.firecrawl.dev", prefixes=("/v1/", "/v2/"), methods=("GET", "POST"),
        key_header="Authorization", key_format="Bearer {key}", env_names=("FIRECRAWL_API_KEY",),
        store_names=("firecrawl",), settings_names=("firecrawl_api_key",)),
}

_LOCK = threading.Lock()
_PROXIES: dict = {}


def _row(name: str) -> Provider:
    try:
        return PROVIDERS[name]
    except KeyError:
        raise UnknownProvider(name) from None


def _from_settings(row: Provider) -> str:
    try:
        from agent_friday.core import _load_settings
        settings = _load_settings() or {}
        for holder in (settings, settings.get("api_keys") or {}, settings.get("search") or {}):
            if isinstance(holder, dict):
                for field in row.settings_names:
                    value = holder.get(field)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
    except Exception:
        pass
    return ""


def _find_key(row: Provider) -> str:
    """The provider's key from wherever the owner put it: environment, then the encrypted store, then settings."""
    for env_name in row.env_names:
        value = (os.environ.get(env_name) or "").strip()
        if value:
            return value
    try:
        from agent_friday.services import credential_store
        for store_name in row.store_names:
            value = credential_store.get_provider_key(store_name)
            if value and value.strip():
                return value.strip()
    except Exception:
        pass
    return _from_settings(row)


def available(name: str) -> bool:
    """Whether a key for this provider exists. The key itself is not returned."""
    return bool(_find_key(_row(name)))


def sources(name: str) -> dict:
    """Where a key for this provider is found, as booleans (environment, encrypted store, settings); no values."""
    row = _row(name)
    found = {"environment": any((os.environ.get(env_name) or "").strip() for env_name in row.env_names),
             "encrypted_store": False, "settings": bool(_from_settings(row))}
    try:
        from agent_friday.services import credential_store
        found["encrypted_store"] = any(bool(credential_store.get_provider_key(n, use=False)) for n in row.store_names)
    except Exception:
        pass
    return found


def stand_in(name: str) -> str:
    """What a caller sends as its key: a fixed dummy when a key exists, else an empty string. Never the real key."""
    return DUMMY_KEY if available(name) else ""


def _keep_loopback_direct() -> None:
    """A system HTTP proxy must never be asked to carry a request to our own loopback listener."""
    for var in ("NO_PROXY", "no_proxy"):
        current = os.environ.get(var, "")
        if "127.0.0.1" not in current:
            os.environ[var] = (current + "," if current else "") + "127.0.0.1"


def url(name: str) -> str:
    """The loopback base URL for a provider (started on first use). Append the provider's path."""
    row = _row(name)
    with _LOCK:
        proxy = _PROXIES.get(name)
        if proxy is None:
            proxy = KeyProxy(upstream=row.upstream, key_provider=lambda: _find_key(row) or None,
                             allowed_prefixes=row.prefixes, allowed_methods=row.methods,
                             key_header=row.key_header, key_format=row.key_format)
            proxy.start()
            _keep_loopback_direct()
            _PROXIES[name] = proxy
        return proxy.url


def stop_all() -> None:
    with _LOCK:
        proxies = list(_PROXIES.values())
        _PROXIES.clear()
    for proxy in proxies:
        proxy.stop()
