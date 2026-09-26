"""A seed image named by path is read only if its bytes are an image.

generate_video, generate_music and the vision QA gate accept a path the owner
or the model names and send the file's bytes to a cloud API. The path is the
owner's to choose, so it is not confined to a folder; what leaves the machine
is. A path aimed at a key file or a settings export must never be uploaded as
a "seed image".
"""
from __future__ import annotations


def test_seed_image_that_is_not_an_image_is_never_loaded(tmp_path):
    from agent_friday.services import creative_engine as ce
    from agent_friday.services import music_engine as me
    secret = tmp_path / "credentials.json"
    secret.write_text('{"token": "not-an-image"}', encoding="utf-8")
    assert ce._load_seed_image(str(secret)) == (None, None)
    assert me._load_seed(str(secret)) is None


def test_seed_image_that_is_an_image_still_loads(tmp_path):
    from agent_friday.services import creative_engine as ce
    img = tmp_path / "seed.bin"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    data, mime = ce._load_seed_image(str(img))
    assert data is not None and mime == "image/png"
