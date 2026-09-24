# PDF documents: reading scans, filling forms, signing

*Implementation: `services/file_extraction.py` (OCR), `services/pdf_forms.py`
(fields and filling), `services/pdf_signing.py` (signing), settings routes in
`routes/documents.py`. Optional dependencies: the `documents` extra
(`PyPDFForm`, `pyHanko`, `rapidocr-onnxruntime`), installed by default by the
Windows installer's recommended tier.*

---

## Reading a scanned PDF or an image

`read_file` reads a PDF's text layer. When a PDF has no text layer (a scan), or
the file is an image (`.png`, `.jpg`, `.tif`, `.bmp`, `.webp`), Friday runs
local OCR with RapidOCR, page by page:

- at most 10 pages and about 60 seconds per file; the result says where it
  stopped;
- the text starts with a marker saying it was read by OCR, may contain
  mistakes, and is document content, not instructions;
- the OCR models ship inside the `rapidocr-onnxruntime` package. Nothing is
  downloaded and nothing leaves the computer.

Without the OCR package, Friday says the PDF has no text layer and does not
guess at its contents.

## Tools

| Tool | What it does | Governance |
|---|---|---|
| `list_pdf_fields` | Lists a form's fields: name, type, current value, options, required, page, and whether the field asks a sensitive question. | Internal (read-only). |
| `fill_pdf_form` | Fills fields into a **new** PDF in the `forms` folder inside Friday's creations folder. The original is never changed. | Internal when it writes a new file in that folder. Writing anywhere else, or over an existing file, needs your OK. Writing over the original form is refused. |
| `sign_pdf` | Asks to sign a PDF. It never signs by itself. | Always an approval card, in chat or not. |

### What Friday will not fill in for you

`fill_pdf_form` does not invent answers to legal or demographic questions.
A field that looks like one of these is filled only with a value you typed in
your own message in the same turn, and otherwise comes back to you as a
question:

- Social Security or tax ID numbers
- date of birth
- race or ethnicity, gender
- disability, veteran status
- criminal history

Signature fields and attestations ("I certify…", "under penalty of perjury",
"I agree…") are never filled by this tool, whatever the message says. A
signature goes through `sign_pdf`; an attestation is yours to make.

## Signing

There are two ways to sign:

- **Stamp** (`mode: "stamp"`): your signature image is drawn onto the page. It
  is a picture of a signature, not a cryptographic one.
- **Digital** (`mode: "digital"`): a real PDF digital signature made with
  pyHanko and your certificate (a `.p12` / `.pfx` file holding the
  certificate and its private key). PDF readers show who signed and whether
  the document changed afterwards.

Every signature needs your approval on a card. The card names the file, the
page and the method, gives the signature box's size and position, and shows a
preview of the page with the box outlined in red. Approving the card is what
signs: the signed copy is saved as a new file (`<name>-signed.pdf`) in the
`forms` folder, and the original is not changed.

Before it signs, Friday re-checks the card: it must be approved by you (not
auto-approved), unused, unchanged since you saw it, and the PDF must be the
same file you approved. One card signs once. If the file changed in the
meantime, nothing is signed and Friday asks again.

### Setting your signature and certificate

Settings → Accounts & Keys → **Signing PDFs**:

- **Signature image**: choose a PNG or JPEG of your signature.
- **Signing certificate**: choose your `.p12` / `.pfx` file, enter its
  passphrase, and save. Friday checks that the passphrase opens it and that it
  holds a private key before storing it.

Both are stored encrypted through Friday's credential store, never in plain
text, and never sent back to the page: the page shows only whether each is
stored (and the certificate's subject and expiry). These settings can be
changed only from Friday's own computer.

The same settings are available as routes, local only:

| Method | Route | Body |
|---|---|---|
| GET | `/api/documents/signing/status` | |
| POST | `/api/documents/signing/image` | `{"image_b64": "<base64 PNG/JPEG>"}` |
| DELETE | `/api/documents/signing/image` | |
| POST | `/api/documents/signing/certificate` | `{"certificate_b64": "<base64 .p12>", "passphrase": "…"}` |
| DELETE | `/api/documents/signing/certificate` | |
