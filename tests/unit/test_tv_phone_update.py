"""The phone's update contract: GET /tv/v1/phone/update and /apk?vc=N (friday-phone docs/PC-UPDATE.md).

Rules guarded here, each with a case that fails if the check is removed:
* only an owner phone or tablet holding ``pc_read`` is answered; without it the device raises that card and is told so;
* the offer carries every field the phone checks (package, form, version code and name, SHA-256, size, signing
  certificate SHA-256, acceptance "passed", built_at); a record lacking any of them, a bad form, or a file over 200 MB
  is ``available: false``, never a half offer; nothing staged is 200 ``available: false``;
* the apk is sent only for the staged version code (404 otherwise), only if its bytes still match the accepted hash,
  with Content-Length and the SHA-256, one receipt per download; no host path in any answer.
"""
import hashlib
import os
import pathlib
import re

import pytest

from agent_friday.services import approvals as ap
from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, pairing, phone_update
from tests.ledger_support import rows
from tests.unit.test_tv_app_update import BYTES, accept
from tests.unit.test_tv_gateway import FakeTv, auth, open_session

CERT = "ab" * 32
PHONE = {"package": "ai.futurespeak.friday.phone.flagship", "form": "phone", "version_name": "0.12.0",
         "cert_sha256": CERT, "built_at": 1795000000}


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(household, "_path", lambda: tmp_path / "household.json")
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    monkeypatch.setattr(pairing, "_OFFERED", {})
    apk_dir = tmp_path / "apks"
    apk_dir.mkdir()
    monkeypatch.setenv("FRIDAY_PHONE_APK_DIR", str(apk_dir))
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    household.ensure_default_members()
    yield
    device_pairing._reset_for_tests()


@pytest.fixture
def apks(tmp_path):
    return tmp_path / "apks"


@pytest.fixture
def client():
    return gateway.create_app().test_client()


def reader(kind="phone", member="stephen", rights=("pc_read",)):
    tv = FakeTv()
    device_pairing.add_device(tv.spki, name="A device", kind=kind)
    if member:
        household.bind_device(tv.id, member)
    for right in rights:
        device_pairing.set_right(tv.id, right, True)
    return tv


def get(client, tv, path, **kw):
    return client.get(path, headers={**auth(open_session(client, tv)), **kw.pop("headers", {})}, **kw)


def test_a_staged_accepted_build_is_offered_in_the_phones_shape(client, apks):
    accept(apks, **PHONE)
    answer = get(client, reader(), "/tv/v1/phone/update").get_json()
    assert answer == {"ok": True, "available": True, "package": PHONE["package"], "form": "phone", "version_code": 40,
                      "version_name": "0.12.0", "sha256": hashlib.sha256(BYTES).hexdigest(), "size": len(BYTES),
                      "cert_sha256": CERT, "acceptance": "passed", "built_at": 1795000000}


def test_nothing_staged_is_200_available_false(client, apks):
    answer = get(client, reader(), "/tv/v1/phone/update")
    assert answer.status_code == 200 and answer.get_json() == {"ok": True, "available": False}


@pytest.mark.parametrize("missing", ["package", "form", "version_name", "cert_sha256", "built_at"])
def test_a_record_without_a_field_the_phone_checks_is_not_offered(client, apks, missing):
    accept(apks, **{k: v for k, v in PHONE.items() if k != missing})
    assert get(client, reader(), "/tv/v1/phone/update").get_json() == {"ok": True, "available": False}


@pytest.mark.parametrize("over", [{"form": "watch"}, {"form": ""}, {"cert_sha256": "nothex"}, {"package": "nodots"},
                                  {"built_at": "yesterday"}, {"built_at": True}, {"version_code": "x"},
                                  {"version_code": 0}, {"version_code": -3}])
def test_a_malformed_field_is_not_offered(client, apks, over):
    accept(apks, **{**PHONE, **over})
    assert get(client, reader(), "/tv/v1/phone/update").get_json()["available"] is False


def test_an_empty_apk_is_not_offered(client, apks):
    accept(apks, data=b"", **PHONE)
    assert get(client, reader(), "/tv/v1/phone/update").get_json() == {"ok": True, "available": False}
    assert get(client, reader(), "/tv/v1/phone/update/apk?vc=40").status_code == 404


def test_a_build_over_200_mb_is_not_offered(client, apks, monkeypatch):
    accept(apks, **PHONE)
    monkeypatch.setattr(phone_update, "MAX_APK_BYTES", len(BYTES) - 1)
    assert get(client, reader(), "/tv/v1/phone/update").get_json()["available"] is False


def test_a_build_changed_after_acceptance_is_a_503_not_an_offer(client, apks):
    accept(apks, **PHONE)
    (apks / "agent-friday-0.12.0-accept-abc1234.apk").write_bytes(BYTES + b"x")
    answer = get(client, reader(), "/tv/v1/phone/update")
    assert answer.status_code == 503 and answer.get_json()["code"] == "apk_changed"


