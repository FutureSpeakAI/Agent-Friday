"""Connector isolation by default (amendment A25, owner ruling 2026-10-23).

* **New connectors** run in Windows' fully isolated mode: a connector server
  (a command, not a URL) that appears in the saved config for the first time and
  does not say otherwise gets ``"isolation": "appcontainer"`` (``apply_default``,
  called by the one function that writes mcp_servers.json). Catalog templates that
  cannot run isolated say ``"isolation": "off"`` with a reason (Docker; reading
  other apps' windows).
* **Existing connectors** (no ``isolation`` key: they were added before A25) are
  offered isolation once, by a card (``offer_existing``). Approving sets it and
  restarts the connector; declining leaves it as it is, and the card is not raised
  again. An unanswered card that expires is offered again at a later start.
  Nothing is switched without the owner's answer.
* **A failure under isolation** says so plainly with how to declare folders, sites,
  or turn it off (mcp_client.MCPServerProcess._isolation_help).

Isolation is a Windows mode; elsewhere the key is kept but nothing changes, and no
card is raised.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional

KIND = "connector_isolation"
SUBJECT_TYPE = "connector_isolation"
ISOLATED = "appcontainer"
_LOCK = threading.Lock()


_OFF = {"off", "false", "no"}


def _is_command_server(spec) -> bool:
    return isinstance(spec, dict) and not spec.get("url") and bool(spec.get("command"))


def is_isolated(spec) -> bool:
    """A connector server's config asks for isolation: the key is present and is not
    an explicit "off". An unclear value (a typo, null, "on") counts as isolated:
    unsure means the safer mode. A missing key is a connector from before A25."""
    if not _is_command_server(spec) or "isolation" not in spec:
        return False
    return str(spec.get("isolation")).strip().lower() not in _OFF


def apply_default(new_cfg: dict, old_cfg: Optional[dict]) -> dict:
    """Mark every connector server that is new in `new_cfg` (not in `old_cfg`) and
    says nothing about isolation as isolated. Returns `new_cfg` (changed in place)."""
    new_servers = (new_cfg or {}).get("servers")
    if not isinstance(new_servers, dict):
        return new_cfg
    old_servers = (old_cfg or {}).get("servers") if isinstance(old_cfg, dict) else None
    old_names = set(old_servers) if isinstance(old_servers, dict) else set()
    added = []
    for name, spec in new_servers.items():
        if name in old_names or not _is_command_server(spec):
            continue
        added.append(name)
        if "isolation" not in spec:
            spec["isolation"] = ISOLATED
    # An add-on added again starts fresh: an earlier answer to its program card was
    # about the add-on that was removed.
    if added:
        try:
            with _LOCK:
                recs = _runtime_records()
                if any(n in recs for n in added):
                    for n in added:
                        recs.pop(n, None)
                    _save_runtime_records(recs)
        except Exception:  # noqa: BLE001
            pass
    return new_cfg


# ── the one-time offer for connectors added before A25 ───────────────────────

def _record_file() -> Path:
    import agent_friday.core as core
    d = Path(core.FRIDAY_DIR) / "mcp-isolation"
    d.mkdir(parents=True, exist_ok=True)
    return d / "offers.json"


def _records() -> Dict[str, dict]:
    try:
        data = json.loads(_record_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_records(data: Dict[str, dict]) -> None:
    f = _record_file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, f)


def needs_offer(servers: dict) -> List[str]:
    """Connector servers added before A25 (no isolation key) not yet offered."""
    recs = _records()
    out = []
    for name, spec in (servers or {}).items():
        if not _is_command_server(spec) or "isolation" in spec:
            continue
        rec = recs.get(name) or {}
        if rec.get("state") in ("accepted", "declined"):
            continue
        if rec.get("state") == "offered" and _card_waiting(rec.get("approval_id")):
            continue
        out.append(name)
    return out


def _card_waiting(approval_id) -> bool:
    """The offered card is still there and pending. A card that expired or vanished
    without its decision hook running (a restart, a sweep before this module loaded)
    is not: the connector is offered again on a new card."""
    if not approval_id:
        return False
    try:
        from agent_friday.services import approvals as _ap
        card = _ap.get_approval(str(approval_id))
    except Exception:
        return True                     # cannot tell: do not raise a second card now
    return bool(card) and card.get("status") == "pending"


def _subject_type(gen: int) -> str:
    # A new card after an unanswered one: a new subject type per generation (cards are
    # looked up by subject, and an expired one would otherwise be found again). The
    # server's name stays the subject id, whatever characters it has.
    return SUBJECT_TYPE if gen <= 0 else "%s:%d" % (SUBJECT_TYPE, gen)


def offer_existing(servers: dict) -> List[str]:
    """Raise one card per connector added before A25, once. Returns the names offered."""
    try:
        from agent_friday.services import appcontainer as _ac
        if not _ac.available():
            return []
    except Exception:
        return []
    from agent_friday.services import approvals as _ap
    _register()
    offered = []
    with _LOCK:
        recs = _records()
        for name in needs_offer(servers):
            prev = recs.get(name) or {}
            gen = int(prev.get("gen") or 0)
            if prev.get("state") == "offered":
                gen += 1                    # its card expired or vanished unanswered
            rec = _ap.create_approval(
                kind=KIND, subject_type=_subject_type(gen), subject_id=str(name),
                title=f"Run the {name} connector in isolated mode?",
                description=(
                    f"New connectors now run in Windows' isolated mode: the connector can "
                    f"reach only its own folder and the folders you list for it, and the "
                    f"internet only if you list a site for it (listing any site opens the "
                    f"internet to it; sites are not checked one by one). {name} was added "
                    f"before this and still runs with your account's full access.\n\n"
                    f"Approve to isolate it from its next start. If it then cannot do its "
                    f"job, Friday will say so and show how to list the folders or sites it "
                    f"needs, or how to turn isolation off for it.\n\n"
                    f"Deny to leave it as it is. You will not be asked again; you can still "
                    f"set \"isolation\": \"appcontainer\" in its settings later."),
                action_description="Turn on isolated mode for this connector",
                payload={"server": str(name)}, requested_by="friday:a25",
                force_gate=True)       # always shown and answered: never decided by a policy
            recs[name] = {"state": "offered", "approval_id": rec.get("approval_id"), "gen": gen}
            offered.append(name)
        if offered:
            _save_records(recs)
    return offered


def _on_decision(record: dict) -> None:
    name = str((record.get("payload") or {}).get("server") or record.get("subject_id") or "")
    if not name:
        return
    status = record.get("status")
    with _LOCK:
        recs = _records()
        cur = recs.get(name) or {}
        if cur.get("approval_id") and record.get("approval_id") != cur.get("approval_id"):
            return                        # an older card's late answer: the newer one decides
        gen = int(cur.get("gen") or 0)
        if status == "approved":
            recs[name] = {"state": "accepted", "approval_id": record.get("approval_id"), "gen": gen}
        elif status == "denied":
            recs[name] = {"state": "declined", "approval_id": record.get("approval_id"), "gen": gen}
        else:
            # Expired unanswered: offered again later, on a new card.
            recs[name] = {"state": "expired", "gen": gen + 1}
        _save_records(recs)
        if status != "approved":
            return
        import logging
        try:
            from agent_friday.services import agent as _agent
            cfg = _agent._load_mcp_servers()
            spec = (cfg.get("servers") or {}).get(name)
            changed = _is_command_server(spec) and "isolation" not in spec
            if changed:
                spec["isolation"] = ISOLATED
                _agent._save_mcp_servers(cfg)
            elif not _is_command_server(spec):
                logging.getLogger(__name__).info(
                    "connector %s was removed before its isolation card was approved", name)
        except Exception as e:  # noqa: BLE001
            # Not applied: the answer is not lost. Offered again on a new card.
            logging.getLogger(__name__).warning("connector isolation for %s not applied: %s", name, e)
            recs = _records()
            recs[name] = {"state": "expired", "gen": gen + 1}
            _save_records(recs)
            return
    if changed:
        # Restarted off the deciding request's thread (a reload stops and starts every
        # connector); one reload at a time.
        threading.Thread(target=_reload, name="a25-isolation-reload", daemon=True).start()


_RELOAD_LOCK = threading.Lock()


def _reload() -> None:
    with _RELOAD_LOCK:
        try:
            from agent_friday.services import agent as _agent
            _agent._mcp_reload()
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning("connector reload after isolation failed: %s", e)


def _register() -> None:
    from agent_friday.services import approvals as _ap
    hooks = _ap._HOOKS.get(KIND) or []
    if _on_decision not in hooks:
        _ap.register_decision_hook(KIND, _on_decision)


# ── a program that cannot run isolated (A25, owner ruling on Node.js) ────────
#
# Some installers (Node.js under Program Files) shut Windows' isolated apps out, and
# only an administrator can change that. Slack and Discord stay isolated by default;
# when such a program blocks a connector, ONE card per connector and program offers:
#   (1) the safe fix: install Node.js for this account only (link and steps); Friday
#       uses a per-user Node.js for isolated connectors from their next start;
#   (2) run this add-on un-isolated, with the risk spelled out. Approving the card is
#       this choice, and it takes a press on this PC's own screen
#       (approvals.SCREEN_ONLY_KINDS); it is recorded in the action ledger (a signed
#       receipt) and the connector's settings say "isolation": "off".
# Declining keeps it isolated (choice 1 stays open); the card is not raised again for
# the same program.

RUNTIME_KIND = "connector_runtime_blocked"
RUNTIME_SUBJECT = "connector_runtime_blocked"
NODE_DOWNLOAD = "https://nodejs.org/en/download"
_NODE_NAMES = ("node", "npx", "npm", "corepack")


def per_user_node_dir() -> Optional[Path]:
    """Where the safe fix puts Node.js: %LOCALAPPDATA%\\Programs\\nodejs."""
    la = os.environ.get("LOCALAPPDATA")
    return (Path(la) / "Programs" / "nodejs") if la else None


def is_node_program(path) -> bool:
    stem = Path(str(path or "")).stem.lower()
    return stem in _NODE_NAMES


def per_user_node_program(path) -> Optional[Path]:
    """The same Node.js program from a per-user install, when there is one."""
    d = per_user_node_dir()
    if d is None or not is_node_program(path):
        return None
    stem = Path(str(path)).stem
    # Windows runs .exe / .cmd; the zip's extensionless files are shell scripts.
    names = (stem + ".exe", stem + ".cmd") if os.name == "nt" else (stem,)
    for cand in (d / n for n in names):
        if cand.is_file():
            return cand
    return None


def runtime_state(name: str, program: str) -> str:
    """The card's answer for this add-on and program: "", offered, declined, ..."""
    cur = _runtime_records().get(str(name)) or {}
    return str(cur.get("state") or "") if cur.get("program") == str(program) else ""


