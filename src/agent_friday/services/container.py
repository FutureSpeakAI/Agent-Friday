"""
agent_friday.services.container — minimal dependency injection container.

Provides a thin DI layer so route handlers can receive settings, vault state,
and model_router as explicit dependencies rather than bare globals from core.
This makes unit testing easier (swap the container's provider without patching
globals) and makes inter-module dependencies visible.

Usage in a route handler::

    from agent_friday.services.container import get_container

    @bp.route("/api/example")
    def example():
        c = get_container()
        settings = c.settings()
        router = c.model_router()
        ...

Usage in tests::

    from agent_friday.services.container import ServiceContainer, set_container
    set_container(ServiceContainer(
        settings_factory=lambda: {...},
        model_router_factory=lambda: mock_router,
    ))

"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional


class ServiceContainer:
    """Holds factory callables for core services.

    All factories are *lazy* — called on first access, then cached for the
    lifetime of this container instance. This mirrors how the globals work
    today (module-level singletons) while making the dependency graph explicit.
    """

    def __init__(
        self,
        settings_factory: Optional[Callable[[], Dict[str, Any]]] = None,
        model_router_factory: Optional[Callable[[], Any]] = None,
        vault_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._settings_factory = settings_factory or _default_settings_factory
        self._model_router_factory = model_router_factory or _default_model_router_factory
        self._vault_factory = vault_factory or _default_vault_factory
        self._lock = threading.Lock()
        self._cache: Dict[str, Any] = {}

    def _get(self, key: str, factory: Callable[[], Any]) -> Any:
        with self._lock:
            if key not in self._cache:
                self._cache[key] = factory()
            return self._cache[key]

    def settings(self) -> Dict[str, Any]:
        """Return the current settings dict (re-read each call — not cached).

        Settings change frequently (every POST /api/settings) so they must not
        be cached; the factory itself handles any in-process caching (the TTL
        cache in core.py) so we just delegate.
        """
        return self._settings_factory()

    def model_router(self) -> Any:
        """Return the model_router module (cached — it's a stateless singleton)."""
        return self._get("model_router", self._model_router_factory)

    def vault(self) -> Any:
        """Return the vault access control object (cached per container)."""
        return self._get("vault", self._vault_factory)

    def invalidate(self, *keys: str) -> None:
        """Drop cached values for the given keys (or all if none specified)."""
        with self._lock:
            if keys:
                for k in keys:
                    self._cache.pop(k, None)
            else:
                self._cache.clear()


def _default_settings_factory() -> Dict[str, Any]:
    from agent_friday.core import _load_settings
    return _load_settings()


def _default_model_router_factory() -> Any:
    from agent_friday.services import model_router
    return model_router


def _default_vault_factory() -> Any:
    try:
        from agent_friday.privacy.vault_access import VaultAccessControl
        return VaultAccessControl()
    except Exception:
        return None


# ── Module-level singleton ────────────────────────────────────────────────────

_CONTAINER_LOCK = threading.Lock()
_CONTAINER: Optional[ServiceContainer] = None


def get_container() -> ServiceContainer:
    """Return the process-wide DI container (created lazily with defaults)."""
    global _CONTAINER
    if _CONTAINER is None:
        with _CONTAINER_LOCK:
            if _CONTAINER is None:
                _CONTAINER = ServiceContainer()
    return _CONTAINER


def set_container(container: ServiceContainer) -> None:
    """Replace the process-wide container — used in tests for mock injection."""
    global _CONTAINER
    with _CONTAINER_LOCK:
        _CONTAINER = container
