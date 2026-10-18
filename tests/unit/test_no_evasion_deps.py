"""Friday identifies herself honestly to web sites (spec WS-E16).

No stealth browser, no automation-hiding launch flag, no CAPTCHA-solving
service, anywhere in the tracked tree. A site that wants a person gets the
person (the owner, in the live view), never a workaround.
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN = [
    r"\bpatchright\b", r"playwright[-_]stealth", r"puppeteer-extra-plugin-stealth",
    r"undetected[-_]chromedriver", r"\brebrowser\b", r"AutomationControlled",
    r"\b2captcha\b", r"\banti-?captcha\b", r"\bcapsolver\b", r"\bcapmonster\b",
]
PATTERN = re.compile("|".join(FORBIDDEN), re.I)
#: Files allowed to NAME the forbidden things, because they forbid them.
EXEMPT = {"tests/unit/test_no_evasion_deps.py"}
SCANNED = (".py", ".txt", ".toml", ".json", ".cfg", ".ini", ".js", ".ts", ".lock")


def _tracked():
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.splitlines()
    except Exception:
        out = [str(p.relative_to(ROOT)).replace("\\", "/") for p in ROOT.rglob("*") if p.is_file()]
    return [p for p in out if p.endswith(SCANNED) and p not in EXEMPT
            and not p.startswith(("docs/", "tests/fixtures/"))]


def offenders(paths, root=ROOT):
    bad = []
    for rel in paths:
        p = root / rel
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = PATTERN.search(text)
        if m:
            bad.append(f"{rel}: {m.group(0)}")
    return bad


def test_no_evasion_dependency_or_flag_in_the_tree():
    assert offenders(_tracked()) == []


def test_the_scan_can_fail(tmp_path):
    (tmp_path / "requirements.txt").write_text("patchright==1.0\n")
    (tmp_path / "launch.py").write_text('args=["--disable-blink-features=AutomationControlled"]\n')
    assert len(offenders(["requirements.txt", "launch.py"], root=tmp_path)) == 2