def _fingerprint(command, args, env=None, cwd=None) -> str:
    """What the add-on runs as configured: command, arguments, environment (as stored,
    encrypted values included) and folder. A press applies only to this exact entry."""
    import hashlib
    env = env if isinstance(env, dict) else {}
    return hashlib.sha256(json.dumps([str(command or ""), [str(a) for a in (args or [])],
                                      sorted((str(k), str(v)) for k, v in env.items()),
                                      str(cwd or "")]).encode("utf-8")).hexdigest()


def spec_fingerprint(spec: dict) -> str:
    spec = spec or {}
    return _fingerprint(spec.get("command"), spec.get("args"), spec.get("env"), spec.get("cwd"))


def _runtime_records_file() -> Path:
    return _record_file().with_name("runtime-offers.json")


def _runtime_records() -> Dict[str, dict]:
    try:
        data = json.loads(_runtime_records_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_runtime_records(data: Dict[str, dict]) -> None:
    f = _runtime_records_file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, f)


def _runtime_card_text(name: str, program: str) -> str:
    node = is_node_program(program)
    target = per_user_node_dir()
    fix = (
        "1. The safe fix (recommended): install Node.js for your account only.\n"
        f"   - Open {NODE_DOWNLOAD} and download the Windows .zip (64-bit), not the installer.\n"
        f"   - Unzip it so that node.exe ends up in {target}\\node.exe (create that folder "
        f"if it is not there; no administrator needed).\n"
        f"   - Restart Friday, or reconnect {name}. Friday uses that Node.js for isolated "
        f"add-ons from then on; the Node.js you already have stays as it is.\n"
        if node else
        "1. The safe fix (recommended): install this program for your account only (an "
        "install under your own user folder), then restart Friday.\n")
    return (
        f"{name} is set to run in Windows' isolated mode, but its program ({program}) "
        f"cannot run there on this PC: it was installed for all users in a way that shuts "
        f"isolated apps out, and only an administrator can change that.\n\n"
        f"You have two choices:\n\n"
        f"{fix}\n"
        f"2. Run {name} un-isolated (Approve, on this PC). The risk: it then runs with your "
        f"account's full access. It can read, change and delete any of your files, read "
        f"other programs' data and saved sign-ins, reach any site on the internet and your "
        f"local network, and anything it is tricked into doing happens with those rights. "
        f"Friday still asks before its outward actions and records them, but cannot fence "
        f"what the add-on does on its own. This choice is recorded in Friday's action "
        f"ledger, and you can set \"isolation\": \"appcontainer\" again at any time.\n\n"
        f"Decline to keep {name} isolated (and off until choice 1 is done).")


