"""scripts/list_suspected_test_records.py lists suspected test records and never changes anything. SYNTHETIC RECORDS ONLY.

A data root is built in a temp directory with seeded suspects (stock placeholder people, placeholder intelligence, a note titled
for a test) beside clean records. What each check bites on:
  1. every seeded suspect is listed with its file and where in it, and no clean record is;
  2. the data root is byte-for-byte the same after a run (a digest of every file), whatever was listed;
  3. --out inside the data root is refused with status 2 and writes nothing; --out outside it is written;
  4. the script contains no delete, move or edit call and no write-mode open outside its one report writer (the check parses
     the source), and that check can fail: a copy with a remove call in it is reported;
  5. status 0 with nothing found, and --json is valid JSON.
"""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "list_suspected_test_records.py"


def _load():
    spec = importlib.util.spec_from_file_location("list_suspected_test_records", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "data_root"
    (r / "wiki" / "briefings").mkdir(parents=True)
    (r / "wiki" / "notes").mkdir(parents=True)
    graph = {"version": 1, "people": {
        "jane_smith": {"name": "Jane Smith", "intelligence": [{"content": "Intel about Jane John"}]},
        "morgan_lee": {"name": "Morgan Lee", "aliases": ["Mo"], "notes": "met at the conference"},
    }}
    (r / "trust_graph.json").write_text(json.dumps(graph), encoding="utf-8")
    (r / "wiki" / "briefings" / "2026-04-15-supplement.md").write_text(
        "# Briefing Supplement\n\n### John Doe -- 22:41\n\ntest intel\n\n### Research -- 23:47\nTest intel about a person\n",
        encoding="utf-8")
    (r / "wiki" / "briefings" / "_index.md").write_text(
        "# Briefings\n- 2026-04-15-supplement.md: Meeting notes for test\n- 2026-04-16.md: Daily briefing\n", encoding="utf-8")
    (r / "wiki" / "notes" / "plan.md").write_text("# Plan\nQuarterly planning notes for the garden project.\n", encoding="utf-8")
    return r


def _digest(root):
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def test_every_seeded_suspect_is_listed_and_no_clean_record_is(root):
    records = _load().scan(root)
    found = {(r["file"], r["locator"]) for r in records}
    assert ("trust_graph.json", "entry 'jane_smith' (name)") in found
    assert ("trust_graph.json", "entry 'jane_smith' (saved text)") in found
    assert ("wiki/briefings/2026-04-15-supplement.md", "line 3") in found       # the John Doe heading
    assert ("wiki/briefings/2026-04-15-supplement.md", "line 5") in found       # test intel
    assert ("wiki/briefings/2026-04-15-supplement.md", "line 8") in found       # Test intel about a person
    assert ("wiki/briefings/_index.md", "line 2") in found                      # Meeting notes for test
    assert not [r for r in records if "morgan_lee" in r["locator"] or r["file"] == "wiki/notes/plan.md"
                or r["locator"] == "line 3" and r["file"].endswith("_index.md")], "a clean record was flagged"


def test_the_data_root_is_unchanged_after_a_run(root, capsys):
    before = _digest(root)
    assert _load().main(["--root", str(root)]) == 0
    assert _digest(root) == before
    assert "Nothing was changed" in capsys.readouterr().out


def test_out_inside_the_root_is_refused_and_outside_is_written(root, tmp_path, capsys):
    mod = _load()
    inside = root / "report.txt"
    assert mod.main(["--root", str(root), "--out", str(inside)]) == 2 and not inside.exists()
    outside = tmp_path / "report.txt"
    assert mod.main(["--root", str(root), "--out", str(outside)]) == 0
    assert "suspected test record" in outside.read_text(encoding="utf-8")


_MUTATING = {"unlink", "remove", "rmtree", "rename", "replace", "rmdir", "write_text", "write_bytes", "truncate", "move"}


def _mutations(source):
    """Calls that change files, outside the one function that writes the report."""
    tree = ast.parse(source)
    found = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        if fn.name == "_write_report":
            continue
        for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
            name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
            if name in _MUTATING:
                found.append((fn.name, name))
            if name == "open" and len(call.args) > 1 and isinstance(call.args[1], ast.Constant) \
                    and any(c in str(call.args[1].value) for c in "wax+"):
                found.append((fn.name, "open for write"))
    top = [n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.Import, ast.ImportFrom, ast.Assign, ast.Expr, ast.If))]
    return found + [("module", type(n).__name__) for n in top]


def test_the_script_has_no_delete_move_or_edit_call_and_the_check_can_fail():
    assert _mutations(SCRIPT.read_text(encoding="utf-8")) == []
    mutant = SCRIPT.read_text(encoding="utf-8").replace("    return scan_people(root) + scan_wiki(root)",
                                                        "    os.remove(root / 'trust_graph.json')\n    return scan_people(root) + scan_wiki(root)")
    assert ("scan", "remove") in _mutations(mutant), "a delete added to the script is reported"


def test_nothing_found_is_status_zero_and_json_is_valid(tmp_path, capsys):
    mod = _load()
    empty = tmp_path / "empty_root"
    empty.mkdir()
    assert mod.main(["--root", str(empty)]) == 0
    capsys.readouterr()
    assert mod.main(["--root", str(empty), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
