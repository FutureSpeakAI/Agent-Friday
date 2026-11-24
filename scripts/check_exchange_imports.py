"""Nothing under services/exchange/ imports a network module, a dynamic-import helper, the process module or the
foreign-function module (NS-40.4-11 to -13, design D.1 and D.4).

Held by tests/unit/test_exchange_guards.py, which also shows the check can fail. A real provider adapter, if one is ever
chosen, lives in its own directory and is the only place that may.

The limit of a static guard, stated plainly: a name built at run time (`getattr`, an `exec` string, a plugin loaded from
disk) is not seen. This guard and the provider's own `test_only` word are two halves of one rule; neither alone proves
that no live provider is reachable.
Exit 0 when clean, 1 with one line per finding."""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "agent_friday" / "services" / "exchange"
NETWORK = frozenset({
    "socket", "ssl", "http", "urllib", "urllib3", "requests", "httpx", "aiohttp", "websocket", "websockets",
    "ftplib", "smtplib", "imaplib", "poplib", "telnetlib", "xmlrpc", "socketserver", "asyncio", "grpc",
    "stripe", "plaid", "paypalrestsdk", "boto3", "flask", "agent_friday.services.peer_transport",
    "importlib", "subprocess", "ctypes", "cffi", "multiprocessing", "pty",
})
DYNAMIC_CALLS = frozenset({"__import__", "import_module", "exec", "eval"})


def imported_modules(source: str) -> set:
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{a.name}" for a in node.names)
    return found


def dynamic_calls(source: str) -> set:
    return {getattr(n.func, "id", getattr(n.func, "attr", "")) for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) in DYNAMIC_CALLS}


def banned(modules: set) -> list:
    return sorted(m for m in modules if m in NETWORK or m.split(".")[0] in NETWORK)


def run(package: Path = PACKAGE) -> list:
    findings = []
    for path in sorted(package.rglob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        for module in banned(imported_modules(source)):
            findings.append(f"{path.relative_to(package)}: imports {module}")
        for call in sorted(dynamic_calls(source)):
            findings.append(f"{path.relative_to(package)}: calls {call}")
    return findings


if __name__ == "__main__":
    problems = run()
    print("\n".join(problems) if problems else "exchange imports: clean")
    sys.exit(1 if problems else 0)
