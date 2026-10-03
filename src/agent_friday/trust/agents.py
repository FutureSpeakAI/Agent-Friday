"""The Federation agent kind: a typed placeholder, disabled and hidden.

The schema, the forget registry and the tests know the kind exists so
Federation can adopt it later. Nothing creates, shows or scores an agent in
this release: every write and read of the kind raises ``NotEnabled`` unless
BOTH ``held_features.federation`` and ``held_features.trust_agents`` are on.
Neither switch is in the settings UI, and turning Federation work back on
waits for the owner's word.

While the kind is held, peer attestations are still stored but their
observations go to a quarantine lane at weight zero: they never move a
local source score (``source_trust_federation._apply_to_graph``).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

KIND = "agent"
TRUST_AGENTS = "trust_agents"
#: The federation.py peer-trust set, reserved so Federation can adopt it.
DIMS_RESERVED = ("reliability", "honesty", "claws_adherence", "competence")


class NotEnabled(RuntimeError):
    """The agent kind is held: not enabled in this release."""

    feature = TRUST_AGENTS

    def body(self) -> Dict[str, Any]:
        from agent_friday.services.held_features import not_enabled_body
        return not_enabled_body(self.feature)


def enabled(settings: Optional[dict] = None) -> bool:
    """Both switches, exactly True; anything else is off."""
    from agent_friday.services import held_features as hf
    return bool(hf.enabled(hf.FEDERATION, settings)) and bool(hf.enabled(TRUST_AGENTS, settings))


def _require() -> None:
    if not enabled():
        raise NotEnabled("the agent kind is not enabled in this release")


def agents_path() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "trust" / "agents.json"


def record(agent_id: str, owner_person_id: str, *, display_name: str = "") -> Dict[str, Any]:
    """The schema of an agent record (never written while the kind is held)."""
    return {
        "kind": KIND,
        "agent_id": str(agent_id),              # Ed25519 public key, hex
        "owner_person_id": str(owner_person_id),  # no agent without a human sponsor
        "display_name": display_name or str(agent_id)[:16],
        "attestation": {"issuer_id": None, "statement": None, "claws_hash": None,
                        "signature": None, "verified_at": None, "expires_at": None},
        "dims_reserved": list(DIMS_RESERVED),
        "created": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def create(agent_id: str, owner_person_id: str, *, display_name: str = "") -> Dict[str, Any]:
    _require()
    if not agent_id or not owner_person_id:
        raise ValueError("an agent needs an id and a human sponsor")
    rec = record(agent_id, owner_person_id, display_name=display_name)
    p = agents_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data[rec["agent_id"]] = rec
    p.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return rec


def read(agent_id: str) -> Optional[Dict[str, Any]]:
    _require()
    p = agents_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get(str(agent_id))
    except Exception:
        return None


def list_agents() -> list:
    _require()
    p = agents_path()
    if not p.exists():
        return []
    try:
        return list(json.loads(p.read_text(encoding="utf-8")).values())
    except Exception:
        return []


# ── Quarantine lane for peer evidence while the kind is held ────────────────

def quarantine_path() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "trust" / "quarantine.jsonl"


def quarantine(attestation: Dict[str, Any]) -> None:
    """Keep a peer observation at weight zero: stored, counted, never scored."""
    p = quarantine_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
           "agent_id": str(attestation.get("agent_id") or "")[:64],
           "source_domain": attestation.get("source_domain"),
           "observation": attestation.get("observation") or {},
           "weight": 0.0}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def quarantined_count() -> int:
    p = quarantine_path()
    if not p.exists():
        return 0
    return sum(1 for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip())
