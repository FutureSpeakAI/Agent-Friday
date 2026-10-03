"""A cut tool result names what was shown and how to get the rest.

Files keep the start and say which line to continue from; commands keep the
end, where the error is; everything else keeps both ends and marks the cut.
Image payloads are never cut. The full text is kept and pruned by retention.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from agent_friday.services import tool_output as to


def _lines(n, width=20):
    return "\n".join(f"line {i:05d} " + "x" * width for i in range(1, n + 1))


class TestHead:
    def test_a_file_keeps_the_start_and_names_the_next_line(self):
        text = _lines(5400)
        out, cut = to.truncate("read_file", text, max_chars=10**9, max_lines=2000)
        assert cut
        assert out.startswith("line 00001")
        assert "line 02000" in out and "line 02001" not in out
        assert "[Showing lines 1-2,000 of 5,400" in out
        assert "Continue with offset=2001." in out

    def test_a_char_limit_also_names_the_line_to_continue_from(self):
        text = _lines(300, width=100)       # ~33k chars, 300 lines
        out, cut = to.truncate("read_file", text, max_chars=8192, max_lines=2000)
        assert cut
        shown = out.split("\n[Showing")[0]
        n = shown.count("\n") + 1
        assert f"Continue with offset={n + 1}." in out
        assert len(shown) <= 8192

    def test_a_short_result_is_untouched(self):
        assert to.truncate("read_file", "hello\nworld") == ("hello\nworld", False)


class TestTail:
    def test_a_command_keeps_the_end_where_the_error_is(self):
        text = _lines(3000) + "\nTraceback: boom"
        out, cut = to.truncate("run_command", text, max_chars=10**9, max_lines=2000)
        assert cut
        assert out.rstrip().endswith("Traceback: boom")
        assert "line 00001 " not in out
        assert out.startswith("[Showing the last 2,000 of 3,001 lines; the first 1,001 lines")

    def test_the_saved_path_is_named(self):
        text = _lines(3000)
        out, _ = to.truncate("run_command", text, max_lines=10, saved_path="C:/x/out.txt")
        assert "Full output saved at C:/x/out.txt" in out


class TestMiddle:
    def test_other_tools_keep_both_ends_and_mark_the_cut(self):
        text = _lines(1000, width=50)
        out, cut = to.truncate("browse_web", text, max_chars=4000, max_lines=2000)
        assert cut
        assert out.startswith("line 00001") and out.rstrip().endswith("line 01000 " + "x" * 50)
        assert "cut from the middle" in out
        assert len(out) <= 4000 + 400

    def test_mcp_tools_use_the_middle_style(self):
        assert to.style_for("mcp_github_search") == to.MIDDLE


class TestExempt:
    def test_image_payloads_are_never_cut(self):
        blob = json.dumps({"image_b64": "A" * 50000, "text": "seen"})
        assert to.clip_result("screenshot", blob) == blob
        assert to.clip_result("office", blob) == blob


class TestWindow:
    def test_window_pages_through_a_file(self):
        text = _lines(50)
        page, info = to.window_lines(text, offset=21, limit=10)
        assert page.startswith("line 00021") and page.count("\n") == 9
        assert info == {"first": 21, "last": 30, "total": 50, "total_chars": len(text),
                        "next_offset": 31}
        assert "Continue with read_file offset=31" in to.page_note(info)

    def test_the_last_page_says_so(self):
        text = _lines(50)
        page, info = to.window_lines(text, offset=41, limit=100)
        assert info["next_offset"] is None and info["last"] == 50
        assert "the end of the file" in to.page_note(info)

    def test_a_whole_small_file_has_no_note(self):
        page, info = to.window_lines("a\nb", offset=1)
        assert page == "a\nb" and to.page_note(info) == ""


class TestKeep:
    def test_clip_result_saves_the_full_text_and_prunes_by_retention(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
        monkeypatch.setattr(to, "retention_days", lambda: 7)
        monkeypatch.setattr(to, "_last_prune", 0.0)
        old = to.output_dir() / (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        old.mkdir(parents=True)
        (old / "stale.txt").write_text("old", encoding="utf-8")
        text = _lines(3000)
        out = to.clip_result("run_command", text, max_lines=100)
        saved = out.split("Full output saved at ")[1].split(";")[0]
        assert open(saved, encoding="utf-8").read() == text
        assert not old.exists(), "a folder past the retention survived"

    def test_retention_zero_keeps_everything(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
        old = to.output_dir() / "2001-01-01"
        old.mkdir(parents=True)
        (old / "a.txt").write_text("x", encoding="utf-8")
        assert to.prune(0) == 0 and old.exists()

    def test_a_self_windowed_reader_is_neither_spilled_nor_cut_again(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
        page, info = to.window_lines(_lines(400, width=60), offset=101, limit=102)
        result = page + to.page_note(info)
        assert to.clip_result("read_file", result, max_lines=100) == result
        assert "Continue with read_file offset=203" in result
        assert not to.output_dir().exists()

    def test_a_page_plus_its_note_stays_under_the_executor_ceiling(self):
        page, info = to.window_lines(_lines(5000, width=200), offset=1)
        assert len(page + to.page_note(info)) <= to.MAX_CHARS

    def test_off_the_record_nothing_is_saved(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
        from agent_friday.services import off_record as off
        monkeypatch.setattr(off, "active", lambda settings=None: True)
        out = to.clip_result("run_command", _lines(3000), max_lines=100)
        assert "Full output saved" not in out and not to.output_dir().exists()

    def test_one_huge_line_keeps_both_ends(self):
        text = "A" * 20000 + "MIDDLE" + "Z" * 20000
        out, cut = to.truncate("browse_web", text, max_chars=4000)
        assert cut and out.startswith("AAAA") and out.rstrip().endswith("ZZZZ")


def test_describe_limits_quotes_the_real_constants():
    s = to.describe_limits()
    assert f"{to.MAX_LINES:,}" in s and f"{to.MAX_CHARS:,}" in s
