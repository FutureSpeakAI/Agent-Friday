"""Laya's triage of what the Library reads: a document's type, its tier and its copies.

The encoder is the Library tests' lexical stand-in (a hashed bag of words), so nothing loads a model. The
documents' vectors are stored directly where a test needs only the heads, and read through the real indexer
where it needs the whole path. Fixtures are synthetic.
"""
from __future__ import annotations

import pytest

from tests.library_fixtures import install_fake_encoder, isolate_library, release_library, write_docs

TEXTS = {
    "note": "Meeting notes. Agenda and discussion points, action items to follow up on, ideas and reminders "
            "to remember later, a to-do list and thoughts for tomorrow.",
    "contract": "This agreement is made between the parties and sets out the terms and conditions. The term of "
                "this contract, termination, confidentiality, liability and governing law. The parties agree "
                "to the obligations and signatures below.",
    "invoice": "Invoice number, invoice date, due date, bill to, line items, quantity, unit price, subtotal, "
               "tax and total amount due. Please remit payment within thirty days. Balance due.",
    "paper": "Abstract. We propose a method and evaluate it on benchmark datasets. Introduction, related work, "
             "methodology, experiments, results and discussion, conclusion and references.",
    "receipt": "Receipt: thank you for your purchase. Items, subtotal, sales tax, total paid by card. Payment "
               "received, transaction id, merchant and amount charged. Paid in full.",
    "cv": "Curriculum vitae: professional summary, work experience, education, skills and references. Resume "
          "with employment history, certifications and awards.",
    "letter": "Dear Sir or Madam, I am writing to you regarding the matter below. Thank you for your letter. "
              "Yours sincerely, with warm regards and kind regards.",
}
OTHER = ("mitochondria photosynthesis volcano magma glacier giraffe zebra savanna lagoon coral reef plankton "
         "tundra basalt quartz granite")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    install_fake_encoder(monkeypatch)
    from agent_friday.services.library import shelf, triage
    monkeypatch.setattr(shelf, "_vault_key", lambda: None)
    monkeypatch.setattr(shelf, "tier_of", lambda title, sample: 1)
    triage.reset_heads()
    yield
    triage.reset_heads()
    release_library(fg, lstore)


def _store():
    from agent_friday.services.library.store import store_for
    return store_for("owner")


def _doc(st, name, text, *, sha=None, size=400, kind="text", shelf="open"):
    """An indexed document with a profile vector, one passage vector and one block, made without a reader."""
    from agent_friday.services.library import embed
    fid = st.folder_id("/scope", "")
    did = st.upsert_document("/scope/%s" % name, sha256=sha or ("sha-" + name), size=size, mtime=1.0, kind=kind,
                             ext="txt", scope="/scope", folder_id=fid, title=name, shelf=shelf)
    st.set_state(did, "indexed")
    v = embed.embed([text, text])
    st.put_vectors([("document", did, did, embed.to_blob(v[0])), ("passage", 1000 + did, did, embed.to_blob(v[1]))])
    st.x("INSERT INTO blocks(doc_id, ord, kind, text) VALUES(?,?,?,?)", (did, 0, "paragraph", text))
    return did


@pytest.mark.parametrize("want", sorted(TEXTS))
def test_each_kind_of_document_is_read_from_its_own_vectors(want):
    from agent_friday.services.library import triage
    st = _store()
    did = _doc(st, want, TEXTS[want])
    got, score = triage.classify_type(st, did)
    assert got == want and score > triage.MIN_SCORE


def test_text_that_resembles_no_type_is_other():
    from agent_friday.services.library import triage
    st = _store()
    assert triage.classify_type(st, _doc(st, "rocks", OTHER))[0] == triage.OTHER


def test_a_head_that_is_not_sure_says_other_rather_than_guessing(monkeypatch):
    from agent_friday.services.library import triage
    st = _store()
    did = _doc(st, "inv", TEXTS["invoice"])
    monkeypatch.setattr(triage, "MIN_SCORE", 0.99)
    assert triage.classify_type(st, did)[0] == triage.OTHER


