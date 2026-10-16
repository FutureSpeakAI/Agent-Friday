"""A failed chat send is marked on the owner's bubble with Resend / Retry.

The retry rule and the bubble marking run under node
(tests/ui/failed_send.test.cjs, wrapped here so one command runs both). The
source checks below hold both copies of the UI to the same surface: no chat
send answers its own failure with a "[Connection error]" reply bubble, the
bubble shows "Not received" with Resend or "didn't finish replying" with
Retry, and a resend carries the bubble's own turn id. That both files still
parse is tests/unit/test_ui_parses.py.
"""
import pathlib
import re
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
node = shutil.which("node")
PAGES = ("index.html", "ui_parts/app.html")


def _text(rel):
    return (REPO / rel).read_text(encoding="utf-8-sig")


@pytest.mark.skipif(not node, reason="node is not on PATH")
def test_the_retry_rule_and_the_bubble_pass_under_node():
    cp = subprocess.run([node, "--test", str(REPO / "tests" / "ui" / "failed_send.test.cjs")],
                        capture_output=True, text=True, encoding="utf-8", timeout=120,
                        cwd=str(REPO))
    assert cp.returncode == 0, cp.stdout[-3000:] + cp.stderr[-1000:]


@pytest.mark.parametrize("rel", PAGES)
def test_no_chat_send_answers_its_failure_with_a_connection_error_bubble(rel):
    text = _text(rel)
    assert not re.search(r"text\s*:\s*'\[Connection error\]'", text), (
        rel + ": a failed send still adds a '[Connection error]' reply")
    assert "fridayMarkFailedSend(" in text, rel


@pytest.mark.parametrize("rel", PAGES)
def test_the_bubble_offers_resend_and_retry_with_its_own_turn_id(rel):
    text = _text(rel)
    for words in ("'Not received'", "didn't finish replying", "'Resend'", "'Retry'"):
        assert words in text, (rel, words)
    compact = re.sub(r"\s+", "", text)
    assert "turnId:msg.turn_id" in compact, rel + ": a resend does not reuse its turn id"
    assert "opts.turnId||" in compact, rel + ": the send ignores a resend's turn id"
    assert "turnFailure!=='not_received'" in compact, rel + ": only an unreceived send is retried"
    assert "fridayServerUp()" in compact, rel + ": no health probe before the retry"