def offer_runtime_choice(name: str, program: str, spec: Optional[dict] = None) -> Optional[dict]:
    """Raise the one card for this connector and program, unless it was answered or
    is still waiting. Returns the card, or None. `spec` (the entry as configured) is
    fingerprinted: a press on this card applies only to that same entry, and a card
    without one never runs anything un-isolated."""
    from agent_friday.services import approvals as _ap
    _register_runtime()
    key = str(name)
    with _LOCK:
        recs = _runtime_records()
        cur = recs.get(key) or {}
        same_program = cur.get("program") == str(program)
        if same_program and cur.get("state") == "declined":
            return None
        if same_program and cur.get("state") == "offered" and _card_waiting(cur.get("approval_id")):
            return None
        gen = int(cur.get("gen") or 0) + (1 if cur else 0)
        rec = _ap.create_approval(
            kind=RUNTIME_KIND,
            subject_type=RUNTIME_SUBJECT if gen <= 0 else "%s:%d" % (RUNTIME_SUBJECT, gen),
            subject_id=key,
            title=f"{name} can't run isolated on this PC: choose how to go on",
            description=_runtime_card_text(key, str(program)),
            action_description=f"Run {key} un-isolated, with your account's full access",
            payload={"server": key, "program": str(program),
                     "fingerprint": spec_fingerprint(spec) if isinstance(spec, dict) else ""},
            requested_by="friday:a25", force_gate=True)
        recs[key] = {"state": "offered", "approval_id": rec.get("approval_id"),
                     "program": str(program), "gen": gen}
        _save_runtime_records(recs)
        return rec


