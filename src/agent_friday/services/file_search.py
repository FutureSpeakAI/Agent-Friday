"""Local file discovery — search_files (WO-14 / WO-16 Stage A, partial Stage B).

Grounding: 09:18, voice session 2026-08-25 — Stephen asked Friday to find his
resume in Downloads. She guessed a filename, failed, and asked him to supply
the exact name. There was no discovery verb anywhere in the registry, only
read-by-exact-path.

Live walk, not an index. The scoped roots are a few thousand files on an
SSD; a bounded os.walk resolves a name query well under a second. An index
would be a standing staleness bug — precisely the failure shape this whole
audit fights ("the index says the file exists" is the same lie as every
other subsystem reporting a stored belief instead of live state).

Privacy: the search itself is deterministic local code, no LLM in the loop.
Results are plain JSON handed back through the SAME single choke point every
tool result already passes through (_execute_tool -> the egress gate's
field-wise JSON descent), so a cloud seat sees fields gated exactly like any
other tool result and a local seat sees them ungated. Snippets gate on their
own content; paths/names pass unless they individually classify — no blanket
redaction, because a path is what the model needs to then call read_file.

Vault roots are excluded from the walk BY CONSTRUCTION, on every surface —
never descended into, regardless of query.
"""
from __future__ import annotations

import difflib
import fnmatch
import os
import time
from pathlib import Path

_MAX_FILES_SCANNED = 4000        # entries examined for a name search
_MAX_CONTENT_CANDIDATES = 500    # files actually opened for a content search
_MAX_CONTENT_BYTES = 2 * 1024 * 1024
_TIME_BUDGET_S = 10.0
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


def _home_roots() -> dict:
    from agent_friday.core import HOME, CREATIONS_DIR
    return {
        "documents": HOME / "Documents",
        "downloads": HOME / "Downloads",
        "desktop": HOME / "Desktop",
        "creations": CREATIONS_DIR,
    }


def _configured_roots() -> dict:
    roots = _home_roots()
    try:
        from agent_friday.core import _load_settings
        extra = (_load_settings() or {}).get("file_search_roots") or []
        for i, r in enumerate(extra):
            try:
                roots[f"extra{i}"] = Path(str(r)).expanduser()
            except Exception:
                continue
    except Exception:
        pass
    return roots


def _vault_root() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return Path(FRIDAY_DIR) / "vault"


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _resolve_root(root_arg: str | None) -> tuple[list[Path], str | None]:
    """Returns (roots_to_search, error_message). error_message is set (and
    roots empty) when `root_arg` names something outside the searchable set —
    every refusal this tool emits names its gate and the remedy, never a bare
    deny."""
    configured = _configured_roots()
    if not root_arg:
        return list(configured.values()), None
    key = root_arg.strip().lower()
    if key in configured:
        return [configured[key]], None
    # Accept an explicit path too, as long as it resolves inside one of the
    # configured roots — no arbitrary filesystem traversal via this verb.
    try:
        p = Path(root_arg).expanduser().resolve()
        if any(_is_within(p, r) for r in configured.values()):
            return [p], None
    except Exception:
        pass
    names = ", ".join(sorted(configured.keys()))
    return [], (f"{root_arg!r} is not in the searchable roots ({names}); "
                f"add it via file_search_roots in Settings.")


def _vault_note() -> dict:
    vroot = _vault_root()
    count = 0
    if vroot.exists():
        try:
            count = sum(1 for _ in vroot.rglob("*") if _.is_file())
        except Exception:
            count = -1
    return {
        "searched": False,
        "document_count": max(count, 0),
        "note": ("vault is not searched by this tool"
                 + (f" — {count} vault document(s) exist; a local seat can "
                    f"search and summarize them" if count > 0 else "")),
    }


def _walk(roots: list[Path], deadline: float, budget: dict):
    vroot = _vault_root()
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dp = Path(dirpath)
            if _is_within(dp, vroot):
                dirnames[:] = []
                continue
            for name in filenames:
                if budget["scanned"] >= _MAX_FILES_SCANNED or time.monotonic() > deadline:
                    budget["truncated"] = True
                    return
                budget["scanned"] += 1
                yield dp / name


def _score_name(query: str, name: str) -> float:
    q, n = query.lower(), name.lower()
    if q == n:
        return 3.0
    if q and q in n:
        return 2.0
    return difflib.SequenceMatcher(None, q, n).ratio()


