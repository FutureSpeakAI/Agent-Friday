"""Every "Settings -> X" we say to a USER must be a tab that exists.

The failure shape: a settings tab that is complete and working (it lists
every provider, takes a key and POSTs it to /api/providers/<name>/key) but has
no entry in the TABS array and no branch in the render chain, so nothing can
reach it -- while the README, the tutorial, the installer and live buttons all
send people there. Clicking such a button opens Settings on an empty pane.

The signposts that matter most are the ones a keyless user meets first: an
error message pointing at "Settings -> API Keys", an installer line saying
"To add a local model later: open Friday, then Settings -> Models", a demo
banner naming "Settings -> AI Providers". Each names a screen that must exist
at the moment the user needs it.

A developer who does not navigate by the signposts never notices, so this
test checks every one mechanically.

SCOPE: user-facing text only. In Python that means string literals but NOT
comments and NOT docstrings -- a stale comment misleads a developer, which is
a smaller crime with a different fix. Markdown and the installer are scanned
whole, because a user reads all of it.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_INDEX = _ROOT / "index.html"

# A tab name is capitalised words. Each word after the first must be
# Capitalised-then-lowercase (or "&"), so trailing prose and SHOUTED words
# stop the match: "Settings -> Providers BEFORE they burn a prompt" yields
# "Providers", and "Settings -> Intelligence displays" yields "Intelligence".
_SIGNPOST = re.compile(
    r"Settings\s*(?:->|→)\s*([A-Z][A-Za-z]*(?:\s+(?:&|[A-Z][a-z]+))*)")

_MARKDOWN_AND_INSTALLER = ("README.md", "docs/*.md",
                           "packaging/windows/*.ps1")
_PYTHON = ("src/agent_friday/**/*.py",)


def _tab_labels() -> set:
    """The tab labels the Settings pane actually renders.

    Both halves are required: a TABS entry with no render branch shows a
    highlighted tab over a blank pane, and a render branch with no TABS entry
    is what SettingsTabProviders had -- reachable only by an event nothing
    visible dispatched.
    """
    src = _INDEX.read_text(encoding="utf-8", errors="replace")
    pairs = re.findall(r"id:\s*'([a-z_]+)',\s*\n\s*label:\s*'([^']+)'", src)
    return {label.lower() for tab_id, label in pairs
            if re.search(r"tab === '%s' && " % re.escape(tab_id), src)}


def _names_a_real_tab(name: str, labels: set) -> bool:
    low = name.lower()
    # Prefix match so "Settings -> Privacy" satisfies "Privacy & Security".
    return any(lab == low or lab.startswith(low) for lab in labels)


def _user_facing_strings(path: Path):
    """(line_no, text) for every string a user could be shown.

    Docstrings are excluded -- an ast.Expr whose value is a bare string is
    documentation for whoever is reading the source, not a message.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            docstrings.add(id(node.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings:
            yield getattr(node, "lineno", 0), node.value


def _offences(labels: set):
    for pattern in _MARKDOWN_AND_INSTALLER:
        for path in _ROOT.glob(pattern):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for line_no, line in enumerate(text.splitlines(), 1):
                for m in _SIGNPOST.finditer(line):
                    if not _names_a_real_tab(m.group(1).strip(), labels):
                        yield path.relative_to(_ROOT), line_no, m.group(1).strip()
    for pattern in _PYTHON:
        for path in _ROOT.glob(pattern):
            if not path.is_file():
                continue
            for line_no, text in _user_facing_strings(path):
                for m in _SIGNPOST.finditer(text):
                    if not _names_a_real_tab(m.group(1).strip(), labels):
                        yield path.relative_to(_ROOT), line_no, m.group(1).strip()


def test_the_tab_list_was_actually_parsed():
    """Guard the guard: a regex that stopped matching would pass everything."""
    labels = _tab_labels()
    assert len(labels) >= 8, "parsed only %d tabs: %s" % (len(labels), labels)
    assert "models" in labels
    assert "accounts & keys" in labels, (
        "Accounts & Keys (provider keys) is unreachable again -- the panel "
        "exists but has lost its TABS entry or its render branch"
    )


def test_the_signpost_regex_still_matches():
    """And guard the other guard."""
    m = _SIGNPOST.search("add one in Settings -> Nonesuch Place, then retry")
    assert m and m.group(1) == "Nonesuch Place"
    trailing = _SIGNPOST.search("at Settings -> Providers BEFORE they burn one")
    assert trailing and trailing.group(1) == "Providers"


def test_every_settings_signpost_shown_to_a_user_names_a_real_tab():
    labels = _tab_labels()
    bad = ["%s:%d says 'Settings -> %s'" % (rel, line_no, name)
           for rel, line_no, name in _offences(labels)]
    assert not bad, (
        "these send a user to a Settings tab that does not render.\n"
        "real tabs: %s\n  %s" % (sorted(labels), "\n  ".join(bad))
    )
