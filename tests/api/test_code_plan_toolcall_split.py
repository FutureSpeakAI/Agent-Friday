"""/api/code/plan finds the plan when a local seat hands back its thinking.

llama-server ends a thinking model's reasoning at the first literal
`<tool_call>` it sees. When the file being fixed contains that tag (any code
that handles tool calls), the rest of the thinking, the `</think>` marker and
the JSON answer all arrive as the reply text. The fixture below has that
shape: the reply opens in the middle of the model quoting the file, carries
the end of its thinking and a draft plan, then `</think>` and the real plan.

The model call is stubbed with `patch_app`; nothing reaches a model.
"""
from __future__ import annotations

import json
import shutil
import time
import uuid

import pytest

import agent_friday.server as friday_server

REL = "agent_friday/services/tool_integrity.py"

OLD_FILE = (
    "import re\n\n"
    "# Tool-call MARKUP is a leak whatever name it carries: `<invoke name=\"x\">`\n"
    "# and `<tool_call>{\"name\": \"x\", ...}`.\n"
    "_FENCE_RE = re.compile(r\"```.*?```\")\n"
)

NEW_FILE = (
    "import re\n\n"
    "# Tool-call MARKUP is a leak whatever name it carries: `<invoke name=\"x\">`\n"
    "# and `<tool_call>{\"name\": \"x\", ...}`.\n"
    "_FENCE_RE = re.compile(r\"```.*?```\", re.S)\n"
)

DRAFT_FILE = "# draft written while thinking\n"


def _plan(content, summary="Add re.S to _FENCE_RE so fenced blocks spanning lines are stripped."):
    return {
        "summary": summary,
        "steps": ["Add the DOTALL flag to _FENCE_RE"],
        "files": [{"path": REL, "action": "modify", "rationale": "multi-line fences",
                   "new_content": content}],
    }


def _split_reply(final_plan_text: str) -> str:
    """The reply text as a local seat returns it after the split."""
    return (
        # The reasoning channel stopped at `<tool_call>`; the reply resumes there,
        # still inside the model's quote of the file.
        "<tool_call>{\"name\": \"x\", ...}`. A local seat wrote the first into a chat\n"
        "# four times in a field report.\n"
        "_MARKUP_RE = re.compile(r'<tool_call>\\s*\\{\\s*\"name\"\\s*:\\s*\"([^\"]+)\"')\n\n"
        "So the fence regex lacks re.S. A first draft of the answer:\n"
        + json.dumps(_plan(DRAFT_FILE, summary="draft")) + "\n"
        "Wait, the draft drops the file. Keep the whole file and only add re.S.\n"
        "I'm confident this is the correct and minimal fix.\n"
        "</think>\n\n"
        + final_plan_text
    )


@pytest.fixture
def repo(patch_app):
    name = "plansplit-" + uuid.uuid4().hex[:8]
    root = friday_server.PROJECTS_DIR / name
    (root / ".git").mkdir(parents=True, exist_ok=True)
    target = root / REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(OLD_FILE, encoding="utf-8", newline="\n")
    patch_app("_repo_tree", lambda *a, **k: [REL])
    patch_app("_get_friday_system_prompt", lambda *a, **k: None)
    yield name
    shutil.rmtree(root, ignore_errors=True)


def _post(client, patch_app, repo, reply):
    patch_app("_generate_text", lambda *a, **k: reply)
    return client.post("/api/code/plan",
                       json={"repo": repo, "instruction": "fix tool_integrity fenced blocks"})


def _only_file(resp):
    assert resp.status_code == 200, resp.get_json()
    files = resp.get_json()["plan"]["files"]
    assert [f["path"] for f in files] == [REL]
    return files[0]


