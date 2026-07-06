"""Graph analysis: community detection, god nodes, surprising connections.

Ported from obsidian-wiki's ``graph_analysis.py`` (MIT). Operates on a plain
``outgoing`` adjacency dict (``{source: [target, ...]}``) instead of re-reading
the vault, so it works on any tier of Friday's knowledge graph.

Community detection tries Leiden (``leidenalg`` + ``igraph``) when installed
and falls back to a pure-Python greedy label-propagation variant. Both paths
are deterministic for a fixed input (nodes are processed in sorted order).
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any


# ── degree tables ─────────────────────────────────────────────

def _build_degree_tables(
    outgoing: dict[str, list[str]]
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    out_deg: dict[str, int] = {n: len(v) for n, v in outgoing.items()}
    in_deg: dict[str, int] = defaultdict(int)
    for targets in outgoing.values():
        for t in targets:
            in_deg[t] += 1
    degree = {n: out_deg.get(n, 0) + in_deg.get(n, 0) for n in outgoing}
    for n in in_deg:
        degree.setdefault(n, in_deg[n])
    return degree, dict(in_deg), out_deg


def god_nodes(outgoing: dict[str, list[str]], top_n: int = 20) -> list[dict]:
    """Most-linked nodes (hubs), ranked by total degree."""
    degree, in_deg, out_deg = _build_degree_tables(outgoing)
    ranked = sorted(degree, key=lambda n: (-degree[n], n))[:top_n]
    return [
        {"page": n, "degree": degree[n],
         "in_degree": in_deg.get(n, 0), "out_degree": out_deg.get(n, 0)}
        for n in ranked
    ]


def dead_ends(outgoing: dict[str, list[str]]) -> list[str]:
    """Nodes with no outgoing edges."""
    return sorted(n for n, targets in outgoing.items() if not targets)


def isolated(outgoing: dict[str, list[str]]) -> list[str]:
    """Nodes with zero edges in either direction."""
    _, in_deg, out_deg = _build_degree_tables(outgoing)
    return sorted(n for n in outgoing
                  if in_deg.get(n, 0) == 0 and out_deg.get(n, 0) == 0)


# ── community detection ───────────────────────────────────────

def detect_communities_greedy(outgoing: dict[str, list[str]]) -> list[set[str]]:
    """Greedy label propagation. O(n·k); deterministic (sorted node order)."""
    nodes = sorted(outgoing.keys())
    if not nodes:
        return []

    adj: dict[str, list[str]] = defaultdict(list)
    for src, targets in outgoing.items():
        for t in targets:
            adj[src].append(t)
            adj[t].append(src)

    labels: dict[str, int] = {n: i for i, n in enumerate(nodes)}

    for _ in range(20):
        changed = False
        for n in nodes:
            neighbours = adj[n]
            if not neighbours:
                continue
            freq: dict[int, int] = defaultdict(int)
            for nb in neighbours:
                freq[labels[nb]] += 1
            best = max(freq, key=lambda lbl: (freq[lbl], -lbl))
            if best != labels[n]:
                labels[n] = best
                changed = True
        if not changed:
            break

    groups: dict[int, set[str]] = defaultdict(set)
    for n, lbl in labels.items():
        groups[lbl].add(n)
    # Stable order: largest first, ties broken by smallest member.
    return sorted(groups.values(), key=lambda c: (-len(c), min(c)))


def detect_communities(outgoing: dict[str, list[str]]) -> list[set[str]]:
    """Leiden when available, else greedy label propagation."""
    try:
        import igraph as ig
        import leidenalg

        nodes = sorted(outgoing.keys())
        node_idx = {n: i for i, n in enumerate(nodes)}
        edges = [
            (node_idx[s], node_idx[t])
            for s, targets in outgoing.items()
            for t in targets
            if t in node_idx
        ]
        g = ig.Graph(n=len(nodes), edges=edges, directed=False)
        partition = leidenalg.find_partition(
            g, leidenalg.ModularityVertexPartition, seed=42)
        comms = [{nodes[i] for i in cluster} for cluster in partition]
        return sorted(comms, key=lambda c: (-len(c), min(c)))
    except ImportError:
        return detect_communities_greedy(outgoing)


# ── surprising connections ────────────────────────────────────

def surprising_connections(
    outgoing: dict[str, list[str]],
    communities: list[set[str]],
    top_n: int = 20,
) -> list[dict]:
    """Cross-community edges ranked by unexpectedness.

    Score = 1 / sqrt(cross_degree(source) * cross_degree(target)) — low
    cross-degree nodes connected across communities are the most surprising.
    """
    node_comm: dict[str, int] = {}
    for i, comm in enumerate(communities):
        for n in comm:
            node_comm[n] = i

    cross_deg: dict[str, int] = defaultdict(int)
    for src, targets in outgoing.items():
        for t in targets:
            if node_comm.get(src) != node_comm.get(t):
                cross_deg[src] += 1
                cross_deg[t] += 1

    results = []
    seen: set[tuple[str, str]] = set()
    for src in sorted(outgoing):
        for t in outgoing[src]:
            pair = tuple(sorted((src, t)))
            if pair in seen:
                continue
            if node_comm.get(src) != node_comm.get(t):
                cd_s = cross_deg.get(src, 1) or 1
                cd_t = cross_deg.get(t, 1) or 1
                score = 1.0 / math.sqrt(cd_s * cd_t)
                results.append({"source": src, "target": t,
                                "score": round(score, 4)})
                seen.add(pair)

    results.sort(key=lambda x: (-x["score"], x["source"], x["target"]))
    return results[:top_n]


# ── full analysis over an adjacency dict ──────────────────────

def analyse_graph(outgoing: dict[str, list[str]],
                  tags_map: dict[str, list[str]] | None = None,
                  top_n: int = 20) -> dict[str, Any]:
    tags_map = tags_map or {}
    if not outgoing:
        return {
            "god_nodes": [], "communities": [],
            "surprising_connections": [], "dead_ends": [], "isolated": [],
            "stats": {"pages": 0, "edges": 0, "communities": 0},
        }

    communities_raw = detect_communities(outgoing)
    total_edges = sum(len(v) for v in outgoing.values())

    return {
        "god_nodes": god_nodes(outgoing, top_n),
        "communities": [
            {"id": i, "size": len(comm), "pages": sorted(comm),
             "label": _label_community(sorted(comm), tags_map)}
            for i, comm in enumerate(communities_raw)
        ],
        "surprising_connections": surprising_connections(
            outgoing, communities_raw, top_n),
        "dead_ends": dead_ends(outgoing),
        "isolated": isolated(outgoing),
        "stats": {"pages": len(outgoing), "edges": total_edges,
                  "communities": len(communities_raw)},
    }


def _label_community(pages: list[str], tags_map: dict[str, list[str]]) -> str:
    """Most common tag, else longest shared name prefix."""
    freq: dict[str, int] = defaultdict(int)
    for p in pages:
        for tag in tags_map.get(p, []):
            if not tag.startswith("visibility/"):
                freq[tag] += 1
    if freq:
        return max(sorted(freq), key=lambda t: freq[t])
    if len(pages) == 1:
        return pages[0]
    prefix = pages[0]
    for p in pages[1:]:
        while not p.startswith(prefix):
            prefix = prefix[:-1]
        if not prefix:
            break
    return prefix.rstrip("-_") or f"cluster-{pages[0][:20]}"
