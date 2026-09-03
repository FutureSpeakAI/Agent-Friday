#!/usr/bin/env python3
"""Static check: no settings control ships behind a key nothing consumes.

Why this exists
----------------
Two controls in the shipped Privacy tab wrote a key nothing read, and both
showed "Saved [check]" regardless:

  * EGRESS GATE / Cloud Mode wrote `egress_mode`. No Python file has ever
    read it -- there is no audit/enforce concept anywhere in
    `services/egress_gate.py`.
  * `ui_parts/app.html`'s Local-Only Mode toggle wrote a top-level
    `vault_local_only`. The server has only ever gated on the NESTED
    `model_routing.vault_local_only` (`privacy/vault_policy.py`).
    `index.html`'s copy of the same control had already been corrected to
    the nested key -- so the two files *disagreed*, and a future rebuild of
    `index.html` from `ui_parts/` would have resurrected the placebo.

Both looked identical to a working control: the click handler ran, the POST
succeeded, the UI printed "Saved [check]". Nothing in the request/response cycle
distinguishes "wrote a key something reads" from "wrote a key that vanishes
on the next load." This script makes that distinction mechanical instead of
relying on someone noticing.

What it checks
---------------
1. **Extract writers.** Every settings key passed to `save({...})` or
   `saveAgentSettings({...})` in `index.html` and `ui_parts/app.html` --
   both files, since a control that exists in only one is exactly the
   divergence class that produced the `vault_local_only` bug. One level of
   object nesting is captured as a dotted path (`model_routing.foo`),
   matching how the real settings tree nests writes today.
2. **Every written key must be in `DEFAULT_SETTINGS`** (top-level component,
   for a dotted key) or in `ALLOWLIST` below with a stated reason. This is
   the check that would have failed both `egress_mode` (absent entirely) and
   the top-level `vault_local_only` (only the *nested* form is declared) --
   `_load_settings_raw()` whitelists top-level keys against this dict and
   silently drops anything else on every read (`core/__init__.py:1409-1417`).
3. **Every written key's top-level component must appear as a Python string
   literal somewhere under `src/`** -- a lightweight proxy for "something
   reads this," not a proof of correct behaviour. Honest limit, stated where
   the design doc states it: this cannot see through an alias layer, and it
   cannot confirm a nested key is read from the RIGHT parent, only that the
   parent name is read from *somewhere*. Check 2 is what catches the nesting
   class of bug; this one catches "declared in DEFAULT_SETTINGS but nothing
   in `src/` ever mentions it at all."
4. **The two HTML files must agree** on their written key sets. Any key
   written by one and not the other is a failure -- this is the exact shape
   of the `vault_local_only` divergence, independent of whether either side
   happens to be correct.

Honest limits, stated so nobody mistakes this for proof of correctness:
regex/bracket-matching extraction can miss a dynamically-constructed key
(`save({[dynamicKey]: v})` is not handled), and "a reader exists somewhere"
proves consumption, not correct behaviour -- see check 3 above. This closes
the *dead-key* class; it does not replace reading the diff.

Runs in under a second, no imports of the app itself required.
Exit 0 = every written key has a home. Exit 1 = at least one does not.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
HTML_FILES = ("index.html", "ui_parts/app.html")
CORE_INIT = SRC / "agent_friday" / "core" / "__init__.py"
TAG = "[settings-readers-check]"

WRITE_CALL_RE = re.compile(r"\b(?:saveAgentSettings|save)\s*\(")

# Keys the checker would otherwise flag, each with the reason it does NOT
# get a code fix here. Two different reasons live in this table on purpose:
#
#   (a) not actually a bug -- the extractor's own limits produce a false
#       positive (it cannot see the destructuring inside `save()` itself).
#   (b) a real, confirmed instance of this exact defect class, found while
#       calibrating this script, deliberately left for a product decision
#       rather than guessed at here (2026-09-03 hunt; see KNOWN_ISSUES.md).
#       Listed here, not silently fixed, so the guard's PRIMARY job --
#       catching the next one -- stays green without pretending these are
#       fine. Removing an entry without shipping the matching fix un-hides
#       a regression, not a false alarm.
ALLOWLIST: dict[str, str] = {
    "personality": "(a) save()/saveAgentSettings() destructure `personality` "
        "OUT of the patch before posting (`const {personality, ...rest} = "
        "patch`) and send it as body.personality, a separate channel from "
        "body.settings -- routes/core_routes.py reads it there. Never a "
        "settings.json key at all.",
    "stream_responses": "(b) persisted-or-not is moot: no code anywhere "
        "reads this key to decide whether a response streams. The toggle "
        "('Stream tokens as they arrive') has no effect in either "
        "position. KNOWN_ISSUES.md.",
    "auto_open_chat": "(b) written and read back only to redraw its own "
        "toggle (index.html:31473-31475) -- no other reference in the "
        "tree. KNOWN_ISSUES.md.",
    "compact_mode": "(b) same shape as auto_open_chat -- self-referential, "
        "no consumer. KNOWN_ISSUES.md.",
    "scene_name": "(b) same shape -- the 3D scene picker persists a "
        "choice nothing reads to pick a scene. KNOWN_ISSUES.md.",
    "startup_workspace": "(b) same shape -- nothing opens this workspace "
        "at startup or any other time. KNOWN_ISSUES.md.",
    # (c) genuinely functional (both keys are declared in DEFAULT_SETTINGS
    # and read by src/agent_friday/cli.py and governance/proof_of_integrity.py)
    # -- the divergence is real, but it is the ALREADY-TRACKED structural
    # issue that ui_parts/app.html is a hand-maintained mirror of index.html
    # that nothing builds from and will drift (KNOWN_ISSUES.md), not a fresh
    # placebo. index.html now sets these through capability_routing.reasoning
    # / capability_routing.creative instead (server-side mirrored back to
    # these flat keys by _sync_capability_routing); app.html still writes
    # the flat keys directly, which still works today.
    "orchestrator_model": "(c) app.html/index.html drift, already tracked "
        "in KNOWN_ISSUES.md -- not dead, just written two different ways.",
    "creative_model": "(c) same drift as orchestrator_model.",
}


def _err(msg: str = "") -> None:
    print(f"{TAG} {msg}".rstrip(), file=sys.stderr)


def _rel(path: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return str(path)


# ─────────────────────────────────────────────────────────────────────────
#  A small brace/string-aware scanner for JS object-literal arguments.
#  Not a JS parser -- just enough to walk `{ ... }` without getting lost
#  inside a nested object, array, string, or template literal.
# ─────────────────────────────────────────────────────────────────────────

_OPEN = {"{": "}", "[": "]", "(": ")"}
_CLOSE = set(_OPEN.values())
_QUOTES = ("'", '"', "`")


def _find_matching_close(text: str, open_idx: int) -> int | None:
    """Index of the character matching text[open_idx], or None if unbalanced."""
    stack = [_OPEN[text[open_idx]]]
    i = open_idx + 1
    n = len(text)
    while i < n:
        c = text[i]
        if c in _QUOTES:
            q = c
            i += 1
            while i < n and text[i] != q:
                if text[i] == "\\":
                    i += 1
                i += 1
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            nl = text.find("\n", i)
            i = nl if nl != -1 else n
            continue
        if c in _OPEN:
            stack.append(_OPEN[c])
            i += 1
            continue
        if c in _CLOSE:
            if not stack or stack[-1] != c:
                return None  # unbalanced -- give up rather than guess
            stack.pop()
            if not stack:
                return i
            i += 1
            continue
        i += 1
    return None


_KEY_RE = re.compile(r"""^\s*(?:'([^']+)'|"([^"]+)"|([A-Za-z_$][\w$]*))\s*:""")


