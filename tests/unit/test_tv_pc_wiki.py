"""Wiki pages from an owner's phone or tablet (``pc_act``; phone workspaces parity PW-G4), against the desktop's REAL wiki
routes on a temporary wiki.

Rules guarded here, each with a case that fails if the check is removed:
* only an owner-bound phone or tablet holding ``pc_act`` may read the source, save or create (the job routes' guard);
* ``rev`` is the desktop's own page hash (LF line endings), so a page with Windows line endings has the same rev;
* a save with the current ``base_rev`` writes the page; a stale ``base_rev`` is a 409 ``conflict`` carrying the PC's
  current text and rev and the page on disk is untouched (two phones saving from the same rev: the second never
  overwrites the first);
* a new page never replaces an existing one; a page path that could leave the wiki never reaches the desktop;
* a locked-vault page is never read or written; oversized text is refused; the receipt names the ref, never the text.
"""
import hashlib
import json

import pytest
from flask import Flask

from agent_friday.services import approvals as ap
from agent_friday.services import device_pairing
from agent_friday.services import wiki_engine as we
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, pairing, pc_read, pc_wiki
from tests.ledger_support import rows
from tests.unit.test_tv_gateway import FakeTv, auth, open_session


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(household, "_path", lambda: tmp_path / "household.json")
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    monkeypatch.setattr(pc_read, "_AUDITED", {})
    monkeypatch.setattr(pairing, "_OFFERED", {})
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    household.ensure_default_members()
    yield
    device_pairing._reset_for_tests()


@pytest.fixture
def wiki(tmp_path, monkeypatch):
    """The desktop's real wiki routes over a temporary wiki, standing in for the PC's own app."""
    root = tmp_path / "wiki"
    (root / "people").mkdir(parents=True)
    monkeypatch.setattr(we, "WIKI_DIR", root)
    monkeypatch.setattr(we, "WIKI_PENDING_FILE", tmp_path / "wiki-pending.json")
    monkeypatch.setattr(we, "_wiki_mirror_dir", lambda: None)
    monkeypatch.setattr(we, "_wiki_encrypted_sections", lambda: set())
    from agent_friday import core
    from agent_friday.routes import wiki as rw
    desktop = Flask("fake_desktop")
    desktop.secret_key = "k" * 32
    desktop.register_blueprint(rw.wiki_bp)
    monkeypatch.setattr(rw, "WIKI_DIR", root)      # the routes import the folder by name
    monkeypatch.setattr(core, "app", desktop)
    monkeypatch.setattr(core, "_current_api_token", lambda: "t")
    return root


def page(root, ref, text, newline="\n"):
    path = root / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return path


def rev_of(text):
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


@pytest.fixture
def client():
    return gateway.create_app().test_client()


def device(kind="phone", member="stephen", rights=("pc_act",)):
    tv = FakeTv()
    device_pairing.add_device(tv.spki, name="A device", kind=kind)
    if member:
        household.bind_device(tv.id, member)
    for right in rights:
        device_pairing.set_right(tv.id, right, True)
    return tv


def call(client, tv, method, path, body=None):
    return client.open(path, method=method, json=body, headers=auth(open_session(client, tv)))


def no_desktop(monkeypatch):
    monkeypatch.setattr(pc_read, "_desk", lambda *a, **k: pytest.fail("the desktop must not be asked"))
    monkeypatch.setattr(pc_wiki, "_desk_send", lambda *a, **k: pytest.fail("the desktop must not be asked"))


JERE = "# Jere\nFather-in-law.\n"


def test_the_source_is_the_markdown_and_its_rev(client, wiki):
    page(wiki, "people/jere.md", JERE)
    answer = call(client, device(), "GET", "/tv/v1/wiki/source/people/jere.md")
    assert answer.status_code == 200
    assert answer.get_json()["markdown"] == JERE and answer.get_json()["rev"] == rev_of(JERE)


def test_a_page_with_windows_line_endings_has_the_same_rev(client, wiki):
    page(wiki, "people/jere.md", JERE, newline="\r\n")
    assert call(client, device(), "GET", "/tv/v1/wiki/source/people/jere.md").get_json()["rev"] == rev_of(JERE)


def test_a_save_with_the_current_rev_writes_the_page(client, wiki):
    path = page(wiki, "people/jere.md", JERE)
    new = "# Jere\nFather-in-law. Lives in Ohio.\n"
    answer = call(client, device(), "PUT", "/tv/v1/wiki/page/people/jere.md", {"base_rev": rev_of(JERE), "markdown": new})
    assert answer.status_code == 200 and answer.get_json()["rev"] == rev_of(new)
    assert path.read_text(encoding="utf-8").replace("\r\n", "\n") == new


