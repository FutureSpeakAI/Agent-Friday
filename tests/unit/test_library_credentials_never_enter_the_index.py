"""A credential pasted inside an ordinary document never enters the Library's index.

A private key or a vendor token in the middle of notes, a Word file or a PDF is cut out of
the extracted text before it is split into passages. After indexing, nothing stored (blocks,
passages, headings, profiles, the full-text table, titles), no search result and no evidence
the model is handed holds any of it, and the text around it is still found. Every secret
here is synthetic and built from pieces so no literal key sits in the repository.
"""
from __future__ import annotations

import base64
import hashlib

import pytest

from tests.library_fixtures import (install_fake_encoder, isolate_library, make_docx, make_pdf,
                                    release_library, write_docs)

PEM_HEAD = "-----BEGIN RSA PRIVATE " + "KEY-----"  # pragma: allowlist secret
PEM_TAIL = "-----END RSA PRIVATE " + "KEY-----"  # pragma: allowlist secret


def _line(i: int) -> str:
    """One 64-character base64 line of a fake key body; no line is recognisable on its own."""
    return base64.b64encode(hashlib.sha256(b"fake-body-%d" % i).digest() * 2).decode()[:64]


BODY = [_line(i) for i in range(1, 21)]                 # 1,300 characters: more than one passage
PEM = "\n".join([PEM_HEAD] + BODY + [PEM_TAIL])
TOKEN = "ghp_" + "Zk3Qm9Tx5Rv2Wn8Yb4Hc7Ld1Pf6Sj0Ua3Ve"  # pragma: allowlist secret
AWS = "AKIA" + "QX7M2PLD9ZRT4NBC"  # pragma: allowlist secret
SECRETS = BODY + [TOKEN, AWS, TOKEN[:20], PEM_HEAD, PEM_TAIL]

BEFORE = "The deployment checklist says to rotate the signing credentials every ninety days."
AFTER = "After the rotation the service restarts itself and the dashboard turns green."
INLINE = f"The release bot signs with {TOKEN} and then publishes the build to the mirror."
MARK = "[credential withheld]"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    install_fake_encoder(monkeypatch)
    from agent_friday.services.library import shelf
    monkeypatch.setattr(shelf, "_vault_key", lambda: None)
    monkeypatch.setattr(shelf, "tier_of", lambda title, sample: 1)
    yield
    release_library(fg, lstore)


def _build(tmp_path, docs: dict):
    from agent_friday.services.library import grants, indexer
    from agent_friday.services.library.store import store_for
    root = tmp_path / "Lib"
    write_docs(root, docs)
    grants.add_scope("owner", str(root))
    st = store_for("owner")
    indexer.sweep_scope(st, root, allowed=lambda p: grants.allowed("owner", p))
    return st


def _everything_stored(st) -> str:
    """Every text the index holds for any document, as one string."""
    out = []
    for table, cols in (("documents", "title"), ("sections", "heading"), ("blocks", "text"),
                        ("passages", "text"), ("profiles", "text"), ("fts", "title, headings, body")):
        for row in st.q(f"SELECT {cols} FROM {table}"):
            out.extend(str(v) for v in tuple(row))
    return "\n".join(out)


def _leaks(text: str) -> list[str]:
    return [s for s in SECRETS if s in text]


def _local_turn(monkeypatch):
    from agent_friday.services import agent
    monkeypatch.setattr(agent, "_CURRENT_PROVIDER", type("V", (), {"get": staticmethod(lambda: "local")}))


# -- the whole path: index, search, tool, envelope ----------------------------------------