class TestToolCallSplitReply:
    def test_plan_after_spilled_thinking_is_found(self, client, patch_app, repo):
        resp = _post(client, patch_app, repo, _split_reply(json.dumps(_plan(NEW_FILE), indent=2)))
        f = _only_file(resp)
        assert f["new_content"] == NEW_FILE
        assert "re.S" in f["diff"]

    def test_the_answer_wins_over_the_draft_in_the_thinking(self, client, patch_app, repo):
        resp = _post(client, patch_app, repo, _split_reply(json.dumps(_plan(NEW_FILE))))
        plan = resp.get_json()["plan"]
        assert plan["summary"] != "draft"
        assert _only_file(resp)["new_content"] == NEW_FILE

    def test_fenced_answer_after_spilled_thinking(self, client, patch_app, repo):
        reply = _split_reply("```json\n" + json.dumps(_plan(NEW_FILE), indent=2) + "\n```\n")
        assert _only_file(_post(client, patch_app, repo, reply))["new_content"] == NEW_FILE

    def test_file_contents_holding_the_markers_survive(self, client, patch_app, repo):
        # The new file itself contains both `<tool_call>` and `</think>`.
        content = NEW_FILE + "THINK_END = \"</think>\"\nTAG = \"<tool_call>\"\n"
        reply = _split_reply(json.dumps(_plan(content)))
        assert _only_file(_post(client, patch_app, repo, reply))["new_content"] == content

    def test_only_a_draft_is_not_a_plan(self, client, patch_app, repo):
        # The answer after the thinking is cut off: the draft must not be offered.
        reply = _split_reply('{"summary": "Add re.S", "files": [{"path": "' + REL + '", "new_')
        resp = _post(client, patch_app, repo, reply)
        assert resp.status_code == 502
        assert resp.get_json()["status"] == "error"


class TestOtherReplyShapes:
    def test_plain_json_reply(self, client, patch_app, repo):
        reply = json.dumps(_plan(NEW_FILE))
        assert _only_file(_post(client, patch_app, repo, reply))["new_content"] == NEW_FILE

    def test_plain_json_with_markers_in_content(self, client, patch_app, repo):
        content = NEW_FILE + "END = \"</think>\"\n"
        reply = json.dumps(_plan(content))
        assert _only_file(_post(client, patch_app, repo, reply))["new_content"] == content

    def test_prose_with_braces_after_json(self, client, patch_app, repo):
        reply = "Here is the plan:\n" + json.dumps(_plan(NEW_FILE)) + "\nDone {ok}."
        assert _only_file(_post(client, patch_app, repo, reply))["new_content"] == NEW_FILE

    def test_no_json_is_502(self, client, patch_app, repo):
        resp = _post(client, patch_app, repo, "I could not work out a change.")
        assert resp.status_code == 502


class TestExtractPlanUnit:
    """The parser on its own (imported inside each test so the route tests
    above still run where the module is missing)."""

    def _extract(self, raw):
        from agent_friday.services.code_plan_parse import extract_plan
        return extract_plan(raw)

    def test_non_string_and_empty(self):
        assert self._extract(None) is None
        assert self._extract("") is None
        assert self._extract("   ") is None

    def test_files_must_be_a_list_when_scanning(self):
        assert self._extract('prose {"files": "nope"} more') is None

    def test_brace_noise_before_the_plan(self):
        noise = "{" * 20000
        assert self._extract(noise + json.dumps(_plan(NEW_FILE)))["files"][0]["new_content"] == NEW_FILE

    def test_brace_noise_after_the_plan(self):
        t0 = time.monotonic()
        plan = self._extract(json.dumps(_plan(NEW_FILE)) + " {" * 20000)
        assert plan["files"][0]["new_content"] == NEW_FILE
        assert time.monotonic() - t0 < 10

    def test_file_with_many_braces_after_spilled_thinking(self):
        # A big stylesheet: thousands of braces inside the answer's strings.
        css = "".join(".c%d { color: red; }\n" % n for n in range(8000))
        plan = self._extract(_split_reply(json.dumps(_plan(css))))
        assert plan["files"][0]["new_content"] == css

    def test_an_inner_object_never_stands_in_for_the_plan(self):
        # new_content written as an object that itself has a `files` list.
        inner = {"name": "pkg", "files": ["dist"]}
        outer = {"summary": "outer", "files": [{"path": "package.json", "new_content": inner}]}
        plan = self._extract("Plan follows.\n" + json.dumps(outer) + "\nThat is all {x}.")
        assert plan["summary"] == "outer"

    def test_deep_nesting_is_not_an_error(self):
        assert self._extract("prose " + "[" * 100000) is None
        assert self._extract("prose " + '{"a":' * 5000) is None

    def test_fixture_shape_matches_the_split(self):
        reply = _split_reply(json.dumps(_plan(NEW_FILE)))
        assert reply.startswith("<tool_call>")
        assert reply.count("</think>") == 1
        assert self._extract(reply)["files"][0]["new_content"] == NEW_FILE
