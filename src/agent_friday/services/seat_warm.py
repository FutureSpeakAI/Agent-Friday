"""Warm the brain's prompt prefix the moment its seat is ready.

Every llama-server restart is a cold start for a hybrid model (the server
cannot persist a Gated DeltaNet state across restarts), so the first request
after a start used to read the whole head: a median 18,958 tokens in 44.7 s
on the reference machine, paid by whoever spoke first, usually the owner's
first turn of the morning or the 07:00 routine.

This module registers one `residency_arbiter.on_seat_ready` hook. When the
seat that serves the brain answers /health, it sends the canonical head (the
frozen Friday system prompt above `prompt_cache.VOLATILE_MARKER`) as a
one-token completion through the SAME call a turn makes, so the seat's slot
and host prompt cache hold the prefix before anyone asks. The volatile tail
(clock and everything the assembler appends after it) is left out on purpose:
it differs on every turn and would only be read again.

The warm is best-effort: a failure is logged and nothing else changes. It
never runs for a seat that is not the brain, and `seat_prefix_warm: false`
turns it off.
"""
from __future__ import annotations

import logging
import time

_log = logging.getLogger("friday.seat_warm")

_installed = False


def canonical_head(workspace: str = "") -> str:
    """The stable prefix a local turn starts with, with no volatile tail."""
    from agent_friday.services.model_router import (
        _get_friday_system_prompt, _get_vault_control, _vault_cloud_fallback,
        _vault_local_only)
    from agent_friday.services.prompt_cache import VOLATILE_MARKER
    vc = _get_vault_control() if _vault_local_only() else None
    full = _get_friday_system_prompt("", workspace or "", provider="local",
                                     vault_control=vc,
                                     vault_fallback=_vault_cloud_fallback()) or ""
    idx = full.find(VOLATILE_MARKER)
    return full[:idx] if idx > 0 else full


def _is_brain(model_id: str) -> bool:
    try:
        from agent_friday.services import local_seats
        return local_seats.resolve("brain") == model_id
    except Exception:
        return False


def warm(model_id: str, port: int | None = None) -> dict:
    """One-token completion with the canonical head on `model_id`'s seat."""
    t0 = time.time()
    try:
        from agent_friday.core import _load_settings
        settings = _load_settings() or {}
    except Exception:
        settings = {}
    if settings.get("seat_prefix_warm", True) is False:
        return {"warmed": False, "seat": model_id, "reason": "seat_prefix_warm is off"}
    if not _is_brain(model_id):
        return {"warmed": False, "seat": model_id, "reason": "not the brain seat"}
    try:
        from agent_friday.services.agent import _generate_agent
        from agent_friday.services.model_router import TIMINGS_SINK
        head = canonical_head(settings.get("active_workspace") or "")
        timings: dict = {}
        tok = TIMINGS_SINK.set(lambda t: timings.update(t or {}))
        try:
            _generate_agent(
                [{"role": "user", "content": "OK."}],
                system=head, model=model_id, max_tokens=1,
                session_ctx={"authenticated": True, "provider": "local",
                             "is_background_task": True, "prefix_warm": True},
                workspace=settings.get("active_workspace") or "",
                orb_label="Prefix warm",
            )
        finally:
            TIMINGS_SINK.reset(tok)
        out = {"warmed": True, "seat": model_id, "port": port,
               "prompt_n": timings.get("prompt_n"),
               "ms": int((time.time() - t0) * 1000)}
        _log.info("seat prefix warm: seat=%s prompt_n=%s in %d ms",
                  model_id, out["prompt_n"], out["ms"])
        return out
    except Exception as e:  # noqa: BLE001
        _log.warning("seat prefix warm failed for %s: %s: %s",
                     model_id, type(e).__name__, e)
        return {"warmed": False, "seat": model_id,
                "reason": f"{type(e).__name__}: {e}"}


def _on_ready(model_id: str, port: int) -> None:
    warm(model_id, port)


def install() -> None:
    """Register the hook once. Safe to call from any module that boots routes."""
    global _installed
    if _installed:
        return
    from agent_friday.services import residency_arbiter as _ra
    _ra.on_seat_ready(_on_ready)
    _installed = True
