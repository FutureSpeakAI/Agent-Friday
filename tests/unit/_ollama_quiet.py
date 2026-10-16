"""Keep the Ollama manager's background probes out of a network canary.

Reading settings can ask the local Ollama manager whether it is up and for its
model list; a stale answer starts a probe of localhost:11434 on its own thread
('ollama-liveness', 'ollama-models'). A canary that patches the network
process-wide records that probe, and whether it fires depends on what an
earlier test left in the manager's cache, so the canary fails by test order.
Those inventory reads are not what a canary is about. With this, neither probe
starts: a caller waiting on a liveness probe gets an answered event and the
manager's last answer (none in a fresh test: not known to be up). A probe an
earlier test already started is waited out before the canary is armed: a
refused loopback connect takes seconds on Windows, so it can still be dialling.

The tests of the probes themselves (test_settings_never_dial,
test_polled_routes_never_wait_on_the_machine) do not use this.
"""
from __future__ import annotations

import threading


def quiet_ollama_probes(monkeypatch) -> None:
    from agent_friday.routing import ollama_manager

    def _no_probe(self):
        done = threading.Event()
        done.set()
        return done

    monkeypatch.setattr(ollama_manager.OllamaManager, "_start_probe", _no_probe)
    monkeypatch.setattr(ollama_manager.OllamaManager, "_start_models_refresh", lambda self: None)
    for t in threading.enumerate():
        if t.name in ("ollama-liveness", "ollama-models") and t is not threading.current_thread():
            t.join(timeout=15)
