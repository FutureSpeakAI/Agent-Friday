"""A helper that takes longer than one lookup's allowance to start is still used: the start has its
own allowance (START_TIMEOUT_S), each lookup keeps LOOKUP_TIMEOUT_S. Under load a cold start (Python
plus espeak) took longer than 5 s, and every first name was spelled out instead of pronounced."""
import sys

from agent_friday.services import g2p_fallback as g2p


def test_a_helper_slower_to_start_than_a_lookup_still_answers(tmp_path, monkeypatch):
    slow = tmp_path / "slow_helper.py"
    slow.write_text(
        "import json, sys, time\n"
        "time.sleep(1.5)\n"
        "print(json.dumps({'ready': True}), flush=True)\n"
        "for line in sys.stdin:\n"
        "    print(json.dumps({'ps': 'nwin', 'rating': 2}), flush=True)\n", encoding="utf-8")
    monkeypatch.setattr(g2p, "HELPER_PATH", slow)
    monkeypatch.setattr(g2p, "LOOKUP_TIMEOUT_S", 1.0)       # one lookup's allowance, shorter than the start
    h = g2p._Helper()
    try:
        assert h.lookup("Nguyen", False) == ("nwin", 2)
    finally:
        h.close()