def test_a_key_and_a_token_in_ordinary_notes_are_not_stored_found_or_quoted(tmp_path, monkeypatch):
    from agent_friday.services.library import search, tools
    notes = f"# Deployment\n\n{BEFORE}\n\n{INLINE}\n\nThe key file is pasted below.\n\n{PEM}\n\n{AFTER}\n"
    st = _build(tmp_path, {"ops/notes.md": notes})
    assert st.counts()["indexed"] == 1
    stored = _everything_stored(st)
    assert _leaks(stored) == []
    assert MARK in stored                                       # the place is marked, not silently emptied
    # the text around the secrets is still there and still found
    assert "rotate the signing credentials every ninety days" in stored
    assert "publishes the build to the mirror" in stored       # the same paragraph as the token
    assert "dashboard turns green" in stored
    res = search.run("how often do we rotate the signing credentials")
    assert res["evidence"] and "ninety days" in res["evidence"][0]["text"]
    assert _leaks(" ".join(e["text"] for e in res["evidence"])) == []
    # questions aimed at the secrets find the words around them, never the secrets
    for q in ("release bot signs token publishes build mirror", "key file pasted below"):
        res = search.run(q)
        assert res["evidence"], q
        assert _leaks(" ".join(e["text"] for e in res["evidence"])) == [], q
    # the tool's own output: the JSON head and the fenced evidence the model is handed
    _local_turn(monkeypatch)
    for q in ("rotate the signing credentials", "release bot signs token publishes", "key file pasted below"):
        out = tools.search_library({"question": q})
        assert _leaks(out) == [], q
        assert "<evidence-" in out, q


def test_a_key_whose_lines_are_separate_paragraphs_in_a_word_file_is_cut_whole(tmp_path):
    paras = ([("Heading1", "Handover"), ("Normal", BEFORE), ("Normal", "The key:")]
             + [("Normal", ln) for ln in [PEM_HEAD] + BODY + [PEM_TAIL]]
             + [("Normal", INLINE), ("Heading1", "Afterwards"), ("Normal", AFTER)])
    st = _build(tmp_path, {"handover.docx": make_docx(paras)})
    assert st.counts()["indexed"] == 1
    stored = _everything_stored(st)
    assert _leaks(stored) == []
    assert "rotate the signing credentials" in stored and "dashboard turns green" in stored
    assert "publishes the build to the mirror" in stored
    ords = [r["ord"] for r in st.q("SELECT ord FROM blocks ORDER BY ord")]
    assert ords == list(range(len(ords)))                       # blocks stay numbered without gaps


def test_a_key_whose_lines_are_separate_blocks_in_a_pdf_is_cut_whole(tmp_path):
    lines = ["# Handover", BEFORE, "The key:", PEM_HEAD] + BODY[:8] + [PEM_TAIL, AFTER]
    st = _build(tmp_path, {"handover.pdf": make_pdf([lines])})
    assert st.counts()["indexed"] == 1
    stored = _everything_stored(st)
    assert _leaks(stored) == []
    assert "rotate the signing credentials" in stored and "dashboard turns green" in stored


def test_a_key_inside_one_long_block_is_not_cut_in_two_by_the_passage_splitter(tmp_path):
    # One fenced block of more than 900 characters. Split into passages as it stands, the key would
    # be torn into halves that no pattern recognises.
    fenced = "```\n" + PEM + "\n```"
    assert len(fenced) > 900
    st = _build(tmp_path, {"keys.md": f"# Keys\n\n{BEFORE}\n\n{fenced}\n\n{AFTER}\n"})
    stored = _everything_stored(st)
    assert _leaks(stored) == []
    assert "rotate the signing credentials" in stored and "dashboard turns green" in stored


def test_two_keys_leave_everything_between_them(tmp_path):
    notes = f"# Keys\n\n{PEM}\n\nThe middle paragraph is ordinary.\n\n{PEM}\n\n{AFTER}\n"
    st = _build(tmp_path, {"two.md": notes})
    stored = _everything_stored(st)
    assert _leaks(stored) == []
    assert "The middle paragraph is ordinary." in stored and "dashboard turns green" in stored


# -- what must not change ---------------------------------------------------------------

def test_a_document_without_credentials_is_stored_exactly_as_extracted(tmp_path):
    from agent_friday.services.library import extract
    st = _build(tmp_path, {"plain.md": f"# Plain\n\n{BEFORE}\n\n{AFTER}\n\n- one thing\n- another thing\n"})
    want = extract.extract_document(tmp_path / "Lib" / "plain.md")["blocks"]
    got = st.q("SELECT ord, kind, level, text FROM blocks ORDER BY ord")
    assert [(g["ord"], g["kind"], g["level"] or 0, g["text"]) for g in got] == \
           [(b["ord"], b["kind"], b["level"], b["text"]) for b in want]
    assert MARK not in _everything_stored(st)