def test_a_stale_rev_is_a_conflict_with_the_pcs_text_and_nothing_is_written(client, wiki):
    theirs = "# Jere\nChanged on the PC.\n"
    path = page(wiki, "people/jere.md", theirs)
    answer = call(client, device(), "PUT", "/tv/v1/wiki/page/people/jere.md", {"base_rev": rev_of(JERE), "markdown": "mine"})
    body = answer.get_json()
    assert answer.status_code == 409 and body["code"] == "conflict"
    assert body["current"] == {"markdown": theirs, "rev": rev_of(theirs)}
    assert path.read_text(encoding="utf-8") == theirs


def test_two_phones_saving_from_the_same_rev_never_overwrite_each_other(client, wiki):
    path = page(wiki, "people/jere.md", JERE)
    base = rev_of(JERE)
    first = call(client, device(), "PUT", "/tv/v1/wiki/page/people/jere.md", {"base_rev": base, "markdown": "first\n"})
    second = call(client, device(), "PUT", "/tv/v1/wiki/page/people/jere.md", {"base_rev": base, "markdown": "second\n"})
    assert first.status_code == 200 and second.status_code == 409
    assert path.read_text(encoding="utf-8") == "first\n"


def test_a_new_page_is_written_with_its_title_and_an_existing_one_is_never_replaced(client, wiki):
    tv = device()
    made = call(client, tv, "POST", "/tv/v1/wiki/page", {"path": "garden/beds.md", "title": "Garden beds", "markdown": "Tomatoes."})
    assert made.status_code == 200
    assert (wiki / "garden" / "beds.md").read_text(encoding="utf-8").startswith("# Garden beds")
    again = call(client, tv, "POST", "/tv/v1/wiki/page", {"path": "garden/beds.md", "title": "Other", "markdown": "overwrite"})
    assert again.status_code == 409 and again.get_json()["code"] == "exists"
    assert "overwrite" not in (wiki / "garden" / "beds.md").read_text(encoding="utf-8")


BAD_REFS = ["../SOUL.md", "people/../../SOUL.md", "people/.hidden.md", ".git/config.md", "people\\jere.md", "people/jere",
            "people/jere.exe", "/etc/passwd.md", "C:/x.md", "people/a:b.md", "a" * 300 + ".md"]


@pytest.mark.parametrize("ref", BAD_REFS)
def test_a_bad_ref_never_reaches_the_desktop(client, wiki, monkeypatch, ref):
    no_desktop(monkeypatch)
    tv = device()
    assert pc_wiki.valid_ref(ref) is False
    assert call(client, tv, "POST", "/tv/v1/wiki/page", {"path": ref, "title": "t", "markdown": "x"}).status_code == 400
    if not ref.startswith("/"):                     # a leading slash cannot be written in the URL form
        saved = call(client, tv, "PUT", "/tv/v1/wiki/page/" + ref, {"base_rev": "0" * 64, "markdown": "x"})
        assert saved.status_code in (400, 404)


def test_a_page_the_pc_does_not_have_is_a_plain_404(client, wiki):
    tv = device()
    assert call(client, tv, "GET", "/tv/v1/wiki/source/people/none.md").status_code == 404
    assert call(client, tv, "PUT", "/tv/v1/wiki/page/people/none.md", {"base_rev": "0" * 64, "markdown": "x"}).status_code == 404


def test_a_page_of_a_locked_vault_is_never_read_or_written(client, wiki):
    path = page(wiki, "people/secret.md", we.VAULT_LOCKED_PLACEHOLDER + " cipher")
    tv = device()
    assert call(client, tv, "GET", "/tv/v1/wiki/source/people/secret.md").get_json()["code"] == "locked"
    saved = call(client, tv, "PUT", "/tv/v1/wiki/page/people/secret.md", {"base_rev": "0" * 64, "markdown": "overwrite"})
    assert saved.status_code == 409 and saved.get_json()["code"] == "locked"
    assert "overwrite" not in path.read_text(encoding="utf-8")


def test_text_over_the_cap_is_refused(client, wiki):
    page(wiki, "people/jere.md", JERE)
    big = "x" * (pc_wiki.MAX_PAGE_BYTES + 1)
    assert call(client, device(), "PUT", "/tv/v1/wiki/page/people/jere.md", {"base_rev": rev_of(JERE), "markdown": big}).status_code == 400


@pytest.mark.parametrize("body", [{}, {"markdown": "x"}, {"base_rev": "nothex", "markdown": "x"}, {"base_rev": "0" * 64},
                                  {"base_rev": "0" * 64, "markdown": 5}])
def test_a_save_without_a_rev_and_text_of_the_right_shape_is_refused(client, wiki, body):
    page(wiki, "people/jere.md", JERE)
    assert call(client, device(), "PUT", "/tv/v1/wiki/page/people/jere.md", body).status_code == 400


CALLS = [("GET", "/tv/v1/wiki/source/people/jere.md", None),
         ("PUT", "/tv/v1/wiki/page/people/jere.md", {"base_rev": "0" * 64, "markdown": "x"}),
         ("POST", "/tv/v1/wiki/page", {"path": "people/new.md", "title": "t", "markdown": "x"})]


