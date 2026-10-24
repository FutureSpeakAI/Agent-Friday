"""Known issues surfaced (north star §36.0-7) and the Doctor never silently
resets the owner's data (§28.4-2).

The shipped KNOWN_ISSUES.md is shown on the Doctor page and counted in the
Doctor report; a missing list says so instead of reading as "none". A failed
settings read is named by a Doctor check, and no Doctor path writes the
owner's settings, persona, goals, governance records, grants or memory, not
even when settings.json cannot be read."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

SAMPLE = """# Known Issues

**For Agent Friday Beta 9 (9.0.0b1).**

Intro text that is not in a section.

## 1. Moving

- **Moving keeps your data.** The installer copies
  the folder first. See [the guide](docs/guide.md).
- A bullet with no bold title.

## 2. Things that leave

No telemetry. The connections are listed in
[background network](docs/net.md).

## 3. Empty
"""


def _ki():
    assert importlib.util.find_spec("agent_friday.services.known_issues") is not None, \
        "the known issues are not read anywhere in the product"
    return importlib.import_module("agent_friday.services.known_issues")


def test_sections_items_and_paragraphs_are_read_as_plain_text():
    d = _ki().parse(SAMPLE)
    assert d["release"] == "For Agent Friday Beta 9 (9.0.0b1)"
    assert [s["title"] for s in d["sections"]] == ["1. Moving", "2. Things that leave"]
    moving = d["sections"][0]["items"]
    assert moving[0] == {"title": "Moving keeps your data.",
                         "text": "The installer copies the folder first. See the guide."}
    assert moving[1] == {"title": "", "text": "A bullet with no bold title."}
    # a paragraph is a note of its section, shown but not counted as an issue
    assert d["sections"][1]["items"] == []
    assert d["sections"][1]["notes"] == ["No telemetry. The connections are listed in background network."]


def test_the_shipped_list_is_read_whole():
    k = _ki().load()
    assert k["available"] is True and k["count"] >= 10
    text = (ROOT / "KNOWN_ISSUES.md").read_text(encoding="utf-8-sig")
    headings = [h for h in re.findall(r"^##\s+(.+?)\s*$", text, re.M)]
    assert [s["title"] for s in k["sections"]] == [_ki()._plain(h) for h in headings]
    body = text[text.index("\n## "):]
    assert k["count"] == len(re.findall(r"^- ", body, re.M)), "a bullet of the list was not read"
    assert "`" not in json.dumps(k) and "](" not in json.dumps(k)
    assert "<withheld>" not in json.dumps(_ki().summary())


def test_a_missing_list_says_so(monkeypatch, tmp_path):
    ki = _ki()
    monkeypatch.setattr(ki, "_candidates", lambda: [tmp_path / "KNOWN_ISSUES.md"])
    k = ki.load()
    assert k["available"] is False and "not in this install" in k["why"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / ".friday"
    h.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(h))
    from agent_friday import core
    # core binds its folder and settings file at import: pointed here too, so
    # a write through core would land where the test looks
    monkeypatch.setattr(core, "FRIDAY_DIR", h)
    monkeypatch.setattr(core, "SETTINGS_FILE", h / "settings.json")
    files = {
        "settings.json": json.dumps({"agent_name": "Friday", "theme": "dark"}),
        "SOUL.md": "# Who Friday is\n",
        "personality.json": json.dumps({"warmth": 0.8}),
        "goals/goal_1.json": json.dumps({"id": "g1", "title": "Run 10k"}),
        "governance/goal_receipts.jsonl": '{"x": 1}\n',
        "core_memory/ME.md": "- likes long walks\n",
        "memory/memory_ledger.jsonl": '{"m": 1}\n',
        "approvals.json": json.dumps({"cards": []}),
        "privacy/file_grants.jsonl": '{"grant": 1}\n',
        "governance/desktop_app_grants.json": json.dumps({"version": 1, "grants": []}),
        "instructions/manifest.json": json.dumps({"version": 1}),
        "vault/.vault_config.json": json.dumps({"salt_hex": "00" * 16}),
        "security/keystore.json": json.dumps({"wrap": "none", "v": 1}),
        "schedules.json": json.dumps({"jobs": []}),
        "mcp_servers.json": json.dumps({"servers": {}}),
        "conversations/c1/messages.jsonl": '{"role": "user", "text": "hi"}\n',
        "wiki/page.md": "# A page\n",
    }
    for rel, text in files.items():
        p = h / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return h


PERSONAL = ("settings.json", "SOUL.md", "personality.json", "goals", "governance", "core_memory",
            "memory", "approvals.json", "privacy", "instructions", "vault", "security", "schedules.json",
            "mcp_servers.json", "conversations", "wiki")


def _snapshot(h: Path) -> dict:
    out = {}
    for top in PERSONAL:
        p = h / top
        for f in ([p] if p.is_file() else sorted(p.rglob("*")) if p.exists() else []):
            if f.is_file():
                out[str(f.relative_to(h))] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def test_the_report_counts_the_known_issues_and_the_weekly_email_leaves_them_out(home):
    from agent_friday.services import doctor, doctor_weekly
    rep = doctor.build()
    assert "known_issues" in rep, "the Doctor report does not mention the known issues"
    assert rep["known_issues"]["available"] is True and rep["known_issues"]["count"] >= 10
    assert "Known issues in this release:" in doctor.render_text(rep)
    assert "Known issues in this release:" not in doctor_weekly._render(rep, False)


def test_a_failed_settings_read_is_named_by_a_check(home, monkeypatch):
    from agent_friday import core
    from agent_friday.services import doctor
    monkeypatch.setattr(core, "_settings_read_degraded", lambda: True)
    checks = {c["id"]: c for c in doctor.build()["checks"]}
    assert "settings_read" in checks, "no Doctor check names a failed settings read"
    assert checks["settings_read"]["state"] == "degraded"
    assert checks["settings_read"]["code"] == "settings_unreadable_last_good_served"
    monkeypatch.setattr(core, "_settings_read_degraded", lambda: False)
    assert {c["id"]: c for c in doctor.build()["checks"]}["settings_read"]["state"] == "healthy"


def test_no_doctor_path_writes_the_owners_data(home, tmp_path, monkeypatch):
    from agent_friday.services import doctor, store_inventory
    before = _snapshot(home)
    doctor.build()
    doctor.save()
    store_inventory.inventory(home)
    if importlib.util.find_spec("agent_friday.services.support_bundle") is not None:
        sb = importlib.import_module("agent_friday.services.support_bundle")
        monkeypatch.setattr(sb, "_off_record", lambda: False)
        monkeypatch.setattr(sb, "_recently_off_record", lambda: False)
        sb.make(tmp_path / "support")
    if importlib.util.find_spec("agent_friday.services.known_issues") is not None:
        importlib.import_module("agent_friday.services.known_issues").load()
    assert _snapshot(home) == before


def test_an_unreadable_settings_file_is_left_exactly_as_it_is(home):
    from agent_friday.services import doctor
    from agent_friday import core
    broken = b'{"agent_name": "Friday", "theme": '            # cut off mid-write
    (home / "settings.json").write_bytes(broken)
    served = core._load_settings_raw()                             # the degraded read Friday makes
    assert core._is_degraded_settings(served)
    rep = doctor.build()
    doctor.save(rep)
    assert (home / "settings.json").read_bytes() == broken
    checks = {c["id"]: c for c in rep["checks"]}
    assert checks["settings"]["state"] == "misconfigured"
    assert checks["settings_read"]["state"] == "degraded"