def test_a_document_that_holds_a_key_is_still_classified_on_what_it_says(tmp_path):
    """The classifier reads the text as extracted, so a document with a key in it is the kind that
    goes to the vault; only what is stored is withheld, the vault shelf included."""
    import os
    from agent_friday.services.library import indexer, shelf
    from agent_friday.services.library.store import store_for
    seen = []

    def choose(path, title, sample):
        seen.append(sample)
        return "vault" if PEM_HEAD in sample else "open"

    d = write_docs(tmp_path / "Lib", {"deploy.md": f"# Deploy\n\n{PEM}\n\n{AFTER}\n"})
    st = store_for("owner")
    shelf.attach(st, os.urandom(32))
    r = indexer.index_file(st, d["deploy.md"], tmp_path / "Lib", classify=choose)
    assert r["state"] == "indexed" and PEM_HEAD in seen[0]
    assert st.get_document(r["doc_id"])["shelf"] == "vault"
    clear = "\n".join(st.dec(x["text"], "vault") for t in ("blocks", "passages", "profiles")
                      for x in st.q(f"SELECT text FROM {t}"))
    assert _leaks(clear) == []
    assert "dashboard turns green" in clear


def test_a_document_whose_armour_is_chunked_to_hide_it_is_not_kept(tmp_path):
    chunked = '"-----BEGIN RSA PRIVATE " + "KEY-----"'  # pragma: allowlist secret
    st = _build(tmp_path, {"build.md": f"# Build\n\n{BEFORE}\n\nkey = {chunked}\n\n{AFTER}\n"})
    row = st.q("SELECT * FROM documents")[0]
    assert row["state"] == "skipped:credential" and "key" in (row["state_detail"] or "")
    assert st.q("SELECT count(*) n FROM blocks")[0]["n"] == 0
    assert st.q("SELECT count(*) n FROM passages")[0]["n"] == 0
    assert st.counts()["skipped"] == 1 and st.counts()["indexed"] == 0


def test_a_filename_that_carries_a_token_is_not_kept_as_a_title(tmp_path):
    st = _build(tmp_path, {f"deploy {TOKEN}.txt": f"{BEFORE}\n\n{AFTER}\n"})
    assert _leaks(_everything_stored(st)) == []
    assert st.counts()["indexed"] == 1


def test_an_index_built_before_credentials_were_withheld_is_read_again(tmp_path):
    """A document an older build indexed still holds whatever its text held. The next sweep
    reads it again, though the file has not changed."""
    from agent_friday.services.library import indexer
    st = _build(tmp_path, {"notes.md": f"# Notes\n\n{BEFORE}\n\n{AFTER}\n"})
    doc = st.q("SELECT id FROM documents")[0]["id"]
    st.x("UPDATE passages SET text = text || ? WHERE doc_id=?", (" " + TOKEN, doc))
    st.x("UPDATE documents SET index_version=1 WHERE id=?", (doc,))
    assert TOKEN in _everything_stored(st)
    again = indexer.index_file(st, tmp_path / "Lib" / "notes.md", tmp_path / "Lib")
    assert again["state"] == "indexed"
    assert _leaks(_everything_stored(st)) == []


def test_a_failing_check_is_a_failure_of_the_document_not_a_pass(tmp_path, monkeypatch):
    from agent_friday.services import credential_paths
    from agent_friday.services.library import indexer
    from agent_friday.services.library.store import store_for
    d = write_docs(tmp_path / "Lib", {"a.md": f"# A\n\n{BEFORE}\n"})

    def boom(text):
        raise RuntimeError("the redactor is broken")

    monkeypatch.setattr(credential_paths, "redact_secrets", boom)
    st = store_for("owner")
    r = indexer.index_file(st, d["a.md"], tmp_path / "Lib")
    assert r["state"] == "failed"
    assert st.q("SELECT count(*) n FROM blocks")[0]["n"] == 0
    assert st.q("SELECT count(*) n FROM passages")[0]["n"] == 0


