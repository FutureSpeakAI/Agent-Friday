"""Laya's contract with each workspace: one manifest per workspace id.

resources/laya_manifests/<workspace_id>.json says what Laya can do in a
workspace, as data a test can hold the code to: the rows the workspace
publishes (kind, ref, facets, whether they tick, how one opens), the verbs
(tool, argument template, gate class, phrasings, undo, eval case ids), and a
`not_for_laya` entry, with a one-line reason, for every UI action that has no
verb.

Standing rule (Stephen, 2026-10-09): when a workspace, a workspace object or a
UI action is added or changed, its manifest changes in the same commit.
tests/test_laya_workspace_contracts.py fails when they drift apart.

The manifest DESCRIBES; the code decides. The gate class in a manifest is never
authoritative: governance/action_gate is, and `gate_class` below reads only its
sets. A manifest cannot loosen a gate. The scattered tables (agent._TICKABLE,
screen_stage's stage tables, desktop_targets.KINDS, the router's workspace
words, agent.WORKSPACE_TOOLS) are CHECKED against these files; a later package
generates them from here.

Each manifest also declares the `jobs` its workspace can launch: every model call
or background job, by trigger, with the task kind and label services/visible_jobs
gives it in the Activity lane. tests/test_laya_workspace_contracts.py holds those
to the visible_job calls in the source.

Pure: reads JSON from this package, imports nothing heavy, never raises on a
missing file (a missing manifest is a finding for the test, not a crash).
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("friday.laya_manifests")

MANIFEST_DIR = Path(__file__).resolve().parent.parent / "resources" / "laya_manifests"

#: The Laya tiers a verb may sit at, from "Laya runs it" to "nothing yet".
#: The packages a not-for-Laya "gap" may name as the wave that closes it. Any declared
#: package id will do (a gap waits on whatever builds the missing tool); a wave may name
#: several, joined by "/".
PACKAGE_IDS = frozenset({
    "WS-2", "WS-3", "WS-4", "WS-5",
    "KW-2", "KW-3", "KW-4", "KW-5",
    "NA-2", "NA-3", "NA-4", "NA-5",
    "MT-2", "MT-3", "EG-2",
    "CS-2", "CS-3", "CS-4",
    "DA-3", "DA-4", "DA-5",
})


def wave_ids(wave) -> list:
    """The package ids a gap's ``wave`` names ("WS-2/WS-3" is two)."""
    return [w.strip() for w in str(wave or "").split("/") if w.strip()]


#: A verb at one of LAYA_TIERS counts toward coverage and needs an eval case.
#: A tier is added here, with its path check in the guard (tests/test_laya_workspace_contracts.py), in the
#: package that builds the path. point_act_tool / point_act_defer: services/point_act on the owner's own
#: screen; resolver: the item resolver the trusted router rung runs. Until then a verb is model_only.
LAYA_TIERS = ("router_rule", "router_seed", "point_act_tool", "point_act_defer", "resolver")
OTHER_TIERS = ("model_only",)
GATE_CLASSES = ("internal", "outward", "self_gated", "by_argument", "card_only_when_outward")
GROUPS = ("read", "navigate", "select", "organize", "create", "settings")

#: What may be written in a `jobs` entry (descriptive; services/visible_jobs is what registers the task).
JOB_TRIGGERS = ("button", "schedule", "idle", "laya", "voice")
JOB_HELPERS = ("visible_job", "task")     # task: a TASKS record that is its own visible task (check_visible_jobs ALLOW)
JOB_MODEL_CLASSES = ("brain", "fast", "encoder", "speech", "image")


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("laya manifest %s unreadable: %s", path.name, e)
        return None


@lru_cache(maxsize=1)
def _all() -> dict:
    out = {}
    if MANIFEST_DIR.is_dir():
        for p in sorted(MANIFEST_DIR.glob("*.json")):
            m = _read(p)
            if isinstance(m, dict) and m.get("id"):
                out[m["id"]] = m
    return out


def reload() -> None:
    """Forget the cached manifests (tests, and an edit while running)."""
    _all.cache_clear()


def all_manifests() -> dict:
    """{workspace id: manifest}, every file in the folder."""
    return dict(_all())


def get(ws_id: str) -> dict | None:
    return _all().get(ws_id)


def ids() -> list[str]:
    return sorted(_all())


