"""The project block that rides in every turn shows no key material.

A project quotes a short excerpt of each text file the user put in it (600
characters each, 1,500 in all). It keeps `read_file`'s rule
(services/credential_paths): a file that is key material is listed and never
quoted, and a key or token inside a file that is quoted is withheld from the
WHOLE text before the excerpt is cut, so a cut can never leave a fragment of
one. All key material here is synthetic.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import conversations as convs
from agent_friday.services import projects
from agent_friday.services import secret_patterns

PEM_HEAD = "-----BEGIN OPENSSH PRIVATE " + "KEY-----"  # pragma: allowlist secret
PEM_TAIL = "-----END OPENSSH PRIVATE " + "KEY-----"  # pragma: allowlist secret
SENTINEL = "SYNTHETICSENTINELKEYMATERIAL0123456789abcdef"
PEM = (PEM_HEAD + "\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n"
       "QyNTUxOQAAACBS" + SENTINEL + "\n" + PEM_TAIL + "\n")
TOKEN = "gh" + "p_" + "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6"  # pragma: allowlist secret
KEYSTORE_BODY = "KEYSTORE-BODY-SENTINEL"


def _project_with_files(monkeypatch, tmp_path):
    """A conversation filed in a project that holds ordinary files and key material."""
    monkeypatch.setattr(projects, "_root", lambda: tmp_path / "projects")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    p = projects.create("Parks desk", instructions="Answer like a city reporter.")
    pid = p["id"]
    projects.add_file(pid, "notes.md", b"The pier closes at dusk.")
    projects.add_file(pid, "deploy.txt", (PEM + "after the key\n").encode("utf-8"))
    projects.add_file(pid, "keystore.json", json.dumps({"note": KEYSTORE_BODY}).encode("utf-8"))
    projects.add_file(pid, "settings.yaml",
                      ("region: us-east-1\ngithub_token: %s\nretries: 3\n" % TOKEN).encode("utf-8"))  # pragma: allowlist secret
    # A token that starts 10 characters before the excerpt's cut: cut first, it would leave a fragment.
    projects.add_file(pid, "straddle.txt", (("x" * 589 + " ") + TOKEN + " tail\n").encode("utf-8"))
    projects.add_file(pid, "id_rsa", PEM.encode("utf-8"))
    projects.add_file(pid, "photo.png", b"\x89PNG\r\n")
    conv = convs.create("Field notes")
    convs.patch(conv["id"], project=pid)
    return conv["id"]


@pytest.fixture
def block(monkeypatch, tmp_path):
    return projects.context_block(_project_with_files(monkeypatch, tmp_path))


def _line(block, name):
    """The one listing line of a file in the project block."""
    hits = [ln for ln in block.splitlines() if ln.startswith("- %s (" % name)]
    assert len(hits) == 1, (name, hits)
    return hits[0]


def test_the_project_block_carries_no_key_material(block):
    assert SENTINEL not in block, "the project block quoted a key's body"
    assert PEM_HEAD not in block and PEM_TAIL not in block, "the project block quoted a key's armor lines"
    assert TOKEN not in block, "the project block quoted a vendor token"
    assert TOKEN[:10] not in block, "the project block quoted a fragment of a vendor token"
    assert KEYSTORE_BODY not in block, "the project block quoted a key-material file's text"


@pytest.mark.parametrize("name", ["deploy.txt", "keystore.json"])
def test_a_text_file_that_is_key_material_is_listed_as_withheld(block, name):
    """By what it holds (deploy.txt) or by its name (keystore.json): the file is
    listed, with a note in place of the excerpt."""
    line = _line(block, name)
    assert line.endswith("withheld: key material)"), line


def test_a_token_in_a_quoted_file_is_withheld_in_place(block):
    line = _line(block, "settings.yaml")
    assert secret_patterns.WITHHELD in line
    assert "region: us-east-1" in line and "retries: 3" in line, "only the token is withheld, not the file"
    assert "withheld: key material" not in line
    assert not line.endswith("…"), "the whole file is quoted, so the excerpt does not say it was cut"


def test_a_token_that_straddles_the_cut_never_shows_as_a_fragment(block):
    line = _line(block, "straddle.txt")
    assert TOKEN[:10] not in line, "the cut left the start of a token in the excerpt"
    assert line.count("x") >= 580, "the excerpt still quotes the file's own text"


def test_ordinary_text_instructions_and_binary_files_stay_as_they_were(block):
    assert "- notes.md (24 bytes): The pier closes at dusk." in block
    assert "Answer like a city reporter." in block
    assert _line(block, "photo.png").endswith("bytes)") and "PNG" not in block, "a binary file is named, never quoted"
    assert _line(block, "id_rsa").endswith("bytes)"), "a file with no text extension is named, never quoted"
