"""Server-side deterministic 3D layout for the knowledge galaxy.

Python port of graphrag-workbench's force-simulation *design* (MIT): community
centers distributed on a Fibonacci sphere, members Fibonacci-clustered around
their center, then a fixed number of spring-relaxation iterations (edge
springs + community centroid attraction + radial shell constraint). No
d3-force-3d dependency; deterministic for a fixed (input, seed) so tests and
the 3D view are stable across rebuilds. Mutates each entity dict in place,
adding ``x``/``y``/``z``.

Scale target: ≤2k nodes in well under a second (the spec's Phase 3 budget
renders precomputed positions; the client never cold-starts a simulation).
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any

SPREAD = 150.0          # outer shell radius (matches workbench spread3D)
COMMUNITY_RADIUS = 34.0  # cluster radius around a community center
ITERATIONS = 80
SPRING = 0.06            # edge spring strength
COMMUNITY_PULL = 0.08    # attraction to community centroid
SHELL = 0.04             # radial constraint strength
REPULSE = 260.0          # short-range within-community repulsion


def _fib_sphere(i: int, n: int) -> tuple[float, float, float]:
    """i-th of n points evenly distributed on a unit sphere."""
    n = max(n, 1)
    golden = math.pi * (3.0 - math.sqrt(5.0))
    phi = math.acos(max(-1.0, min(1.0, 1.0 - 2.0 * (i + 0.5) / n)))
    theta = golden * i
    return (math.sin(phi) * math.cos(theta),
            math.sin(phi) * math.sin(theta),
            math.cos(phi))


def compute_layout(entities: list[dict], relationships: list[dict],
                   communities: list[dict], seed: int = 1337) -> dict[str, Any]:
    """Assign x/y/z to every entity (in place). Returns layout metadata."""
    if not entities:
        return {"seed": seed, "algorithm": "fibonacci+relax", "nodes": 0,
                "iterations": 0, "radius": SPREAD}

    rng = random.Random(seed)
    by_id = {e["id"]: e for e in entities}

    # Abstraction score → shell radius (hubs live nearer the core).
    scores = [e.get("degree", 0) + 0.5 * e.get("frequency", 0)
              for e in entities]
    lo, hi = min(scores), max(scores)
    span = (hi - lo) or 1.0

    # Community centers on a Fibonacci sphere, radius by mean abstraction.
    comm_ids = [c["community"] for c in communities] or ["0"]
    comm_members: dict[str, list[dict]] = defaultdict(list)
    for e in entities:
        comm_members[str(e.get("community", "0"))].append(e)

    centers: dict[str, tuple[float, float, float]] = {}
    ordered = sorted(comm_members, key=lambda c: (-len(comm_members[c]), c))
    for ci, comm in enumerate(ordered):
        ux, uy, uz = _fib_sphere(ci, len(ordered))
        member_scores = [e.get("degree", 0) + 0.5 * e.get("frequency", 0)
                         for e in comm_members[comm]]
        norm = ((sum(member_scores) / len(member_scores)) - lo) / span
        radius = SPREAD * (0.35 + 0.65 * (1.0 - norm))
        centers[comm] = (ux * radius, uy * radius, uz * radius)

    # Initial member positions: Fibonacci cluster + seeded jitter.
    for comm, members in sorted(comm_members.items()):
        cx, cy, cz = centers[comm]
        members_sorted = sorted(members, key=lambda e: e["id"])
        r_local = COMMUNITY_RADIUS * (0.6 + 0.4 * math.log2(1 + len(members)))
        for i, e in enumerate(members_sorted):
            ux, uy, uz = _fib_sphere(i, len(members_sorted))
            jitter = 0.85 + 0.3 * rng.random()
            e["x"] = cx + ux * r_local * jitter
            e["y"] = cy + uy * r_local * jitter
            e["z"] = cz + uz * r_local * jitter

    # Edge list resolved to entity dicts.
    edges = []
    for r in relationships:
        a, b = by_id.get(r["source"]), by_id.get(r["target"])
        if a is not None and b is not None:
            edges.append((a, b, float(r.get("weight", 1.0))))

    for _ in range(ITERATIONS):
        # Springs pull linked nodes together.
        for a, b, w in edges:
            dx, dy, dz = b["x"] - a["x"], b["y"] - a["y"], b["z"] - a["z"]
            f = SPRING * w * 0.5
            a["x"] += dx * f; a["y"] += dy * f; a["z"] += dz * f
            b["x"] -= dx * f; b["y"] -= dy * f; b["z"] -= dz * f

        for comm, members in comm_members.items():
            cx, cy, cz = centers[comm]
            # Centroid attraction keeps clusters coherent.
            for e in members:
                e["x"] += (cx - e["x"]) * COMMUNITY_PULL
                e["y"] += (cy - e["y"]) * COMMUNITY_PULL
                e["z"] += (cz - e["z"]) * COMMUNITY_PULL
            # Short-range pairwise repulsion within the community only
            # (cross-community spacing is handled by the center placement).
            ms = sorted(members, key=lambda e: e["id"])
            if len(ms) > 400:            # cap the O(n²) term
                ms = ms[:400]
            for i in range(len(ms)):
                a = ms[i]
                for j in range(i + 1, len(ms)):
                    b = ms[j]
                    dx, dy, dz = b["x"] - a["x"], b["y"] - a["y"], b["z"] - a["z"]
                    d2 = dx * dx + dy * dy + dz * dz
                    if d2 < 1e-6:
                        dx, d2 = 0.5, 0.25
                    if d2 < 900.0:       # only near neighbours
                        f = REPULSE / (d2 * math.sqrt(d2))
                        a["x"] -= dx * f; a["y"] -= dy * f; a["z"] -= dz * f
                        b["x"] += dx * f; b["y"] += dy * f; b["z"] += dz * f

        # Radial shell constraint: hubs toward the core.
        for e in entities:
            score = e.get("degree", 0) + 0.5 * e.get("frequency", 0)
            norm = (score - lo) / span
            target_r = SPREAD * (0.25 + 0.75 * (1.0 - norm))
            d = math.sqrt(e["x"] ** 2 + e["y"] ** 2 + e["z"] ** 2) or 1.0
            f = (target_r - d) / d * SHELL
            e["x"] += e["x"] * f; e["y"] += e["y"] * f; e["z"] += e["z"] * f

    for e in entities:
        e["x"] = round(e["x"], 3)
        e["y"] = round(e["y"], 3)
        e["z"] = round(e["z"], 3)

    return {"seed": seed, "algorithm": "fibonacci+relax",
            "nodes": len(entities), "iterations": ITERATIONS,
            "radius": SPREAD,
            "community_centers": {c: [round(v, 3) for v in xyz]
                                  for c, xyz in centers.items()}}
