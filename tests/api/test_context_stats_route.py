"""Compression savings endpoint — surfaces the stats the compressor has
always tracked internally."""
from __future__ import annotations


def test_compression_stats_endpoint(client):
    resp = client.get("/api/context/compression-stats")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    comp = data["compression"]
    # Exact values depend on whether headroom is installed; the contract is
    # the shape (and graceful availability reporting), not the numbers.
    assert "available" in comp
    if "calls" in comp:
        assert comp["calls"] >= 0
        assert "tokens_saved" in comp
