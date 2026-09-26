"""A seed image named by path is read only if its bytes are an image.

generate_video, generate_music and the vision QA gate send the file's bytes to
a cloud API. Where the file may come from is decided by services/seed_images
(tests/unit/test_seed_image_confinement.py); these tests run as an approved
call so only the second rule is under test: a path aimed at a key file or a
settings export is never uploaded as a "seed image", even when approved.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def approved():
    from agent_friday.governance import action_gate
    tok = action_gate.DECIDED.set("apr_test")
    yield
    action_gate.DECIDED.reset(tok)


def test_seed_image_that_is_not_an_image_is_never_loaded(tmp_path, approved):
    from agent_friday.services import creative_engine as ce
    from agent_friday.services import music_engine as me
    secret = tmp_path / "credentials.json"
    secret.write_text('{"token": "not-an-image"}', encoding="utf-8")
    assert ce._load_seed_image(str(secret)) == (None, None)
    assert me._load_seed(str(secret)) is None


def test_seed_image_that_is_an_image_still_loads(tmp_path, approved):
    from agent_friday.services import creative_engine as ce
    img = tmp_path / "seed.bin"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    data, mime = ce._load_seed_image(str(img))
    assert data is not None and mime == "image/png"