def verbs(ws_id: str) -> list[dict]:
    return list((get(ws_id) or {}).get("verbs") or [])


def jobs(ws_id: str) -> list[dict]:
    """The model calls and background jobs the workspace can launch (see services/visible_jobs)."""
    return list((get(ws_id) or {}).get("jobs") or [])


def not_for_laya(ws_id: str) -> list[dict]:
    return list((get(ws_id) or {}).get("not_for_laya") or [])


def objects(ws_id: str) -> list[dict]:
    return list((get(ws_id) or {}).get("objects") or [])


def all_objects() -> list[tuple[str, dict]]:
    """[(workspace id, object)] across every manifest."""
    return [(w, o) for w, m in sorted(_all().items()) for o in (m.get("objects") or [])]


def laya_verbs(ws_id: str) -> list[dict]:
    """The verbs Laya's System 1 runs or resolves (coverage counts these)."""
    return [v for v in verbs(ws_id) if v.get("laya_tier") in LAYA_TIERS]


def coverage(ws_id: str, action_ids) -> dict:
    """Laya coverage of one workspace against its list of UI actions.

    ``laya`` is the count of actions that are a verb at a Laya tier. ``eligible`` is
    every action except those the manifest marks ``by_design`` not-for-Laya (an
    owner-only control Laya must never take), and ``pct`` is ``laya`` over
    ``eligible``. A new owner-only control, listed by_design with its reason, does
    not lower coverage; a gap does."""
    ours = {v.get("action") or v.get("id") for v in laya_verbs(ws_id)}
    by_design = {n.get("action") for n in not_for_laya(ws_id) if n.get("kind") == "by_design"}
    ids_ = list(action_ids)
    n = len(ids_)
    hit = sum(1 for a in ids_ if a in ours)
    eligible = sum(1 for a in ids_ if a not in by_design)
    return {"workspace": ws_id, "actions": n, "eligible": eligible, "laya": hit,
            "pct": round(100.0 * hit / eligible, 1) if eligible else 0.0}


def gate_class(tool: str) -> str:
    """The class action_gate's own tables give a tool, in the manifest's words.

    Read from the gate's sets, never decided here. Order: a tool the gate
    leaves to its own card (SELF_GATED) first, then one it judges per call
    (BY_ARGUMENT), then the generation tools that need a card only when they
    send something out, then the two plain sets. A tool in neither is unknown
    and the gate treats unknown tools as outward."""
    from agent_friday.governance import action_gate as ag
    if tool in ag.SELF_GATED:
        return "self_gated"
    if tool in ag.BY_ARGUMENT:
        return "by_argument"
    if tool in ag.CARD_ONLY_WHEN_OUTWARD:
        return "card_only_when_outward"
    if tool in ag.OUTWARD_TOOLS:
        return "outward"
    if tool in ag.INTERNAL_TOOLS:
        return "internal"
    return "outward"


def check_workspace_tools(keys, registry_ids=None) -> list[str]:
    """Problems between agent.WORKSPACE_TOOLS' keys and the manifests (a check
    only; WS-5 generates the table).

    Each manifest names `workspace_tools`: {"key": <the WORKSPACE_TOOLS key
    that is this workspace's own, or null>, "pools": [<pool keys it draws on>]}.
    A key in WORKSPACE_TOOLS is fine when it is some manifest's own key, or a
    pool a manifest names. A manifest naming a key that is not there is also a
    finding. `hub` and `on_demand` are pools, not workspaces."""
    keys = set(keys or [])
    own, pools, problems = {}, set(), []
    for ws, m in sorted(_all().items()):
        wt = m.get("workspace_tools") or {}
        k = wt.get("key")
        if k:
            if registry_ids is not None and k not in registry_ids:
                problems.append("%s: workspace_tools.key %r is not a workspace id" % (ws, k))
            own[k] = ws
        pools.update(wt.get("pools") or [])
    for k in sorted(keys):
        if k not in own and k not in pools:
            problems.append("WORKSPACE_TOOLS key %r is claimed by no manifest" % k)
    for k, ws in sorted(own.items()):
        if k not in keys:
            problems.append("%s names workspace_tools.key %r but WORKSPACE_TOOLS has no such key" % (ws, k))
    for p in sorted(pools):
        if p not in keys:
            problems.append("a manifest names pool %r but WORKSPACE_TOOLS has no such key" % p)
    return problems
