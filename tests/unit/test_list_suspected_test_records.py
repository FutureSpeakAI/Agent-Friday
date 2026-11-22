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


_MUTATING = {"unlink", "remove", "rmtree", "rename", "replace", "rmdir", "write_text", "write_bytes", "truncate", "move",
             "touch", "mkdir", "makedirs", "chmod", "utime", "symlink", "symlink_to", "hardlink_to", "link", "link_to",
             "copy", "copyfile", "copy2", "copytree"}
_WRITE_FLAGS = {"O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND", "O_TRUNC"}


def _write_mode(call):
    """The mode an open() call names, positionally (open(p, mode) / p.open(mode)) or by keyword; '' when none."""
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    pos = 0 if isinstance(call.func, ast.Attribute) and not (isinstance(call.func.value, ast.Name)
                                                             and call.func.value.id in ("io", "os", "codecs")) else 1
    if len(call.args) > pos and isinstance(call.args[pos], ast.Constant):
        return str(call.args[pos].value)
    return ""


def _mutations(source):
    """Calls that change files, anywhere in the script (module level included) outside the one report writer."""
    tree = ast.parse(source)
    skip = set()
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_write_report"]:
        skip |= {id(n) for n in ast.walk(fn)}
    owner = {}
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for n in ast.walk(fn):
            owner.setdefault(id(n), fn.name)
    found = []
    for call in [n for n in ast.walk(tree) if isinstance(n, ast.Call) and id(n) not in skip]:
        where = owner.get(id(call), "module")
        name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
        if name in _MUTATING:
            found.append((where, name))
        elif name == "open":
            flags = {n.attr for n in ast.walk(call) if isinstance(n, ast.Attribute)} & _WRITE_FLAGS
            if flags or any(c in _write_mode(call) for c in "wax+"):
                found.append((where, "open for write"))
    return found


#: each line is a change someone could slip into scan(); every one must be reported
_MUTANTS = (
    "    os.remove(root / 'trust_graph.json')",
    "    (root / 'x').open('w')",
    "    open(root / 'x', mode='w')",
    "    open(root / 'x', 'a')",
    "    (root / 'x').touch()",
    "    (root / 'x').mkdir()",
    "    os.open(str(root / 'x'), os.O_CREAT | os.O_WRONLY)",
    "    shutil.copy(root / 'a', root / 'b')",
)


@pytest.mark.parametrize("mutant", _MUTANTS)
def test_the_script_has_no_delete_move_or_edit_call_and_the_check_can_fail(mutant):
    source = SCRIPT.read_text(encoding="utf-8")
    assert _mutations(source) == []
    line = "    return scan_people(root) + scan_wiki(root)"
    assert source.count(line) == 1
    assert _mutations(source.replace(line, mutant + chr(10) + line)), "a write added to the script is not reported: " + mutant
    assert _mutations(source + chr(10) + mutant.strip() + chr(10)), "a module-level write is not reported: " + mutant


def test_out_in_the_owners_real_root_is_refused_whatever_root_is_scanned(tmp_path, monkeypatch):
    """--out is refused inside the owner's data root (FRIDAY_HOME / ~/.friday), not only inside --root: scanning a
    sub-folder of it, or a backup elsewhere, must not let the report land in it."""
    mod = _load()
    home = tmp_path / "home" / ".friday"
    (home / "wiki").mkdir(parents=True)
    backup = tmp_path / "backup"
    backup.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    for scanned, out in ((home / "wiki", home / "report.txt"), (backup, home / "report2.txt")):
        assert mod.main(["--root", str(scanned), "--out", str(out)]) == 2
        assert not out.exists()


def test_out_through_a_hard_link_is_refused(root, tmp_path):
    """A report path that is another name for a file (a hard link) would write through to it: refused."""
    import os
    target = root / "notes.txt"
    target.write_text("keep me", encoding="utf-8")
    link = tmp_path / "report.txt"
    os.link(target, link)
    assert _load().main(["--root", str(root), "--out", str(link)]) == 2
    assert target.read_text(encoding="utf-8") == "keep me"


