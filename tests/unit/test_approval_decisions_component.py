"""Approval cards live in Friday's approval tray only, and any card there can be decided at any time, held under node.

The chat draws no approval card (at most one line saying something waits in the tray), and no surface holds one busy
flag for all its cards: a decision's request returns only after the approved action has run, so a shared flag would
freeze every other card on that surface. The rules are in scripts/check_approval_decisions.cjs; this runs it so pytest
and CI hold them. CI's ubuntu and windows runners both have node; a machine without it fails loudly, not skips."""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_approval_decisions.cjs"


def _run(root=None):
    node = shutil.which("node")
    assert node, "node is required to check the approval decisions"
    args = [node, str(SCRIPT)] + ([str(root)] if root else [])
    return subprocess.run(args, capture_output=True, text=True, cwd=ROOT, timeout=120)


def _copy_of_the_pages(tmp_path):
    (tmp_path / "ui_parts").mkdir()
    shutil.copy(ROOT / "index.html", tmp_path / "index.html")
    shutil.copy(ROOT / "ui_parts" / "app.html", tmp_path / "ui_parts" / "app.html")
    return tmp_path


def test_no_card_renders_in_the_chat_and_tray_cards_are_decided_at_once_in_any_order():
    run = _run()
    assert run.returncode == 0 and "all checks hold" in run.stdout, run.stdout + run.stderr


def test_a_card_drawn_in_the_chat_again_fails_the_check(tmp_path):
    # works on copies in tmp_path: the tracked page files are never rewritten
    pages = _copy_of_the_pages(tmp_path)
    index = pages / "index.html"
    index.write_text(index.read_text(encoding="utf-8") + (
        "\nfunction ChatApprovalCards({ convId }) {\n"
        "  return React.createElement(ApprovalCardBody, { a: {}, onDecide: () => {} });\n}\n"), encoding="utf-8")
    run = _run(pages)
    assert run.returncode != 0 and "still has ChatApprovalCards" in run.stderr, run.stdout + run.stderr
    assert "not a tray surface" in run.stderr, run.stderr
    assert _run().returncode == 0, "the real tree is untouched and still holds"


def test_a_tray_popup_that_swallows_a_click_again_fails_the_check(tmp_path):
    pages = _copy_of_the_pages(tmp_path)
    index = pages / "index.html"
    page = index.read_text(encoding="utf-8")
    start = page.index("function ApprovalPopups(")
    put_back = page[start:].replace("const decide = (id, decision) => {\n",
                                    "const decide = (id, decision) => {\n    if (busy) return;\n", 1)
    assert put_back != page[start:], "the popup's decide handler was not found"
    index.write_text(page[:start] + put_back, encoding="utf-8")
    run = _run(pages)
    assert run.returncode != 0 and "ApprovalPopups still swallows a click" in run.stderr, run.stdout + run.stderr
    assert _run().returncode == 0, "the real tree is untouched and still holds"


def _mutated_copy(tmp_path, old, new):
    """Both page files with `old` replaced by `new` once each, in tmp_path (the tracked files are never rewritten)."""
    pages = _copy_of_the_pages(tmp_path)
    for rel in ("index.html", "ui_parts/app.html"):
        path = pages / rel
        page = path.read_text(encoding="utf-8")
        assert page.count(old) == 1, rel + ": the line to break was not found"
        path.write_text(page.replace(old, new), encoding="utf-8")
    return pages


def test_a_grouped_card_whose_reject_does_nothing_fails_the_check(tmp_path):
    line = "fridayDecideApproval(it.approval_id, 'deny').then(() => onRejected && onRejected());"
    pages = _mutated_copy(tmp_path, line, "onRejected && onRejected();")
    run = _run(pages)
    assert run.returncode != 0 and "its own Reject" in run.stderr, run.stdout + run.stderr


def test_edits_drawn_one_card_each_again_fails_the_check(tmp_path):
    line = "batches.map(b => h(LaneBatch, { key: b.batch_id, b: b, items: items, onDecided: onDecided })),"
    pages = _mutated_copy(tmp_path, line, "[],")
    run = _run(pages)
    assert run.returncode != 0 and "a planned group is one tray card" in run.stderr, run.stdout + run.stderr
