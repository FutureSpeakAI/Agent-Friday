"""The settings card "Context Compression (Headroom)" reads this route.

It answered {"available": false, "error": "_get_context_compressor() missing 1
required positional argument: 'cfg'"} on the live server, so the card could
only ever say the core was missing, true or not.
"""


def test_compression_stats_route_answers_without_error(client):
    body = client.get("/api/context/compression-stats").get_json()
    assert body["status"] == "ok"
    comp = body["compression"]
    assert "error" not in comp, comp
    assert "available" in comp and "calls" in comp and "tokens_saved" in comp
    if not comp["available"]:
        assert comp.get("reason"), "an unavailable compressor must say why"
    assert "compactions" in body["compaction"]
