"""The Settings export button produces the same data export as `friday export`:
the owner's data, without the keystore root key or any credential file."""
from __future__ import annotations

import io
import zipfile

import pytest

from agent_friday.core import FRIDAY_DIR

PLANTED = {
    "security/keystore.json": True,
    "providers/keys/zz-export-test.key": True,
    "secret_key": True,
    "wiki/zz-export-test/page.md": False,
}


@pytest.fixture
def planted():
    made = []
    for rel in PLANTED:
        p = FRIDAY_DIR / rel
        if p.exists():
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("planted", encoding="utf-8")
        made.append(p)
    yield
    for p in made:
        p.unlink(missing_ok=True)


def test_settings_export_has_data_and_no_keys(client, planted):
    r = client.get("/api/data/export")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.data)) as zf:
        names = {n.replace("\\", "/") for n in zf.namelist()}
    for rel, secret in PLANTED.items():
        assert ((".friday/" + rel) in names) is (not secret), rel
