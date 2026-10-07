"""I10 / zero telemetry: See & Touch logs counts only and never leaves the process.

A log line carries ws, op, count, rule: never a title, a ref, a sender or a query.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from agent_friday.services import agent, desktop_bus, screen_stage as ss
from tests.screen_fixtures import Page, newsletter_stage, report

SRC = Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "services"
SECRET = "Harbor Legal Billing"


class _Cap(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def cap():
    h = _Cap()
    root = logging.getLogger()
    old = root.level
    root.addHandler(h)
    root.setLevel(logging.DEBUG)
    desktop_bus.reset()
    yield h
    root.removeHandler(h)
    root.setLevel(old)
    desktop_bus.reset()


def test_a_select_and_a_look_log_only_counts(cap, monkeypatch):
    st = newsletter_stage(selected=[1, 2])
    for it in st["items"]:
        it["title"], it["who"] = SECRET + " statement", SECRET
    report(st)
    Page(monkeypatch, answer={"ok": True, "applied": 3, "missing": 0, "accepted": 3, "count": 3})
    agent._tool_screen_select({"op": "select", "scope": "screen", "match": {"category": "newsletters"},
                               "label": SECRET})
    agent._tool_check_situation({"look": "screen"})
    mine = [ln for ln in cap.lines if ln.startswith("screen ")]
    assert mine, cap.lines
    for ln in mine:
        assert re.fullmatch(r"screen \w+ ws=\w+ count=\d+( rule=\w+)?", ln), ln
    blob = "\n".join(cap.lines)
    assert SECRET not in blob and "mail:acct_work" not in blob and "substack" not in blob


def test_the_stage_modules_name_no_network_client_and_write_no_file():
    for name in ("screen_stage.py",):
        src = (SRC / name).read_text(encoding="utf-8")
        assert not re.search(r"\b(requests|urllib|httpx|socket|aiohttp)\b", src), name
        assert not re.search(r"\bopen\(|write_text|write_bytes|json\.dump\(", src), name
    bus = (SRC / "desktop_bus.py").read_text(encoding="utf-8")
    assert not re.search(r"\b(requests|urllib|httpx|aiohttp)\b|write_text|json\.dump\(", bus)


def test_log_counts_never_takes_content():
    import inspect
    assert list(inspect.signature(ss.log_counts).parameters) == ["op", "ws", "count", "rule"]
