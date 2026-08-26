"""Every "Settings -> X" we say to a USER must be a tab that exists.

This is the generalisation of what Janet found on 2026-08-26.

`SettingsTabProviders` was complete and working -- it lists every provider,
takes a key and POSTs it to /api/providers/<name>/key -- and had no entry in
the TABS array and no branch in the render chain, so nothing could reach it.
Meanwhile README.md, docs/TUTORIAL.md ("Settings -> Providers -> Anthropic ->
paste your key -> Save"), docs/INSTALLATION.md, the voice spec's error table,
routes/creations.py and two live buttons in the Studio prompt bar all sent
people there. Clicking the button opened Settings on an empty pane.

Sweeping for the rest of the shape found 22 signposts naming 13 distinct
tabs, against 11 tabs that actually render. The worst were the ones a keyless
user meets first:

  * "Settings -> API Keys" x6 -- there has never been such a tab. Two of them
    read "ANTHROPIC_API_KEY is not set. Set it via the setup wizard (Settings
    -> API Keys) or as an environment variable, then restart". That is
    Stephen's complaint in message form.
  * "Settings -> Models" x4 -- including packaging/windows/install.ps1, which
    tells someone who has just declined a local model "To add a local model
    later: open Friday, then Settings -> Models". The exact sentence Janet
    would have read, naming a screen that does not exist, at the moment she
    needed it.
  * "Settings -> AI Providers" in demo_mode.py, shown to a user with no key
    at all.

Nobody noticed because the author does not navigate by the signposts.

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
    assert "intelligence" in labels
    assert "providers" in labels, (
        "Providers is unreachable again -- the panel exists but has lost its "
        "TABS entry or its render branch"
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
