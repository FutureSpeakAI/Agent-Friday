"""Child-process entry for document tasks (see procrun).

Imports only the standard library and the parsing libraries a task needs, so
the child starts fast and never loads the Flask app. Reads one JSON request
from stdin, writes one JSON response to stdout.
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys

from agent_friday.services.library import caps


def _check_file(path: str) -> int:
    size = os.path.getsize(path)
    if size > caps.MAX_FILE_BYTES:
        raise caps.CapExceeded("too large")
    return size


def _pdf_info(args: dict) -> dict:
    import pypdfium2 as pdfium

    path = args["path"]
    _check_file(path)
    pdf = pdfium.PdfDocument(path)
    try:
        n = len(pdf)
        if n > caps.MAX_PAGES:
            raise caps.CapExceeded("too many pages")
        return {"pages": n}
    finally:
        pdf.close()


def _render_page(args: dict) -> dict:
    """One page as a raster image. The only thing a viewer ever gets of a PDF:
    pixels, with no links, scripts or fonts attached."""
    import pypdfium2 as pdfium

    path, n = args["path"], int(args["page"])
    width = max(120, min(int(args.get("width", 900)), caps.MAX_RENDER_EDGE))
    _check_file(path)
    pdf = pdfium.PdfDocument(path)
    try:
        total = len(pdf)
        if total > caps.MAX_PAGES:
            raise caps.CapExceeded("too many pages")
        if not 1 <= n <= total:
            raise caps.CapExceeded("no such page")
        page = pdf[n - 1]
        w_pt, h_pt = page.get_size()
        if w_pt <= 0 or h_pt <= 0:
            raise caps.CapExceeded("page has no size")
        scale = width / w_pt
        if h_pt * scale > caps.MAX_RENDER_EDGE:
            scale = caps.MAX_RENDER_EDGE / h_pt
        if (w_pt * scale) * (h_pt * scale) > caps.MAX_RENDER_PIXELS:
            scale = (caps.MAX_RENDER_PIXELS / (w_pt * h_pt)) ** 0.5
        img = page.render(scale=scale).to_pil().convert("RGB")
        buf = io.BytesIO()
        fmt = "WEBP" if args.get("fmt", "webp") == "webp" else "PNG"
        img.save(buf, fmt, **({"quality": 82} if fmt == "WEBP" else {}))
        return {"page": n, "pages": total, "width": img.width, "height": img.height,
                "page_pt": [w_pt, h_pt], "mime": "image/webp" if fmt == "WEBP" else "image/png",
                "data": base64.b64encode(buf.getvalue()).decode("ascii")}
    finally:
        pdf.close()


_TASKS = {"pdf_info": _pdf_info, "render_page": _render_page}


def register(name: str, fn) -> None:
    _TASKS[name] = fn


def run(task: str, args: dict) -> dict:
    if task not in _TASKS:
        # Heavier tasks live in their own modules and register on import.
        if task == "extract":
            from agent_friday.services.library import extract  # noqa: F401
        elif task == "ocr_page":
            from agent_friday.services.library import extract  # noqa: F401
    fn = _TASKS.get(task)
    if fn is None:
        raise ValueError(f"unknown task {task!r}")
    return fn(args)


def main() -> int:
    try:
        req = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        result = run(req["task"], req.get("args", {}))
        out = {"ok": True, "result": result}
    except caps.CapExceeded as e:
        out = {"ok": False, "error": str(e), "kind": "cap"}
    except MemoryError:
        out = {"ok": False, "error": "needs more memory than is allowed", "kind": "memory"}
    except Exception as e:  # noqa: BLE001 - the child reports, the parent decides
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300], "kind": "failed"}
    sys.stdout.buffer.write(json.dumps(out).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