def _on_runtime_decision(record: dict) -> None:
    payload = record.get("payload") or {}
    name = str(payload.get("server") or record.get("subject_id") or "")
    if not name:
        return
    status = record.get("status")
    import logging
    with _LOCK:
        recs = _runtime_records()
        cur = recs.get(name) or {}
        if cur.get("approval_id") and record.get("approval_id") != cur.get("approval_id"):
            return
        gen = int(cur.get("gen") or 0)
        if status == "denied":
            recs[name] = dict(cur, state="declined")
            _save_runtime_records(recs)
            return
        if status != "approved":
            recs[name] = {"state": "expired", "program": cur.get("program"), "gen": gen}
            _save_runtime_records(recs)
            return
        # Approve = run it un-isolated: only a verified press on this PC's own screen
        # (the store already refuses any other approval of this kind; checked again).
        if record.get("decided_by") != "owner:ui" or record.get("decided_source") != "desktop_press":
            logging.getLogger(__name__).warning(
                "un-isolated run for %s refused: not a press on this PC", name)
            return
        log = logging.getLogger(__name__)
        # Off the record nothing about this choice could be kept in full: refused.
        try:
            from agent_friday.services import off_record as _off
            if _off.active():
                log.warning("un-isolated run for %s refused: off the record", name)
                return
        except ImportError:
            pass
        try:
            from agent_friday.services import agent as _agent
            cfg = _agent._load_mcp_servers()
            spec = (cfg.get("servers") or {}).get(name)
        except Exception as e:  # noqa: BLE001
            log.warning("un-isolated run for %s not applied: %s", name, e)
            return
        # The press applies to the entry the card was about, and only while its program
        # still cannot run isolated (a stale card never runs a changed entry un-isolated).
        fp = payload.get("fingerprint") or ""
        if not _is_command_server(spec) or not fp or fp != spec_fingerprint(spec):
            log.warning("un-isolated run for %s refused: the add-on changed since its card", name)
            return
        try:
            from agent_friday.services import appcontainer as _ac
            if _ac.readable_by_containers(str(payload.get("program") or "")) is True \
                    or per_user_node_program(payload.get("program")) is not None:
                log.warning("un-isolated run for %s refused: it can run isolated now", name)
                return
        except Exception:
            pass
        try:
            from agent_friday.services import approvals as _ap
            if hasattr(_ap, "claim_for_execution") and not _ap.claim_for_execution(record["approval_id"]):
                return
        except Exception:
            return
        # Recorded first: no ledger entry, no un-isolated run.
        if not _ledger(name, payload.get("program"), record):
            return
        changed = False
        try:
            spec["isolation"] = "off"
            spec["isolation_note"] = ("Run un-isolated by the owner's choice on this PC "
                                      "(its program cannot run in Windows' isolated mode).")
            _agent._save_mcp_servers(cfg)
            changed = True
        except Exception as e:  # noqa: BLE001
            log.warning("un-isolated run for %s not applied: %s", name, e)
            _ledger(name, payload.get("program"), record, applied=False)
            return
        recs[name] = dict(cur, state="unisolated")
        _save_runtime_records(recs)
    if changed:
        threading.Thread(target=_reload, name="a25-unisolated-reload", daemon=True).start()


