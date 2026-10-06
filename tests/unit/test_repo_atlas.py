"""A repository map is bounded evidence, never execution or trusted prose."""
from __future__ import annotations

import copy
import json
import os
import stat
from types import SimpleNamespace

import pytest

from agent_friday.services import codebases as cb
from agent_friday.services import repo_atlas as atlas


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(cb, "repo_path", lambda cid: root)
    monkeypatch.setattr(cb, "load", lambda cid: {"title": "Example"})
    atlas._CACHE.clear()
    atlas._ACTIVE.clear()
    yield root
    atlas._CACHE.clear()
    atlas._ACTIVE.clear()


def put(repo, path, body):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def assert_integrity(graph):
    ids = [node["id"] for node in graph["nodes"]]
    assert len(set(ids)) == len(ids)
    assert all(edge["source"] in ids and edge["target"] in ids for edge in graph["edges"])
    assert all(ref in ids for key in ("layers", "tour") for group in graph[key] for ref in group["nodeIds"])
    for node in graph["nodes"]:
        if "lineRange" in node:
            assert 1 <= node["lineRange"][0] <= node["lineRange"][1]


def test_structure_has_real_source_references_and_resolved_imports(repo):
    put(repo, "README.md", "# Example\nA small example.\n")
    put(repo, "pyproject.toml", "[project]\nname='example'\n")
    put(repo, "src/demo/__init__.py", "")
    put(repo, "src/demo/helper.py", "def help_me():\n    return 1\n")
    put(repo, "src/demo/app.py", "from .helper import help_me\nclass App:\n    def run(self):\n        return help_me()\n")
    put(repo, "web/helper.ts", "export function helper() { return 1; }\n")
    put(repo, "web/app.ts", "import { helper } from './helper.js';\nexport function app() { return helper(); }\n")
    graph = atlas.build("example")
    assert_integrity(graph)
    assert graph["version"] == "1.0.0"
    assert graph["friday"]["mode"] == "local"
    assert graph["project"]["languages"] == ["Python", "TypeScript"]
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert nodes["file:README.md"]["type"] == "document"
    assert nodes["file:pyproject.toml"]["type"] == "config"
    method = next(node for node in graph["nodes"] if node["name"] == "App.run")
    assert method["filePath"] == "src/demo/app.py"
    assert method["lineRange"] == [3, 4]
    imports = {(edge["source"], edge["target"]) for edge in graph["edges"] if edge["type"] == "imports"}
    assert ("file:src/demo/app.py", "file:src/demo/helper.py") in imports
    assert ("file:web/app.ts", "file:web/helper.ts") in imports
    assert graph["tour"] and graph["layers"]
    assert any("heuristic" in line.lower() for line in graph["friday"]["coverage"]["limitations"])