def _split_top_level_entries(body: str) -> list[tuple[str, str]]:
    """`body` is the text strictly between one object literal's `{` and `}`.

    Returns (key, value_text) for every top-level `key: value` entry,
    splitting only at depth-0 commas. Entries with no `key:` prefix (a
    spread like `...(s.model_routing||{})`) are skipped -- they name no key.
    """
    entries, depth, start = [], 0, 0
    i, n = 0, len(body)
    while i < n:
        c = body[i]
        if c in _QUOTES:
            q = c
            i += 1
            while i < n and body[i] != q:
                if body[i] == "\\":
                    i += 1
                i += 1
            i += 1
            continue
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth -= 1
        elif c == "," and depth == 0:
            entries.append(body[start:i])
            start = i + 1
        i += 1
    entries.append(body[start:])

    out = []
    for entry in entries:
        m = _KEY_RE.match(entry)
        if not m:
            continue
        key = m.group(1) or m.group(2) or m.group(3)
        value = entry[m.end():].strip()
        out.append((key, value))
    return out


def _keys_from_object_literal(text: str) -> list[str]:
    """Top-level keys, plus one level of dotted nesting for object values."""
    text = text.strip()
    if not text.startswith("{"):
        return []
    close = _find_matching_close(text, 0)
    if close is None:
        return []
    body = text[1:close]
    keys = []
    for key, value in _split_top_level_entries(body):
        keys.append(key)
        if value.startswith("{"):
            inner_close = _find_matching_close(value, 0)
            if inner_close is not None:
                for nested_key, _ in _split_top_level_entries(
                        value[1:inner_close]):
                    keys.append(f"{key}.{nested_key}")
    return keys


