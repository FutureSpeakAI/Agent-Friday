"""The knowledge graph has a text alternative (north star NS-33.1-12; WCAG 1.1.1).

The Knowledge galaxy drew notes and links in 3D with nothing a screen reader could
read. Now the scene is a group named by what it shows ("N notes and M links"), and
"The graph as a list" above it lists the same notes, most connected first, each with
what it links to and a button that opens its page. Built only when opened.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/app.html"])
def test_the_graph_has_a_list_beside_it_and_a_name(rel):
    s = (ROOT / rel).read_text(encoding="utf-8")
    comp = s[s.index("function KgAsList({ kg, stats, onOpen })"):]
    comp = comp[:comp.index("\n}\n")]
    assert "'The graph as a list'" in comp
    assert "if (links[r.source] && byId[r.target]) links[r.source].add(byId[r.target]);" in comp
    assert "flexShrink: 0" in comp and "maxHeight: 'min(320px, 33vh)'" in comp, "the galaxy keeps its room"
    assert ".sort((a, b) => b.n - a.n" in comp and "onClick: () => onOpen && onOpen(e)" in comp
    graph = s[s.index("function KnowledgeGraphWS("):]
    assert ("React.createElement(KgAsList, {\n    kg: kgRef.current,\n    stats: stats,\n    onOpen: openWikiPage" in graph
            or "<KgAsList kg={kgRef.current} stats={stats} onOpen={openWikiPage}/>" in graph)
    assert "'Knowledge graph: ' + stats.nodes + ' notes and ' + stats.edges + ' links, drawn in 3D." in graph