def test_the_types_are_the_seven_the_owner_named():
    from agent_friday.services.library import triage
    assert triage.DOC_TYPES == ("note", "contract", "invoice", "paper", "receipt", "cv", "letter")
    assert set(triage.PROTOTYPES) == set(triage.DOC_TYPES)


def test_triage_writes_a_receipt_with_versions_and_leaves_the_shelf_alone():
    import json
    from agent_friday.services.library import embed, triage
    st = _store()
    did = _doc(st, "inv", TEXTS["invoice"])
    res = triage.triage_document(st, did)
    row = st.get_doc_meta(did)
    assert res["ok"] and row["doc_type"] == "invoice" and row["degraded"] == 0
    v = json.loads(row["versions"])
    assert v == {"triage": triage.VERSION, "encoder": triage._encoder_id(), "heads": triage.heads_id()}
    assert st.get_document(did)["shelf"] == "open"
    assert st.untriaged() == []


def test_a_new_triage_version_makes_the_notes_again(monkeypatch):
    from agent_friday.services.library import triage
    st = _store()
    did = _doc(st, "inv", TEXTS["invoice"])
    triage.triage_document(st, did)
    assert st.untriaged() == []
    monkeypatch.setattr(triage, "VERSION", triage.VERSION + 1)
    assert st.untriaged() == [did]


def test_the_same_bytes_are_an_exact_copy_and_the_later_one_points_at_the_earlier():
    from agent_friday.services.library import triage
    st = _store()
    a = _doc(st, "a", TEXTS["letter"], sha="same-bytes")
    b = _doc(st, "b", TEXTS["letter"], sha="same-bytes")
    assert triage.triage_pending(st) == 2
    ra, rb = st.get_doc_meta(a), st.get_doc_meta(b)
    assert ra["dup_of"] is None and rb["dup_of"] == a and rb["dup_kind"] == "exact"


def test_a_near_copy_is_noted_and_a_different_document_is_not():
    from agent_friday.services.library import triage
    words = ["alpha%d" % i for i in range(120)]
    base = " ".join(words)
    near = " ".join(["changed"] + words[1:])
    st = _store()
    a = _doc(st, "a", base)
    b = _doc(st, "b", near)
    c = _doc(st, "c", TEXTS["contract"])
    triage.triage_pending(st)
    assert st.get_doc_meta(b)["dup_of"] == a and st.get_doc_meta(b)["dup_kind"] == "near"
    assert st.get_doc_meta(a)["dup_of"] is None
    assert st.get_doc_meta(c)["dup_of"] is None


def test_a_near_copy_of_a_very_different_size_is_not_called_a_copy():
    from agent_friday.services.library import triage
    words = ["alpha%d" % i for i in range(120)]
    st = _store()
    a = _doc(st, "a", " ".join(words), size=400)
    b = _doc(st, "b", " ".join(["changed"] + words[1:]), size=400 * 10)
    triage.triage_pending(st)
    assert st.get_doc_meta(b)["dup_of"] is None


def test_a_vault_document_is_noted_as_private_and_never_compared():
    from agent_friday.services.library import triage
    words = " ".join("beta%d" % i for i in range(60))
    st = _store()
    a = _doc(st, "open", words)
    v = _doc(st, "vault", words, shelf="vault")
    triage.triage_pending(st)
    rv = st.get_doc_meta(v)
    assert rv["sensitivity"] >= 2 and rv["dup_of"] is None
    assert st.get_doc_meta(a)["dup_of"] is None


def test_without_an_encoder_the_document_is_marked_degraded_not_guessed(monkeypatch):
    from agent_friday.services.library import embed, triage
    st = _store()
    did = _doc(st, "inv", TEXTS["invoice"])
    monkeypatch.setattr(embed, "available", lambda: False)
    triage.triage_document(st, did)
    row = st.get_doc_meta(did)
    assert row["doc_type"] is None and row["degraded"] == 1 and row["sensitivity"] is not None


