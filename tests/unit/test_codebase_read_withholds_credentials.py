"""`codebase_read` opens a file the way `read_file` does: key material never reaches the model.

A codebase can be a folder the user pointed at, and such a folder can hold a
private key, or an ordinary file with a key pasted inside. The tool is the
model's way into that folder, so it keeps `read_file`'s two protections
(services/credential_paths): a key-material file is refused, and a key block
inside an ordinary file is withheld from the whole text before it is capped.
All key material here is synthetic.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import codebases as cb
from agent_friday.services import credential_paths as cred
from agent_friday.services import secret_patterns

PEM_HEAD = "-----BEGIN OPENSSH PRIVATE " + "KEY-----"  # pragma: allowlist secret
PEM_TAIL = "-----END OPENSSH PRIVATE " + "KEY-----"  # pragma: allowlist secret
SENTINEL = "SYNTHETICSENTINELKEYMATERIAL0123456789abcdef"
PEM_BODY = ("b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n"
            "QyNTUxOQAAACBS" + SENTINEL + "\n")
PEM = PEM_HEAD + "\n" + PEM_BODY + PEM_TAIL + "\n"
CERT = "-----BEGIN CERTIFICATE-----\nMIIBsomethingpublic\n-----END CERTIFICATE-----\n"

BEFORE = "the notes before the key"
AFTER = "the notes after the key"


def _codebase_over_a_folder(monkeypatch, tmp_path):
    """A codebase over a folder the user pointed at, holding ordinary files and key material."""
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    folder = tmp_path / "theirs"
    (folder / "deploy").mkdir(parents=True)
    (folder / "index.html").write_text("<h1>hello</h1>\n", encoding="utf-8")
    # The key sits past the head a by-content sniff reads, inside ordinary prose,
    # so only the redaction can keep it out: no refusal applies to this file.
    filler = "filler line of ordinary prose.\n" * (cred._SNIFF_HEAD_BYTES // 30 + 100)
    (folder / "notes.md").write_text(BEFORE + "\n" + filler + PEM + AFTER + "\n", encoding="utf-8")
    (folder / "id_rsa").write_text(PEM, encoding="utf-8")
    (folder / "deploy" / "id_ed25519").write_text(PEM, encoding="utf-8")
    (folder / "server.pem").write_text(PEM, encoding="utf-8")
    (folder / "ca.pem").write_text(CERT, encoding="utf-8")
    return cb.create("Theirs", existing_path=str(folder))


@pytest.fixture
def folder_codebase(monkeypatch, tmp_path):
    return _codebase_over_a_folder(monkeypatch, tmp_path)


def _read(rec, rel):
    return ag.CLAUDE_TOOL_HANDLERS["codebase_read"]({"codebase_id": rec["id"], "path": rel})


def test_a_key_pasted_inside_an_ordinary_file_is_withheld(folder_codebase):
    out = _read(folder_codebase, "notes.md")
    assert isinstance(out, dict) and out["status"] == "ok", out
    body = out["content"]
    assert SENTINEL not in body, "codebase_read returned the key's body"
    assert PEM_HEAD not in body and PEM_TAIL not in body, "codebase_read returned the key's armor lines"
    assert secret_patterns.WITHHELD in body
    assert BEFORE in body and AFTER in body, "only the key is withheld, not the file"


@pytest.mark.parametrize("rel", ["id_rsa", "deploy/id_ed25519", "server.pem"])
def test_a_key_material_file_is_refused(folder_codebase, rel):
    out = _read(folder_codebase, rel)
    assert isinstance(out, str), "codebase_read returned the key file's content: %r" % (out,)
    assert cred.is_refusal(out), out
    assert SENTINEL not in out and PEM_HEAD not in out


def test_an_ordinary_file_in_the_folder_is_returned_whole(folder_codebase):
    out = _read(folder_codebase, "index.html")
    assert out == {"status": "ok", "path": "index.html", "content": "<h1>hello</h1>\n"}


def test_a_public_certificate_is_still_readable(folder_codebase):
    out = _read(folder_codebase, "ca.pem")
    assert out == {"status": "ok", "path": "ca.pem", "content": CERT}


def test_a_path_outside_the_codebase_is_still_refused(folder_codebase):
    out = _read(folder_codebase, "../theirs-secret.txt")
    assert isinstance(out, str) and out.startswith("codebase_read refused"), out


def test_path_of_names_the_file_read_does_and_refuses_traversal(folder_codebase):
    cid = folder_codebase["id"]
    p = cb.path_of(cid, "deploy/id_ed25519")
    assert p.is_absolute() and p.name == "id_ed25519" and p.parent.name == "deploy"
    assert p.read_text(encoding="utf-8") == cb.read(cid, "deploy/id_ed25519")
    for bad in ("../x", "/etc/passwd", ".git/config", ".friday/receipts/x", "a/../../x", ""):
        with pytest.raises(ValueError):
            cb.path_of(cid, bad)