def _ledger(name: str, program, record: dict, applied: bool = True) -> bool:
    """The choice in Friday's action ledger: a signed decision receipt. False when it
    could not be written (then nothing changes). `applied=False` records that a choice
    already written could not be carried out."""
    try:
        from agent_friday.governance import action_gate as _gate
        _gate._receipt({"tool": "connector_run_unisolated", "class": getattr(_gate, "OUTWARD", "outward"),
                        "decision": "allow" if applied else "not_applied",
                        "reason": ("owner chose to run this add-on un-isolated (its program "
                                   "cannot run in Windows' isolated mode)") if applied else
                                  "the owner's choice to run this add-on un-isolated could not be applied",
                        "approval": record.get("approval_id"),
                        "surface": "connector_isolation", "target": str(name)[:80],
                        "program": str(program or "")[:260], "approval_id": record.get("approval_id"),
                        "decided_by": record.get("decided_by"),
                        "decided_source": record.get("decided_source")})
        return True
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("ledger entry for un-isolated %s failed: %s", name, e)
        return False


def _register_runtime() -> None:
    from agent_friday.services import approvals as _ap
    hooks = _ap._HOOKS.get(RUNTIME_KIND) or []
    if _on_runtime_decision not in hooks:
        _ap.register_decision_hook(RUNTIME_KIND, _on_runtime_decision)


try:            # decisions arrive after a restart too: the hook is there from import
    _register()
    _register_runtime()
except Exception:  # noqa: BLE001
    pass


__all__ = ["apply_default","offer_existing", "needs_offer", "KIND", "ISOLATED"]
