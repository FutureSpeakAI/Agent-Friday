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

from agent_friday.services.salon_proxy import DUMMY_KEY, NO_KEY_HEADER, KeyProxy


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
    timeout_s: int = 60
    core_attrs: tuple = ()      # module constants in agent_friday.core that hold the key at boot, read first
    key_prefix: str = ""        # a real key starts with this; checked here so the key never leaves
    shape_message: str = ""
    migrate_plaintext: bool = False   # move a key found only in plaintext settings into the encrypted store


PROVIDERS = {
    "brave": Provider(
        name="brave", upstream="https://api.search.brave.com", prefixes=("/res/v1/",), methods=("GET",),
        key_header="X-Subscription-Token", key_format="{key}", env_names=("BRAVE_SEARCH_API_KEY",),
        # "brave-search" is the name the first-run key screen used to store it under.
        store_names=("brave", "brave-search"), settings_names=("brave_search_api_key", "brave_api_key")),
    "firecrawl": Provider(
        name="firecrawl", upstream="https://api.firecrawl.dev", prefixes=("/v1/", "/v2/"), methods=("GET", "POST"),
        key_header="Authorization", key_format="Bearer {key}", env_names=("FIRECRAWL_API_KEY",),
        store_names=("firecrawl",), settings_names=("firecrawl_api_key",),
        timeout_s=180),   # search with page content waits up to firecrawl.SEARCH_TIMEOUT_S
    "elevenlabs": Provider(
        name="elevenlabs", upstream="https://api.elevenlabs.io", prefixes=("/v1/",), methods=("GET", "POST"),
        key_header="xi-api-key", key_format="{key}", env_names=("ELEVENLABS_API_KEY",),
        store_names=("elevenlabs",), settings_names=("elevenlabs_api_key",), timeout_s=120,
        core_attrs=("ELEVENLABS_API_KEY",), key_prefix="sk_", migrate_plaintext=True,
        shape_message=("this looks like an ElevenLabs key *id*, not a key - real keys begin with 'sk_'. The short hex "
                       "string beside a key in the dashboard is its id and the API will reject it.")),
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


def _from_core(row: Provider) -> str:
    try:
        from agent_friday import core
        for attr in row.core_attrs:
            value = getattr(core, attr, "") or ""
            if str(value).strip():
                return str(value).strip()
    except Exception:
        pass
    return ""


def _migrate_plaintext(row: Provider, key: str) -> None:
    """A key found in plaintext settings is encrypted into the store and the plaintext copy blanked (best effort)."""
    try:
        from agent_friday.services import credential_store
        credential_store.set_provider_key(row.store_names[0], key)
        if (credential_store.get_provider_key(row.store_names[0]) or "") != key:
            return
        from agent_friday.core import _save_settings
        _save_settings({row.settings_names[0]: ""})
    except Exception:
        pass


def _find_key(row: Provider) -> str:
    """The provider's key from wherever the owner put it: core's boot constant, the environment, the encrypted store,
    then settings (a key found only in plaintext settings is moved into the store)."""
    from_core = _from_core(row)
    if from_core:
        return from_core
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
    found = _from_settings(row)
    if found and row.migrate_plaintext:
        _migrate_plaintext(row, found)
    return found


def shape_problem(name: str) -> Optional[str]:
    """Why the stored key cannot be right (wrong prefix), or None. The key is checked here and never returned."""
    row = _row(name)
    key = _find_key(row)
    if key and row.key_prefix and not key.startswith(row.key_prefix):
        return row.shape_message or "this key does not look right"
    return None


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


def no_key(response) -> bool:
    """True when this answer is the gateway's own "no key" refusal, not the provider's."""
    return getattr(response, "status_code", 0) == 401 and response.headers.get(NO_KEY_HEADER) == "no-key"


def _keep_loopback_direct() -> None:
    """A system HTTP proxy must never be asked to carry a request to our own loopback listener. An existing "*" already
    means "bypass everything" and is left alone; otherwise 127.0.0.1 is added as a whole entry."""
    for var in ("NO_PROXY", "no_proxy"):
        entries = [part.strip() for part in os.environ.get(var, "").split(",") if part.strip()]
        if "*" not in entries and "127.0.0.1" not in entries:
            os.environ[var] = ",".join(entries + ["127.0.0.1"])


def url(name: str) -> str:
    """The loopback base URL for a provider (started on first use). Append the provider's path."""
    row = _row(name)
    with _LOCK:
        proxy = _PROXIES.get(name)
        if proxy is None:
            proxy = KeyProxy(upstream=row.upstream, key_provider=lambda: _find_key(row) or None,
                             allowed_prefixes=row.prefixes, allowed_methods=row.methods,
                             key_header=row.key_header, key_format=row.key_format, upstream_timeout=row.timeout_s)
            proxy.start()
            _keep_loopback_direct()
            _PROXIES[name] = proxy
        return proxy.url


def _is_cloud_endpoint(base_url: str) -> bool:
    """Whether a provider's base URL is a public https host (the only kind that gets the key through the gateway)."""
    from urllib.parse import urlsplit
    from agent_friday.routing.provider_descriptors import is_private_host
    parts = urlsplit(base_url or "")
    return parts.scheme == "https" and bool(parts.netloc) and not is_private_host(base_url)


def _auth_shape(prov: dict) -> Optional[tuple]:
    """(header name, key format) a descriptor puts its key under, found by asking it to build the header for a marker.
    None when the descriptor sends no key header."""
    from agent_friday.routing.provider_descriptors import auth_headers
    marker = "<<key>>"
    for header, value in auth_headers(prov, marker).items():
        if marker in value:
            return header, value.replace("{", "{{").replace("}", "}}").replace(marker, "{key}")
    return None


def descriptor_url(prov: dict) -> Optional[str]:
    """The loopback base URL for a provider descriptor, with its path (``http://127.0.0.1:N/v1``), or None when the
    descriptor should be called directly: a local or private host, plain http, or no key header. The listener is pinned
    to this descriptor's own host and path and sends its key only there; a caller picks the descriptor, never the
    destination. Guarded by tests/unit/test_key_gateway_descriptor.py."""
    from urllib.parse import urlsplit
    base_url = (prov.get("base_url") or "").rstrip("/")
    shape = _auth_shape(prov)
    if (prov.get("auth") or {}).get("type", "env_var") != "env_var" or not shape or not _is_cloud_endpoint(base_url):
        return None
    header, key_format = shape
    parts = urlsplit(base_url)
    origin = "%s://%s" % (parts.scheme, parts.netloc)
    path = parts.path.rstrip("/")
    name = str(prov.get("name") or parts.netloc)
    from agent_friday.routing.provider_descriptors import provider_env_keys
    identity = (name, origin, path, header, key_format, tuple(provider_env_keys(prov)))

    def key_now() -> Optional[str]:
        from agent_friday.routing.provider_descriptors import provider_api_key
        return provider_api_key(prov) or None

    with _LOCK:
        proxy = _PROXIES.get(identity)
        if proxy is None:
            # an edited descriptor replaces its old listener instead of leaving it running
            for old in [k for k in _PROXIES if isinstance(k, tuple) and k[0] == name]:
                _PROXIES.pop(old).stop()
            proxy = KeyProxy(upstream=origin, key_provider=key_now, allowed_prefixes=(path + "/",),
                             allowed_methods=("GET", "POST"), key_header=header, key_format=key_format,
                             upstream_timeout=180)
            proxy.start()
            _keep_loopback_direct()
            _PROXIES[identity] = proxy
        return proxy.url + path


def stop_all() -> None:
    with _LOCK:
        proxies = list(_PROXIES.values())
        _PROXIES.clear()
    for proxy in proxies:
        proxy.stop()