def test_the_apk_is_sent_for_the_staged_version_code_with_its_length_and_one_receipt(client, apks):
    accept(apks, **PHONE)
    answer = get(client, reader(), "/tv/v1/phone/update/apk?vc=40")
    assert answer.status_code == 200 and answer.data == BYTES
    assert answer.mimetype == "application/vnd.android.package-archive"
    assert answer.headers["Content-Length"] == str(len(BYTES)) and answer.headers["Cache-Control"] == "no-store"
    assert answer.headers["X-Friday-SHA256"] == hashlib.sha256(BYTES).hexdigest()
    assert len(rows("device_app_download")) == 1


@pytest.mark.parametrize("query", ["", "?vc=39", "?vc=41", "?vc=x", "?vc=-40", "?vc=040x", "?vc=%C2%B2",
                                   "?vc=%D9%A4%D9%A0", "?vc=" + "9" * 11])
def test_any_other_version_code_is_a_404(client, apks, query):
    accept(apks, **PHONE)
    answer = get(client, reader(), "/tv/v1/phone/update/apk" + query)
    assert answer.status_code == 404 and answer.get_json()["code"] == "not_the_staged_build"
    assert rows("device_app_download") == []


def test_an_incomplete_record_serves_no_apk_either(client, apks):
    accept(apks)
    assert get(client, reader(), "/tv/v1/phone/update/apk?vc=40").status_code == 404


def test_a_changed_apk_is_not_sent(client, apks):
    accept(apks, **PHONE)
    (apks / "agent-friday-0.12.0-accept-abc1234.apk").write_bytes(BYTES + b"x")
    assert get(client, reader(), "/tv/v1/phone/update/apk?vc=40").status_code == 503


def test_a_range_resumes_and_is_not_a_second_download(client, apks):
    accept(apks, **PHONE)
    answer = get(client, reader(), "/tv/v1/phone/update/apk?vc=40", headers={"Range": "bytes=100-199"})
    assert answer.status_code == 206 and answer.data == BYTES[100:200] and rows("device_app_download") == []


def test_a_head_or_a_one_byte_probe_is_not_a_download(client, apks):
    accept(apks, **PHONE)
    tv = reader()
    headers = auth(open_session(client, tv))
    assert client.head("/tv/v1/phone/update/apk?vc=40", headers=headers).status_code == 200
    assert client.get("/tv/v1/phone/update/apk?vc=40", headers={**headers, "Range": "bytes=0-0"}).status_code == 206
    assert rows("device_app_download") == []


@pytest.mark.parametrize("path", ["/tv/v1/phone/update", "/tv/v1/phone/update/apk?vc=40"])
def test_without_the_read_right_the_pc_asks_and_the_phone_is_told(client, apks, path):
    accept(apks, **PHONE)
    answer = get(client, reader(rights=()), path)
    assert answer.status_code == 403 and answer.get_json()["code"] == "needs_right"


@pytest.mark.parametrize("path", ["/tv/v1/phone/update", "/tv/v1/phone/update/apk?vc=40"])
@pytest.mark.parametrize("kind,member", [("watch", "stephen"), ("tv", "stephen"), ("phone", "family"), ("phone", None)])
def test_a_watch_a_tv_a_family_or_unbound_device_is_refused(client, apks, path, kind, member):
    accept(apks, **PHONE)
    assert get(client, reader(kind, member), path).status_code == 403


def test_a_tablet_form_build_is_offered_with_its_form(client, apks):
    accept(apks, **{**PHONE, "form": "tablet"})
    assert get(client, reader("tablet"), "/tv/v1/phone/update").get_json()["form"] == "tablet"


def test_no_host_path_is_in_any_answer(client, apks):
    accept(apks, **PHONE)
    tv = reader()
    for path in ("/tv/v1/phone/update", "/tv/v1/phone/update/apk?vc=39"):
        text = get(client, tv, path).get_data(as_text=True)
        assert str(apks) not in text and "LATEST.json" not in text


def test_the_tailnet_door_offers_it_and_the_receipt_says_so(apks):
    accept(apks, **PHONE)
    tv = reader()
    tail = gateway.create_app(tailnet=True).test_client()
    env = {"REMOTE_ADDR": "100.70.222.100"}
    nonce = tail.post("/tv/v1/session/challenge", environ_overrides=env).get_json()["nonce"]
    token = tail.post("/tv/v1/session/open", environ_overrides=env,
                      json={"device_id": tv.id, "nonce": nonce, "sig": tv.sign(nonce)}).get_json()["session"]
    assert tail.get("/tv/v1/phone/update/apk?vc=40", environ_overrides=env, headers=auth(token)).status_code == 200
    assert rows("device_app_download")[-1]["via"] == "tailnet"


# The phone app's own description of the offer (friday-phone docs/PC-UPDATE.md). The copy below keeps its shape under
# test where that repo is not checked out; where it is, the first test fails if the phone's doc and this copy drift.
DOC_EXAMPLE = (
    '{"ok":true,"available":true,"package":"ai.futurespeak.friday.phone.flagship","form":"phone","version_code":33,'
    '"version_name":"0.12.0","sha256":"<64 hex>","size":<bytes>,"cert_sha256":"<64 hex>","acceptance":"passed",'
    '"built_at":<epoch s>}')
