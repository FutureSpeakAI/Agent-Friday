"""The structural query reuses the parsed wiki until the wiki changes.

The ambient knowledge block runs a structural query for every system prompt.
A parse reads and decrypts every page and builds a mention trie, so repeating
it per prompt churned hundreds of megabytes per turn on a large wiki.
"""
from __future__ import annotations

import os

import pytest

from agent_friday.services.knowledge_graph import structural_query, wiki_graph


@pytest.fixture
def wiki(tmp_path, monkeypatch):
    w = tmp_path / "wiki"
    (w / "research").mkdir(parents=True)
    (w / "research" / "graphrag.md").write_text(
        "# GraphRAG\n\nUses [[Community Detection]] over extracted entities.\n", encoding="utf-8")
    (w / "research" / "community-detection.md").write_text(
        "# Community Detection\n\nClusters nodes for the galaxy view.\n", encoding="utf-8")
    monkeypatch.setattr(wiki_graph, "WIKI_DIR", w)
    monkeypatch.setattr(wiki_graph, "SOUL_FILE", tmp_path / "SOUL.md")
    wiki_graph.clear_wiki_index_cache()
    yield w
    wiki_graph.clear_wiki_index_cache()


@pytest.fixture
def reads(monkeypatch):
    seen: list[str] = []
    real = wiki_graph._read

    def counting(path):
        seen.append(path.name)
        return real(path)
    monkeypatch.setattr(wiki_graph, "_read", counting)
    return seen


