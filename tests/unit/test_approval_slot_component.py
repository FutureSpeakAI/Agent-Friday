"""The approval slot component in both page files, run under node (NS-41.3-1).

The rules are in scripts/check_approval_slot.cjs; this runs it so pytest and CI hold them.
CI's ubuntu and windows runners both have node; a machine without it fails loudly, not skips."""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_approval_slot.cjs"


def _run():
    node = shutil.which("node")
    assert node, "node is required to check the approval slot component"
    return subprocess.run([node, str(SCRIPT)], capture_output=True, text=True, cwd=ROOT, timeout=60)


def test_the_slot_component_holds_its_rules_in_both_page_files():
    run = _run()
    assert run.returncode == 0 and "all checks hold" in run.stdout, run.stdout + run.stderr


def test_a_changed_copy_in_one_page_file_fails_the_check(monkeypatch, tmp_path):
    page = ROOT / "ui_parts" / "app.html"
    original = page.read_bytes()
    try:
        page.write_bytes(original.replace(b"margin: '8px 0', padding: 8", b"margin: '9px 0', padding: 8", 1))
        run = _run()
    finally:
        page.write_bytes(original)
    assert run.returncode != 0 and "must carry the same ApprovalSlot" in run.stderr
