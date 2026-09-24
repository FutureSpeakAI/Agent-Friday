"""Settings for PDF signing over HTTP: local only, secrets in and never out."""
import base64
import io
import json

import pytest

from agent_friday.services import pdf_signing


@pytest.fixture
def signing_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    return tmp_path


def _png_b64() -> str:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (40, 20), (0, 0, 0, 255)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_the_blueprint_is_registered(app):
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/documents/signing/status" in rules
    assert "/api/documents/signing/certificate" in rules


def test_an_image_goes_in_and_only_its_status_comes_back(client, signing_home):
    b64 = _png_b64()
    r = client.post("/api/documents/signing/image", json={"image_b64": b64})
    assert r.status_code == 200, r.get_data(as_text=True)
    raw = r.get_data(as_text=True)
    assert b64 not in raw and json.loads(raw)["signature_image"] == "stored"
    assert client.get("/api/documents/signing/status").get_json()["signature_image"] == "stored"
    r = client.delete("/api/documents/signing/image")
    assert r.get_json()["signature_image"] == "missing"


def test_a_bad_certificate_is_refused(client, signing_home):
    r = client.post("/api/documents/signing/certificate",
                    json={"certificate_b64": base64.b64encode(b"nope").decode(),
                          "passphrase": "x"})
    assert r.status_code == 400
    assert pdf_signing.status()["certificate"]["state"] == "missing"


def test_a_form_post_is_refused(client, signing_home):
    r = client.post("/api/documents/signing/image", data={"image_b64": _png_b64()})
    assert r.status_code == 415
    assert pdf_signing.status()["signature_image"] == "missing"


@pytest.mark.parametrize("headers", [
    {"CF-Connecting-IP": "203.0.113.9"},
    {"X-Forwarded-For": "203.0.113.9"},
])
def test_a_tunnelled_request_cannot_set_a_signature(app, signing_home, headers):
    c = app.test_client()
    r = c.post("/api/documents/signing/image", json={"image_b64": _png_b64()},
               headers=headers, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code in (401, 403)
    assert pdf_signing.status()["signature_image"] == "missing"


def test_an_authenticated_remote_session_is_still_refused(app, signing_home, monkeypatch):
    from agent_friday.routes import documents
    monkeypatch.setattr(documents, "_is_local_request", lambda: False)
    r = app.test_client().post("/api/documents/signing/image", json={"image_b64": _png_b64()})
    assert r.status_code == 403
    assert pdf_signing.status()["signature_image"] == "missing"
