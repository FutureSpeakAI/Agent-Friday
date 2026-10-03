"""Never start a second copy of the local model.

A llama-server seat holds ~13 GB; a second one on this machine pushes the
commit charge past its limit. The arbiter already adopts a live seat instead of
spawning another (residency_arbiter.LlamaServerBackend._already_serving); this
covers the other door, a command the model runs. While a seat answers on a known
seat port, a command that would launch a llama-server or an Ollama model server
is refused with the truth: the local model is already running, and it is the one
speaking.
"""
from __future__ import annotations

import json
import re
import urllib.request

#: Commands that start a model server. Reading process or port state is not a
#: launch (Get-Process llama-server, Get-NetTCPConnection ...).
_LAUNCH = re.compile(
    r"(?i)(?:(?<![-\w])start-process\b[^|;\n]*llama[-_]server|"
    r"(?:^|[\s&;|'\"\\/])llama[-_]server(?:\.exe)?['\"]?\s+-|"
    r"\bollama(?:\.exe)?\s+(?:serve|run)\b)")


def _ports():
    try:
        from agent_friday.services.residency_arbiter import PORT_BASE
    except Exception:
        PORT_BASE = 8090
    return range(PORT_BASE, PORT_BASE + 4)


def answering_seat():
    """(model_id, port) of a local seat answering on a known seat port, or None."""
    for port in _ports():
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/v1/models" % port, timeout=1.5) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            continue
        models = data.get("models") or data.get("data") or []
        if models:
            first = models[0]
            return (str(first.get("model") or first.get("id") or first.get("name") or "a local model"),
                    port)
    return None


def second_seat_refusal(command: str):
    """The refusal text when `command` would start a second model server, else None."""
    if not command or not _LAUNCH.search(command):
        return None
    seat = answering_seat()
    if not seat:
        return None
    model, port = seat
    return ("Not run: the local model is already running, and it's me - %s is answering on "
            ":%d. Starting a second copy would need another ~13 GB on a machine near its "
            "memory limit." % (model, port))
