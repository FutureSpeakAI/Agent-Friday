"""The map follows conversation scope, auth and the governed read path."""
import json

import pytest
from flask import Flask

from agent_friday import core
from agent_friday.governance import action_gate
from agent_friday.routes.codebases import codebases_bp
from agent_friday.services import agent as ag
from agent_friday.services import codebases as cb
from agent_friday.services import repo_atlas, repo_atlas_context


@pytest.fixture
def graph(monkeypatch):
    value = {
        "project": {"name": "Example"}, "friday": {"mode": "local", "coverage": {"truncated": False}},
        "nodes": [{"id": f"file:src/{i}.py", "name": f"module_{i}", "filePath": f"src/{i}.py", "summary": "A component"} for i in range(70)],
        "edges": [{"source": "file:src/0.py", "target": "file:src/1.py", "type": "imports"}],
        "layers": [{"name": "Source", "description": "Modules", "nodeIds": ["file:src/0.py", "file:src/1.py"]}],
        "tour": [{"order": 1, "title": "Start", "description": "Read the source", "nodeIds": ["file:src/0.py"]}],
    }
    monkeypatch.setattr(repo_atlas, "build", lambda cid, refresh=False: value)
    return value


def test_brief_focus_has_neighbors_and_real_source_references(graph):
    result = repo_atlas_context.brief("cb-one", mode="adapt", node_id="file:src/0.py")
    assert [n["id"] for n in result["nodes"]] == ["file:src/0.py", "file:src/1.py"]
    assert len(result["edges"]) == 1
    assert "plan_first" in result["guidance"]
    assert "untrusted" in result["data_notice"]
    assert result["nodes"][0]["filePath"] == "src/0.py"


def test_brief_bounds_results_and_does_not_lose_json_structure(graph):
    for node in graph["nodes"]:
        node["summary"] = "x" * 2000
    result = repo_atlas_context.brief("cb-one")
    assert len(json.dumps(result, ensure_ascii=False)) <= repo_atlas_context.MAX_BRIEF_CHARS
    assert result["omitted_nodes"] > 0
    ids = {n["id"] for n in result["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in result["edges"])


def test_brief_search_and_stale_selection(graph):
    result = repo_atlas_context.brief("cb-one", query="module_69")
    assert [n["name"] for n in result["nodes"]] == ["module_69"]
    with pytest.raises(ValueError):
        repo_atlas_context.brief("cb-one", node_id="missing")
    with pytest.raises(ValueError):
        repo_atlas_context.brief("cb-one", mode="run")
    with pytest.raises(ValueError):
        repo_atlas_context.brief("cb-one", mode=[])


def test_tool_and_gate_resolve_current_conversation(monkeypatch, graph):
    monkeypatch.setattr(cb, "for_conversation", lambda cid: {"id": "cb-one"} if cid == "conv-one" else None)
    monkeypatch.setattr(cb, "load", lambda cid: {"id": cid} if cid == "cb-one" else None)
    marker = ag._CURRENT_CONVERSATION.set("conv-one")
    try:
        assert action_gate.classify("codebase_understand", {})[0] == action_gate.INTERNAL
        result = ag.CLAUDE_TOOL_HANDLERS["codebase_understand"]({"mode": "learn"})
        assert result["codebase_id"] == "cb-one" and result["mode"] == "learn"
        assert any(t["name"] == "codebase_understand" for t in ag.WORKSPACE_TOOLS["hub"])
        assert not any(t["name"] == "codebase_understand" for t in ag.CLAUDE_TOOLS)
    finally:
        ag._CURRENT_CONVERSATION.reset(marker)
    assert action_gate.classify("codebase_understand", {"codebase_id": "missing"})[0] == action_gate.OUTWARD


def test_atlas_route_requires_auth_and_is_not_browser_cached(monkeypatch, graph):
    app = Flask(__name__)
    app.secret_key = "synthetic-test-only"
    app.register_blueprint(codebases_bp)
    monkeypatch.setattr(core, "_HTTP_AUTH_KEY", "")
    monkeypatch.setattr(core, "_loopback_trusted", lambda: False)
    client = app.test_client()
    assert client.get("/api/codebases/cb-one/atlas").status_code == 403
    monkeypatch.setattr(core, "_loopback_trusted", lambda: True)
    result = client.get("/api/codebases/cb-one/atlas")
    assert result.status_code == 200
    assert result.headers["Cache-Control"] == "no-store"
    assert result.json["atlas"]["project"]["name"] == "Example"


def test_study_route_uses_copy_intake(monkeypatch, tmp_path):
    from agent_friday.services import repo_intake, conversations
    app = Flask(__name__)
    app.secret_key = "synthetic-test-only"
    app.register_blueprint(codebases_bp)
    monkeypatch.setattr(core, "_loopback_trusted", lambda: True)
    monkeypatch.setattr(conversations, "load", lambda cid: {"id": cid})
    seen = []
    monkeypatch.setattr(repo_intake, "create", lambda title, source_path, conversation_id=None: seen.append((title, source_path, conversation_id)) or {"id": "cb-copy", "source_snapshot": {"kind": "local-folder"}})
    def forbidden(*args, **kwargs):
        pytest.fail("study mode must not use the branch-switching existing-folder path")
    monkeypatch.setattr(cb, "create", forbidden)
    result = app.test_client().post("/api/codebases", json={"title": "Sample", "study_path": str(tmp_path), "conversation_id": "conv-one"})
    assert result.status_code == 200 and result.json["codebase"]["source_snapshot"]
    assert seen == [("Sample", str(tmp_path), "conv-one")]


def test_refused_study_folder_does_not_create_an_empty_chat(monkeypatch):
    from agent_friday.services import repo_intake, conversations
    app = Flask(__name__)
    app.secret_key = "synthetic-test-only"
    app.register_blueprint(codebases_bp)
    monkeypatch.setattr(core, "_loopback_trusted", lambda: True)
    def refused(*args, **kwargs):
        raise ValueError("Choose a repository folder")
    monkeypatch.setattr(repo_intake, "create", refused)
    def no_chat(*args, **kwargs):
        pytest.fail("A refused study should leave no empty chat")
    monkeypatch.setattr(conversations, "create", no_chat)
    result = app.test_client().post("/api/codebases", json={"study_path": "missing"})
    assert result.status_code == 400


def test_real_study_bootstrap_makes_a_committed_mapped_copy(monkeypatch, tmp_path):
    from agent_friday.services import repo_intake
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text("def schedule():\n    return 1\n", encoding="utf-8")
    (source / ".gitignore").write_text("*.py\n", encoding="utf-8")
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "managed")
    monkeypatch.setattr(repo_intake, "user_home", lambda: tmp_path / "home")
    monkeypatch.setattr(repo_intake, "friday_home", lambda: tmp_path / "runtime")
    record = repo_intake.create("Sample", str(source))
    assert not (source / ".git").exists()
    assert (source / "main.py").read_text() == "def schedule():\n    return 1\n"
    assert cb.is_managed(record["id"])
    assert len(cb.head(record["id"])) == 40
    assert cb.read(record["id"], "main.py").startswith("def schedule")
    graph = repo_atlas.build(record["id"])
    assert any(n["name"] == "schedule" for n in graph["nodes"])
    assert any(n.get("filePath") == ".gitignore" for n in graph["nodes"])