def test_same_size_same_timestamp_edit_invalidates_cache(repo):
    path = put(repo, "app.py", "def first():\n    pass\n")
    first = atlas.build("example")
    assert atlas.build("example")["friday"]["cacheHit"] is True
    before = path.stat()
    path.write_text("def other():\n    pass\n", encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    changed = atlas.build("example")
    assert changed["friday"]["fingerprint"] != first["friday"]["fingerprint"]
    assert changed["friday"]["cacheHit"] is False
    assert any(node["name"] == "other" for node in changed["nodes"])
    assert not any(node["name"] == "first" for node in changed["nodes"])


def test_refresh_rebuilds_and_callers_cannot_mutate_cache(repo):
    put(repo, "app.py", "def example(): pass\n")
    first = atlas.build("example")
    first["nodes"].clear()
    assert atlas.build("example")["nodes"]
    assert atlas.build("example", refresh=True)["friday"]["cacheHit"] is False


def test_scan_excludes_hidden_dependencies_secrets_binary_and_large_files(repo, monkeypatch):
    put(repo, "app.py", "raise RuntimeError('source must never execute')\n")
    for path in (".env", ".private/hidden.py", "node_modules/vendor.js", "dist/bundle.js", "keystore.json", "credentials.json"):
        put(repo, path, "{}")
    put(repo, "large.py", "x" * 200)
    (repo / "binary.py").write_bytes(b"x\x00x")
    monkeypatch.setattr(atlas, "MAX_FILE_BYTES", 100)
    graph = atlas.build("example")
    assert [node["filePath"] for node in graph["nodes"] if "filePath" in node] == ["app.py"]
    assert graph["friday"]["coverage"]["filesSkipped"] >= 8


def test_credential_policy_runs_before_file_open(repo, monkeypatch):
    put(repo, "allowed.py", "x = 1\n")
    put(repo, "blocked.py", "x = 2\n")
    original = atlas.credentials.check
    monkeypatch.setattr(atlas.credentials, "check", lambda path, **kwargs: "private" if path.name == "blocked.py" else original(path, **kwargs))
    graph = atlas.build("example")
    assert not any(node.get("filePath") == "blocked.py" for node in graph["nodes"])


def test_rejected_key_content_consumes_the_aggregate_read_budget(repo, monkeypatch):
    synthetic = "-----BEGIN PRIVATE " + "KEY-----\nexample material\n"
    for name in ("a.txt", "b.txt", "c.txt"):
        put(repo, name, synthetic)
    # Use stored bytes because native newline conversion can change their size.
    file_size = (repo / "a.txt").stat().st_size
    monkeypatch.setattr(atlas, "MAX_BYTES", file_size * 2)
    reads = []
    original_read = atlas._read
    def observed_read(root, rel, limit, **kwargs):
        data = original_read(root, rel, limit, **kwargs)
        reads.append((rel, len(data)))
        return data
    monkeypatch.setattr(atlas, "_read", observed_read)
    graph = atlas.build("example")
    coverage = graph["friday"]["coverage"]
    assert reads == [("a.txt", file_size), ("b.txt", file_size)]
    assert coverage["bytesRead"] == file_size * 2
    assert coverage["filesScanned"] == 0
    assert coverage["truncated"] is True
    assert not graph["nodes"]


def test_links_and_junctions_are_not_followed(repo, tmp_path):
    outside = put(tmp_path, "outside/secret.py", "def outside_marker(): pass\n")
    try:
        (repo / "linked.py").symlink_to(outside)
        (repo / "linked_folder").symlink_to(outside.parent, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this host")
    put(repo, "safe.py", "x = 1\n")
    graph = atlas.build("example")
    assert not any("linked" in node.get("filePath", "") for node in graph["nodes"])
    assert "outside_marker" not in json.dumps(graph)


def test_windows_reparse_attribute_is_rejected_without_following_target():
    info = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
    assert atlas._is_link(info)


@pytest.mark.parametrize("bad", ["../outside.py", "/absolute.py", "folder/../outside.py", "a\\b.py", "a.py:stream", ".env", "vendor/lib.py", "a/./b.py", "a./b.py"])
def test_source_paths_refuse_escape_and_hidden_content(bad):
    with pytest.raises(ValueError):
        atlas._relative(bad)


def imported_graph():
    return {"version": "1.0.0", "project": {"name": "Imported Example", "languages": ["Python"], "frameworks": [], "description": "Existing analysis", "analyzedAt": "2001-01-01T00:00:00Z", "gitCommitHash": ""},
            "nodes": [{"id": "file:app.py", "type": "file", "name": "app.py", "filePath": "app.py", "lineRange": [1, 1], "summary": "An app.", "tags": [], "complexity": "simple"}],
            "edges": [], "layers": [{"id": "app", "name": "App", "description": "", "nodeIds": ["file:app.py"]}],
            "tour": [{"order": 1, "title": "Start", "description": "Read the app", "nodeIds": ["file:app.py"]}]}


def test_import_is_compatible_but_never_claimed_current_or_semantically_verified(repo):
    put(repo, "app.py", "x = 1\n")
    put(repo, ".ua/knowledge-graph.json", json.dumps(imported_graph()))
    graph = atlas.build("example")
    assert_integrity(graph)
    assert graph["project"]["name"] == "Imported Example"
    assert graph["friday"]["mode"] == "imported"
    assert graph["friday"]["coverage"]["importedCoverage"] == "unknown"
    assert graph["friday"]["stale"] is True
    assert atlas.build("example")["friday"]["stale"] is True
    assert "current" in " ".join(graph["friday"]["warnings"])


def test_saved_graph_survives_local_scan_deadline(repo, monkeypatch):
    put(repo, "app.py", "x = 1\n")
    put(repo, ".ua/knowledge-graph.json", json.dumps(imported_graph()))
    clock = [0.0]
    monkeypatch.setattr(atlas.time, "monotonic", lambda: clock[0])
    original_scan = atlas._scan
    def expire_scan(root, deadline):
        clock[0] = deadline + 1
        return original_scan(root, deadline)
    monkeypatch.setattr(atlas, "_scan", expire_scan)
    graph = atlas.build("example")
    assert graph["friday"]["mode"] == "imported"
    assert graph["project"]["name"] == "Imported Example"
    assert graph["friday"]["coverage"]["truncated"] is True
    assert graph["friday"]["coverage"]["filesScanned"] == 0
    assert graph["friday"]["coverage"]["importBytesRead"] > 0


def test_long_source_symbols_keep_selectable_bounded_ids(repo):
    put(repo, "app.py", "def " + "symbol" * 150 + "(): pass\n")
    graph = atlas.build("example")
    assert any(node["type"] == "function" for node in graph["nodes"])
    assert all(len(node["id"]) <= 800 for node in graph["nodes"])


def test_import_source_references_are_revalidated_even_on_cache_hit(repo):
    path = put(repo, "asset.dat", "opaque source")
    raw = imported_graph()
    raw["nodes"][0]["filePath"] = "asset.dat"
    put(repo, ".ua/knowledge-graph.json", json.dumps(raw))
    assert atlas.build("example")["friday"]["mode"] == "imported"
    path.unlink()
    graph = atlas.build("example")
    assert graph["friday"]["mode"] == "local"
    assert graph["friday"]["cacheHit"] is False


def test_javascript_symbol_line_references_skip_preceding_blank_lines(repo):
    put(repo, "app.js", "\n\nfunction example() {}\n")
    graph = atlas.build("example")
    node = next(node for node in graph["nodes"] if node["name"] == "example")
    assert node["lineRange"] == [3, 3]


@pytest.mark.parametrize("mutation", ["duplicate", "edge", "layer", "tour", "path", "lines", "weight", "unknown_type"])
def test_invalid_upstream_graph_falls_back_to_local_structure(repo, mutation):
    put(repo, "app.py", "def example(): pass\n")
    raw = imported_graph()
    if mutation == "duplicate":
        raw["nodes"].append(copy.deepcopy(raw["nodes"][0]))
    elif mutation == "edge":
        raw["edges"].append({"source": "file:app.py", "target": "missing", "type": "imports", "direction": "forward", "weight": 1})
    elif mutation in {"layer", "tour"}:
        raw["layers" if mutation == "layer" else "tour"][0]["nodeIds"] = ["missing"]
    elif mutation == "path":
        raw["nodes"][0]["filePath"] = "../outside.py"
    elif mutation == "lines":
        raw["nodes"][0]["lineRange"] = [5, 1]
    elif mutation == "weight":
        raw["edges"].append({"source": "file:app.py", "target": "file:app.py", "type": "imports", "direction": "forward", "weight": float("nan")})
    else:
        raw["nodes"][0]["type"] = "unrecognised"
    put(repo, ".understand-anything/knowledge-graph.json", json.dumps(raw))
    graph = atlas.build("example")
    assert graph["friday"]["mode"] == "local"
    assert any("invalid" in warning for warning in graph["friday"]["warnings"])
    assert any(node["name"] == "example" for node in graph["nodes"])
    assert_integrity(graph)


def test_malformed_and_oversized_imports_do_not_block_local_map(repo, monkeypatch):
    put(repo, "app.py", "x = 1\n")
    graph_file = put(repo, ".ua/knowledge-graph.json", "{not-json")
    assert atlas.build("example")["friday"]["mode"] == "local"
    graph_file.write_text(json.dumps(imported_graph()), encoding="utf-8")
    monkeypatch.setattr(atlas, "MAX_IMPORT_BYTES", 10)
    graph = atlas.build("example")
    assert graph["friday"]["mode"] == "local"
    assert any("safely read" in warning for warning in graph["friday"]["warnings"])


def test_legacy_graph_directory_has_upstream_precedence(repo):
    put(repo, "app.py", "x = 1\n")
    (repo / ".understand-anything").mkdir()
    put(repo, ".ua/knowledge-graph.json", json.dumps(imported_graph()))
    assert atlas.build("example")["friday"]["mode"] == "local"


def test_safe_hidden_project_config_is_mapped_and_imported(repo):
    paths = [".gitignore", ".github/workflows/check.yml", ".eslintrc.json"]
    for path in paths:
        put(repo, path, "{}\n")
    local = atlas.build("example")
    assert set(paths) <= {node.get("filePath") for node in local["nodes"]}
    raw = imported_graph()
    raw["nodes"] = [dict(raw["nodes"][0], id="file:" + path, filePath=path, name=path) for path in paths]
    raw["layers"] = []
    raw["tour"] = [{"order": 2, "title": "Second", "description": "", "nodeIds": ["file:" + paths[1]]},
                   {"order": 1, "title": "First", "description": "", "nodeIds": ["file:" + paths[0]]}]
    put(repo, ".ua/knowledge-graph.json", json.dumps(raw))
    graph = atlas.build("example")
    assert graph["friday"]["mode"] == "imported"
    assert [step["order"] for step in graph["tour"]] == [1, 2]
    assert_integrity(graph)


def test_parse_failure_examples_are_bounded_without_hiding_failure_count(repo):
    for index in range(12):
        put(repo, f"bad{index}.py", "def incomplete(\n")
    graph = atlas.build("example")
    coverage = graph["friday"]["coverage"]
    assert coverage["parseFailures"] == 12
    assert len([item for item in coverage["limitations"] if "Could not parse" in item]) == 8


def test_imported_prose_is_redacted_before_leaving_inspector(repo):
    put(repo, "app.py", "x = 1\n")
    raw = imported_graph()
    synthetic = "gh" + "p_" + "a1b2c3d4e5f6" * 3  # pragma: allowlist secret
    raw["nodes"][0]["summary"] = "Example " + synthetic
    put(repo, ".ua/knowledge-graph.json", json.dumps(raw))
    graph = atlas.build("example")
    assert graph["friday"]["mode"] == "imported"
    assert synthetic not in json.dumps(graph)


def test_saved_graph_with_key_material_is_rejected_and_its_read_is_accounted(repo):
    put(repo, "app.py", "x = 1\n")
    raw = imported_graph()
    raw["nodes"][0]["summary"] = "-----BEGIN PRIVATE " + "KEY-----"
    path = put(repo, ".ua/knowledge-graph.json", json.dumps(raw))
    graph = atlas.build("example")
    assert graph["friday"]["mode"] == "local"
    assert graph["friday"]["coverage"]["importBytesRead"] == path.stat().st_size
    assert any("safely read" in warning for warning in graph["friday"]["warnings"])
    assert "BEGIN PRIVATE" not in json.dumps(graph)


def test_file_byte_entry_and_node_limits_keep_graph_integrity(repo, monkeypatch):
    for index in range(8):
        put(repo, f"area{index}/app.py", "def example(): pass\n")
    monkeypatch.setattr(atlas, "MAX_FILES", 3)
    monkeypatch.setattr(atlas, "MAX_NODES", 5)
    graph = atlas.build("example")
    assert graph["friday"]["coverage"]["filesScanned"] == 3
    assert len(graph["nodes"]) <= 5
    assert graph["friday"]["coverage"]["truncated"] is True
    assert_integrity(graph)
    monkeypatch.setattr(atlas, "MAX_BYTES", 25)
    graph = atlas.build("example", refresh=True)
    assert graph["friday"]["coverage"]["bytesRead"] <= 25
    monkeypatch.setattr(atlas, "MAX_ENTRIES", 2)
    graph = atlas.build("example", refresh=True)
    assert graph["friday"]["coverage"]["truncated"] is True


def test_deadline_returns_a_bounded_honest_partial_map(repo, monkeypatch):
    put(repo, "app.py", "x = 1\n")
    monkeypatch.setattr(atlas, "MAX_SECONDS", 0)
    graph = atlas.build("example")
    assert not graph["nodes"]
    assert graph["friday"]["coverage"]["truncated"] is True


def test_scan_deadline_leaves_time_for_symbols_and_imports(repo, monkeypatch):
    put(repo, "app.py", "import helper\ndef start(): return helper.run()\n")
    put(repo, "helper.py", "def run(): return 1\n")
    clock = [0.0]
    monkeypatch.setattr(atlas.time, "monotonic", lambda: clock[0])
    original_scan = atlas._scan
    def use_scan_budget(root, deadline):
        files, coverage, fingerprint = original_scan(root, deadline)
        clock[0] = deadline
        coverage["truncated"] = True
        return files, coverage, fingerprint
    monkeypatch.setattr(atlas, "_scan", use_scan_budget)
    graph = atlas.build("example")
    coverage = graph["friday"]["coverage"]
    assert coverage["truncated"] is True
    assert coverage["filesParsed"] == 2
    assert any(node["type"] == "function" and node["name"] == "start" for node in graph["nodes"])
    assert any(edge["type"] == "imports" and edge["source"] == "file:app.py" and edge["target"] == "file:helper.py" for edge in graph["edges"])
    assert coverage["limits"]["scanSeconds"] < coverage["limits"]["seconds"]
    assert clock[0] < atlas.MAX_SECONDS


def test_bounded_scan_prioritizes_source_over_documentation(repo, monkeypatch):
    put(repo, "docs/guide.md", "# Guide\n")
    put(repo, "src/app.py", "def start(): pass\n")
    monkeypatch.setattr(atlas, "MAX_FILES", 1)
    graph = atlas.build("example")
    assert graph["friday"]["coverage"]["filesScanned"] == 1
    assert graph["friday"]["coverage"]["filesParsed"] == 1
    assert any(node.get("filePath") == "src/app.py" for node in graph["nodes"])
    assert not any(node.get("filePath") == "docs/guide.md" for node in graph["nodes"])


def test_busy_returns_stale_cache_or_fast_error_and_cache_is_bounded(repo, monkeypatch):
    put(repo, "app.py", "x = 1\n")
    atlas.build("example")
    key = ("example", os.path.normcase(str(repo)))
    atlas._ACTIVE.add(key)
    assert atlas.build("example")["friday"]["stale"] is True
    atlas._ACTIVE.add(("other", "root"))
    with pytest.raises(atlas.AtlasBusyError):
        atlas.build("new")
    atlas._ACTIVE.clear()
    monkeypatch.setattr(atlas, "MAX_CACHED_REPOS", 2)
    atlas.build("another")
    atlas.build("third")
    assert len(atlas._CACHE) == 2
    assert key not in atlas._CACHE
