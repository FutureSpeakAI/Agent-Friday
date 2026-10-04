"""PDF page images for every viewer of a PDF.

A PDF is never handed to the browser's own viewer. The page is rendered in a
limited child process and only the pixels are served, so nothing in the file
(script, launch action, link, font, remote reference) reaches the page.
"""
from __future__ import annotations

import base64
import threading
from collections import OrderedDict
from pathlib import Path

from agent_friday.services.library import procrun

_CACHE_MAX = 48
_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_lock = threading.Lock()


def _key(path: Path, page: int, width: int, fmt: str) -> tuple:
    st = path.stat()
    return (str(path), st.st_mtime_ns, st.st_size, page, width, fmt)


def render_pdf_page(path: Path, page: int, width: int = 900, fmt: str = "webp") -> dict:
    """{data: bytes, mime, pages, width, height, page_pt}. Raises procrun.TaskFailed."""
    width = max(120, min(int(width), 2400))
    key = _key(path, int(page), width, fmt)
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    res = procrun.run_task("render_page", {"path": str(path), "page": int(page),
                                           "width": width, "fmt": fmt}, wall_s=45)
    out = dict(res)
    out["data"] = base64.b64decode(res["data"])
    with _lock:
        _cache[key] = out
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return out


def purge_document(doc) -> None:
    """Drop a document's cached page renders (forget, remove)."""
    if not doc:
        return
    p = str(Path(doc["path"]))
    with _lock:
        for k in [k for k in _cache if k[0] == p]:
            _cache.pop(k, None)


def clear() -> None:
    with _lock:
        _cache.clear()


def pdf_page_count(path: Path) -> int:
    return int(procrun.run_task("pdf_info", {"path": str(path)}, wall_s=30)["pages"])
