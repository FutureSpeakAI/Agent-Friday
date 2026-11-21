"""The approval slot component in both page files, run under node (NS-41.3-1).

The rules are in scripts/check_approval_slot.cjs; this runs it so pytest and CI hold them.
CI's ubuntu and windows runners both have node; a machine without it fails loudly, not skips."""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_approval_slot.cjs"


def _run(root=None):
    node = shutil.which("node")
    assert node, "node is required to check the approval slot component"
    args = [node, str(SCRIPT)] + ([str(root)] if root else [])
    return subprocess.run(args, capture_output=True, text=True, cwd=ROOT, timeout=60)


def test_the_slot_component_holds_its_rules_in_both_page_files():
    run = _run()
    assert run.returncode == 0 and "all checks hold" in run.stdout, run.stdout + run.stderr


def test_a_changed_copy_in_one_page_file_fails_the_check(tmp_path):
    # works on a copy in tmp_path: the tracked page files are never rewritten, so parallel workers cannot read a
    # half-changed file and a killed run cannot leave the tree modified
    (tmp_path / "ui_parts").mkdir()
    shutil.copy(ROOT / "index.html", tmp_path / "index.html")
    original = (ROOT / "ui_parts" / "app.html").read_bytes()
    changed = original.replace(b"margin: '8px 0', padding: 8", b"margin: '9px 0', padding: 8", 1)
    assert changed != original
    (tmp_path / "ui_parts" / "app.html").write_bytes(changed)
    run = _run(tmp_path)
    assert run.returncode != 0 and "must carry the same ApprovalSlot" in run.stderr
    assert _run().returncode == 0, "the real tree is untouched and still holds"
