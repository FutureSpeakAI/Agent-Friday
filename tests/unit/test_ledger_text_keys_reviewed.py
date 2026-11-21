"""Every key a literal action-ledger append writes is classified: hidden as free text, or reviewed as structure (Q1).

FREE_TEXT_KEYS is a deny list; a new writer that puts words under a new key would leak them into an export unless the key is
added there. This test reads every `<ledger>.append("kind", {...})` call in src/ and fails on a key that is in neither set, so
the author has to decide (hide it, or say here why it is structure). A key in STRUCTURE that holds text is a review error. It sees only literal dicts: a record built in a variable
(decision `entry`, goal `receipt`) is not checked, so those writers are reviewed by hand.
It can fail: remove a key from STRUCTURE and it names it (test_the_guard_names_an_unclassified_key)."""
import ast
from pathlib import Path

from agent_friday.services import ledger_text

STRUCTURE = frozenset({
    "action_id", "after", "approval", "at", "attempts", "before", "bundle_id", "by", "categories", "checked", "class",
    "codes", "decision", "decision_hash", "decision_policy_version", "decision_seq", "duration_ms", "entries", "event",
    "failed", "grant", "index", "limit", "local_only_too", "manifest_sha256", "members", "ok", "outcome", "period",
    "policy_version", "provider", "range_end", "range_start", "reason", "seconds", "setting", "signed", "spend", "stage",
    "answer", "from", "to", "scope", "status", "surface", "terminal", "tool", "verified", "list", "made_by", "goal_id", "kept", "label", "kind"})
SRC = Path(__file__).resolve().parents[2] / "src"


def _literal_keys(source: str) -> set:
    found = set()
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("append", "try_append")
                and len(node.args) >= 2 and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                and isinstance(node.args[1], ast.Dict)
                and any(w in ast.unparse(node.func.value) for w in ("ledger", "_al", "al", "L"))):
            found |= {k.value for k in node.args[1].keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        # the ledger's own internal writes: _append_locked(key, [("kind", {...}), ...], durable)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_append_locked"
                and len(node.args) >= 2 and isinstance(node.args[1], ast.List)):
            for item in node.args[1].elts:
                if (isinstance(item, ast.Tuple) and len(item.elts) == 2 and isinstance(item.elts[1], ast.Dict)):
                    found |= {k.value for k in item.elts[1].keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return found


def _all_keys() -> set:
    keys = set()
    for path in SRC.rglob("*.py"):
        try:
            keys |= _literal_keys(path.read_bytes().decode("utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError):
            pass
    return keys


def test_every_key_a_literal_ledger_append_writes_is_classified():
    unclassified = sorted(_all_keys() - ledger_text.FREE_TEXT_KEYS - STRUCTURE)
    assert unclassified == [], f"classify these ledger record keys (hide in ledger_text.FREE_TEXT_KEYS, or add to STRUCTURE): {unclassified}"


def test_the_guard_also_reads_the_ledgers_own_internal_writes():
    # critic on #1051: legacy_reappeared is written through _append_locked, which the append-only scan never saw
    src = '_append_locked(key, [("legacy_reappeared", {"store": kind, "from": _rel(p), "kept_as": kept})], True)'
    assert _literal_keys(src) == {"store", "from", "kept_as"}


def test_the_guard_names_an_unclassified_key():
    assert "ledger_unreviewed_key" in _literal_keys('al.append("x", {"ledger_unreviewed_key": 1})')
    assert _literal_keys('other.add("x", {"y": 1})') == set()