def extract_written_keys(path: pathlib.Path) -> dict[str, list[int]]:
    """key -> sorted line numbers it is written at, in this file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    out: dict[str, list[int]] = {}
    for m in WRITE_CALL_RE.finditer(text):
        open_paren = m.end() - 1
        close_paren = _find_matching_close(text, open_paren)
        if close_paren is None:
            continue
        arg_text = text[open_paren + 1:close_paren]
        line = text.count("\n", 0, m.start()) + 1
        for key in _keys_from_object_literal(arg_text):
            out.setdefault(key, []).append(line)
    return out


# ─────────────────────────────────────────────────────────────────────────
#  DEFAULT_SETTINGS: the whitelist a top-level key must clear to survive a
#  save/reload cycle at all (core/__init__.py:1409-1417).
# ─────────────────────────────────────────────────────────────────────────

def default_settings_keys() -> set[str]:
    source = CORE_INIT.read_text(encoding="utf-8-sig", errors="replace")
    tree = ast.parse(source, filename=str(CORE_INIT))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "DEFAULT_SETTINGS"
                    for t in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        keys = set()
        for k in node.value.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                keys.add(k.value)
        return keys
    raise RuntimeError(f"DEFAULT_SETTINGS assignment not found in {CORE_INIT}")


# ─────────────────────────────────────────────────────────────────────────
#  "Something in src/ mentions this name" -- the lightweight reader proxy.
# ─────────────────────────────────────────────────────────────────────────

def _src_text() -> str:
    parts = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            parts.append(path.read_text(encoding="utf-8-sig", errors="replace"))
        except OSError:
            continue
    return "\n".join(parts)


def has_python_reader(top_level_key: str, src_text: str) -> bool:
    return (f'"{top_level_key}"' in src_text) or (f"'{top_level_key}'" in src_text)


# ─────────────────────────────────────────────────────────────────────────

def check() -> list[str]:
    """Returns a list of human-readable violation strings; empty = clean."""
    violations: list[str] = []

    per_file: dict[str, dict[str, list[int]]] = {}
    for rel in HTML_FILES:
        path = ROOT / rel
        if not path.exists():
            violations.append(f"{rel}: file not found")
            continue
        per_file[rel] = extract_written_keys(path)

    default_keys = default_settings_keys()
    src_text = _src_text()

    all_keys: set[str] = set()
    for keys in per_file.values():
        all_keys |= set(keys)

    for key in sorted(all_keys):
        if key in ALLOWLIST:
            continue
        top = key.split(".", 1)[0]

        if top not in default_keys:
            sites = ", ".join(
                f"{rel}:{ln}" for rel, keys in per_file.items()
                for ln in keys.get(key, []))
            violations.append(
                f"'{key}' is written ({sites}) but '{top}' is not a key in "
                f"DEFAULT_SETTINGS -- _load_settings_raw() silently discards "
                f"it on every read (core/__init__.py:1409-1417)")
            continue

        if not has_python_reader(top, src_text):
            violations.append(
                f"'{key}' is written but '{top}' appears as a string "
                f"literal nowhere under src/ -- nothing reads it")

    written_sets = {rel: set(keys) for rel, keys in per_file.items()}
    if len(written_sets) == 2:
        (rel_a, keys_a), (rel_b, keys_b) = written_sets.items()
        only_a = sorted((keys_a - keys_b) - set(ALLOWLIST))
        only_b = sorted((keys_b - keys_a) - set(ALLOWLIST))
        for key in only_a:
            violations.append(
                f"'{key}' is written by {rel_a} but not by {rel_b} -- the "
                f"two Privacy-tab copies disagree")
        for key in only_b:
            violations.append(
                f"'{key}' is written by {rel_b} but not by {rel_a} -- the "
                f"two Privacy-tab copies disagree")

    return violations


def main() -> int:
    violations = check()
    if violations:
        for v in sorted(violations):
            _err(v)
        _err("")
        _err(f"{len(violations)} settings-control violation(s). A control "
             "that writes a key nothing reads shows \"Saved\" and does "
             "nothing -- see docs/design/security-boundary.md #18.")
        return 1
    _err("OK - every written settings key has a DEFAULT_SETTINGS entry, a "
         "reader, and agreement between index.html and ui_parts/app.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