@pytest.mark.parametrize("method,path,body", CALLS)
def test_without_the_act_right_nothing_reaches_the_desktop_and_the_pc_asks(client, wiki, monkeypatch, method, path, body):
    no_desktop(monkeypatch)
    answer = call(client, device(rights=("pc_read",)), method, path, body)
    assert answer.status_code == 403 and answer.get_json()["code"] == "needs_right"


@pytest.mark.parametrize("method,path,body", CALLS)
@pytest.mark.parametrize("kind,member", [("watch", "stephen"), ("tv", "stephen"), ("phone", "family"), ("phone", None)])
def test_a_watch_a_tv_a_family_or_unbound_device_cannot_touch_the_wiki(client, wiki, monkeypatch, method, path, body,
                                                                       kind, member):
    no_desktop(monkeypatch)
    answer = call(client, device(kind, member), method, path, body)
    assert answer.status_code == 403 and answer.get_json()["code"] == "pc_act_not_for_this_device"


def test_the_receipt_names_the_page_and_never_its_text(client, wiki):
    page(wiki, "people/jere.md", JERE)
    call(client, device(), "PUT", "/tv/v1/wiki/page/people/jere.md", {"base_rev": rev_of(JERE), "markdown": "SECRET WORDS\n"})
    [receipt] = rows("device_act")
    fields = (receipt["action"], receipt["wiki"], receipt["outcome"], receipt["right"])
    assert fields == ("wiki_save", "people/jere.md", 200, "pc_act")
    assert "SECRET WORDS" not in json.dumps(receipt)


def test_every_desktop_path_the_wiki_routes_call_is_still_a_desktop_route():
    import pathlib
    import re
    root = pathlib.Path(pc_read.__file__).resolve().parents[1] / "routes"
    src = "\n".join(p.read_text(encoding="utf-8") for p in root.glob("*.py"))
    declared = set(re.findall(r"route\(\s*[\"'](/api/[^\"']+)[\"']", src))
    for path in ("/api/wiki/page", "/api/wiki/update", "/api/wiki/edit"):
        assert path in declared, path


def test_the_rev_returned_by_a_save_is_the_rev_of_what_was_written(client, wiki):
    page(wiki, "people/jere.md", JERE)
    tv = device()
    saved = call(client, tv, "PUT", "/tv/v1/wiki/page/people/jere.md", {"base_rev": rev_of(JERE), "markdown": "no newline at the end"})
    assert saved.status_code == 200
    assert saved.get_json()["rev"] == call(client, tv, "GET", "/tv/v1/wiki/source/people/jere.md").get_json()["rev"]


def test_the_rev_does_not_come_from_a_second_read_that_another_writer_could_change(client, wiki, monkeypatch):
    page(wiki, "people/jere.md", JERE)
    real_read = pc_wiki._read_page
    reads = []

    def read_then_someone_else_writes(ref):
        reads.append(ref)
        answer = real_read(ref)
        if len(reads) == 2:
            pytest.fail("the rev must not come from a second read")
        return answer
    monkeypatch.setattr(pc_wiki, "_read_page", read_then_someone_else_writes)
    saved = call(client, device(), "PUT", "/tv/v1/wiki/page/people/jere.md", {"base_rev": rev_of(JERE), "markdown": "new\n"})
    assert saved.status_code == 200 and len(reads) == 1


def test_a_page_removed_during_a_conflict_is_reported_as_removed(client, wiki, monkeypatch):
    path = page(wiki, "people/jere.md", "# Jere\nChanged.\n")

    def remove_then_stale(method, route, body):
        path.unlink()
        return 409, {}
    monkeypatch.setattr(pc_wiki, "_desk_send", remove_then_stale)
    answer = call(client, device(), "PUT", "/tv/v1/wiki/page/people/jere.md", {"base_rev": rev_of(JERE), "markdown": "mine\n"})
    assert answer.status_code == 404 and answer.get_json()["code"] == "no_page"


def test_an_empty_save_is_refused(client, wiki):
    path = page(wiki, "people/jere.md", JERE)
    answer = call(client, device(), "PUT", "/tv/v1/wiki/page/people/jere.md", {"base_rev": rev_of(JERE), "markdown": "  \n"})
    assert answer.status_code == 400 and answer.get_json()["code"] == "empty_page"
    assert path.read_text(encoding="utf-8") == JERE


def test_a_title_with_line_breaks_stays_one_heading_line(client, wiki):
    made = call(client, device(), "POST", "/tv/v1/wiki/page", {"path": "garden/x.md", "title": "Two\nlines", "markdown": "Body"})
    assert made.status_code == 200
    assert (wiki / "garden" / "x.md").read_text(encoding="utf-8").startswith("# Two lines\n")
