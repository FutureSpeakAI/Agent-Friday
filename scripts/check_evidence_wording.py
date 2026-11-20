#!/usr/bin/env python3
"""Guard: the evidence pack never claims certification or compliance (brief K6, rule 8).

Every string constant (docstrings included) in the pack modules, in the evidence-pack
route and the evidence-pack section of static/ledger.html is checked for the words below. The one fixed sentence in evidence_pack.SUPPORTS_SENTENCE
is the only exception, matched exactly, with no allow-list entry anywhere else.

Held by tests/unit/test_evidence_wording_guard.py. Run: python scripts/check_evidence_wording.py
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "src" / "agent_friday" / "services"
FILES = [SERVICES / "evidence_pack.py", SERVICES / "evidence_pack_export.py",
         SERVICES / "evidence_pack_checker.py"]
ROUTE = ROOT / "src" / "agent_friday" / "routes" / "ledger.py"
ROUTE_FUNCTION = "api_ledger_evidence_pack"
PAGE = ROOT / "static" / "ledger.html"
PAGE_SECTION = re.compile(r'<section[^>]*id="evidence-pack".*?</section>', re.DOTALL)
SENTENCE = ("This export supports ISO 27001 evidence-gathering. "
            "It is not a certification and makes no compliance claim.")
BANNED = re.compile(r"\b(certif\w*|complian\w*|accredit\w*)\b", re.IGNORECASE)


def strings(tree):
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def findings(label, texts):
    out = []
    for text in texts:
        for hit in BANNED.findall(text.replace(SENTENCE, "")):
            out.append(f"{label}: '{hit}' in: {text.strip()[:70]!r}")
    return out


def route_strings():
    tree = ast.parse(ROUTE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == ROUTE_FUNCTION:
            return strings(node)
    return None


def page_text():
    found = PAGE_SECTION.search(PAGE.read_text(encoding="utf-8"))
    return None if found is None else found.group(0)


def run():
    problems = []
    for path in FILES:
        problems += findings(path.name, strings(ast.parse(path.read_text(encoding="utf-8"))))
    texts = route_strings()
    if texts is None:
        problems.append(f"routes/ledger.py: {ROUTE_FUNCTION} not found")
    else:
        problems += findings("routes/ledger.py", texts)
    page = page_text()
    if page is None:
        problems.append("static/ledger.html: the evidence-pack section is not found")
    else:
        problems += findings("static/ledger.html", [page])
        if SENTENCE not in page:
            problems.append("static/ledger.html: the fixed sentence is missing or changed")
    source = ast.parse((SERVICES / "evidence_pack.py").read_text(encoding="utf-8"))
    if SENTENCE not in strings(source):
        problems.append("evidence_pack.py: the fixed sentence is missing or changed")
    return problems


def main():
    problems = run()
    for line in problems:
        print("[evidence-wording] FAIL", line)
    if not problems:
        print("[evidence-wording] OK - no certification or compliance claim in the evidence pack")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
