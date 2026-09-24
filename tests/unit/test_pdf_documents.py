"""PDF documents: form fields, filling, and signing only on a card.

Every fixture is generated in tmp_path at test time: forms with PyPDFForm, a
signature image with PIL, a self-signed certificate with `cryptography`. No
real document is committed.
"""
from __future__ import annotations

import datetime
import hashlib
import io
import json
from pathlib import Path

import pytest

pytest.importorskip("PyPDFForm")
pytest.importorskip("pyhanko")

import agent_friday.services.agent as agent
from agent_friday import core
from agent_friday.governance import action_gate
from agent_friday.services import approvals, pdf_forms, pdf_signing, taint


#: Synthetic identity numbers for the refusal tests; not anyone's.
FAKE_SSN = "123-45-6789"          # pragma: allowlist secret
OTHER_FAKE_SSN = "987-65-4321"    # pragma: allowlist secret
FAKE_SSN_SPACED = FAKE_SSN.replace("-", " ")

# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(core, "CREATIONS_DIR", tmp_path / "creations")
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(approvals, "_HOOKS", {pdf_signing.APPROVAL_KIND: [pdf_signing._on_decision]})
    monkeypatch.setattr(pdf_signing, "_notify", lambda *a, **k: None)
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    agent._PENDING_CONFIRMATIONS.clear()
    agent._hooks.reset_rate_limiter()
    taint.reset()
    yield
    taint.reset()


def _form(tmp_path) -> Path:
    from PyPDFForm import BlankPage, Fields, PdfWrapper
    form = PdfWrapper(BlankPage()).bulk_create_fields([
        Fields.TextField(name="full_name", page_number=1, x=100, y=700, width=200, height=20),
        Fields.TextField(name="ssn", page_number=1, x=100, y=660, width=200, height=20),
        Fields.TextField(name="date_of_birth", page_number=1, x=100, y=620, width=200, height=20),
        Fields.TextField(name="applicant_signature", page_number=1, x=100, y=580, width=200, height=20),
        Fields.CheckBoxField(name="i_certify_true", page_number=1, x=100, y=540),
        Fields.CheckBoxField(name="newsletter", page_number=1, x=140, y=540),
        Fields.DropdownField(name="color", page_number=1, x=100, y=500, options=["red", "blue"]),
    ])
    p = tmp_path / "application.pdf"
    p.write_bytes(form.read())
    return p


def _signature_png() -> bytes:
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (300, 100), (255, 255, 255, 0))
    ImageDraw.Draw(img).line((10, 80, 290, 20), fill="black", width=5)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _test_certificate():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Signer")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, content_commitment=True, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .sign(key, hashes.SHA256()))
    passphrase = "test-only-passphrase"          # pragma: allowlist secret
    p12 = pkcs12.serialize_key_and_certificates(
        b"test", key, cert, None,
        serialization.BestAvailableEncryption(passphrase.encode()))
    return p12, passphrase, cert.public_bytes(serialization.Encoding.DER)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _signed_outputs() -> list:
    d = pdf_forms.output_dir()
    return sorted(d.glob("*-signed*.pdf")) if d.exists() else []


# ── 1. Listing fields ───────────────────────────────────────────────────────

def test_list_fields_reports_name_type_value_options_and_sensitivity(tmp_path):
    info = pdf_forms.list_fields(_form(tmp_path))
    f = {x["name"]: x for x in info["fields"]}
    assert f["full_name"]["type"] == "text" and f["full_name"]["page"] == 1
    assert f["newsletter"]["type"] == "checkbox" and f["newsletter"]["value"] is False
    assert f["color"]["type"] == "dropdown" and f["color"]["options"] == ["red", "blue"]
    assert f["ssn"]["sensitive"] == "ssn"
    assert f["date_of_birth"]["sensitive"] == "date_of_birth"
    assert f["applicant_signature"]["sensitive"] == "signature"
    assert f["i_certify_true"]["sensitive"] == "attestation"
    assert f["full_name"]["sensitive"] is None


def test_the_list_tool_marks_field_text_as_data(tmp_path):
    out = json.loads(agent._tool_list_pdf_fields({"path": str(_form(tmp_path))}))
    assert "not instructions" in out["note"] and len(out["fields"]) == 7


