"""The writing issuer journal is constructed only where one authority can hold it (NS-40.4-3, design 10.4).

`IssuerJournal(` may be called in src only inside services/issuer_journal.py (its own definition) and
services/issuer_authority.py (the one object that holds the lock and signs the heads). Everything else is handed an
`Authority`, or a read-only view. Tests are not scanned: the journal's own write path refuses every other writer while
an Authority holds the issuer (tests/unit/test_issuer_authority.py::test_while_an_authority_holds_an_issuer_no_other_object_can_post_to_it).

A call is found by its AST, so a mention in a comment or a string is not one. `getattr` and `importlib` tricks are not
caught: the limit of a static guard, and the reason the runtime refusal exists as well.
Exit 0 when clean, 1 with one line per finding."""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
ALLOWED = {"src/agent_friday/services/issuer_journal.py", "src/agent_friday/services/issuer_authority.py"}


def findings(path: str, source: str) -> list:
    normal = path.replace("\\", "/")
    if normal in ALLOWED:
        return []
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if name == "IssuerJournal":
                out.append(f"{normal}:{node.lineno}: constructs IssuerJournal; hand this code an Authority instead")
    return out


def run(root: Path = ROOT) -> list:
    problems = []
    for file in sorted((root / "src").rglob("*.py")):
        relative = file.relative_to(root).as_posix()
        problems += findings(relative, file.read_text(encoding="utf-8-sig"))
    return problems


if __name__ == "__main__":
    found = run()
    print("\n".join(found) if found else "journal writers: clean")
    sys.exit(1 if found else 0)
