"""A document nobody can find was not really delivered.

The `office` tool writes into its own folder, which is what stops a wrong path
reaching the rest of the disk. That folder is not the creations folder the
Studio gallery lists, so without this the .pptx Friday just made would exist and
be invisible. The listing covers both, and the delivery gate's render rides
along as the thumbnail — the gallery already renders a PNG and already maps
`pptx` to an icon, so no UI change was needed.
"""

import pytest

from agent_friday.services import office_engine as oe


@pytest.fixture()
def docs(tmp_path, monkeypatch):
    d = tmp_path / "documents"
    (d / oe.RENDER_DIR).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(oe, "DOCUMENTS_DIR", d)
    return d


def test_an_office_document_and_its_render_are_listed(client, docs):
    (docs / "quarterly.pptx").write_bytes(b"PK\x03\x04 pretend deck")
    (docs / oe.RENDER_DIR / "quarterly.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    r = client.get("/api/creations")
    assert r.status_code == 200
    names = [f["name"] for f in r.get_json()["files"]]
    assert "quarterly.pptx" in names, names
    assert "quarterly.png" in names, names


def test_a_document_without_a_render_still_appears(client, docs):
    (docs / "notes.docx").write_bytes(b"PK\x03\x04 pretend doc")
    r = client.get("/api/creations")
    names = [f["name"] for f in r.get_json()["files"]]
    assert "notes.docx" in names


def test_only_office_documents_are_pulled_in(client, docs):
    """The documents folder is Friday's working area; the gallery is for the
    things she made, not for whatever else lands there."""
    (docs / "scratch.tmp").write_bytes(b"x")
    (docs / "deck.pptx").write_bytes(b"PK\x03\x04")
    r = client.get("/api/creations")
    names = [f["name"] for f in r.get_json()["files"]]
    assert "deck.pptx" in names
    assert "scratch.tmp" not in names


def test_the_document_can_be_served(client, docs):
    (docs / "deck.pptx").write_bytes(b"PK\x03\x04 pretend deck")
    r = client.get("/api/creations/deck.pptx")
    assert r.status_code == 200
    assert r.data.startswith(b"PK")


def test_the_render_can_be_served(client, docs):
    (docs / oe.RENDER_DIR / "deck.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    r = client.get("/api/creations/deck.png")
    assert r.status_code == 200
    assert r.data.startswith(b"\x89PNG")


def test_the_serving_route_cannot_be_walked_out_of(client, docs):
    """`serve_creation` is reachable from the browser, so a filename must not
    become a way to read the rest of the disk."""
    secret = docs.parent / "secret.txt"
    secret.write_text("not for the browser", encoding="utf-8")
    r = client.get("/api/creations/..%2Fsecret.txt")
    assert r.status_code in (400, 403, 404), r.status_code
    assert b"not for the browser" not in r.data


def test_the_gallery_survives_office_being_absent(client, monkeypatch, docs):
    """Office is optional. The gallery must not break when it is missing."""
    monkeypatch.setattr(oe, "DOCUMENTS_DIR", docs.parent / "nothing-here")
    r = client.get("/api/creations")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"
