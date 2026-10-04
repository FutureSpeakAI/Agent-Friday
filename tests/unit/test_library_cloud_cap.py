"""One answer may send only so many characters of the owner's documents to a
cloud model; the cap is a setting with a default, and a trim is said plainly."""
from __future__ import annotations

import pytest

from tests.library_fixtures import (install_fake_encoder, isolate_library, release_library, write_docs)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    install_fake_encoder(monkeypatch)
    from agent_friday.services import agent
    from agent_friday.services.library import shelf, tools
    monkeypatch.setattr(shelf, "_vault_key", lambda: None)
    monkeypatch.setattr(shelf, "tier_of", lambda title, sample: 1)
    monkeypatch.setattr(agent, "_CURRENT_PROVIDER", type("V", (), {"get": staticmethod(lambda: "anthropic")}))
    tools._TURN_SENT.clear()
    yield
    tools._TURN_SENT.clear()
    release_library(fg, lstore)


def _ev(n, size, doc_id=1):
    return [{"label": "1.%d" % (i + 1), "doc": "D", "doc_id": doc_id, "text": ("x%d " % i) * (size // 3), "ref": "lib:1#%d" % i}
            for i in range(n)]


def test_the_cap_has_a_default_and_is_clamped():
    from agent_friday.services.library import tools
    assert tools.cloud_char_cap({}) == 6000
    assert tools.cloud_char_cap({"library_cloud_char_cap": 12000}) == 12000
    assert tools.cloud_char_cap({"library_cloud_char_cap": 5}) == tools.CLOUD_CAP_MIN
    assert tools.cloud_char_cap({"library_cloud_char_cap": 10**9}) == tools.CLOUD_CAP_MAX
    assert tools.cloud_char_cap({"library_cloud_char_cap": "junk"}) == 6000


def test_the_setting_is_declared_with_its_default_and_a_row_in_both_page_files():
    from pathlib import Path
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["library_cloud_char_cap"] == 6000
    root = Path(__file__).resolve().parents[2]
    for f in ("index.html", "ui_parts/app.html"):
        t = (root / f).read_text(encoding="utf-8")
        assert "library_cloud_char_cap" in t and "(default)" in t


def test_under_the_cap_nothing_is_trimmed_and_nothing_is_said():
    from agent_friday.services.library import tools
    kept, note = tools._apply_cap(_ev(3, 900), 6000)
    assert len(kept) == 3 and note is None


def test_over_the_cap_the_best_passages_go_and_the_rest_are_left_out_and_named():
    from agent_friday.services.library import tools
    kept, note = tools._apply_cap(_ev(5, 800), 2000)
    assert [e["label"] for e in kept] == ["1.1", "1.2"]
    assert sum(len(e["text"]) for e in kept) <= 2000
    assert "3 passages left out" in note and "2,000 characters" in note and "Settings" in note and "locally" in note


def test_a_single_long_passage_is_cut_at_the_cap_and_the_cut_is_said():
    from agent_friday.services.library import tools
    kept, note = tools._apply_cap(_ev(1, 5000), 2000)
    assert len(kept) == 1 and len(kept[0]["text"]) <= 2001 and kept[0]["text"].endswith("…")
    assert "1 cut short" in note


def test_the_cap_is_per_answer_so_a_second_search_in_the_same_turn_shares_it(monkeypatch):
    import agent_friday.core as core
    from agent_friday.services.library import tools
    monkeypatch.setattr(core._TURN_LOCAL, "turn_id", "turn-1", raising=False)
    k1, n1 = tools._apply_cap(_ev(2, 800), 2000)
    assert len(k1) == 2 and n1 is None
    k2, n2 = tools._apply_cap(_ev(2, 800), 2000)
    assert len(k2) == 1 and len(k2[0]["text"]) <= 410           # 400 characters were all that remained
    assert "1 passage left out" in n2 and "1 cut short" in n2
    monkeypatch.setattr(core._TURN_LOCAL, "turn_id", "turn-2", raising=False)
    k3, _ = tools._apply_cap(_ev(2, 800), 2000)
    assert len(k3) == 2                                          # a new answer starts a new allowance


def test_a_cloud_search_trims_to_the_cap_and_only_what_is_sent_is_registered(tmp_path, monkeypatch):
    from agent_friday.services import file_grants as fg
    from agent_friday.services.library import grants, indexer, tools
    from agent_friday.services.library.store import store_for
    root = tmp_path / "Lib"
    body = "\n\n".join(("The harbor inspection %d found a cracked hull and a leaking valve on pier number %d. " % (i, i)) * 9
                       for i in range(12))
    docs = write_docs(root, {"report.txt": body})
    grants.add_scope("owner", str(root))
    fg.create_file_grant(str(docs["report.txt"]))
    st = store_for("owner")
    indexer.sweep_scope(st, root, allowed=lambda p: grants.allowed("owner", p))
    sent = []
    monkeypatch.setattr(fg, "on_file_read", lambda path, text: sent.append(text))
    monkeypatch.setattr(tools, "_settings", lambda: {"library_cloud_answers": True, "library_cloud_char_cap": 1000})
    out = tools.search_library({"question": "harbor inspection cracked hull leaking valve"})
    assert "left out" in out or "cut short" in out
    assert "at most 1,000 characters" in out
    assert sum(len(t) for t in sent) <= 1000
    assert len(sent) >= 1 and all(t in out for t in sent[:1])


def test_a_local_turn_is_not_capped(tmp_path, monkeypatch):
    from agent_friday.services import agent
    from agent_friday.services.library import grants, indexer, tools
    from agent_friday.services.library.store import store_for
    root = tmp_path / "Lib"
    write_docs(root, {"a.txt": ("The harbor inspection found a cracked hull. " * 40 + "\n\n") * 8})
    grants.add_scope("owner", str(root))
    st = store_for("owner")
    indexer.sweep_scope(st, root, allowed=lambda p: grants.allowed("owner", p))
    monkeypatch.setattr(agent, "_CURRENT_PROVIDER", type("V", (), {"get": staticmethod(lambda: "local")}))
    monkeypatch.setattr(tools, "_settings", lambda: {"library_cloud_char_cap": 1000})
    out = tools.search_library({"question": "harbor inspection cracked hull"})
    assert "left out" not in out and "cut short" not in out
