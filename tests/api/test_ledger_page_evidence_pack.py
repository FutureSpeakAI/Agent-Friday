"""The Export control on the ledger page (K6 PR C, the part the owner presses).

The page is static/ledger.html. These tests hold that the control the owner sees is wired to the real route: the
form names the two days the route takes, the page posts where the route lives, the one fixed sentence is on the page,
and a press built from the page's own form fields gets a real zip back. Nothing is checked against a stand-in."""
import io
import re
import zipfile
from pathlib import Path

from tests.api.test_evidence_pack_route import _day, _press, _seed

PAGE = (Path(__file__).resolve().parents[2] / "static" / "ledger.html").read_text(encoding="utf-8")
SECTION = re.search(r'<section[^>]*id="evidence-pack".*?</section>', PAGE, re.DOTALL).group(0)
SENTENCE = ("This export supports ISO 27001 evidence-gathering. "
            "It is not a certification and makes no compliance claim.")


def test_the_form_asks_for_the_two_days_the_route_takes_and_both_are_required():
    fields = re.findall(r'<input name="(\w+)" type="date"( required)?', SECTION)
    assert fields == [("since", " required"), ("until", " required")]


def test_the_page_posts_to_the_route_that_exists_and_the_fixed_sentence_is_on_it_once():
    assert "fetch('/api/ledger/evidence-pack'" in PAGE
    from agent_friday import core
    assert any(r.rule == "/api/ledger/evidence-pack" and "POST" in r.methods for r in core.app.url_map.iter_rules())
    assert SECTION.count(SENTENCE) == 1


def test_a_press_built_from_the_pages_own_fields_gets_a_zip(client):
    _seed()
    body = {name: _day() for name, _ in re.findall(r'<input name="(\w+)" type="date"( required)?', SECTION)}
    r = client.post("/api/ledger/evidence-pack", json=body, headers=_press())
    assert r.status_code == 200 and r.headers["Content-Type"] == "application/zip"
    assert "manifest.json" in zipfile.ZipFile(io.BytesIO(r.data)).namelist()


def test_the_page_names_the_download_from_the_answer_and_says_what_failed():
    assert "Content-Disposition" in PAGE and "nothing was exported" in PAGE
