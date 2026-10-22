"""The context manifest of each send that leaves this PC (north star §12.5,
§14.3, §26.7; BD-15 slice P3).

Every send the egress gate releases (seal_outbound, gate_text,
gate_worker_payload, a stand-alone field gate) gets one manifest, written when
the gate closes the send's call row and filed under that row's manifest_id
("cm-" + the call id). It lists, by name, size and class only and never by
content:

  sources              what parts of the payload were there (system prompt,
                       messages, tools, images ...), each with its sensitivity
                       class and what the gate did with it; when opened, the
                       prompt sections the turn assembled (from the retrieval
                       ledger: name, tier, size, whether sent), said as the
                       turn's, since a turn can send several times
  principal            whose request it served
  sensitivity_classes  every class the gate saw
  withheld/redactions  how much was withheld and in which parts
  grants               what allowed otherwise restricted content to leave
  destination/purpose  where it went and why
  estimated_tokens     bytes / 4, said as an estimate
  previewed            whether the owner saw the payload before it left
                       (always False today: Friday shows no outbound preview
                       yet, and the manifest says so rather than implying one)

Off the record the manifest keeps the counts and classes only: no section
names. A turn's receipt lists its manifests (services/turn_receipt), and the
privacy map opens one from a send's receipt.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_log = logging.getLogger("friday.context_manifest")
_LOCK = threading.Lock()
MANIFEST_VERSION = 1
#: The manifest file rotates past this size (the newest stay readable).
MAX_FILE_BYTES = 20 * 1024 * 1024
#: Bytes searched in each file when a manifest is opened by id or by turn:
#: the whole of a file (it rotates at MAX_FILE_BYTES), so every kept
#: manifest can be opened. Only on the owner's click, never on a send.
_TAIL_BYTES = MAX_FILE_BYTES + 1024 * 1024
_ID = re.compile(r"cm-[A-Za-z0-9_.:-]{1,80}")
_INDEX = re.compile(r"\[\d+\]")

PREVIEW_WORDS = "Not previewed: Friday does not show an outbound preview yet."


def path() -> Path:
    """Beside the egress log it belongs to (vault/context-manifests.jsonl)."""
    from agent_friday.services import egress_gate as _eg
    return Path(_eg._DEFAULT_LOG).with_name("context-manifests.jsonl")


#: Path segments after which the rest of a field path is data (keys of a
#: tool's arguments or result), not the payload's own structure.
_DATA_SEGMENTS = {"tool_result", "tool_use", "input", "arguments", "args", "function_response",
                  "functionResponse", "result", "parameters"}
_STRUCT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,40}(\[\])?")


def part_name(field: str) -> str:
    """A payload field as a part name: list positions folded, so forty
    messages are one source ("messages[].content"), never forty. Only the
    payload's own structure is kept: the path stops at the first segment that
    is data (a tool's argument or result keys, which can be an address or a
    file name) or that doesn't look like a structural name."""
    segs = _INDEX.sub("[]", str(field or "?")).split(".")
    out = []
    for i, seg in enumerate(segs):
        if not _STRUCT.fullmatch(seg):
            out.append("{…}")
            break
        out.append(seg)
        if seg.rstrip("[]") in _DATA_SEGMENTS and i + 1 < len(segs):
            out.append("{…}")
            break
    return ".".join(out)[:80]


def tally(call: Dict[str, Any], field: str, tier: str, action: str) -> None:
    """Count one gated field into its send's sources (called by the gate)."""
    src = call.setdefault("sources", {})
    key = (part_name(field), str(tier or "?")[:24], str(action or "?")[:16])
    src[key] = src.get(key, 0) + 1


def _principal(turn_id: Optional[str]) -> str:
    try:
        from flask import g, has_request_context
        if has_request_context():
            p = getattr(g, "friday_principal", None)
            if p:
                return "you" if p == "user" else str(p)[:40]
    except Exception:
        pass
    # A send made for a turn (often from the reply's own stream thread, with
    # no request context) serves the person whose turn it is.
    return "you" if turn_id else "Friday's own work"


def _sections(turn_id: str) -> List[dict]:
    try:
        from agent_friday.services import turn_receipt as _tr
        out = []
        for s in _tr._sections(turn_id)[:60]:
            out.append({k: s.get(k) for k in ("name", "tier", "chars", "sent", "planner") if k in s})
        return out
    except Exception:
        return []


