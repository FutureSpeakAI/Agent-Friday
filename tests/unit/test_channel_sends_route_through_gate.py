"""Every Telegram and Discord send goes through the sealing manager.

KNOWN_ISSUES listed this as unverified. Verified 2026-09-06: the only
transports are telegram_bridge._send_raw and discord_bridge._send_raw, and
the only caller of either is channels/base.py ChannelAdapter.send(), after
_gate_channel_text; inbound replies are sealed again in manager.gate_reply.
This test keeps that true: nothing outside services/channels/ may name
_send_raw, and the base adapter must gate before it calls it.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "agent_friday"


def test_send_raw_is_only_reachable_through_the_channel_adapter():
    offenders = []
    for py in SRC.rglob("*.py"):
        rel = py.relative_to(SRC).as_posix()
        if rel.startswith("services/channels/"):
            continue
        text = py.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\b_send_raw\b", text):
            offenders.append(rel)
    assert offenders == [], f"a module outside services/channels names _send_raw: {offenders}"


def test_channel_adapter_gates_before_it_sends():
    base = (SRC / "services" / "channels" / "base.py").read_text(encoding="utf-8")
    gate_at = base.index("_gate_channel_text(")
    send_body = base[base.index("def send("):]
    assert "_gate_channel_text(" in send_body, "ChannelAdapter.send must gate the text"
    assert send_body.index("_gate_channel_text(") < send_body.index("_send_raw("), "gate must run before the raw send"
    assert gate_at >= 0