def _bump(path, text):
    path.write_text(text, encoding="utf-8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def test_repeated_queries_read_each_page_once(wiki, reads):
    for _ in range(5):
        r = structural_query.query("tell me about graphrag")
        assert r["candidates"], r
    assert sorted(reads) == ["community-detection.md", "graphrag.md"]


def test_an_edited_page_is_seen_on_the_next_query(wiki, reads):
    structural_query.query("tell me about graphrag")
    _bump(wiki / "research" / "graphrag.md",
          "---\nsummary: Rewritten summary about zeppelins.\n---\n# GraphRAG\n")
    r = structural_query.query("tell me about graphrag")
    assert any("zeppelins" in c["summary"] for c in r["candidates"]), r


def test_an_added_or_removed_page_is_seen_on_the_next_query(wiki):
    structural_query.query("tell me about graphrag")
    (wiki / "research" / "zeppelin-history.md").write_text(
        "# Zeppelin History\n\nAirships.\n", encoding="utf-8")
    r = structural_query.query("tell me about zeppelin history")
    assert any(c["title"] == "Zeppelin History" for c in r["candidates"]), r
    (wiki / "research" / "zeppelin-history.md").unlink()
    r = structural_query.query("tell me about zeppelin history")
    assert not any(c["title"] == "Zeppelin History" for c in r["candidates"]), r


@pytest.mark.parametrize("before,after", [(None, b"k" * 32), (b"k" * 32, None), (b"k" * 32, b"j" * 32)])
def test_any_change_of_vault_key_reparses(wiki, reads, monkeypatch, before, after):
    # Locked pages read as a placeholder and unlocked ones as plaintext, and a
    # new passphrase means a new key: whichever way the key changes, the same
    # unchanged files must be parsed again.
    import sys
    import types
    state = {"key": before}
    fake = types.SimpleNamespace(_get_vault_key=lambda: state["key"])
    monkeypatch.setitem(sys.modules, "agent_friday.services.agent", fake)
    structural_query.query("tell me about graphrag")
    n = len(reads)
    state["key"] = after
    structural_query.query("tell me about graphrag")
    assert len(reads) == 2 * n


def test_the_cache_key_never_holds_the_vault_key(wiki, monkeypatch):
    import sys
    import types
    key = b"secret-vault-key-bytes-32-long!!"  # pragma: allowlist secret
    monkeypatch.setitem(sys.modules, "agent_friday.services.agent",
                        types.SimpleNamespace(_get_vault_key=lambda: key))
    structural_query.query("tell me about graphrag")
    assert key not in repr(wiki_graph._index_cache["key"]).encode()
    assert not any(part is key for part in wiki_graph._index_cache["key"])


def test_concurrent_misses_parse_once(wiki, monkeypatch):
    # Turns arriving while a parse runs wait for it instead of each starting
    # their own: concurrent parses were the peak this cache exists to remove.
    import threading
    import time as _t
    real = wiki_graph.build_wiki_index
    builds = []

    def slow_build(*a, **k):
        builds.append(1)
        _t.sleep(0.3)
        return real(*a, **k)
    monkeypatch.setattr(wiki_graph, "build_wiki_index", slow_build)
    out = []
    threads = [threading.Thread(target=lambda: out.append(wiki_graph.cached_wiki_index())) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(out) == 4 and all(o is out[0] for o in out)
    assert len(builds) == 1


def test_a_parse_overtaken_by_a_clear_is_not_kept(wiki, monkeypatch):
    # A passphrase armed while a parse runs: what that parse decrypted under
    # the old key must not be stored afterwards.
    real = wiki_graph.build_wiki_index

    def build_then_clear(*a, **k):
        index = real(*a, **k)
        wiki_graph.clear_wiki_index_cache()
        return index
    monkeypatch.setattr(wiki_graph, "build_wiki_index", build_then_clear)
    wiki_graph.cached_wiki_index()
    assert wiki_graph._index_cache["index"] is None


def test_an_atomic_same_size_replace_is_seen(wiki, reads):
    # wiki_write_text replaces a page atomically; same size and a restored
    # mtime must still read as a change.
    page = wiki / "research" / "graphrag.md"
    structural_query.query("tell me about graphrag")
    st = page.stat()
    old = page.read_text(encoding="utf-8")
    tmp = page.with_name("graphrag.md.tmp")
    tmp.write_text(old.replace("GraphRAG", "GraphRaG"), encoding="utf-8")
    os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(tmp, page)
    assert page.stat().st_size == st.st_size and page.stat().st_mtime_ns == st.st_mtime_ns
    n = len(reads)
    structural_query.query("tell me about graphrag")
    assert len(reads) > n


class _FakeTimer:
    made: list = []

    def __init__(self, interval, fn, args=(), kwargs=None):
        self.interval, self.fn, self.cancelled, self.daemon = interval, fn, False, False
        self.args = tuple(args)
        _FakeTimer.made.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.fn(*self.args)


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    _FakeTimer.made = []
    monkeypatch.setattr(wiki_graph, "_Timer", _FakeTimer, raising=False)
    monkeypatch.setattr(wiki_graph, "_clock", lambda: now["t"], raising=False)
    return now


def _live_timer():
    live = [t for t in _FakeTimer.made if not t.cancelled]
    return live[-1] if live else None


def test_an_idle_cache_is_dropped_after_ten_minutes(wiki, reads, clock):
    structural_query.query("tell me about graphrag")
    timer = _live_timer()
    assert timer is not None and timer.interval <= 600
    clock["t"] += 601
    timer.fire()
    assert wiki_graph._index_cache["index"] is None
    n = len(reads)
    structural_query.query("tell me about graphrag")
    assert len(reads) > n


def test_use_keeps_the_cache_and_idle_still_ends_it(wiki, reads, clock):
    structural_query.query("tell me about graphrag")
    clock["t"] += 300
    structural_query.query("tell me about graphrag")       # a hit: the idle clock restarts
    clock["t"] += 300
    _live_timer().fire()                                     # 600 s after the parse, 300 s idle
    assert wiki_graph._index_cache["index"] is not None
    clock["t"] += 301
    _live_timer().fire()
    assert wiki_graph._index_cache["index"] is None


def test_an_old_timer_never_unhooks_the_current_one(wiki, clock):
    structural_query.query("tell me about graphrag")
    old = _live_timer()
    wiki_graph.clear_wiki_index_cache()
    old.cancelled = False                    # it was already running when cancelled
    structural_query.query("tell me about graphrag")
    current = wiki_graph._index_cache["timer"]
    assert current is not None and current is not old
    old.fire()
    assert wiki_graph._index_cache["timer"] is current
    assert wiki_graph._index_cache["index"] is not None


def test_a_timer_that_cannot_start_keeps_nothing(wiki, clock, monkeypatch):
    class Broken(_FakeTimer):
        def start(self):
            raise RuntimeError("can't start new thread")
    monkeypatch.setattr(wiki_graph, "_Timer", Broken)
    index = wiki_graph.cached_wiki_index()
    assert index                                # the caller still gets its answer
    assert wiki_graph._index_cache["index"] is None


def test_a_late_timer_never_serves_a_stale_cache(wiki, reads, clock):
    # If the timer has not run yet, a query after the idle limit still re-reads.
    structural_query.query("tell me about graphrag")
    n = len(reads)
    clock["t"] += 601
    structural_query.query("tell me about graphrag")
    assert len(reads) == 2 * n


def test_building_and_serving_the_cache_writes_nothing(wiki, monkeypatch):
    import builtins
    import pathlib
    real_open = builtins.open

    def guarded_open(file, mode="r", *a, **k):
        assert not any(c in mode for c in "wax+"), f"opened {file} for writing"
        return real_open(file, mode, *a, **k)
    monkeypatch.setattr(builtins, "open", guarded_open)
    for name in ("write_text", "write_bytes", "touch", "rename", "replace", "mkdir"):
        monkeypatch.setattr(pathlib.Path, name,
                            lambda self, *a, _n=name, **k: pytest.fail(f"Path.{_n}({self})"))
    monkeypatch.setattr(os, "replace", lambda *a, **k: pytest.fail("os.replace"))
    for _ in range(3):
        structural_query.query("tell me about graphrag")


def _repeat_queries(tmp_path, monkeypatch, *, drop_cache_between=False):
    """Ten queries after the first over a 150-page wiki: (parses before the
    ten, parses in all, wiki pages read during the ten). Pages are counted
    where every page is read (wiki_graph._read), so another thread's work
    can never count; a re-parse reads all 150 again."""
    w = tmp_path / "wiki"
    (w / "notes").mkdir(parents=True)
    for i in range(150):
        links = " ".join(f"[[Topic {j:03d}]]" for j in range(i % 7, 150, 37))
        (w / "notes" / f"topic-{i:03d}.md").write_text(
            f"# Topic {i:03d}\n\nAbout topic {i:03d} and its neighbours. {links}\n" + "filler text " * 400,
            encoding="utf-8")
    monkeypatch.setattr(wiki_graph, "WIKI_DIR", w)
    monkeypatch.setattr(wiki_graph, "SOUL_FILE", tmp_path / "SOUL.md")
    wiki_graph.clear_wiki_index_cache()
    real_build = wiki_graph.build_wiki_index
    parses = []

    def counted(*a, **k):
        parses.append(1)
        return real_build(*a, **k)
    monkeypatch.setattr(wiki_graph, "build_wiki_index", counted)
    real_read = wiki_graph._read
    reads = {"n": 0, "on": False}
    root = str(w.resolve())

    def counted_read(path):
        if reads["on"] and str(path.resolve()).startswith(root):
            reads["n"] += 1
        return real_read(path)
    monkeypatch.setattr(wiki_graph, "_read", counted_read)
    try:
        structural_query.query("tell me about topic 042")
        parsed_before = len(parses)
        reads["on"] = True
        for _ in range(10):
            if drop_cache_between:
                wiki_graph.clear_wiki_index_cache()
            structural_query.query("tell me about topic 042")
    finally:
        wiki_graph.clear_wiki_index_cache()
    return parsed_before, len(parses), reads["n"]


def test_repeated_queries_allocate_almost_nothing(tmp_path, monkeypatch):
    # Flat memory: after the first parse, ten more queries over an unchanged
    # wiki parse nothing and read none of its pages (a parse reads and
    # decrypts every page; reading none is what keeps memory flat).
    parsed_before, parses, pages_read = _repeat_queries(tmp_path, monkeypatch)
    assert parsed_before == 1 and parses == 1, f"repeated queries parsed the wiki {parses - 1} more time(s)"
    assert pages_read == 0, f"repeated queries read {pages_read} wiki pages"


def test_the_page_count_catches_a_real_re_parse(tmp_path, monkeypatch):
    # Teeth for the test above: a cache dropped before each query re-parses
    # the wiki, and the page count sees every page of every re-parse.
    parsed_before, parses, pages_read = _repeat_queries(tmp_path, monkeypatch, drop_cache_between=True)
    assert parses == 11 and pages_read >= 10 * 150, (parses, pages_read)
