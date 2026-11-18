"""Resource readings and money modules import nothing of each other (NS-49.1-5, design: a resource vector is never money).

Resource side: resource_vector, resource_measure. Money side: issuer_journal, issuer_registry, stage_escrow, economy,
journal_commitment, and the whole exchange package. A module on one side may not import the other side in any form:
`import a.b.c`, `from a.b import c`, `from . import c`, `from .c import d`, inside a function or at the top.

Held by tests/unit/test_resource_money_guard_script.py, which also shows the check can fail; run by CI in tests.yml.
The limit of a static guard, stated plainly: a module named in a string and loaded at run time is not seen
(scripts/check_exchange_imports.py forbids the dynamic-import helpers inside the exchange package).
Usage: check_resource_money_imports.py [services_dir]. Exit 0 when clean, 1 with one line per finding."""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "src" / "agent_friday" / "services"
RESOURCE = frozenset({"resource_vector", "resource_measure"})
MONEY = frozenset({"issuer_journal", "issuer_registry", "stage_escrow", "exchange", "economy", "journal_commitment"})


def imported_names(source: str) -> set:
    """Every dotted-path component of every module an import statement names, and every imported name."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.update(alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            found.update((node.module or "").split("."))
            found.update(alias.name for alias in node.names)
    found.discard("")
    return found


def side_of(path: Path, services: Path):
    """'resource', 'money' or None for a file under services/ (a package directory takes its package's side)."""
    first = path.relative_to(services).parts[0]
    stem = first[:-3] if first.endswith(".py") else first
    if stem in RESOURCE:
        return "resource"
    if stem in MONEY:
        return "money"
    return None


def run(services: Path = SERVICES) -> list:
    findings = []
    for path in sorted(services.rglob("*.py")):
        side = side_of(path, services)
        if side is None:
            continue
        other = MONEY if side == "resource" else RESOURCE
        rel = path.relative_to(services).as_posix()
        for name in sorted(imported_names(path.read_text(encoding="utf-8-sig")) & other):
            findings.append(f"{rel}: imports {name}")
    return findings


if __name__ == "__main__":
    problems = run(Path(sys.argv[1]) if len(sys.argv) > 1 else SERVICES)
    print("\n".join(problems) if problems else "resource/money imports: clean")
    sys.exit(1 if problems else 0)
