"""The phone's "Made by Friday" shelf asks the PC for what Friday made (MOBILE_FULL_PARITY_STORM MP-2; Q19, 2026-11-26).

* ``made_by=friday`` keeps a card whose maker names Friday or one of her engines and drops the owner's own ("You") and an
  unknown maker, BEFORE paging, so the shelf can page through every creation instead of filtering the first 50 on the phone;
* the gateway passes ``made_by`` to the desktop and nothing else new.
"""
import pytest

import agent_friday.core as core
from agent_friday.services import media_index as mi
from tests.unit.test_tv_pc_read import client, desk, get, paired, _clean  # noqa: F401 (fixtures)


@pytest.mark.parametrize("maker, made", [
    ("Friday · this PC", True), ("claude-opus-5-5", True), ("Office engine · this PC", True), ("Composer · this PC", True),
    ("Local model · bonsai2:27b", True), ("You", False), ("You · this PC", False), ("you", False), ("", False), ("  ·  ", False),
])
def test_who_counts_as_friday(maker, made):
    assert mi.made_by_friday(maker) is made


@pytest.fixture
def index(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path / ".friday")
    con = mi._connect()
    try:
        for i in range(60):
            mi._upsert(con, mi._card(id="you%02d" % i, title="mine %d" % i, maker="You", when_ts=2000.0 + i))
        for i in range(7):
            mi._upsert(con, mi._card(id="fri%02d" % i, title="front page %d" % i, maker="Friday · this PC", when_ts=1000.0 + i))
        con.commit()
    finally:
        con.close()


def test_made_by_friday_filters_before_paging(index):
    first = mi.query(made_by="friday", sort="newest", limit=5, offset=0)
    rest = mi.query(made_by="friday", sort="newest", limit=5, offset=5)
    assert first["total"] == 7 and [c["id"] for c in first["cards"]] == ["fri06", "fri05", "fri04", "fri03", "fri02"]
    assert [c["id"] for c in rest["cards"]] == ["fri01", "fri00"]


def test_without_made_by_the_list_is_unchanged(index):
    assert mi.query(limit=0)["total"] == 67


def test_the_gateway_passes_made_by_and_the_offset_to_the_desktop(client, desk):
    get(client, paired(), "/tv/v1/media?made_by=friday&offset=50&limit=50&sort=newest")
    path, params = desk.calls[0]
    assert path == "/api/media" and params == {"made_by": "friday", "offset": "50", "limit": 50, "sort": "newest"}


def test_a_document_counts_as_fridays_only_when_the_office_engine_recorded_making_it(tmp_path, monkeypatch):
    from agent_friday.services import office_engine
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path / ".friday")
    docs = tmp_path / "documents"
    docs.mkdir()
    monkeypatch.setattr(office_engine, "DOCUMENTS_DIR", docs)
    (docs / "fridays.docx").write_bytes(b"x")
    (docs / "owners own.docx").write_bytes(b"y")
    office_engine._record_made([docs / "fridays.docx"])
    con = mi._connect()
    try:
        mi._scan_documents(con)
        con.commit()
    finally:
        con.close()
    assert [c["title"] for c in mi.query(made_by="friday")["cards"]] == ["Fridays"]
    assert mi.query(limit=0)["total"] == 2
