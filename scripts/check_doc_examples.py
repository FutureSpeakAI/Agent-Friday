"""Documentation examples are real (north star §34.2-12).

scripts/check_doc_links.py proves the links in the docs resolve. This proves
the EXAMPLES do:

  * every ```json block parses (``{ ... }`` / ``[ ... ]`` / ``"..."`` stand for
    elided content and are accepted as such);
  * every ``friday <command>`` the docs tell someone to type is a command the
    CLI has (cli.build_parser);
  * every ``### `METHOD /path` `` endpoint in docs/reference/api.md is a route
    the app registers, with that method;
  * every setting in the docs/user-guide/configuration.md tables is one
    DEFAULT_SETTINGS has.

    python scripts/check_doc_examples.py

CI runs the same rules as tests/unit/test_doc_examples.py.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_FENCE = re.compile(r"^```([\w-]*)[^\n]*\n(.*?)^```", re.M | re.S)
_ELIDED = (
    (re.compile(r"\{\s*(?:\.\.\.|…)\s*\}"), "{}"),
    (re.compile(r"\[\s*(?:\.\.\.|…)\s*\]"), "[]"),
)
_CLI = re.compile(r"(?:^|[`\s(>$])friday\s+([a-z][a-z-]*)\b")
_ENDPOINT = re.compile(r"^###\s+`(GET|POST|PUT|PATCH|DELETE)\s+(/[^`\s]*)`", re.M)
_SETTING_ROW = re.compile(r"^\|\s*`([a-zA-Z_][\w.]*)`\s*\|", re.M)


def doc_files(root: Path = ROOT) -> list:
    files = sorted((root / "docs").rglob("*.md"))
    for name in ("README.md", "CONTRIBUTING.md"):
        if (root / name).exists():
            files.append(root / name)
    # Decision records quote history; they are not instructions.
    return [f for f in files if "decisions" not in f.parts and "archive" not in f.parts]


def _line(text: str, pos: int) -> int:
    return text[:pos].count("\n") + 1


def json_problems(files) -> list:
    out = []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in _FENCE.finditer(text):
            if m.group(1).lower() != "json":
                continue
            body = m.group(2)
            for rx, sub in _ELIDED:
                body = rx.sub(sub, body)
            try:
                json.loads(body)
            except ValueError as e:
                out.append("%s:%d: the JSON example does not parse (%s)"
                           % (f.relative_to(ROOT).as_posix() if f.is_relative_to(ROOT) else f,
                              _line(text, m.start()), e))
    return out


def cli_commands_named(files) -> dict:
    """{command: first 'file:line' naming it}, from text a person is told to type."""
    found: dict = {}
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in _CLI.finditer(text):
            found.setdefault(m.group(1), "%s:%d" % (f.name, _line(text, m.start())))
    return found


def cli_problems(files, commands: set) -> list:
    return ["%s: `friday %s` is not a command the CLI has" % (where, cmd)
            for cmd, where in sorted(cli_commands_named(files).items()) if cmd not in commands]


def endpoint_problems(api_md: Path, rules: dict) -> list:
    """`rules`: {path pattern: {methods}} from the app's url_map. A doc path's
    <param> segments match any converter."""
    text = api_md.read_text(encoding="utf-8")
    norm = {}
    for rule, methods in rules.items():
        norm.setdefault(re.sub(r"<[^>]+>", "<>", rule), set()).update(methods)
    out = []
    for m in _ENDPOINT.finditer(text):
        method, path = m.group(1), m.group(2).split("?", 1)[0]
        key = re.sub(r"<[^>]+>|\{[^}]+\}|:[a-z_]+", "<>", path)
        if key not in norm:
            out.append("api.md:%d: %s %s is not a route the app has" % (_line(text, m.start()), method, path))
        elif method not in norm[key]:
            out.append("api.md:%d: %s %s: the route does not accept %s" % (_line(text, m.start()), method,
                                                                            path, method))
    return out


def setting_problems(config_md: Path, defaults: dict) -> list:
    """The rows of the "## Settings keys" tables. A row names a top-level
    setting, a dotted path, or a key inside one of the settings blocks (the
    `model_routing` and `capability_routing` tables list their own keys)."""
    text = "\n" + config_md.read_text(encoding="utf-8")
    start = text.find("\n## Settings keys")
    end = text.find("\n## ", start + 5) if start >= 0 else -1
    if start < 0:
        return ["configuration.md has no \"## Settings keys\" section"]
    section = text[start:end if end > 0 else len(text)]
    nested = set()
    for v in defaults.values():
        if isinstance(v, dict):
            nested |= set(v)
            for vv in v.values():
                if isinstance(vv, dict):
                    nested |= set(vv)
    out = []
    for m in _SETTING_ROW.finditer(section):
        key = m.group(1)
        node, ok = defaults, True
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                ok = False
                break
        if not ok and "." not in key and key in nested:
            ok = True
        if not ok:
            out.append("configuration.md:%d: `%s` is not a setting Friday has"
                       % (_line(text, start + m.start()) - 1, key))
    return out


def app_facts():
    """(cli commands, url_map rules, DEFAULT_SETTINGS) from the real code."""
    sys.path.insert(0, str(ROOT / "src"))
    # Importing the app writes into its home; never the person's own. Under
    # the test suite (FRIDAY_TESTING already set) the run's own test home is
    # used, and the environment is left as it was found.
    if not os.environ.get("FRIDAY_TESTING"):
        import tempfile
        os.environ.setdefault("FRIDAY_HOME", tempfile.mkdtemp(prefix="friday-check-"))
    os.environ.setdefault("FRIDAY_TESTING", "1")
    from agent_friday import cli
    from agent_friday.core import DEFAULT_SETTINGS
    from agent_friday import server
    parser = cli.build_parser()
    commands = set()
    for action in parser._actions:                       # argparse keeps subparsers here
        if getattr(action, "choices", None) and isinstance(action.choices, dict):
            commands |= set(action.choices)
    rules: dict = {}
    for r in server.app.url_map.iter_rules():
        rules.setdefault(str(r.rule), set()).update(r.methods or ())
    return commands, rules, DEFAULT_SETTINGS


def problems() -> list:
    commands, rules, defaults = app_facts()
    files = doc_files()
    return (json_problems(files) + cli_problems(files, commands)
            + endpoint_problems(ROOT / "docs" / "reference" / "api.md", rules)
            + setting_problems(ROOT / "docs" / "user-guide" / "configuration.md", defaults))


def main() -> int:
    found = problems()
    for p in found:
        print("[doc-examples] " + p)
    if found:
        print("[doc-examples] FAIL - %d problem(s)" % len(found))
        return 1
    print("[doc-examples] OK - JSON examples parse; commands, endpoints and settings exist")
    return 0


if __name__ == "__main__":
    sys.exit(main())
