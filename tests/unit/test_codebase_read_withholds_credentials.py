"""What a codebase shows the model never carries key material, by either door.

A codebase can be a folder the user pointed at, and such a folder can hold a
private key, or an ordinary file with a key pasted inside. The model reaches
that folder two ways, and both keep `read_file`'s protections
(services/credential_paths):

* `codebase_read` refuses a key-material file and withholds a key block pasted
  inside an ordinary one, from the whole text before it is capped;
* the context block (`context_block_for`) that rides in every turn lists a
  key-material file and never shows its text, and withholds a key or token
  pasted inside a file it does show.

All key material here is synthetic.
"""
from __future__ import annotations

import json

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
TOKEN = "gh" + "p_" + "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6"  # pragma: allowlist secret
KEYSTORE_BODY = "KEYSTORE-BODY-SENTINEL"

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
    # Small text files, so the context block would show them whole.
    (folder / "config.json").write_text(json.dumps({"name": "deploy", "deploy_key": PEM}, indent=1), encoding="utf-8")
    (folder / "keystore.json").write_text(json.dumps({"note": KEYSTORE_BODY}), encoding="utf-8")
    (folder / "settings.yaml").write_text("region: us-east-1\ngithub_token: %s\nretries: 3\n" % TOKEN, encoding="utf-8")  # pragma: allowlist secret
    (folder / "ca.txt").write_text(CERT, encoding="utf-8")
    return cb.create("Theirs", existing_path=str(folder))


@pytest.fixture
def folder_codebase(monkeypatch, tmp_path):
    return _codebase_over_a_folder(monkeypatch, tmp_path)


def _read(rec, rel):
    return ag.CLAUDE_TOOL_HANDLERS["codebase_read"]({"codebase_id": rec["id"], "path": rel})


def _block(rec):
    return cb.context_block_for(rec["id"])


def _line(block, name):
    """The one listing line of a file in the context block."""
    hits = [ln for ln in block.splitlines() if ln.startswith("--- %s (" % name)]
    assert len(hits) == 1, (name, hits)
    return hits[0]


# ── codebase_read ────────────────────────────────────────────────────────────

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


# ── the context block ────────────────────────────────────────────────────────

def test_the_context_block_carries_no_key_material(folder_codebase):
    block = _block(folder_codebase)
    assert SENTINEL not in block, "the context block carried a key's body"
    assert PEM_HEAD not in block and PEM_TAIL not in block, "the context block carried a key's armor lines"
    assert TOKEN not in block, "the context block carried a vendor token"
    assert KEYSTORE_BODY not in block, "the context block carried a key-material file's text"


@pytest.mark.parametrize("name", ["config.json", "keystore.json"])
def test_a_file_the_block_would_show_that_is_key_material_is_listed_and_never_shown(folder_codebase, name):
    """By what it holds (config.json) or by its name (keystore.json): the path is
    listed, with a note in place of the text the block would have shown."""
    assert "withheld: key material" in _line(_block(folder_codebase), name)


@pytest.mark.parametrize("name", ["id_rsa", "deploy/id_ed25519", "server.pem"])
def test_a_key_file_that_is_not_text_is_listed_by_name_alone(folder_codebase, name):
    block = _block(folder_codebase)
    lines = block.splitlines()
    at = lines.index(_line(block, name))
    assert "not shown" in lines[at]
    assert lines[at + 1].startswith(("---", "Last steps")), "no text follows a file the block does not show"


def test_a_vendor_token_in_a_file_the_block_shows_is_withheld_in_place(folder_codebase):
    block = _block(folder_codebase)
    assert "withheld" not in _line(block, "settings.yaml"), "the file is shown, not withheld"
    assert secret_patterns.WITHHELD in block
    assert "region: us-east-1" in block and "retries: 3" in block, "only the token is withheld, not the file"


def test_ordinary_files_and_a_public_certificate_stay_in_the_block(folder_codebase):
    block = _block(folder_codebase)
    assert "<h1>hello</h1>" in block
    assert CERT.strip() in block, "a public certificate is not key material"
    for name in ("index.html", "ca.txt"):
        assert "withheld" not in _line(block, name) and "not shown" not in _line(block, name), name
    # Not a text extension, so it is listed by name as before, and codebase_read shows it.
    assert "not shown; codebase_read to see it" in _line(block, "ca.pem")
    assert "not shown; codebase_read to see it" in _line(block, "notes.md"), "past the inline size, listed by name"
