"""A cut tool result can be paged back through read_file, and only Friday's own tool-output folder is opened for it.

An over-limit result is saved under <friday_home>/tool-output and the model is told to read the rest from there.
Friday's state folder is otherwise off limits to the file tools, so the reader must be allowed that one folder:
reads only, the resolved path inside the resolved folder, nothing else. Synthetic text only.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from agent_friday.paths import friday_home
from agent_friday.services import agent, path_policy
from agent_friday.services import tool_output as to

DENIED = path_policy.REFUSAL_PREFIX


def _lines(n: int, width: int = 20) -> str:
    return "\n".join("line %05d " % i + "x" * width for i in range(1, n + 1))


def _saved(text: str) -> str:
    """Cut an over-limit command result the way the executor does and return the saved file's path."""
    out = to.clip_result("run_command", text, max_lines=100)
    assert "Full output saved at " in out, out
    return out.split("Full output saved at ")[1].split(";")[0]


def _page_back(path: str) -> str:
    """Read the whole file through read_file, page by page, following each page's own note."""
    seen, offset = [], 1
    for _ in range(200):
        page = agent._tool_read_file({"path": path, "offset": offset})
        assert DENIED not in page, page
        match = re.search(r"Continue with (?:read_file )?offset=(\d+)", page)
        seen.append(page.split("\n[Showing")[0])
        if not match:
            return "\n".join(seen)
        offset = int(match.group(1))
    raise AssertionError("the paging never ended")


def test_an_over_limit_result_can_be_paged_back_in_full_through_read_file():
    text = _lines(3000)
    paged = _page_back(_saved(text))
    assert paged.splitlines()[0].startswith("line 00001") and paged.splitlines()[-1].startswith("line 03000")
    assert paged.count("\n") + 1 == 3000, "a page was skipped or repeated"


def test_the_executors_own_hooks_let_the_read_through_too():
    saved = _saved(_lines(3000))
    out = agent._execute_tool("read_file", {"path": saved}, session_ctx={"authenticated": True})
    assert DENIED not in out and "line 00001" in out, out[:300]


def test_the_judgement_allows_a_read_of_the_folder_and_nothing_else_about_it():
    saved = Path(_saved(_lines(3000)))
    assert path_policy.judge(str(saved), "read")[0] == saved.resolve()
    assert path_policy.judge(str(saved), "write")[0] is None, "the folder is opened for reading only"
    assert path_policy.judge(str(saved.parent), "write")[0] is None


@pytest.mark.parametrize("rel", ["settings.json", "vault/notes.txt", "approvals.json", "tool-output-old/x.txt"])
def test_the_rest_of_friday_state_is_still_refused(rel):
    target = friday_home() / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("synthetic state", encoding="utf-8")
    out = agent._tool_read_file({"path": str(target)})
    assert DENIED in out, out[:200]


def test_a_path_that_climbs_out_of_the_folder_is_refused():
    saved = Path(_saved(_lines(3000)))
    secret = friday_home() / "secret.txt"
    secret.write_text("synthetic secret", encoding="utf-8")
    climbing = str(saved.parent / ".." / ".." / "secret.txt")
    assert DENIED in agent._tool_read_file({"path": climbing})


def test_a_link_inside_the_folder_that_points_out_of_it_is_refused():
    saved = Path(_saved(_lines(3000)))
    secret = friday_home() / "secret.txt"
    secret.write_text("synthetic secret", encoding="utf-8")
    link = saved.parent / "innocent.txt"
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError):
        pytest.skip("this machine does not allow links")
    assert DENIED in agent._tool_read_file({"path": str(link)})


def test_the_folder_name_as_a_prefix_of_another_folder_does_not_open_it():
    other = friday_home() / (to.output_dir().name + "-evil")
    other.mkdir(parents=True, exist_ok=True)
    (other / "x.txt").write_text("synthetic", encoding="utf-8")
    assert DENIED in agent._tool_read_file({"path": str(other / "x.txt")})


@pytest.mark.parametrize("tool", ["run_command", "search_web", "browse_web"])
def test_the_path_the_cut_off_notice_names_is_absolute_inside_the_folder_and_readable(tool):
    out = to.clip_result(tool, _lines(3000), max_lines=100)
    match = re.search(r"Full output saved at (.+?); read it with read_file\.", out)
    assert match, "the cut-off notice does not name where the rest is: " + out[-300:]
    named = Path(match.group(1))
    assert named.is_absolute(), "the notice names a path read_file cannot resolve: %s" % named
    assert named.resolve().parent.parent == to.output_dir().resolve(), "the notice names a path outside the folder"
    first = agent._tool_read_file({"path": str(named)})
    assert DENIED not in first and "line 00001" in first, first[:200]


@pytest.mark.parametrize("tool,args", [("browse_web", {"url": "https://pages-example.test/long"}),
                                       ("search_web", {"query": "synthetic long result"})])
def test_a_cut_off_result_of_the_real_tool_path_names_a_path_read_file_opens(monkeypatch, tool, args):
    """Whatever path a tool's notice offers, read_file can open it: through `_execute_tool`, the way a turn meets it."""
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, tool, lambda inp: _lines(3000))
    ctx = agent.prepare_confirmation_ctx("2026-11-22-tooloutput", "look at https://pages-example.test/long",
                                         {"authenticated": True, "owner_surface": True})
    out = agent._execute_tool(tool, dict(args), session_ctx=ctx)
    match = re.search(r"Full output saved at (.+?); read it with read_file\.", str(out))
    assert match, "the notice did not name where the rest is: " + str(out)[-300:]
    named = match.group(1)
    first = agent._tool_read_file({"path": named})
    assert DENIED not in first and "line 00001" in first, first[:200]
    assert DENIED not in agent._execute_tool("read_file", {"path": named}, session_ctx=ctx)