DOC_NOTHING_STAGED = '{"ok":true,"available":false}'


def _doc_folders() -> list:
    """Where the phone repo's docs may be: FRIDAY_PHONE_DOCS, else ~/Projects/friday-phone/docs under the real home
    (the tests move HOME; tests/conftest.py keeps the real one in FRIDAY_REAL_HOME)."""
    real_home = pathlib.Path(os.environ.get("FRIDAY_REAL_HOME") or pathlib.Path.home())
    folders = [pathlib.Path(os.environ["FRIDAY_PHONE_DOCS"])] if os.environ.get("FRIDAY_PHONE_DOCS") else []
    return folders + [real_home / "Projects" / "friday-phone" / "docs"]


def _phone_doc():
    for folder in _doc_folders():
        if (folder / "PC-UPDATE.md").is_file():
            return (folder / "PC-UPDATE.md").read_text(encoding="utf-8")
    return None


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def test_the_example_copied_here_is_the_one_in_the_phone_doc():
    doc = _phone_doc()
    if doc is None:
        pytest.skip("PC-UPDATE.md not found in: " + ", ".join(str(folder) for folder in _doc_folders()))
    assert _squash(DOC_EXAMPLE) in _squash(doc) and _squash(DOC_NOTHING_STAGED) in _squash(doc)


def _kinds(example: str) -> dict:
    """Each field of the doc's example as what it must be: a literal, 'hex64', or 'int' (a placeholder)."""
    kinds = {}
    for key, raw in re.findall(r'"(\w+)":\s*("(?:[^"]*)"|[^,}\s]+)', example):
        if raw == '"<64 hex>"':
            kinds[key] = "hex64"
        elif raw in ("<bytes>", "<epoch s>"):
            kinds[key] = "int"
        elif raw.startswith('"'):
            kinds[key] = raw.strip('"')
        elif raw in ("true", "false"):
            kinds[key] = raw == "true"
        else:
            kinds[key] = "int"
    return kinds


def test_the_offer_has_the_docs_fields_and_kinds_and_nothing_else(client, apks):
    accept(apks, **PHONE)
    offer = get(client, reader(), "/tv/v1/phone/update").get_json()
    expected = _kinds(DOC_EXAMPLE)
    assert set(offer) == set(expected)
    for key, kind in expected.items():
        if kind == "hex64":
            assert re.fullmatch(r"[0-9a-f]{64}", offer[key]), key
        elif kind == "int":
            assert isinstance(offer[key], int) and not isinstance(offer[key], bool), key
        elif key in ("ok", "available", "acceptance", "package", "form"):
            assert offer[key] == kind, key


def test_nothing_staged_is_exactly_the_docs_answer(client, apks):
    import json
    assert get(client, reader(), "/tv/v1/phone/update").get_json() == json.loads(DOC_NOTHING_STAGED)


# -- one record per form ------------------------------------------------------------------------------------------

TABLET = {**PHONE, "form": "tablet", "version_name": "0.12.0-tablet"}


def both_staged(apks):
    accept(apks, name="phone-build.apk", manifest="LATEST-phone.json", version_code=41, **PHONE)
    accept(apks, name="tablet-build.apk", manifest="LATEST-tablet.json", version_code=42, **TABLET)


def test_a_phone_and_a_tablet_are_each_offered_their_own_build(client, apks):
    both_staged(apks)
    phone = get(client, reader("phone"), "/tv/v1/phone/update").get_json()
    tablet = get(client, reader("tablet"), "/tv/v1/phone/update").get_json()
    assert (phone["form"], phone["version_code"]) == ("phone", 41)
    assert (tablet["form"], tablet["version_code"]) == ("tablet", 42)


def test_each_form_downloads_only_its_own_version_code(client, apks):
    both_staged(apks)
    phone, tablet = reader("phone"), reader("tablet")
    assert get(client, phone, "/tv/v1/phone/update/apk?vc=41").status_code == 200
    assert get(client, phone, "/tv/v1/phone/update/apk?vc=42").status_code == 404
    assert get(client, tablet, "/tv/v1/phone/update/apk?vc=42").status_code == 200
    assert get(client, tablet, "/tv/v1/phone/update/apk?vc=41").status_code == 404


def test_a_form_with_no_record_is_offered_nothing_not_the_other_forms_build(client, apks):
    accept(apks, name="phone-build.apk", manifest="LATEST-phone.json", **PHONE)
    assert get(client, reader("tablet"), "/tv/v1/phone/update").get_json() == {"ok": True, "available": False}


def test_the_single_latest_record_is_offered_only_to_its_own_form(client, apks):
    accept(apks, **PHONE)
    assert get(client, reader("phone"), "/tv/v1/phone/update").get_json()["available"] is True
    assert get(client, reader("tablet"), "/tv/v1/phone/update").get_json() == {"ok": True, "available": False}


def test_a_per_form_record_naming_the_other_form_is_not_offered(client, apks):
    accept(apks, name="wrong.apk", manifest="LATEST-tablet.json", **PHONE)
    assert get(client, reader("tablet"), "/tv/v1/phone/update").get_json() == {"ok": True, "available": False}
