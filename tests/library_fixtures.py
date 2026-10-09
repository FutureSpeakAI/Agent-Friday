"""Hand-built documents for the Library's tests: no third-party writer, so a
fixture is the same bytes on every machine."""
from __future__ import annotations

import io
import zipfile


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]], *, javascript: bool = False, size=(612, 792)) -> bytes:
    """A PDF whose page i holds the given lines of Helvetica 12 pt text, one
    paragraph per 'line' entry, 36 pt apart so each is its own block.

    Entries starting with '# ' are drawn at 20 pt (a heading)."""
    objs: list[bytes] = []

    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    bold = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    page_ids: list[int] = []
    contents: list[int] = []
    for lines in pages:
        ops = ["BT"]
        y = size[1] - 72
        for ln in lines:
            if ln.startswith("# "):
                ops.append(f"/F2 20 Tf 1 0 0 1 72 {y} Tm ({_esc(ln[2:])}) Tj")
                y -= 40
            else:
                ops.append(f"/F1 12 Tf 1 0 0 1 72 {y} Tm ({_esc(ln)}) Tj")
                y -= 36
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1", "replace")
        contents.append(add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"))
    pages_id = len(objs) + len(pages) + 1
    for c in contents:
        page_ids.append(add(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {size[0]} {size[1]}] "
            f"/Resources << /Font << /F1 {font} 0 R /F2 {bold} 0 R >> >> /Contents {c} 0 R >>".encode()))
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    assert add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()) == pages_id
    extra = ""
    if javascript:
        js = add(b"<< /S /JavaScript /JS (app.alert\\(1\\)) >>")
        extra = f" /OpenAction {js} 0 R"
    root = add(f"<< /Type /Catalog /Pages {pages_id} 0 R{extra} >>".encode())
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, b in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + b + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for o in offsets:
        out.write(f"{o:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root {root} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def make_docx(paragraphs: list[tuple[str, str]], *, doctype: str = "") -> bytes:
    """A DOCX of (style, text) paragraphs, style being 'Heading1', 'Heading2',
    'Normal' and so on."""
    body = []
    for style, text in paragraphs:
        t = text.replace("&", "&amp;").replace("<", "&lt;")
        body.append(f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t>{t}</w:t></w:r></w:p>')
    xml = ('<?xml version="1.0" encoding="UTF-8"?>' + doctype
           + '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           + "".join(body) + "</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def isolate_library(tmp_path, monkeypatch):
    """A scratch Friday home, ledger and store registry for one test."""
    from agent_friday.services import file_grants as fg
    from agent_friday.services.library import store as lstore
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path / "fh"))
    monkeypatch.setattr(fg, "_ledger_path", lambda: tmp_path / "privacy" / "file_grants.jsonl")
    fg._invalidate_cache()
    lstore.forget_open_stores()
    return fg, lstore


def release_library(fg, lstore):
    fg._invalidate_cache()
    lstore.forget_open_stores()


def write_docs(folder, docs: dict):
    """{relative name: text or bytes} -> files under folder."""
    from pathlib import Path
    out = {}
    for name, body in docs.items():
        p = Path(folder) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            p.write_bytes(body)
        else:
            p.write_text(body, encoding="utf-8")
        out[name] = p
    return out


def install_fake_encoder(monkeypatch):
    """A deterministic stand-in for the sentence encoder: hashed bag-of-words,
    unit length. Lexical overlap is similarity, which is enough to test the
    routing logic without loading a model."""
    import hashlib
    import re

    import pytest

    np = pytest.importorskip("numpy", reason="the lexical test embedder builds numpy vectors")

    from agent_friday.services.library import embed

    def vec(text: str):
        v = np.zeros(embed.DIM, dtype=np.float32)
        for w in re.findall(r"[a-z0-9]{3,}", text.lower()):
            h = int(hashlib.md5(w.encode()).hexdigest(), 16)
            v[h % embed.DIM] += 1.0 if (h >> 20) % 2 else -1.0 + 2.0   # keep positive weights
        n = float(np.linalg.norm(v))
        return v / n if n else v

    def fake_embed(texts, batch=16):
        return np.vstack([vec(t) for t in texts]) if texts else np.zeros((0, embed.DIM), dtype=np.float32)

    monkeypatch.setattr(embed, "available", lambda: True)
    monkeypatch.setattr(embed, "embed", fake_embed)
    return fake_embed