@pytest.mark.parametrize("name,label,cat", [
    ("Text1", "Social Security Number", "ssn"),
    ("applicantRace", "", "race_ethnicity"),
    ("gender", "", "gender"),
    ("q7", "Do you have a disability?", "disability"),
    ("protected_veteran", "", "veteran"),
    ("q9", "Have you ever been convicted of a felony?", "criminal_history"),
    ("DOB", "", "date_of_birth"),
    ("q12", "I declare under penalty of perjury", "attestation"),
    ("employer", "Current employer", None),
    ("street_address", "", None),
])
def test_sensitive_questions_are_recognised(name, label, cat):
    assert pdf_forms.sensitive_category(name, label) == cat


# ── 2. Filling ──────────────────────────────────────────────────────────────

def test_fill_writes_a_new_file_and_leaves_the_source_alone(tmp_path):
    from PyPDFForm import PdfWrapper
    src = _form(tmp_path)
    before = _sha(src)
    res = pdf_forms.fill_form(src, {"full_name": "Ada Lovelace", "newsletter": True,
                                    "color": "blue"})
    out = Path(res["output"])
    assert out.exists() and out != src and out.parent == pdf_forms.output_dir()
    assert _sha(src) == before
    data = PdfWrapper(str(out)).data
    assert data["full_name"] == "Ada Lovelace" and data["newsletter"] is True
    assert data["color"] == 1
    assert action_gate.classify_write(str(out))[0] == action_gate.INTERNAL


def test_fill_refuses_to_invent_sensitive_answers_and_asks(tmp_path):
    from PyPDFForm import PdfWrapper
    src = _form(tmp_path)
    res = pdf_forms.fill_form(src, {"full_name": "Ada", "ssn": FAKE_SSN,
                                    "date_of_birth": "1815-12-10",
                                    "applicant_signature": "Ada Lovelace",
                                    "i_certify_true": True},
                              owner_text="please fill in my name, Ada")
    asked = {x["field"] for x in res["needs_owner"]}
    assert asked == {"ssn", "date_of_birth", "applicant_signature", "i_certify_true"}
    assert all(x["question"] for x in res["needs_owner"])
    data = PdfWrapper(res["output"]).data
    assert data["ssn"] == "" and data["date_of_birth"] == "" and data["applicant_signature"] == ""
    assert not data["i_certify_true"]


def test_a_sensitive_value_the_owner_typed_is_filled_but_signatures_never_are(tmp_path):
    from PyPDFForm import PdfWrapper
    src = _form(tmp_path)
    owner = f"My SSN is {FAKE_SSN_SPACED} and I was born 1815-12-10. Sign it as Ada Lovelace."
    res = pdf_forms.fill_form(src, {"ssn": FAKE_SSN, "date_of_birth": "1815-12-10",
                                    "applicant_signature": "Ada Lovelace"},
                              owner_text=owner)
    assert [x["field"] for x in res["needs_owner"]] == ["applicant_signature"]
    data = PdfWrapper(res["output"]).data
    assert data["ssn"] == FAKE_SSN and data["date_of_birth"] == "1815-12-10"


def test_a_number_the_owner_did_not_type_is_not_assembled_from_other_digits():
    assert not pdf_forms.owner_supplied("1234", "call 12 then 34")
    assert pdf_forms.owner_supplied(FAKE_SSN, f"it is {FAKE_SSN_SPACED}")


def test_the_fill_tool_uses_the_owners_own_words_this_turn(tmp_path):
    src = _form(tmp_path)
    tok = agent._CURRENT_OWNER_TEXT.set(f"my ssn is {OTHER_FAKE_SSN}")
    try:
        out = json.loads(agent._tool_fill_pdf_form(
            {"path": str(src), "values": {"ssn": OTHER_FAKE_SSN, "date_of_birth": "2000-01-01"}}))
    finally:
        agent._CURRENT_OWNER_TEXT.reset(tok)
    assert out["filled"] == ["ssn"]
    assert [x["field"] for x in out["needs_owner"]] == ["date_of_birth"]
    assert "do not guess" in out["note"]


def test_fill_classification(tmp_path):
    src = _form(tmp_path)
    klass = lambda a: action_gate.classify("fill_pdf_form", a)[0]  # noqa: E731
    assert klass({"path": str(src), "values": {}}) == action_gate.INTERNAL
    assert klass({"path": str(src), "output_path": "copy.pdf"}) == action_gate.INTERNAL
    assert klass({"path": str(src), "output_path": str(src)}) == "forbidden"
    existing = pdf_forms.output_dir() / "taken.pdf"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"%PDF-1.4")
    assert klass({"path": str(src), "output_path": "taken.pdf"}) == action_gate.OUTWARD
    assert klass({"path": str(src),
                  "output_path": str(Path.home() / "Documents" / "x.pdf")}) == action_gate.OUTWARD