def _row(p: Path, snippet: str | None = None) -> dict:
    try:
        st = p.stat()
        size, mtime = st.st_size, st.st_mtime
    except Exception:
        size, mtime = None, None
    row = {"path": str(p), "name": p.name, "size": size, "mtime": mtime}
    if snippet is not None:
        row["snippet"] = snippet
    return row


def search_files(query: str = "", root: str | None = None,
                  content_query: str = "", newest_first: bool = True,
                  limit: int = _DEFAULT_LIMIT) -> dict:
    """Name/metadata search (Stage A) with optional bounded content search
    (Stage B — hollow for PDF/docx until WO-14.1's extraction is in use)."""
    limit = max(1, min(int(limit or _DEFAULT_LIMIT), _MAX_LIMIT))
    roots, err = _resolve_root(root)
    if err:
        return {"query": query, "results": [], "scanned": 0, "truncated": False,
                "error": err, "vault": _vault_note()}

    deadline = time.monotonic() + _TIME_BUDGET_S
    budget = {"scanned": 0, "truncated": False}
    query = (query or "").strip()

    if content_query:
        return _search_content(roots, query, content_query, newest_first,
                                limit, deadline, budget)

    candidates = []
    for path in _walk(roots, deadline, budget):
        score = _score_name(query, path.name) if query else 1.0
        if not query or score >= 0.4:
            candidates.append((score, path))
    candidates.sort(key=lambda t: (-t[0], -t[1].stat().st_mtime if newest_first else 0))
    rows = [_row(p) for _, p in candidates[:limit]]
    return {
        "query": query, "results": rows, "scanned": budget["scanned"],
        "truncated": budget["truncated"],
        "roots_searched": [str(r) for r in roots],
        "vault": _vault_note(),
        **({"receipt": f"scanned {budget['scanned']} of the searchable roots "
                        f"before the time/entry budget — narrow the query or "
                        f"name a root"} if budget["truncated"] else {}),
    }


def _search_content(roots, query, content_query, newest_first, limit, deadline, budget):
    from agent_friday.services.file_extraction import extract_text
    cq = content_query.lower()
    candidates = []
    examined = 0
    for path in _walk(roots, deadline, budget):
        if query and _score_name(query, path.name) < 0.4:
            continue
        if examined >= _MAX_CONTENT_CANDIDATES or time.monotonic() > deadline:
            budget["truncated"] = True
            break
        try:
            if path.stat().st_size > 20 * _MAX_CONTENT_BYTES:
                continue
        except Exception:
            continue
        examined += 1
        result = extract_text(path)
        if result.text is None:
            continue
        text = result.text[: _MAX_CONTENT_BYTES]
        # WO-17 KNOWN GAP (2026-08-25): a content-search snippet does NOT yet
        # feed the grant registry. It used to call file_grants.on_file_read
        # here, before this handler's JSON result is PII-scrubbed by the
        # generic post-tool hook — the same order-of-operations bug fixed for
        # read_file in _hook_file_grant_registration (see agent.py). Doing
        # the equivalent fix here needs a JSON-aware post-hook that re-walks
        # results[].snippet against results[].path after scrubbing, which is
        # real additional work, not a one-line move. Until then: a granted
        # file's full read passes; its content-search snippet still gates
        # normally. Safe (fails toward gating, not toward leaking) but
        # incomplete — do not remove this comment when closing the gap.
        idx = text.lower().find(cq)
        if idx == -1:
            continue
        start = max(0, idx - 80)
        snippet = text[start:idx + len(content_query) + 80].strip()
        candidates.append((path, snippet))
    candidates.sort(key=lambda t: -t[0].stat().st_mtime if newest_first else 0)
    rows = [_row(p, snippet=s) for p, s in candidates[:limit]]
    out = {
        "query": query, "content_query": content_query, "results": rows,
        "scanned": budget["scanned"], "examined": examined,
        "truncated": budget["truncated"],
        "roots_searched": [str(r) for r in roots],
        "vault": _vault_note(),
    }
    if budget["truncated"]:
        out["receipt"] = (f"scanned {budget['scanned']} of the searchable roots, "
                           f"examined {examined} candidates' content before the "
                           f"time budget — narrow the query or name a root")
    return out
