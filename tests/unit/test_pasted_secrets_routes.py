"""Routes outside the chat family that take the owner's text keep no key.

`pasted_secrets.withholds_request_body` cleans every string in a JSON body
before the handler reads it. The routes that persist or forward raw text
carry it (checked from the source, so a route that loses it fails here), and
the voice debug log withholds what it writes.
"""
from __future__ import annotations

import ast
from pathlib import Path

from flask import Flask, jsonify, request

from agent_friday.services import pasted_secrets as ps
from tests.unit.test_pasted_secrets import ANTHROPIC, GITHUB

SRC = Path(__file__).resolve().parents[2] / "src" / "agent_friday"


def _app(seen):
    app = Flask(__name__)

    @app.route("/t", methods=["POST"])
    @ps.withholds_request_body
    def handler():
        seen.append(request.get_json(silent=True))
        seen.append(request.get_json())
        return jsonify({"status": "ok"}), 201

    return app


def test_every_string_in_the_body_is_withheld_before_the_handler_reads_it():
    seen = []
    body = {"message": f"use {ANTHROPIC}", "task": {"kind": "prompt", "steps": [f"push with {GITHUB}"]},
            "n": 3}
    r = _app(seen).test_client().post("/t", json=body)
    assert r.status_code == 201
    for got in seen:
        blob = repr(got)
        assert ANTHROPIC not in blob and GITHUB not in blob
        assert got["n"] == 3 and got["task"]["kind"] == "prompt"
    d = r.get_json()
    assert {k["id"] for k in d["key_withheld"]["kinds"]} == {"anthropic", "github"}
    assert [a["type"] for a in d["actions"]] == ["key_form"]
    assert ANTHROPIC not in r.get_data(as_text=True)


def test_a_body_with_no_key_is_untouched():
    seen = []
    r = _app(seen).test_client().post("/t", json={"message": "remind me at 9"})
    assert seen[0] == {"message": "remind me at 9"}
    assert "key_withheld" not in r.get_json()


ROUTES = {
    "routes/workspace_studio.py": "ws_chat_post",
    "routes/tasks.py": "api_agent_steer",
    "routes/scheduler.py": "create_schedule",
    "routes/workflows.py": ("draft_generate", "workflow_chains_create"),
}


def _decorators(path, fn_name):
    tree = ast.parse((SRC / path).read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            return [getattr(d, "id", getattr(d, "attr", None)) for d in node.decorator_list]
    raise AssertionError(f"{fn_name} not found in {path}")


def test_the_routes_that_take_raw_owner_text_withhold_keys():
    for path, names in ROUTES.items():
        for name in (names if isinstance(names, tuple) else (names,)):
            assert "withholds_request_body" in _decorators(path, name), f"{path}:{name}"


def test_the_voice_debug_log_withholds_keys():
    src = (SRC / "routes" / "voice.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    vlog = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_vlog")
    body = ast.get_source_segment(src, vlog)
    assert "_withhold(str(msg))" in body
