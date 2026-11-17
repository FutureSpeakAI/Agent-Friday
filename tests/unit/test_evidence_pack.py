"""The evidence pack (brief K6, PR A): the pure module and its stdlib checker.

Every rule named in services/evidence_pack.py is held here. Ledgers are built with a chain
written independently of the code under test."""
import ast
import hashlib
import json
import subprocess
import sys
import zipfile
from itertools import product
from pathlib import Path

import pytest

from agent_friday.services import evidence_pack as pack
from agent_friday.services import evidence_pack_checker as checker

GENESIS = "0" * 64
SERVICES = Path(pack.__file__).parent
KINDS = ("decision", "outcome", "grant")


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def make_ledger(specs):
    """specs: [(t, kind, rec)] -> whole chained entries."""
    out, prev = [], GENESIS
    for seq, (t, kind, rec) in enumerate(specs, start=1):
        entry = {"v": 1, "seq": seq, "ts": "x", "t": t, "kind": kind, "principal": "owner",
                 "rec": rec, "kid": "k", "prev": prev, "pv": "pv_1"}
        entry["hash"] = hashlib.sha256(canon(entry)).hexdigest()
        entry["sig"] = "s" * 64
        out.append(entry)
        prev = entry["hash"]
    return out


OK = {"ok": True}


def build(entries, start=0.0, end=1000.0, approvals=(), grants=(), verify=OK):
    return pack.build_pack(entries, approvals, grants, verify, pack.Range(start, end), "9.9", 5.0)


def seeded(n=6):
    return make_ledger([(10.0 * (i + 1), KINDS[i % 3], {"action_id": f"a{i}", "label": "x"})
                        for i in range(n)])


def unpack(tmp_path, built):
    folder = tmp_path / "pack"
    folder.mkdir()
    for name, data in built.files.items():
        (folder / name).write_bytes(data)
    return folder


def ledger_lines(built):
    return built.files["ledger.jsonl"].split(b"\n")[:-1]