def test_nothing_found_is_status_zero_and_json_is_valid(tmp_path, capsys):
    mod = _load()
    empty = tmp_path / "empty_root"
    empty.mkdir()
    assert mod.main(["--root", str(empty)]) == 0
    capsys.readouterr()
    assert mod.main(["--root", str(empty), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


_BS = chr(92)   # one backslash; built, not escaped, so no layer can collapse the doubled prefixes below


def _spelled(text: str, spelling: str) -> str:
    """`text` (an absolute C:\\... path) written another way that names the same folder."""
    if spelling == "extended":
        return _BS * 2 + "?" + _BS + text                                   # \\?\C:\...
    return _BS * 2 + "localhost" + _BS + text[0] + "$" + text[2:]          # \\localhost\C$\...


def test_the_spellings_name_the_same_folder(tmp_path):
    """Guard for the test below: each spelling really is the folder (two leading backslashes, resolvable)."""
    if __import__("os").name != "nt":
        pytest.skip("Windows path spellings")
    import os
    for spelling in ("extended", "unc"):
        other = _spelled(str(tmp_path.resolve()), spelling)
        assert other.startswith(_BS * 2) and os.path.samefile(other, tmp_path), spelling


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows path spellings")
@pytest.mark.parametrize("spelling", ["extended", "unc"])
@pytest.mark.parametrize("home_exists", [True, False], ids=["home-exists", "home-not-yet"])
def test_out_under_another_spelling_of_the_root_is_refused(tmp_path, monkeypatch, spelling, home_exists):
    """The same folder written another way (extended-length or localhost UNC) is still the data root, whether or not the
    data root exists yet: "inside" is decided by which folder it IS, not by how the path is spelled."""
    home = tmp_path / ".friday"
    if home_exists:
        home.mkdir()
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    spelled_home = _spelled(str(tmp_path.resolve()), spelling) + _BS + ".friday"
    for out in (spelled_home + _BS + "report.txt", spelled_home):
        assert _load().main(["--root", str(tmp_path / "elsewhere"), "--out", out]) == 2, out
    assert not (home / "report.txt").exists()
    assert home.is_dir() if home_exists else not home.exists()


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows name trimming")
@pytest.mark.parametrize("tail", [".", " "], ids=["trailing-dot", "trailing-space"])
@pytest.mark.parametrize("home_exists", [True, False], ids=["home-exists", "home-not-yet"])
def test_out_with_a_name_windows_trims_is_refused(tmp_path, monkeypatch, tail, home_exists):
    """Windows drops a trailing dot or space when it opens a name, so ".friday." IS ".friday": a report path whose names
    end that way is refused outright rather than compared."""
    home = tmp_path / ".friday"
    if home_exists:
        home.mkdir()
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    for out in (str(home) + tail, str(home) + tail + _BS + "report.txt"):
        assert _load().main(["--root", str(tmp_path / "elsewhere"), "--out", out]) == 2, repr(out)
    assert home.is_dir() if home_exists else not home.exists()


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows name trimming and streams")
@pytest.mark.parametrize("name", [".friday.", ".friday ", ".friday:rep"])
@pytest.mark.parametrize("home_exists", [True, False], ids=["home-exists", "home-not-yet"])
def test_a_relative_out_is_checked_like_an_absolute_one(tmp_path, monkeypatch, name, home_exists):
    """Run from the folder that holds the data root, a one-name --out has no drive in front of it: every name is still
    checked, so a trimmed or stream name never lands on (or as) the data root."""
    home = tmp_path / ".friday"
    if home_exists:
        home.mkdir()
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    assert _load().main(["--root", str(tmp_path / "elsewhere"), "--out", name]) == 2, repr(name)
    assert home.is_dir() if home_exists else not home.exists()


def test_out_naming_a_folder_the_data_root_will_be_made_in_is_refused(tmp_path, monkeypatch):
    """A file where the data root's parent folder belongs would stop the data root from ever being created."""
    home = tmp_path / "not-yet" / ".friday"
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    assert _load().main(["--root", str(tmp_path / "elsewhere"), "--out", str(tmp_path / "not-yet")]) == 2
    assert not (tmp_path / "not-yet").exists()


@pytest.mark.skipif(__import__("os").name != "nt", reason="NTFS streams")
def test_out_naming_a_stream_is_refused(tmp_path, monkeypatch):
    """`<folder>:name` writes a hidden stream ON that folder (on the data root itself): a report path never names one."""
    home = tmp_path / ".friday"
    home.mkdir()
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    for out in (str(home) + ":rep", str(tmp_path / "report.txt") + ":hidden"):
        assert _load().main(["--root", str(tmp_path / "elsewhere"), "--out", out]) == 2, out
