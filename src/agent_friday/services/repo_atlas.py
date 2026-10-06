"""Bounded repository structure for the salon's Understand surface.

This is static inspection: source is never imported or executed. The graph uses
Understand-Anything's portable format; Friday metadata states what was actually
inspected, including heuristic extraction and unverified imported analysis.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
import os
import posixpath
import re
import stat
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from agent_friday.services import codebases as cb
from agent_friday.services import credential_paths as credentials

MAX_FILES = 500
MAX_FILE_BYTES = 256_000
MAX_BYTES = 8_000_000
MAX_NODES = 2_000
MAX_EDGES = 6_000
MAX_ENTRIES = 16_000
MAX_DEPTH = 24
MAX_SECONDS = 5.0
SCAN_BUDGET_FRACTION = 0.6
MAX_IMPORT_SECONDS = 5.0
MAX_IMPORT_BYTES = 2_000_000
MAX_CACHED_REPOS = 4
MAX_ACTIVE_BUILDS = 2
_LOCK = threading.Lock()
_CACHE: OrderedDict[tuple, dict] = OrderedDict()
_ACTIVE: set[tuple] = set()
_SKIP_DIRS = {
    "node_modules", "vendor", "vendors", "venv", "env", "__pycache__", "dist",
    "build", "coverage", "target", "out", "obj", "bin", "site-packages", "secrets", "credentials",
}
_SECRET_FILES = {"credentials.json", "credentials.yaml", "credentials.yml", "secrets.json", "secrets.yaml", "secrets.yml", "tokens.json", "secret_key"}
_PROJECT_DOTFILES = {".gitignore", ".gitattributes", ".editorconfig", ".eslintrc", ".eslintrc.json", ".eslintrc.yaml", ".eslintrc.yml", ".prettierrc", ".prettierrc.json"}
_SOURCE_DIRS = {"src", "lib", "app", "apps", "packages", "services", "server", "client", "components"}
_SECONDARY_DIRS = {".github", "docs", "doc", "documentation", "tests", "test", "examples", "fixtures", "assets", "images"}
_STRUCTURED_EXT = {".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}
_LANGUAGES = {
    ".py": "Python", ".pyi": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
    ".mjs": "JavaScript", ".cjs": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".rb": "Ruby",
    ".cs": "C#", ".c": "C", ".h": "C", ".cpp": "C++", ".hpp": "C++",
    ".php": "PHP", ".swift": "Swift", ".kt": "Kotlin", ".scala": "Scala",
    ".sh": "Shell", ".ps1": "PowerShell", ".sql": "SQL", ".html": "HTML",
    ".htm": "HTML", ".css": "CSS", ".scss": "SCSS", ".vue": "Vue", ".svelte": "Svelte",
}
_DOC_EXT = {".md", ".markdown", ".rst", ".txt", ".adoc"}
_CONFIG_EXT = {".json", ".toml", ".yaml", ".yml", ".xml", ".ini", ".cfg"}
_SPECIAL_FILES = {"dockerfile", "makefile", "license", "licence", "readme", "procfile"}
_LOCKFILES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "uv.lock", "cargo.lock"}
_NODE_TYPES = set("file function class module concept config document service table endpoint pipeline schema resource domain flow step article entity topic claim source page screen component componentSet instance token".split())
_EDGE_TYPES = set("imports exports contains inherits implements calls subscribes publishes middleware reads_from writes_to transforms validates depends_on tested_by configures related similar_to deploys serves provisions triggers migrates documents routes defines_schema contains_flow flow_step cross_domain cites contradicts builds_on exemplifies categorized_under authored_by instance_of variant_of uses_token".split())
_JS_IMPORT = re.compile(r"\b(?:import|export)\s+(?:[^;\n]{0,400}?\s+from\s*)?[\"'](\.[^\"'\n]{1,250})[\"']|\b(?:require|import)\s*\(\s*[\"'](\.[^\"'\n]{1,250})[\"']\s*\)")
_JS_SYMBOL = re.compile(r"^[^\S\r\n]*(?:export\s+(?:default\s+)?)?(?:(?:async|abstract)\s+)?(class|function)\s+([A-Za-z_$][\w$]*)", re.M)


class AtlasBusyError(RuntimeError):
    """The bounded inspector is occupied; callers can retry without waiting."""


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _root(cid: str) -> Path:
    path = Path(os.path.abspath(cb.repo_path(cid)))
    try:
        if any(_is_link(part.lstat()) for part in (path, *path.parents)):
            raise ValueError("Repository inspection does not follow links or junctions.")
        if not path.is_dir() or credentials.check(path, sniff=False):
            raise ValueError("The repository folder is unavailable for inspection.")
    except OSError as exc:
        raise ValueError("The repository folder is unavailable for inspection.") from exc
    return path


def _relative(value: str, *, hidden: bool = False) -> str:
    if not isinstance(value, str) or not value or len(value) > 600 or "\\" in value or ":" in value:
        raise ValueError("Unsafe source reference.")
    parts = value.split("/")
    if any(not p or p in {".", ".."} or p.rstrip(" .") != p or any(ord(c) < 32 for c in p) for p in parts):
        raise ValueError("Unsafe source reference.")
    allowed_hidden = lambda index, part: (index == 0 and part == ".github") or (index == len(parts) - 1 and part in _PROJECT_DOTFILES)
    if not hidden and any((p.startswith(".") and not allowed_hidden(index, p)) or p.lower() in _SKIP_DIRS for index, p in enumerate(parts)):
        raise ValueError("Source reference is outside inspected content.")
    if parts[-1].lower() in _SECRET_FILES:
        raise ValueError("Credential files are excluded from inspection.")
    if credentials.redact_secrets(value) != value:
        raise ValueError("Source reference contains withheld material.")
    return value


def _safe_path(root: Path, rel: str, *, hidden: bool = False) -> Path:
    _relative(rel, hidden=hidden)
    if any(_is_link(part.lstat()) for part in (root, *root.parents)):
        raise ValueError("Repository inspection does not follow links or junctions.")
    path = root
    for part in rel.split("/"):
        path = path / part
        if _is_link(path.lstat()):
            raise ValueError("Repository inspection does not follow links or junctions.")
    if not stat.S_ISREG(path.lstat().st_mode) or credentials.check(path, sniff=False):
        raise ValueError("Source is unavailable for inspection.")
    return path


def _read(root: Path, rel: str, limit: int, *, hidden: bool = False) -> bytes:
    """Refuse links and non-regular files before any content read."""
    path = _safe_path(root, rel, hidden=hidden)
    before = path.lstat()
    if before.st_size > limit:
        raise ValueError("File exceeds inspection limit.")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or _is_link(opened) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("Source changed while opening it.")
        # Check again after opening to catch a replaced parent directory.
        _safe_path(root, rel, hidden=hidden)
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("File exceeds inspection limit.")
    return data


def _has_key_material(data: bytes) -> bool:
    from agent_friday.services import secret_patterns
    return secret_patterns.contains_key_material(data.decode("latin-1"))


def _clean(value: str, limit: int = 500) -> str:
    # Redact before clipping: a credential must never be cut into an unrecognised prefix.
    return credentials.redact_secrets(value)[:limit]


def _coverage() -> dict:
    return {"filesScanned": 0, "filesParsed": 0, "parseFailures": 0, "filesSkipped": 0, "bytesRead": 0,
            "truncated": False, "limits": {"files": MAX_FILES, "fileBytes": MAX_FILE_BYTES,
            "bytes": MAX_BYTES, "nodes": MAX_NODES, "edges": MAX_EDGES, "entries": MAX_ENTRIES,
            "seconds": MAX_SECONDS, "scanSeconds": MAX_SECONDS * SCAN_BUDGET_FRACTION,
            "importSeconds": MAX_IMPORT_SECONDS, "importBytes": MAX_IMPORT_BYTES}, "limitations": [
                "Static structure only; runtime behavior and intent are not inferred.",
                "Python definitions and imports use the standard AST. JavaScript and TypeScript extraction is heuristic.",
                "Other languages are inventoried by file. External packages and dynamic imports are not resolved.",
                "Hidden files except selected project configuration, generated, dependency, binary, oversized and credential files are excluded; gitignore is not evaluated.",
            ]}


def _scan(root: Path, deadline: float) -> tuple[dict[str, str], dict, str]:
    files: dict[str, str] = {}
    coverage = _coverage()
    digest = hashlib.sha256()
    pending = [(root, "", 0)]
    entries_seen = 0
    while pending:
        if time.monotonic() >= deadline or entries_seen >= MAX_ENTRIES or len(files) >= MAX_FILES:
            coverage["truncated"] = True
            break
        folder, prefix, depth = pending.pop()
        entries = []
        try:
            if _is_link(folder.lstat()):
                coverage["filesSkipped"] += 1
                continue
            with os.scandir(folder) as iterator:
                for entry in iterator:
                    if entries_seen >= MAX_ENTRIES or time.monotonic() >= deadline:
                        coverage["truncated"] = True
                        break
                    entries_seen += 1
                    entries.append(entry)
        except OSError:
            coverage["filesSkipped"] += 1
            continue
        subdirs = []
        for entry in sorted(entries, key=lambda item: item.name):
            if time.monotonic() >= deadline or len(files) >= MAX_FILES:
                coverage["truncated"] = True
                break
            name = entry.name
            rel = prefix + name
            try:
                _relative(rel)
                if name.lower() in _SKIP_DIRS or _is_link(entry.stat(follow_symlinks=False)):
                    coverage["filesSkipped"] += 1
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if depth < MAX_DEPTH:
                        subdirs.append((Path(entry.path), rel + "/", depth + 1))
                    else:
                        coverage["truncated"] = True
                        coverage["filesSkipped"] += 1
                    continue
                suffix = PurePosixPath(name).suffix.lower()
                if not entry.is_file(follow_symlinks=False) or name.lower() in _LOCKFILES or (suffix not in _LANGUAGES and suffix not in _DOC_EXT and suffix not in _CONFIG_EXT and name.lower() not in _SPECIAL_FILES and name not in _PROJECT_DOTFILES):
                    coverage["filesSkipped"] += 1
                    continue
                size = entry.stat(follow_symlinks=False).st_size
                if size > MAX_FILE_BYTES:
                    coverage["filesSkipped"] += 1
                    continue
                if coverage["bytesRead"] + size > MAX_BYTES:
                    coverage["truncated"] = True
                    coverage["filesSkipped"] += 1
                    continue
                data = _read(root, rel, min(MAX_FILE_BYTES, MAX_BYTES - coverage["bytesRead"]))
                coverage["bytesRead"] += len(data)
                # Rejected content still consumed I/O and counts against the
                # same aggregate budget as source that appears in the map.
                if _has_key_material(data) or b"\x00" in data:
                    coverage["filesSkipped"] += 1
                    continue
                body = data.decode("utf-8-sig")
                files[rel] = body
                digest.update(rel.encode("utf-8") + b"\x00" + hashlib.sha256(data).digest())
            except (OSError, UnicodeError, ValueError):
                coverage["filesSkipped"] += 1
        # Visit likely implementation areas before documentation and fixtures so
        # a bounded inventory can still reveal the repository's working parts.
        subdirs.sort(key=lambda item: (0 if item[0].name.lower() in _SOURCE_DIRS else 2 if item[0].name.lower() in _SECONDARY_DIRS else 1, item[1]))
        pending.extend(reversed(subdirs))
    coverage["filesScanned"] = len(files)
    digest.update(json.dumps({key: coverage[key] for key in ("filesScanned", "filesSkipped", "bytesRead", "truncated")}, sort_keys=True).encode())
    return files, coverage, digest.hexdigest()


def _node(node_id: str, kind: str, name: str, summary: str, *, path: str = "", lines=None, tags=()) -> dict:
    node = {"id": node_id, "type": kind, "name": _clean(name, 200), "summary": _clean(summary),
            "tags": list(tags), "complexity": "simple"}
    if path:
        node["filePath"] = path
    if lines:
        node["lineRange"] = list(lines)
    return node


def _edge(source: str, target: str, kind: str, description: str = "") -> dict:
    return {"source": source, "target": target, "type": kind, "direction": "forward", "weight": 1,
            "description": description}


def _symbol_id(kind: str, path: str, name: str, line: int) -> str:
    if len(name) > 100:
        name = name[:80] + ":" + hashlib.sha256(name.encode()).hexdigest()[:16]
    return f"{kind}:{path}:{name}:{line}"


def _resolve_js(path: str, spec: str, files: dict) -> str | None:
    candidate = posixpath.normpath(posixpath.join(posixpath.dirname(path), spec))
    if candidate.startswith("../") or candidate.startswith("/"):
        return None
    candidates = [candidate]
    # TypeScript commonly writes the emitted .js spelling in source imports.
    stem, suffix = posixpath.splitext(candidate)
    if suffix in {".js", ".mjs", ".cjs"}:
        candidates.extend(stem + ext for ext in (".ts", ".tsx", ".mts", ".cts"))
    candidates.extend(candidate + ext for ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json"))
    candidates.extend(candidate + "/index" + ext for ext in (".js", ".jsx", ".ts", ".tsx"))
    return next((name for name in candidates if name in files), None)


def _resolve_python(path: str, item: ast.AST, files: dict) -> list[str]:
    if isinstance(item, ast.Import):
        modules = [alias.name.replace(".", "/") for alias in item.names]
    else:
        parent = posixpath.dirname(path)
        level = item.level
        if level:
            for _ in range(level - 1):
                if not parent:
                    return []
                parent = posixpath.dirname(parent)
            base = posixpath.join(parent, (item.module or "").replace(".", "/"))
        else:
            base = (item.module or "").replace(".", "/")
        modules = [base] + [posixpath.join(base, alias.name.replace(".", "/")) for alias in item.names if alias.name != "*"]
    found = []
    for module in modules:
        roots = [module] if getattr(item, "level", 0) else [module, "src/" + module]
        for root in roots:
            hit = next((name for name in (root + ".py", root + "/__init__.py", root + ".pyi") if name in files), None)
            if hit:
                found.append(hit)
                break
    return found


def _local(files: dict[str, str], coverage: dict, title: str, deadline: float) -> dict:
    nodes, edges, layers = [], [], []
    groups: dict[str, list[str]] = {}
    languages = set()
    for path, body in files.items():
        if len(nodes) >= MAX_NODES:
            coverage["truncated"] = True
            break
        suffix = PurePosixPath(path).suffix.lower()
        language = _LANGUAGES.get(suffix)
        if language:
            languages.add(language)
        kind = "document" if suffix in _DOC_EXT or PurePosixPath(path).name.lower() in {"readme", "license", "licence"} else "config" if suffix in _CONFIG_EXT or PurePosixPath(path).name.lower() in _SPECIAL_FILES or PurePosixPath(path).name in _PROJECT_DOTFILES else "file"
        count = max(1, len(body.splitlines()))
        summary = f"{language or kind.capitalize()} file; {count} lines."
        nodes.append(_node("file:" + path, kind, PurePosixPath(path).name, summary, path=path, lines=(1, count), tags=[language.lower() if language else kind]))
        group = path.split("/")[0] if "/" in path else "Root files"
        groups.setdefault(group, []).append("file:" + path)
    for group, ids in groups.items():
        group_id = "module:" + group
        if len(nodes) >= MAX_NODES:
            coverage["truncated"] = True
            break
        nodes.append(_node(group_id, "module", group, f"Folder group containing {len(ids)} inspected files.", tags=["folder-group"]))
        layers.append({"id": "layer:" + group, "name": _clean(group, 200), "description": "Directory grouping; not an inferred architectural layer.", "nodeIds": [group_id, *ids]})
        edges.extend(_edge(group_id, node_id, "contains") for node_id in ids)
    for path, body in sorted(files.items(), key=lambda item: (PurePosixPath(item[0]).suffix.lower() not in _STRUCTURED_EXT, item[0])):
        if time.monotonic() >= deadline or len(nodes) >= MAX_NODES:
            coverage["truncated"] = True
            break
        suffix = PurePosixPath(path).suffix.lower()
        file_id = "file:" + path
        try:
            if suffix in {".py", ".pyi"}:
                tree = ast.parse(body, filename=path)
                coverage["filesParsed"] += 1
                # Iterative traversal keeps hostile nesting away from Python recursion.
                stack = [(tree, "", file_id)]
                while stack:
                    if len(nodes) >= MAX_NODES or time.monotonic() >= deadline:
                        coverage["truncated"] = True
                        break
                    item, scope, owner = stack.pop()
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        qualified = scope + item.name
                        if credentials.redact_secrets(qualified) != qualified:
                            continue
                        kind = "class" if isinstance(item, ast.ClassDef) else "function"
                        symbol_id = _symbol_id(kind, path, qualified, item.lineno)
                        nodes.append(_node(symbol_id, kind, qualified, f"Python {kind} defined in {path}.", path=path, lines=(item.lineno, item.end_lineno or item.lineno), tags=["python", "ast"]))
                        edges.append(_edge(owner, symbol_id, "contains"))
                        scope, owner = qualified + ".", symbol_id
                    elif isinstance(item, (ast.Import, ast.ImportFrom)):
                        edges.extend(_edge(file_id, "file:" + target, "imports", "Python import resolved within inspected files.") for target in _resolve_python(path, item, files) if target != path)
                    stack.extend((child, scope, owner) for child in reversed(list(ast.iter_child_nodes(item))))
            elif suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}:
                coverage["filesParsed"] += 1
                for match in _JS_IMPORT.finditer(body):
                    target = _resolve_js(path, match.group(1) or match.group(2), files)
                    if target and target != path:
                        edges.append(_edge(file_id, "file:" + target, "imports", "Heuristic JavaScript/TypeScript relative import; comments and strings may match."))
                for match in _JS_SYMBOL.finditer(body):
                    if len(nodes) >= MAX_NODES:
                        coverage["truncated"] = True
                        break
                    kind, name = match.groups()
                    if credentials.redact_secrets(name) != name:
                        continue
                    line = body.count("\n", 0, match.start()) + 1
                    symbol_id = _symbol_id(kind, path, name, line)
                    nodes.append(_node(symbol_id, kind, name, f"Heuristic {kind} declaration; verify in the source.", path=path, lines=(line, line), tags=["heuristic"]))
                    edges.append(_edge(file_id, symbol_id, "contains"))
        except (SyntaxError, ValueError, RecursionError):
            coverage["parseFailures"] += 1
            if coverage["parseFailures"] <= 8:
                coverage["limitations"].append(f"Could not parse {_clean(path, 200)}; its file remains in the map.")
        if len(edges) > MAX_EDGES:
            del edges[MAX_EDGES:]
            coverage["truncated"] = True
    node_ids = {node["id"] for node in nodes}
    unique_edges = {(edge["source"], edge["target"], edge["type"]): edge for edge in edges if edge["source"] in node_ids and edge["target"] in node_ids}
    by_type = {kind: [node["id"] for node in nodes if node["type"] == kind] for kind in ("document", "config", "module", "file")}
    tour = []
    for kind, heading, description in [
        ("document", "Start with the repository's own explanation", "Read the README, documentation and license to establish intent and reuse terms."),
        ("module", "Find the main areas", "Browse these directory groups, then follow their files and import relationships."),
        ("config", "Understand the setup", "Inspect configuration and manifests to learn how the project is assembled. No commands have run."),
        ("file", "Follow an implementation", "Open a source file, inspect its definitions and follow resolved imports. Ask Friday to explain or plan an adaptation."),
    ]:
        if by_type[kind]:
            tour.append({"order": len(tour) + 1, "title": heading, "description": description, "nodeIds": by_type[kind][:8]})
    return {"version": "1.0.0", "kind": "codebase", "project": {"name": _clean(title, 200), "languages": sorted(languages), "frameworks": [], "description": "A bounded local map of repository structure.", "analyzedAt": datetime.now(timezone.utc).isoformat(), "gitCommitHash": ""}, "nodes": nodes, "edges": list(unique_edges.values()), "layers": layers, "tour": tour}


def _string(value, limit=500, *, empty=True) -> str:
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError("Invalid graph text.")
    return _clean(value, limit)


def _strings(value, limit=100) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError("Invalid graph list.")
    return [_string(item, 300) for item in value]


def _import_graph(root: Path, data: bytes, deadline: float) -> dict:
    raw = json.loads(data)
    if not isinstance(raw, dict) or raw.get("kind", "codebase") != "codebase":
        raise ValueError("Not a codebase graph.")
    graph = {"version": _string(raw.get("version"), 40, empty=False), "kind": "codebase"}
    project = raw.get("project")
    if not isinstance(project, dict):
        raise ValueError("Missing graph project.")
    graph["project"] = {key: _string(project.get(key, ""), 2000 if key == "description" else 200) for key in ("name", "description", "analyzedAt", "gitCommitHash")}
    graph["project"].update({key: _strings(project.get(key, [])) for key in ("languages", "frameworks")})
    nodes = raw.get("nodes")
    if not isinstance(nodes, list) or not nodes or len(nodes) > MAX_NODES:
        raise ValueError("Invalid graph node count.")
    ids = set()
    graph["nodes"] = []
    for node in nodes:
        if time.monotonic() >= deadline:
            raise ValueError("Graph validation reached its time limit.")
        if not isinstance(node, dict) or node.get("type") not in _NODE_TYPES or node.get("complexity") not in {"simple", "moderate", "complex"}:
            raise ValueError("Invalid graph node.")
        node_id = _string(node.get("id"), 800, empty=False)
        if node_id in ids or node_id != node.get("id"):
            raise ValueError("Duplicate or unsafe graph node id.")
        ids.add(node_id)
        clean = {"id": node_id, "type": node["type"], "name": _string(node.get("name"), 300, empty=False), "summary": _string(node.get("summary", ""), 4000), "complexity": node["complexity"], "tags": _strings(node.get("tags", []), 30)}
        if "filePath" in node:
            path = _relative(node["filePath"])
            _safe_path(root, path)
            clean["filePath"] = path
        if "lineRange" in node:
            lines = node["lineRange"]
            if "filePath" not in clean or not isinstance(lines, list) or len(lines) != 2 or any(type(v) is not int for v in lines) or not 1 <= lines[0] <= lines[1] <= 1_000_000:
                raise ValueError("Invalid source lines.")
            clean["lineRange"] = lines
        if "languageNotes" in node:
            clean["languageNotes"] = _string(node["languageNotes"], 4000)
        graph["nodes"].append(clean)
    raw_edges = raw.get("edges", [])
    if not isinstance(raw_edges, list) or len(raw_edges) > MAX_EDGES:
        raise ValueError("Invalid graph edge count.")
    graph["edges"] = []
    for edge in raw_edges:
        if time.monotonic() >= deadline:
            raise ValueError("Graph validation reached its time limit.")
        if not isinstance(edge, dict) or edge.get("source") not in ids or edge.get("target") not in ids or edge.get("type") not in _EDGE_TYPES or edge.get("direction") not in {"forward", "backward", "bidirectional"}:
            raise ValueError("Invalid graph edge reference.")
        weight = edge.get("weight", 1)
        if type(weight) not in (int, float) or not math.isfinite(weight) or not 0 <= weight <= 1:
            raise ValueError("Invalid graph edge weight.")
        graph["edges"].append({"source": edge["source"], "target": edge["target"], "type": edge["type"], "direction": edge["direction"], "weight": weight, "description": _string(edge.get("description", ""), 2000)})
    for key in ("layers", "tour"):
        items = raw.get(key, [])
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError("Invalid graph group count.")
        graph[key] = []
        seen = set()
        for item in items:
            if time.monotonic() >= deadline:
                raise ValueError("Graph validation reached its time limit.")
            if not isinstance(item, dict):
                raise ValueError("Invalid graph group.")
            refs = item.get("nodeIds")
            if not isinstance(refs, list) or len(refs) > MAX_NODES or any(not isinstance(ref, str) or ref not in ids for ref in refs):
                raise ValueError("Invalid graph group references.")
            clean = {"nodeIds": list(dict.fromkeys(refs)), "description": _string(item.get("description", ""), 4000)}
            if key == "layers":
                clean.update(id=_string(item.get("id"), 300, empty=False), name=_string(item.get("name"), 300, empty=False))
                identity = clean["id"]
            else:
                order = item.get("order")
                if type(order) is not int or order < 0 or order > 10000:
                    raise ValueError("Invalid tour order.")
                clean.update(order=order, title=_string(item.get("title"), 300, empty=False))
                identity = order
                if "languageLesson" in item:
                    clean["languageLesson"] = _string(item["languageLesson"], 4000)
            if identity in seen:
                raise ValueError("Duplicate graph group.")
            seen.add(identity)
            graph[key].append(clean)
    graph["tour"].sort(key=lambda step: step["order"])
    return graph


def _upstream(root: Path) -> tuple[bytes | None, str, str | None, int]:
    # Match upstream precedence: an existing legacy directory wins over .ua.
    legacy = root / ".understand-anything"
    folder = ".understand-anything" if os.path.lexists(legacy) else ".ua"
    rel = folder + "/knowledge-graph.json"
    if not os.path.lexists(root / folder):
        return None, "", None, 0
    bytes_read = 0
    try:
        if _is_link((root / folder).lstat()):
            raise ValueError("Linked graph folder.")
        if not os.path.lexists(root / rel):
            return None, "", None, 0
        data = _read(root, rel, MAX_IMPORT_BYTES, hidden=True)
        bytes_read = len(data)
        if _has_key_material(data):
            raise ValueError("Key material is excluded from inspection.")
        return data, rel, None, bytes_read
    except (OSError, ValueError):
        return None, "", "The saved Understand-Anything graph could not be safely read; showing local structure.", bytes_read


def build(cid: str, refresh: bool = False) -> dict:
    """Inspect a registered codebase, with bounded caching and no source execution.

    Content is hashed on every request, including same-size edits. A concurrent
    caller gets the previous map marked stale, or a prompt busy response.
    """
    root = _root(cid)
    key = (str(cid), os.path.normcase(str(root)))
    with _LOCK:
        if key in _ACTIVE or len(_ACTIVE) >= MAX_ACTIVE_BUILDS:
            if key in _CACHE:
                result = copy.deepcopy(_CACHE[key])
                result["friday"].update(cacheHit=True, stale=True)
                result["friday"]["warnings"].append("Inspection is already running; this is the previous map.")
                return result
            raise AtlasBusyError("Repository inspection is busy. Try again shortly.")
        _ACTIVE.add(key)
    try:
        # The saved graph has its own bounded validation budget. A large local
        # inventory must not starve the portable graph's import path.
        import_deadline = time.monotonic() + MAX_IMPORT_SECONDS
        data, source, import_warning, import_bytes_read = _upstream(root)
        warnings = [import_warning] if import_warning else []
        graph = None
        if data:
            try:
                graph = _import_graph(root, data, import_deadline)
            except (ValueError, TypeError, OSError, RecursionError, KeyError):
                warnings.append("The saved Understand-Anything graph is invalid, has unsafe references, or exceeded validation limits; showing local structure.")
        local_start = time.monotonic()
        deadline = local_start + MAX_SECONDS
        # Collection and interpretation share the total local budget. Reserving
        # the final portion makes partial inventories useful even on slow disks.
        scan_deadline = local_start + MAX_SECONDS * SCAN_BUDGET_FRACTION
        files, coverage, fingerprint = _scan(root, scan_deadline)
        coverage["importBytesRead"] = import_bytes_read
        rec = cb.load(cid) or {}
        title = rec.get("title") or "Codebase"
        fingerprint = hashlib.sha256((fingerprint + str(warnings) + title).encode() + (data or b"")).hexdigest()
        # Imported references are revalidated before a cache hit. A linked or
        # deleted source must never retain a previously valid source action.
        with _LOCK:
            cached = _CACHE.get(key)
            if not refresh and cached and cached["friday"]["fingerprint"] == fingerprint:
                _CACHE.move_to_end(key)
                result = copy.deepcopy(cached)
                result["friday"]["cacheHit"] = True
                return result
        mode = "imported" if graph is not None else "local"
        if graph is None:
            graph = _local(files, coverage, title, deadline)
        else:
            coverage["filesParsed"] = 0
            coverage["importedCoverage"] = "unknown"
            coverage["limitations"].append("Imported analysis is untrusted and may be stale. Its semantics, source lines and claimed analysis date are not verified.")
            warnings.append("Showing a saved Understand-Anything graph. Local file checks do not establish that its analysis is current.")
        if coverage["truncated"]:
            warnings.append("Inspection reached a resource limit; the map covers only part of the repository.")
        graph["schemaVersion"] = "friday-atlas/1"
        graph["friday"] = {"mode": mode, "fingerprint": fingerprint, "cacheHit": False, "stale": mode == "imported",
                           "coverage": coverage, "warnings": warnings, "source": source if mode == "imported" else "local"}
        with _LOCK:
            _CACHE[key] = copy.deepcopy(graph)
            _CACHE.move_to_end(key)
            while len(_CACHE) > MAX_CACHED_REPOS:
                _CACHE.popitem(last=False)
        return graph
    finally:
        with _LOCK:
            _ACTIVE.discard(key)