def test_a_tier_recorded_when_the_document_was_read_is_reused_not_asked_again(monkeypatch):
    from agent_friday.services.library import shelf, triage

    def never(title, sample):
        raise AssertionError("the classifier was asked a second time")
    st = _store()
    did = _doc(st, "inv", TEXTS["invoice"])
    st.note_tier(did, 3)
    monkeypatch.setattr(shelf, "tier_of", never)
    triage.triage_document(st, did)
    assert st.get_doc_meta(did)["sensitivity"] == 3


def test_a_classifier_that_cannot_answer_is_recorded_as_private_and_degraded(monkeypatch):
    from agent_friday.services.library import shelf, triage

    def boom(title, sample):
        raise RuntimeError("no classifier")
    st = _store()
    did = _doc(st, "inv", TEXTS["invoice"])
    monkeypatch.setattr(shelf, "tier_of", boom)
    triage.triage_document(st, did)
    row = st.get_doc_meta(did)
    assert row["sensitivity"] == 2 and row["degraded"] == 1


def test_removing_a_document_removes_its_notes_and_clears_pointers_to_it():
    from agent_friday.services.library import triage
    st = _store()
    a = _doc(st, "a", TEXTS["letter"], sha="same")
    b = _doc(st, "b", TEXTS["letter"], sha="same")
    triage.triage_pending(st)
    assert st.get_doc_meta(b)["dup_of"] == a
    st.purge_document(a)
    assert st.get_doc_meta(a) is None
    assert st.get_doc_meta(b)["dup_of"] is None


def test_triage_is_scoped_to_a_folder():
    from agent_friday.services.library import triage
    st = _store()
    a = _doc(st, "a", TEXTS["letter"])
    assert triage.triage_pending(st, "/elsewhere") == 0
    assert st.get_doc_meta(a) is None
    assert triage.triage_pending(st, "/scope") == 1


def test_the_whole_path_reads_a_folder_notes_each_document_and_asks_the_classifier_once_each(tmp_path, monkeypatch):
    from agent_friday.services.library import grants, indexer, shelf, triage
    calls = []
    monkeypatch.setattr(shelf, "tier_of", lambda title, sample: calls.append(title) or 1)
    root = tmp_path / "Lib"
    write_docs(root, {"inv.txt": TEXTS["invoice"] + "\n\n" + TEXTS["invoice"],
                      "cv.txt": TEXTS["cv"] + "\n\n" + TEXTS["cv"],
                      "inv_copy.txt": TEXTS["invoice"] + "\n\n" + TEXTS["invoice"]})
    grants.add_scope("owner", str(root))
    st = _store()
    indexer.sweep_scope(st, root, allowed=lambda p: grants.allowed("owner", p), classify=shelf.chooser("owner"))
    assert len(calls) == 3
    n = triage.triage_pending(st, str(root.resolve()))
    assert n == 3 and len(calls) == 3, "triage reused the tier recorded at index time"
    from pathlib import Path
    rows = {Path(r["path"]).name: st.get_doc_meta(r["id"]) for r in st.list_documents("indexed")}
    types = {t: m["doc_type"] for t, m in rows.items()}
    assert types["cv.txt"] == "cv" and types["inv.txt"] == "invoice"
    copies = [m for m in rows.values() if m["dup_kind"] == "exact"]
    assert len(copies) == 1


def test_triage_code_imports_no_network_library():
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "services" / "library"
    net = {"socket", "ssl", "http", "urllib", "urllib3", "requests", "httpx", "aiohttp", "websocket", "openai",
           "anthropic", "google"}
    for name in ("triage.py", "changes.py", "index_job.py", "staging.py"):
        tree = ast.parse((src / name).read_text(encoding="utf-8"))
        mods = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods += [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                mods.append(n.module)
        assert [m for m in mods if m.split(".")[0] in net] == [], name