def build(call: Dict[str, Any], entry: Dict[str, Any], *, off_record: bool) -> Dict[str, Any]:
    """The manifest for one closed send. Pure and cheap (it runs before every
    send): the call's own tally only. The turn's prompt sections are attached
    when the manifest is opened, not here. Off the record the parts are not
    named: classes, actions and counts only."""
    sources = [{"part": k[0], "sensitivity": k[1], "action": k[2], "count": n}
               for k, n in sorted((call.get("sources") or {}).items())]
    if off_record:
        merged: Dict[tuple, int] = {}
        for s in sources:
            merged[(s["sensitivity"], s["action"])] = merged.get((s["sensitivity"], s["action"]), 0) + s["count"]
        sources = [{"sensitivity": k[0], "action": k[1], "count": n} for k, n in sorted(merged.items())]
    classes = sorted({s["sensitivity"] for s in sources})
    redactions = [{k: s[k] for k in ("part", "action", "count") if k in s}
                  for s in sources if s["action"] in ("redact", "drop", "block")]
    size = int(entry.get("bytes") or 0)
    m: Dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "manifest_id": entry.get("manifest_id"), "call_id": entry.get("call_id"),
        "ts": entry.get("ts") or time.time(), "principal": _principal(entry.get("turn_id")),
        "provider": entry.get("provider"), "destination": entry.get("destination"),
        "model": entry.get("model"), "purpose": entry.get("purpose"), "via": entry.get("via"),
        "result": entry.get("result"),
        "sources": sources, "sensitivity_classes": classes,
        "withheld": int(entry.get("withheld") or 0), "redactions": redactions,
        "grants": list(entry.get("grants") or []),
        "bytes": size, "estimated_tokens": (size + 3) // 4 if size else 0,
        "previewed": False, "previewed_words": PREVIEW_WORDS,
    }
    if entry.get("turn_id"):
        m["turn_id"] = entry["turn_id"]
    if call.get("nested"):
        # A send made while another was being sealed (a judge asked about the
        # payload, say): the turn's prompt was not part of it.
        m["nested"] = True
    if off_record:
        m["off_record"] = True
    return m


def _with_sections(m: Dict[str, Any]) -> Dict[str, Any]:
    """On opening: the prompt sections the turn assembled, said as the turn's
    (a turn can send several times), never on a nested send or off the record."""
    if m.get("turn_id") and not m.get("off_record") and not m.get("nested"):
        m = dict(m, assembled_sections=_sections(m["turn_id"]),
                 assembled_words="The prompt sections this turn assembled (for all of its sends).")
    return m


def write(manifest: Dict[str, Any]) -> bool:
    """Append one manifest to the real file. Never raises."""
    try:
        dest = path()
        dest.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(manifest, ensure_ascii=False, default=str) + "\n"
        with _LOCK:
            try:
                if dest.stat().st_size > MAX_FILE_BYTES:
                    dest.replace(dest.with_name(dest.name + ".1"))
            except OSError:
                pass
            with open(dest, "a", encoding="utf-8") as fh:
                fh.write(line)
        return True
    except Exception as e:
        _log.warning("context manifest not written: %s", type(e).__name__)
        return False


def _tail(p: Path) -> List[str]:
    try:
        size = p.stat().st_size
        with p.open("rb") as fh:
            if size > _TAIL_BYTES:
                fh.seek(size - _TAIL_BYTES)
                fh.readline()
            return fh.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _rows(needle: str) -> List[dict]:
    p = path()
    out = []
    for f in (p.with_name(p.name + ".1"), p):
        for raw in _tail(f):
            if needle not in raw:
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def read(manifest_id: str) -> Optional[Dict[str, Any]]:
    mid = str(manifest_id or "")
    if not _ID.fullmatch(mid):
        return None
    hits = [r for r in _rows('"' + mid + '"') if r.get("manifest_id") == mid]
    return _with_sections(hits[-1]) if hits else None


def for_turn(turn_id: str) -> List[Dict[str, Any]]:
    """A turn's manifests, summarised for its receipt (newest last)."""
    tid = str(turn_id or "")
    if not tid:
        return []
    out = []
    for r in _rows(tid):
        if r.get("turn_id") != tid:
            continue
        out.append({"manifest_id": r.get("manifest_id"), "destination": r.get("destination"),
                    "model": r.get("model"), "purpose": r.get("purpose"),
                    "sensitivity_classes": r.get("sensitivity_classes") or [],
                    "withheld": r.get("withheld") or 0, "estimated_tokens": r.get("estimated_tokens") or 0,
                    "previewed": bool(r.get("previewed")),
                    "open": "/api/privacy/manifest/" + str(r.get("manifest_id"))})
    return out
