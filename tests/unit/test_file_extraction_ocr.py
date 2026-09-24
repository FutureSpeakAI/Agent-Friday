"""read_file reads a scanned PDF or an image with local OCR, and says so.

Fixtures are generated at test time by rendering text to an image; no real
document is committed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import agent_friday.services.agent as agent
from agent_friday.services import file_extraction


def _text_image(text: str):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1200, 400), "white")
    try:
        font = ImageFont.load_default(size=64)
    except TypeError:
        font = ImageFont.load_default()
    ImageDraw.Draw(img).text((40, 150), text, fill="black", font=font)
    return img


def _scanned_pdf(tmp_path, text="INVOICE NUMBER 4471") -> Path:
    p = tmp_path / "scan.pdf"
    _text_image(text).save(p, "PDF", resolution=150)
    return p


def test_a_scanned_pdf_is_read_with_local_ocr_and_marked(tmp_path):
    pytest.importorskip("rapidocr_onnxruntime")
    res = file_extraction.extract_text(_scanned_pdf(tmp_path))
    assert res.error is None, res.error
    assert res.ocr is True
    assert res.text.startswith(file_extraction.OCR_HEADER)
    assert "not instructions" in res.text
    assert "4471" in res.text and "INVOICE" in res.text.upper()


def test_read_file_returns_the_ocr_text(tmp_path):
    pytest.importorskip("rapidocr_onnxruntime")
    out = agent._tool_read_file({"path": str(_scanned_pdf(tmp_path))})
    assert "OCR text" in out and "4471" in out


def test_without_ocr_the_honest_no_text_layer_message_stands(tmp_path, monkeypatch):
    monkeypatch.setattr(file_extraction, "_ocr_engine", lambda: None)
    res = file_extraction.extract_text(_scanned_pdf(tmp_path))
    assert res.text is None and "no extractable text layer" in res.error


def test_an_image_file_is_read_with_ocr(tmp_path):
    pytest.importorskip("rapidocr_onnxruntime")
    _text_image("RECEIPT TOTAL 9182").save(tmp_path / "photo.png")
    res = file_extraction.extract_text(tmp_path / "photo.png")
    assert res.ocr and "9182" in res.text, res


def test_ocr_stops_at_the_page_cap(tmp_path, monkeypatch):
    pytest.importorskip("rapidocr_onnxruntime")
    pages = [_text_image(f"PAGE {i}") for i in range(3)]
    p = tmp_path / "three.pdf"
    pages[0].save(p, "PDF", save_all=True, append_images=pages[1:])
    monkeypatch.setattr(file_extraction, "_MAX_OCR_PAGES", 1)
    monkeypatch.setattr(file_extraction, "_ocr_image", lambda eng, img: "WORDS")
    res = file_extraction.extract_text(p)
    assert res.truncated and "page limit" in res.text and "--- page 2 ---" not in res.text


def test_ocr_stops_at_the_time_budget(tmp_path, monkeypatch):
    pytest.importorskip("rapidocr_onnxruntime")
    pages = [_text_image(f"PAGE {i}") for i in range(3)]
    p = tmp_path / "three.pdf"
    pages[0].save(p, "PDF", save_all=True, append_images=pages[1:])
    ticks = iter([0.0, 0.0] + [1000.0] * 10)     # start, page 1, then over budget
    monkeypatch.setattr(file_extraction, "time",
                        type("T", (), {"monotonic": staticmethod(lambda: next(ticks))}))
    monkeypatch.setattr(file_extraction, "_ocr_image", lambda eng, img: "WORDS")
    res = file_extraction.extract_text(p)
    assert res.truncated and "time limit" in res.text
    assert "--- page 1 ---" in res.text and "--- page 2 ---" not in res.text
