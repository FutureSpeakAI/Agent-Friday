"""The retrieval ledger — security-boundary.md §20.

The gap this closes, from the spec's §1.4: vault access-log rows **stop
entirely** when prompt gating is off. In today's posture
(`vault_local_only: false`, Stephen's deliberate choice, `e1f1874`) an
ungated cloud prompt produces no record of what vault material it carried —
the 4,486-character TIER_2 measurement (`vault_policy.py:15-18`) had to be
taken by hand, once, during the 2026-09-01 split-brain investigation. The
egress log records what the *gate* did; nothing recorded what *assembly*
put on the table. This does.

Interpretation, per the spec's own note at §20: this is the ASSEMBLY-side
record — one row per named, tier-tagged section that
`model_router._build_context_prompt` produced, written at assembly time,
regardless of gating posture. Local assemblies are recorded too, cheaply,
so the ledger can answer "what does a local turn see that a cloud turn
doesn't" as well as "was the vault protecting anything that day".

Names, tiers, and sizes only — NEVER content. The content record is the
context log (`core.__init__`'s `context-log/`), which has its own switch
and disclosure; this ledger must be safe to keep forever.

Rotation/HMAC parity with the rest of §10's ledger family is future work
(§10 describes it as not yet built for the egress log either — "the egress
log has 65 MB and none"); this ledger is deliberately built to the same
plain-JSONL, append-only shape as `egress_gate.py`'s `_log()` today, so it
can pick up rotation/HMAC in the same pass rather than diverging now and
reconciling later.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from agent_friday.paths import friday_home

_LOG_LOCK = threading.Lock()
_DEFAULT_LOG = friday_home() / "vault" / "retrieval-log.jsonl"

# Tier -> what a cloud egress gate would do with this material. A HINT for
# the review surface (security-boundary.md §6.5), not a live decision — the
# egress log is the record of what actually happened to a SEND; this is the
# record of what was RETRIEVED, before any send exists to gate.
_ACTION_HINT = {1: "allow", 2: "redact", 3: "drop"}

_SECTION_NAME_RE = re.compile(r"==\s*([A-Za-z0-9 /\-\(\):,\.']+?)\s*==")


def derive_section_name(text: str, index: int) -> str:
    """Name a prompt section from its own '== NAME ==' header.

    `_build_context_prompt`'s sections are (tier, text) pairs, not
    (name, tier, text) triples — most carry a human-authored '== NAME =='
    header in the text itself (that IS the name assembly gave the section),
    so this reads it back rather than requiring every one of that
    function's ~20 call sites to be touched to carry a redundant name
    field. The few sections that render with no header (the system prompt,
    the trailing clock block) fall back to a positional slot.
    """
    if text:
        m = _SECTION_NAME_RE.search(text[:160])
        if m:
            name = re.sub(r"[^a-z0-9]+", "_", m.group(1).strip().lower()).strip("_")
            if name:
                return name[:60]
    return f"section_{index}"


def new_turn_id() -> str:
    return uuid.uuid4().hex[:12]


def record_assembly(
    sections: list[tuple[int, str]],
    *,
    turn_id: str,
    destination_class: str,
    gated: bool,
    policy_source: str,
    log_path: Path | None = None,
) -> int:
    """Write one row per named section of a prompt assembly.

    `sections` is the (tier, text) list `_build_context_prompt` already
    builds — names are derived per section via `derive_section_name`.
    Never raises: a ledger failure must not break prompt assembly. Returns
    the number of rows written (0 on any failure), which the caller can
    ignore; it exists so tests can assert without inspecting the file.
    """
    rows: list[str] = []
    ts = time.time()
    for i, (tier, text) in enumerate(sections):
        if not text:
            continue
        entry: dict[str, Any] = {
            "ts": ts,
            "turn_id": turn_id,
            "destination_class": destination_class,
            "section": derive_section_name(text, i),
            "tier": tier,
            "chars": len(text),
            "gated": bool(gated),
            "policy_source": policy_source,
            "action_hint": _ACTION_HINT.get(tier, "unknown"),
        }
        try:
            rows.append(json.dumps(entry))
        except (TypeError, ValueError):
            continue
    if not rows:
        return 0
    dest = log_path or _DEFAULT_LOG
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_LOCK:
            with open(dest, "a", encoding="utf-8") as f:
                f.write("\n".join(rows) + "\n")
    except Exception:
        return 0
    return len(rows)