def test_a_clean_interpreter_with_only_the_standard_library_passes_the_check(tmp_path):
    folder = unpack(tmp_path, build(seeded()))
    run = subprocess.run([sys.executable, "-I", "-S", str(folder / "check_pack.py"), str(folder)],
                         capture_output=True, text=True, cwd=tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "everything checked holds" in run.stdout and "per-entry signatures" in run.stdout


@pytest.mark.parametrize("name", [f.value for f in pack.PackFile if f is not pack.PackFile.MANIFEST])
def test_changing_a_file_by_one_byte_fails_the_check_and_names_the_file(tmp_path, name):
    folder = unpack(tmp_path, build(seeded()))
    target = folder / name
    data = bytearray(target.read_bytes() or b" ")
    data[0] ^= 1
    target.write_bytes(bytes(data))
    problems, _ = checker.check(folder)
    assert any(p.startswith(name) for p in problems), problems


@pytest.mark.parametrize("damage", ["remove", "duplicate", "swap"])
def test_removing_or_reordering_a_ledger_line_fails_and_names_the_line(tmp_path, damage):
    built = build(seeded())
    lines = ledger_lines(built)
    {"remove": lambda: lines.pop(2),
     "duplicate": lambda: lines.insert(3, lines[3]),
     "swap": lambda: lines.__setitem__(slice(1, 3), [lines[2], lines[1]])}[damage]()
    folder = unpack(tmp_path, built)
    damaged = b"\n".join(lines) + b"\n"
    (folder / "ledger.jsonl").write_bytes(damaged)
    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["files"]["ledger.jsonl"] = hashlib.sha256(damaged).hexdigest()
    (folder / "manifest.json").write_bytes(canon(manifest))
    problems, _ = checker.check(folder)
    assert any(p.startswith("ledger.jsonl line") for p in problems), problems


def test_the_pack_holds_exactly_the_entries_in_range_for_every_small_ledger():
    times = [10.0, 20.0, 30.0, 40.0]
    boundaries = [5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 45.0]
    for n in range(0, 5):
        for kinds in product(KINDS, repeat=min(n, 2)):
            specs = [(times[i], (kinds + KINDS)[i] if i < len(kinds) else "outcome", {"label": "x"})
                     for i in range(n)]
            entries = make_ledger(specs)
            for start, end in product(boundaries, repeat=2):
                if not start < end:
                    continue
                built = build(entries, start, end)
                want = [e for e in entries if start <= e["t"] < end]
                assert ledger_lines(built) == [canon(e) for e in want], (n, start, end)
                before = [e for e in entries if e["t"] < start and (not want or e["seq"] < want[0]["seq"])]
                expect_prev = (before[-1]["hash"] if before else GENESIS) if want else GENESIS
                assert built.manifest["prev_hash"] == expect_prev, (n, start, end)
                problems, _ = checker.check_bytes(built.files) if hasattr(checker, "check_bytes") \
                    else (None, None)
                assert built.manifest["entries"] == len(want)


def test_a_mid_chain_range_checks_from_the_stated_starting_hash(tmp_path):
    entries = seeded(6)
    built = build(entries, 30.0, 55.0)
    assert built.manifest["prev_hash"] == entries[1]["hash"]
    problems, _ = checker.check(unpack(tmp_path, built))
    assert problems == []


def test_a_range_over_the_old_export_cap_is_written_whole(tmp_path):
    from agent_friday.routes.ledger import EXPORT_MAX
    count = EXPORT_MAX + 50
    entries = make_ledger([(float(i), "outcome", {"label": "x"}) for i in range(count)])
    built = build(entries, 0.0, float(count) + 1)
    assert built.manifest["entries"] == count and len(ledger_lines(built)) == count
    problems, _ = checker.check(unpack(tmp_path, built))
    assert problems == []


def test_content_fields_in_the_ledger_refuse_the_pack():
    canary = "CANARY-9d41"
    for key in ("prompt", "result", "message", "text"):
        entries = make_ledger([(10.0, "outcome", {"action_id": "a", "detail": {key: canary}})])
        with pytest.raises(pack.PackRefused) as refused:
            build(entries)
        assert canary not in str(refused.value) and "Entry 1" in str(refused.value)


def test_the_summary_and_approvals_carry_no_free_text(tmp_path):
    canary = "CANARY-9d41"
    entries = make_ledger([(10.0, "outcome", {"action_id": "a", "label": "move"})])
    approvals = [{"approval_id": "p1", "kind": "send", "status": "approved", "created_at": 11.0,
                  "decided_by": "owner:ui", "decided_source": "pc", "description": canary,
                  "title": canary, "text": canary, "off_record": True}]
    built = build(entries, approvals=approvals)
    assert all(canary.encode() not in data for data in built.files.values())
    row = json.loads(built.files["approvals.jsonl"])
    assert set(row) <= set(pack.APPROVAL_FIELDS) and row["approval_id"] == "p1"


def test_the_pack_modules_import_nothing_that_reaches_a_network():
    banned = {"socket", "http", "urllib", "requests", "httpx", "ssl", "ftplib", "smtplib"}
    for name in ("evidence_pack.py", "evidence_pack_checker.py"):
        tree = ast.parse((SERVICES / name).read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        assert not roots & banned, (name, roots & banned)


def test_the_checker_uses_only_the_standard_library():
    tree = ast.parse((SERVICES / "evidence_pack_checker.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
            assert all(n.split(".")[0] in sys.stdlib_module_names for n in names), names


def test_summary_numbers_equal_a_recount_of_the_pack_files():
    entries = seeded(6)
    approvals = [{"approval_id": f"p{i}", "kind": "send", "status": s, "created_at": 15.0 + i,
                  "decided_source": src}
                 for i, (s, src) in enumerate([("approved", "pc"), ("denied", "phone"),
                                               ("approved", "phone")])]
    grants = [{"grant_id": "g1", "created_at": 20.0}]
    built = build(entries, approvals=approvals, grants=grants)
    text = built.files["SUMMARY.md"].decode()
    ledger = [json.loads(line) for line in ledger_lines(built)]
    rows = [json.loads(line) for line in built.files["approvals.jsonl"].split(b"\n")[:-1]]
    assert f"Ledger entries: {len(ledger)}" in text
    for kind in KINDS:
        assert f"- {kind}: {sum(e['kind'] == kind for e in ledger)}" in text
    assert f"Approvals: {len(rows)}" in text
    assert f"- status approved: {sum(r['status'] == 'approved' for r in rows)}" in text
    assert f"- decided from phone: {sum(r.get('decided_source') == 'phone' for r in rows)}" in text
    assert "Grants: 1" in text and "Chain: intact" in text


def test_a_break_is_the_first_line_of_the_summary_and_in_the_manifest():
    broken = {"ok": False, "first_break": "seq 3 does not follow seq 2"}
    built = build(seeded(), verify=broken)
    first_body_line = built.files["SUMMARY.md"].decode().splitlines()[2]
    assert "seq 3 does not follow seq 2" in first_body_line
    assert built.manifest["ledger_verified"] is False
    assert json.loads(built.files["verify.json"])["ok"] is False


def test_the_summary_says_supports_and_the_fixed_sentence_is_present_exactly():
    text = build(seeded()).files["SUMMARY.md"].decode()
    assert pack.SUPPORTS_SENTENCE in text
    rest = text.replace(pack.SUPPORTS_SENTENCE, "").lower()
    for word in ("certified", "compliant", "accredited"):
        assert word not in rest


def test_a_range_that_ends_before_it_starts_is_refused():
    with pytest.raises(pack.PackRefused):
        pack.Range(5.0, 5.0)


def test_the_zip_holds_every_file_and_the_check_passes_on_the_zip(tmp_path):
    built = build(seeded())
    path = tmp_path / "p.zip"
    path.write_bytes(built.zip_bytes())
    with zipfile.ZipFile(path) as z:
        assert set(z.namelist()) == set(built.files) == {f.value for f in pack.PackFile}
    problems, _ = checker.check(path)
    assert problems == []
