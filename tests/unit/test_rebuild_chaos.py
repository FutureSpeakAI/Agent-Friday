"""Chaos: Friday killed in the middle of rebuilding an index (north star
§34.16-3, the index-rebuild part; services/derived_rebuild.py).

A real child process starts the conversation-search rebuild that an upgrade
or a restore asked for and is killed (os._exit) part way through writing the
new index. Then: the old index is still whole and searchable (the rebuild
writes in one transaction), the rebuild is still waiting with no try used up
and no result recorded, and the next run finishes it."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORDS = ["Greenwich", "Thamesside", "marmalade", "quaternion", "Okavango", "fjordland"]

CHILD = r"""
import os, sys
from pathlib import Path
conv_root, home = Path(sys.argv[1]), Path(sys.argv[2])
from agent_friday.services import conversations as cv, conversation_fts as fts, safe_mode
from agent_friday.services import derived_rebuild as drb
cv._root = lambda: conv_root
fts.BACKGROUND_BUILD = False
safe_mode._failed_starts = lambda: 0
drb.INDEXES = {"conversation_search": drb.INDEXES["conversation_search"]}
real = fts._insert
seen = {"n": 0}
def insert(conn, cid, m):
    seen["n"] += 1
    if seen["n"] == 3:
        os._exit(9)          # killed with the new index half written
    return real(conn, cid, m)
fts._insert = insert
drb.run_pending(home)
sys.exit(0)
"""


@pytest.fixture
def world(tmp_path, monkeypatch):
    from agent_friday.services import conversation_fts as fts, conversations as cv, safe_mode
    home = tmp_path / ".friday"
    home.mkdir()
    root = tmp_path / "conversations"
    monkeypatch.setattr(cv, "_root", lambda: root)
    monkeypatch.setattr(fts, "_BUILT", {})
    monkeypatch.setattr(fts, "BACKGROUND_BUILD", False)
    monkeypatch.setattr(safe_mode, "_failed_starts", lambda: 0)
    for k in ("FRIDAY_SAFE_START", "FRIDAY_DIAGNOSTIC_START"):
        monkeypatch.setenv(k, "0")
    safe_mode.reset_for_tests()
    for i, word in enumerate(WORDS):
        cid = cv.create("Chat %d" % i)["id"]
        cv.append(cid, {"role": "user", "text": "We talked about %s at length today." % word,
                        "meta": {"kind": "turn", "via": "chat"}})
    fts.ensure_built()
    yield home, root
    safe_mode.reset_for_tests()


def _all_found():
    from agent_friday.services import conversation_fts as fts
    return [w for w in WORDS if fts.search(w)]


def test_a_rebuild_killed_part_way_leaves_the_old_index_and_is_finished_next_time(world, monkeypatch, tmp_path):
    home, root = world
    from agent_friday.services import derived_rebuild as drb
    assert _all_found() == WORDS
    drb.request(["conversation_search"], "a restore", home)

    env = dict(os.environ, FRIDAY_HOME=str(home), HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               PYTHON_KEYRING_BACKEND="keyring.backends.fail.Keyring", FRIDAY_SAFE_START="0",
               PYTHONPATH=str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""))
    r = subprocess.run([sys.executable, "-c", CHILD, str(root), str(home)], env=env, cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 9, (r.returncode, r.stderr[-2000:])

    assert _all_found() == WORDS, "the old index did not survive a rebuild killed part way"
    pend = json.loads((home / "migrations" / drb.PENDING).read_text(encoding="utf-8"))
    assert list(pend) == ["conversation_search"] and int(pend["conversation_search"].get("attempts") or 0) == 0
    assert not (home / "migrations" / drb.RESULTS).exists()        # nothing claims it finished

    monkeypatch.setattr(drb, "INDEXES", {"conversation_search": drb.INDEXES["conversation_search"]})
    out = drb.run_pending(home)
    assert [x["index"] for x in out["ran"] if x["ok"]] == ["conversation_search"]
    assert drb.state(home)["waiting"] == [] and _all_found() == WORDS
