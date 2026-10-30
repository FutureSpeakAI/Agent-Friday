"""The credential values Friday actually holds, so they can be taken out of any text whatever
their shape (BD-06, NS-23.3-3).

The log filter and the tool-result redactor know secret SHAPES (vendor prefixes, key blocks,
`api_key=...`). A key with no recognisable shape (a custom OpenAI-compatible key, a search or
voice provider key, a Twilio secret, an unprefixed bot token) passed all of them: in an
exception message, a print, a tool result. This keeps the set of values Friday holds (stored
provider, platform and channel keys; phone secrets; key fields in settings.json) and replaces
each with "[redacted]" wherever `redact_known` runs (services/log_redaction for every log line
and the console, services/credential_paths.redact_secrets for what tools hand the model).

Values shorter than 8 characters are left alone (they would match ordinary words). The set is
read at most once a minute and dropped whenever a key is set or deleted. Reading it never
logs, and a read in progress on this thread is never re-entered.
"""
from __future__ import annotations

import re
import threading
import time
from typing import List

MIN_LEN = 8
_TTL_S = 60.0
_LOCK = threading.Lock()
_STATE = {"at": 0.0, "values": []}
_BUSY = threading.local()
REDACTED = "[redacted]"

#: settings.json fields that hold a credential (core and web_search read these).
SECRET_SETTING = re.compile(r"(?i)(api[_-]?key$|(^|_)token$|(^|_)secret$|(^|_)password$|(^|_)passphrase$)")


def invalidate() -> None:
    """A key was set or deleted: the next read builds the set again."""
    with _LOCK:
        _STATE["at"] = 0.0


def _settings_values(d, out: list, depth: int = 0) -> None:
    if depth > 3 or not isinstance(d, dict):
        return
    for k, v in d.items():
        if isinstance(v, dict):
            _settings_values(v, out, depth + 1)
        elif isinstance(v, str) and SECRET_SETTING.search(str(k)):
            out.append(v)


def _collect() -> List[str]:
    vals: list = []
    try:
        from agent_friday.services import credential_store as cs
        for row in cs.list_provider_keys() or []:
            name = row.get("provider") if isinstance(row, dict) else row
            if not name:
                continue
            try:
                v = cs.get_provider_key(str(name), use=False)
            except Exception:  # noqa: BLE001
                v = None
            if v:
                vals.append(v)
    except Exception:  # noqa: BLE001
        pass
    try:
        from agent_friday.phone import config as pc
        for n in pc.SECRET_NAMES:
            try:
                v = pc.get_secret(n)
            except Exception:  # noqa: BLE001
                v = None
            if v:
                vals.append(v)
    except Exception:  # noqa: BLE001
        pass
    try:
        from agent_friday.core import _load_settings
        _settings_values(_load_settings() or {}, vals)
    except Exception:  # noqa: BLE001
        pass
    clean = {str(v).strip() for v in vals if isinstance(v, str)}
    return sorted((v for v in clean if len(v) >= MIN_LEN and "[redacted]" not in v), key=len, reverse=True)


def known_values() -> List[str]:
    """The stored credential values (longest first). Empty while a read is already running on
    this thread (a log line written during the read itself)."""
    if getattr(_BUSY, "on", False):
        return list(_STATE["values"])
    now = time.monotonic()
    if now - _STATE["at"] < _TTL_S:
        return list(_STATE["values"])
    _BUSY.on = True
    try:
        vals = _collect()
    finally:
        _BUSY.on = False
    with _LOCK:
        _STATE["values"], _STATE["at"] = vals, now
    return list(vals)


def redact_known(text) -> str:
    """`text` with every stored credential value replaced. Never raises; a text that cannot be
    checked is returned with nothing known removed only when no value is known."""
    s = str(text)
    vals = known_values()
    for v in vals:
        if v in s:
            s = s.replace(v, REDACTED)
    return s