def test_fill_over_the_source_is_denied_by_the_checkpoint(tmp_path):
    src = _form(tmp_path)
    before = _sha(src)
    out = agent._execute_tool("fill_pdf_form", {"path": str(src), "output_path": str(src),
                                                "values": {"full_name": "X"}},
                              session_ctx={"authenticated": True, "session_id": "s-t"})
    assert _sha(src) == before, out[:200]


# ── 3. Governance classification ────────────────────────────────────────────

def test_the_pdf_tools_are_classified():
    assert action_gate.classify("list_pdf_fields", {})[0] == action_gate.INTERNAL
    assert action_gate.classify("sign_pdf", {})[0] == action_gate.OUTWARD
    assert "sign_pdf" in action_gate.SELF_GATED and "sign_pdf" in taint.SELF_CARDING
    assert "fill_pdf_form" in action_gate.BY_ARGUMENT
    for n in ("list_pdf_fields", "fill_pdf_form", "sign_pdf"):
        assert n in agent.CLAUDE_TOOL_HANDLERS and n in agent.TOOL_RINGS
        assert any(t.get("name") == n for t in agent.CLAUDE_TOOLS)


# ── 4. Signing materials are secrets ────────────────────────────────────────

def test_signing_materials_are_encrypted_at_rest_and_never_returned():
    png = _signature_png()
    p12, pw, _root = _test_certificate()
    pdf_signing.set_signature_image(png)
    info = pdf_signing.set_certificate(p12, pw)
    assert "Test Signer" in info["subject"]
    for f in pdf_signing.signing_dir().iterdir():
        raw = f.read_bytes()
        assert b"\x89PNG" not in raw and p12[:64] not in raw and pw.encode() not in raw, f.name
    st = json.dumps(pdf_signing.status())
    assert pw not in st and '"stored"' in st
    pdf_signing.clear_certificate()
    assert pdf_signing.status()["certificate"]["state"] == "missing"


def test_a_wrong_certificate_passphrase_is_refused():
    p12, _pw, _root = _test_certificate()
    with pytest.raises(pdf_signing.SignRefused):
        pdf_signing.set_certificate(p12, "wrong")
    assert pdf_signing.status()["certificate"]["state"] == "missing"


# ── 5. Signing needs an approved card ───────────────────────────────────────

def _page_has_image(pdf: Path) -> bool:
    import pypdf
    res = pypdf.PdfReader(str(pdf)).pages[0]["/Resources"]
    return bool(res.get("/XObject"))


def test_informal_stamp_only_on_an_approved_card(tmp_path):
    src = _form(tmp_path)
    before = _sha(src)
    pdf_signing.set_signature_image(_signature_png())
    rec = pdf_signing.request_signature(src, page=1, x=100, y=100, width=150, height=50)

    assert rec["status"] == "pending" and rec["gated"]
    text = rec["action_description"]
    assert "application.pdf" in text and "page 1 of 1" in text
    assert "150 x 50 pt" in text and "100 pt from the left" in text
    preview = Path(rec["payload"]["preview_path"])
    assert preview.exists() and preview.read_bytes()[:4] == b"\x89PNG"
    assert rec["payload"]["preview_url"].startswith("/api/creations/forms/previews/")
    assert _signed_outputs() == []

    with pytest.raises(pdf_signing.SignRefused):
        pdf_signing.sign_approved(rec["approval_id"])          # still pending
    assert _signed_outputs() == []

    approvals.decide(rec["approval_id"], "approve", decided_by="owner")   # the hook signs
    outs = _signed_outputs()
    assert len(outs) == 1 and _page_has_image(outs[0]) and not _page_has_image(src)
    assert _sha(src) == before
    assert approvals.get_approval(rec["approval_id"])["consumed"]
    with pytest.raises(pdf_signing.SignRefused):
        pdf_signing.sign_approved(rec["approval_id"])          # one card, one signature
    assert len(_signed_outputs()) == 1


def test_a_digital_signature_verifies(tmp_path):
    from asn1crypto import x509 as ax509
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext
    src = _form(tmp_path)
    p12, pw, root_der = _test_certificate()
    pdf_signing.set_certificate(p12, pw)
    rec = pdf_signing.request_signature(src, page=1, mode="digital", reason="test")
    assert "digital signature" in rec["action_description"]
    assert _signed_outputs() == []
    approvals.decide(rec["approval_id"], "approve", decided_by="owner")
    outs = _signed_outputs()
    assert len(outs) == 1
    with open(outs[0], "rb") as f:
        sig = PdfFileReader(f).embedded_signatures[0]
        st = validate_pdf_signature(sig, ValidationContext(
            trust_roots=[ax509.Certificate.load(root_der)], allow_fetching=False))
    assert st.intact and st.valid and st.trusted