# -- the cutter on its own ------------------------------------------------------------------

def _blocks(*rows):
    """(text, page) rows -> extractor-shaped blocks."""
    return [{"kind": "para", "text": t, "page": p, "bbox": None, "level": 0, "ord": i}
            for i, (t, p) in enumerate(rows)]


def test_blocks_without_a_credential_come_back_untouched():
    from agent_friday.services.library import withhold
    items = _blocks(("One paragraph.", 1), ("Another one.", 1), ("A third, with a [bracket] and 100% of the words.", 2))
    assert withhold.blocks(items) is items


def test_a_credential_inside_a_block_leaves_the_words_around_it():
    from agent_friday.services.library import withhold
    out = withhold.blocks(_blocks(("Before.", 1), (f"Sign with {TOKEN} please.", 2), ("After.", 3)))
    assert [b["text"] for b in out] == ["Before.", f"Sign with {MARK} please.", "After."]
    assert [b["ord"] for b in out] == [0, 1, 2] and [b["page"] for b in out] == [1, 2, 3]


def test_a_key_split_over_many_blocks_becomes_one_marker_and_the_rest_keeps_its_place():
    from agent_friday.services.library import withhold
    rows = [("Intro.", 1), ("The key:", 1), (PEM_HEAD, 2)] + [(ln, 2 + i // 8) for i, ln in enumerate(BODY)] \
        + [(PEM_TAIL, 4), ("Outro.", 5)]
    out = withhold.blocks(_blocks(*rows))
    assert [b["text"] for b in out] == ["Intro.", "The key:", MARK, "Outro."]
    assert [b["ord"] for b in out] == [0, 1, 2, 3]
    assert [b["page"] for b in out] == [1, 1, 2, 5]            # each block keeps the page it began on


def test_a_key_that_starts_inside_a_block_and_ends_inside_another_keeps_both_edges():
    from agent_friday.services.library import withhold
    rows = [("Intro.", 1), (f"Here it is: {PEM_HEAD}", 1)] + [(ln, 2) for ln in BODY] + [(f"{PEM_TAIL} Thanks.", 3), ("Outro.", 3)]
    out = withhold.blocks(_blocks(*rows))
    assert [b["text"] for b in out] == ["Intro.", f"Here it is: {MARK} Thanks.", "Outro."]


def test_two_keys_across_blocks_leave_the_blocks_between_them():
    from agent_friday.services.library import withhold
    one = [(PEM_HEAD, 1)] + [(ln, 1) for ln in BODY[:6]] + [(PEM_TAIL, 1)]
    out = withhold.blocks(_blocks(*one, ("Between one.", 2), ("Between two.", 2), *one, ("End.", 3)))
    assert [b["text"] for b in out] == [MARK, "Between one.", "Between two.", MARK, "End."]


@pytest.mark.parametrize("count,at", [(5, 2), (257, 255), (257, 256), (1025, 1024), (1025, 3)])
def test_many_blocks_keep_their_own_identity_whatever_the_count(count, at):
    from agent_friday.services.library import withhold
    texts = [f"Paragraph number {i} about nothing in particular." for i in range(count)]
    texts[at] = f"The token is {TOKEN} here."
    out = withhold.blocks(_blocks(*[(t, i + 1) for i, t in enumerate(texts)]))
    assert len(out) == count
    for i, b in enumerate(out):
        want = f"The token is {MARK} here." if i == at else texts[i]
        assert b["text"] == want and b["page"] == i + 1 and b["ord"] == i


def test_a_table_header_is_withheld_with_its_table():
    from agent_friday.services.library import withhold
    header = f"name | {TOKEN}"
    out = withhold.blocks([{"kind": "table", "text": header + "\nada | 1", "header": header, "page": None,
                            "bbox": None, "level": 0, "ord": 0}])
    assert TOKEN not in out[0]["text"] and TOKEN not in out[0]["header"]
    assert out[0]["header"] == out[0]["text"].split("\n")[0]


def test_text_that_is_only_a_credential_is_not_a_document():
    from agent_friday.services.library import withhold
    with pytest.raises(withhold.Withheld):
        withhold.blocks(_blocks((TOKEN, 1)))
