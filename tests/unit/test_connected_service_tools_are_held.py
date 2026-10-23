"""A built-in tool that reaches the owner's Google account is held in safe
mode (connectors off) and while all action authority is revoked, though the
checkpoint judges it internal. Found by discovery: every registered tool
whose handler reaches Google is in action_gate.CONNECTED_SERVICE_TOOLS."""
from __future__ import annotations

import importlib
import inspect
import re
import sys

import pytest

from agent_friday.governance import action_gate
from agent_friday.governance import proof_of_integrity as poi

GOOGLE_MARKERS = ("google_accounts", "gmail_send", "_google_multi_account_tool",
                  "_google_connectivity", "google_api", "gmail_api", "calendar_write",
                  "merged_gmail", "_gmail_search", "freebusy", "_voice_mail",
                  "googleapiclient", "busy_across_accounts", "gmail_mailbox")
#: The checkpoint itself and the approval store mention Gmail (a card can be
#: a send); walking into them would find every tool.
NOT_WALKED = ("agent_friday.governance", "agent_friday.services.approvals")
DEPTH = 3


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(h))
    monkeypatch.setattr(action_gate, "friday_home", lambda: str(h))
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", h / "vault" / ".governance-key")
    monkeypatch.setitem(sys.modules, "keyring", None)
    from agent_friday.services import action_ledger
    action_ledger.reset_caches()
    return h


def _decide(tool):
    return action_gate.authorize(tool, {"query": "x", "file_id": "f", "title": "t"},
                                 {"is_background_task": True})


_CALL = re.compile(r"\b([A-Za-z_]\w*)(?:\.([A-Za-z_]\w*))?\s*\(")
_IMPORT = re.compile(r"from\s+(agent_friday[\w.]*)\s+import\s+(\w+)(?:\s+as\s+(\w+))?")


def _reaches_google(fn, depth=DEPTH, seen=None) -> bool:
    """Whether fn, or what it calls inside agent_friday up to DEPTH levels,
    names a Google client."""
    seen = set() if seen is None else seen
    if id(fn) in seen or inspect.isclass(fn) or not callable(fn):
        return False
    seen.add(id(fn))
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        return False
    if any(m in src for m in GOOGLE_MARKERS):
        return True
    if depth <= 0:
        return False
    names = dict(getattr(fn, "__globals__", {}))
    for mod, attr, alias in _IMPORT.findall(src):
        try:
            m = importlib.import_module(mod)
            names[alias or attr] = getattr(m, attr, None) or importlib.import_module(mod + "." + attr)
        except Exception:
            continue
    for head, attr in _CALL.findall(src):
        obj = names.get(head)
        if attr and obj is not None:
            obj = getattr(obj, attr, None)
        mod = (getattr(obj, "__module__", "") or "") if callable(obj) else ""
        if mod.startswith("agent_friday") and not mod.startswith(NOT_WALKED) \
                and _reaches_google(obj, depth - 1, seen):
            return True
    return False


def _tools() -> dict:
    """Every tool name with every handler it has: the chat table and the
    voice engine's governed tools (one name can have both)."""
    from agent_friday.services import agent
    from agent_friday.services import voice_engine as ve
    out: dict = {}
    for name, fn in agent.CLAUDE_TOOL_HANDLERS.items():
        out.setdefault(name, []).append(fn)
    for name, fn in re.findall(r'return _governed\("([a-z_]+)", (_tool_[a-z_]+)', inspect.getsource(ve)):
        if getattr(ve, fn, None):
            out.setdefault(name, []).append(getattr(ve, fn))
    return out


def test_every_tool_that_reaches_google_is_listed():
    missing = [name for name, handlers in sorted(_tools().items())
               if action_gate.classify(name, {})[0] == action_gate.INTERNAL
               and name not in action_gate.CONNECTED_SERVICE_TOOLS
               and any(_reaches_google(h) for h in handlers)]
    assert not missing, missing


def test_the_discovery_sees_the_known_ones():
    """The scan finds the tools listed by hand (or the scan is too shallow)."""
    tools = _tools()
    unseen = [n for n in sorted(action_gate.CONNECTED_SERVICE_TOOLS)
              if n in tools and not any(_reaches_google(h) for h in tools[n])]
    assert not unseen, unseen


@pytest.mark.parametrize("tool", sorted(action_gate.CONNECTED_SERVICE_TOOLS))
def test_held_in_safe_mode(home, monkeypatch, tool):
    from agent_friday.services import safe_mode
    monkeypatch.setattr(safe_mode, "active", lambda *a, **k: True)
    v = _decide(tool)
    assert v.action == "deny" and "safe mode" in v.reason, v


@pytest.mark.parametrize("tool", sorted(action_gate.CONNECTED_SERVICE_TOOLS))
def test_held_while_authority_is_revoked(home, monkeypatch, tool):
    from agent_friday.services import authority
    monkeypatch.setattr(authority, "hold_reason",
                        lambda: "you revoked all action authority (test); restore it on the Doctor page")
    v = _decide(tool)
    assert v.action == "deny" and "revoked" in v.reason, v


def test_allowed_otherwise(home):
    v = _decide("search_email")
    assert v.action != "deny", v


@pytest.mark.parametrize("tool", sorted(action_gate.CONNECTED_SERVICE_WRITES))
def test_a_write_waits_after_a_restore_and_on_a_ledger_break(home, monkeypatch, tool):
    from agent_friday.services import action_ledger, friday_bundle
    monkeypatch.setattr(friday_bundle, "restore_hold", lambda *a, **k: "restored from a bundle (test)")
    v = _decide(tool)
    assert v.action == "deny" and "restored" in v.reason, v
    monkeypatch.setattr(friday_bundle, "restore_hold", lambda *a, **k: None)
    monkeypatch.setattr(action_ledger, "hold_reason", lambda *a, **k: "the ledger has a break (test)")
    v = _decide(tool)
    assert v.action == "deny" and "break" in v.reason, v


def test_a_read_is_not_held_by_a_restore(home, monkeypatch):
    from agent_friday.services import friday_bundle
    monkeypatch.setattr(friday_bundle, "restore_hold", lambda *a, **k: "restored from a bundle (test)")
    assert action_gate.CONNECTED_SERVICE_WRITES <= action_gate.CONNECTED_SERVICE_TOOLS
    assert _decide("search_email").action != "deny"