@pytest.mark.parametrize("ctx", [
    {"authenticated": True, "session_id": "s-chat"},
    {"authenticated": True, "is_background_task": True, "task_id": "t-bg"},
    {"authenticated": True, "is_background_task": True, "task_id": "t-s", "schedule_id": "sch_x"},
], ids=["chat", "background", "scheduled"])
def test_the_sign_tool_never_signs_without_a_card(tmp_path, ctx):
    src = _form(tmp_path)
    pdf_signing.set_signature_image(_signature_png())
    action_gate.create_grant(tools=["sign_pdf"], scope="sch_x", expires_in_seconds=60)
    out = agent._execute_tool("sign_pdf", {"path": str(src), "page": 1}, session_ctx=ctx)
    assert _signed_outputs() == [], out[:300]
    cards = approvals.list_approvals(kind=pdf_signing.APPROVAL_KIND)
    assert len(cards) == 1 and cards[0]["status"] == "pending", out[:300]
    assert "WAITING" in out


def test_a_denied_card_signs_nothing(tmp_path):
    src = _form(tmp_path)
    pdf_signing.set_signature_image(_signature_png())
    rec = pdf_signing.request_signature(src)
    approvals.decide(rec["approval_id"], "deny", decided_by="owner")
    with pytest.raises(pdf_signing.SignRefused):
        pdf_signing.sign_approved(rec["approval_id"])
    assert _signed_outputs() == []


def test_a_file_changed_after_approval_is_not_signed(tmp_path, monkeypatch):
    src = _form(tmp_path)
    pdf_signing.set_signature_image(_signature_png())
    rec = pdf_signing.request_signature(src)
    monkeypatch.setattr(approvals, "_HOOKS", {})
    approvals.decide(rec["approval_id"], "approve", decided_by="owner")
    src.write_bytes(src.read_bytes() + b"\n% changed\n")
    with pytest.raises(pdf_signing.SignRefused, match="changed"):
        pdf_signing.sign_approved(rec["approval_id"])
    assert _signed_outputs() == []


def test_a_tampered_card_payload_is_not_signed(tmp_path, monkeypatch):
    src = _form(tmp_path)
    pdf_signing.set_signature_image(_signature_png())
    rec = pdf_signing.request_signature(src, page=1)
    monkeypatch.setattr(approvals, "_HOOKS", {})
    approvals.decide(rec["approval_id"], "approve", decided_by="owner")
    approvals._patch(rec["approval_id"], payload=dict(rec["payload"], box=[0, 0, 612, 792]))
    with pytest.raises(pdf_signing.SignRefused, match="changed"):
        pdf_signing.sign_approved(rec["approval_id"])
    assert _signed_outputs() == []


def test_signing_is_held_when_the_claws_check_fails(tmp_path, monkeypatch):
    src = _form(tmp_path)
    pdf_signing.set_signature_image(_signature_png())
    rec = pdf_signing.request_signature(src)
    monkeypatch.setattr(approvals, "_HOOKS", {})
    approvals.decide(rec["approval_id"], "approve", decided_by="owner")
    monkeypatch.setattr(action_gate, "verify_claws", lambda: (False, "tampered"))
    with pytest.raises(pdf_signing.SignRefused, match="governance"):
        pdf_signing.sign_approved(rec["approval_id"])
    assert _signed_outputs() == []
    assert not approvals.get_approval(rec["approval_id"]).get("consumed")


def test_signing_without_materials_is_refused_before_a_card(tmp_path):
    src = _form(tmp_path)
    with pytest.raises(pdf_signing.SignRefused, match="signature image"):
        pdf_signing.request_signature(src)
    with pytest.raises(pdf_signing.SignRefused, match="certificate"):
        pdf_signing.request_signature(src, mode="digital")
    assert approvals.list_approvals(kind=pdf_signing.APPROVAL_KIND) == []


def test_a_box_off_the_page_is_refused(tmp_path):
    src = _form(tmp_path)
    pdf_signing.set_signature_image(_signature_png())
    with pytest.raises(pdf_signing.SignRefused, match="does not fit"):
        pdf_signing.request_signature(src, x=600, y=10, width=100, height=40)
