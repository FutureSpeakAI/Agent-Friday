"""Previews and details for every Media card, made locally, lazily, in a
low-priority background pass, cached on disk, memory-aware.

Every kind gets a real preview or an honest none, never a placeholder file:

- image: a downscaled thumbnail, with its dimensions;
- image set (a Media record naming several files): a 2x2 mosaic;
- deck, sheet, office document: the first page as the office tool renders it,
  else a rendered text card of the first slide's or page's words;
- page (HTML): a screenshot of the first viewport from a headless browser with
  no network (every request that is not the file itself is refused), closed
  when the batch is done;
- video: a poster frame, an eight-frame strip for hover scrub, the duration;
- audio, music, episode: the waveform peaks (drawn, and as numbers for the
  page), the duration;
- text documents: a text card and the first lines;
- anything else: no image; the details carry the type and size.

The details beside the image: a real title (sidecar, first heading, <title>,
first slide, the episode's own), bytes, dimensions, duration, page count,
the maker, the prompt and the sources, a text snippet, and a perceptual hash
(a 9x8 difference hash) for the clean-up pass. Nothing here leaves this PC.

The worker is one thread, one job at a time, a pause between jobs, and it
waits while the machine is short of memory (RAM_FLOOR_MIB) or, for the
browser, shorter still (BROWSER_FLOOR_MIB). Subprocesses run below normal
priority with no window. Under tests (FRIDAY_TESTING) nothing starts by
itself; ``ensure_all(sync=True)`` runs the queue inline.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import zipfile
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

import agent_friday.core as core

VERSION = "v1"
SIZE = 512                      #: the long edge of a preview
STRIP_FRAMES = 8                #: frames in a video's hover-scrub strip
PEAKS = 160                     #: bars in a waveform
RAM_FLOOR_MIB = 2048            #: no job below this much free RAM
BROWSER_FLOOR_MIB = 3072        #: no headless browser below this
PAUSE_S = 0.15                  #: between jobs: the pass yields to everything else
RAM_WAIT_S = 10.0               #: how long to wait when the machine is short
IMAGE_KINDS = ("image", "imageset", "chart")
VIDEO_KINDS = ("video",)
AUDIO_KINDS = ("audio", "music", "episode")
OFFICE_SUFFIXES = (".pptx", ".docx", ".xlsx")
TEXT_SUFFIXES = (".md", ".txt", ".csv", ".json", ".py", ".js", ".html", ".htm")

_LOCK = threading.RLock()
_QUEUE: Deque[Dict[str, Any]] = deque()
_QUEUED: set = set()
_WORKER: Optional[threading.Thread] = None
_STATE: Dict[str, Any] = {"pending": 0, "done": 0, "failed": 0, "building": None, "last": None}
_DETAILS_CACHE: Dict[str, Dict[str, Any]] = {}
_BROWSER: Dict[str, Any] = {}
_SUBPROCESS_FLAGS = (0x08000000 | 0x00004000) if sys.platform == "win32" else 0   # no window, below normal


# ── where things live ────────────────────────────────────────────────────────

def cache_dir() -> Path:
    return Path(core.FRIDAY_DIR) / "cache" / "media_previews"


def media_path(card: Dict[str, Any]) -> Optional[Path]:
    """The file a card's preview is made from, or None when there is none."""
    p = card.get("path") or ""
    if not p:
        return None
    path = Path(p)
    if card.get("source_kind") == "episode":
        if path.is_dir():
            ex = card.get("extra") or {}
            try:
                rec = json.loads((path / "episode.json").read_text(encoding="utf-8"))
            except Exception:
                rec = {}
            a = rec.get("audio") or ex.get("audio")
            if a:
                ap = Path(str(a))
                if not ap.is_absolute():
                    ap = path / ap
                if ap.is_file():
                    return ap
            for cand in sorted(path.glob("*")):
                if cand.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg"):
                    return cand
            return None
        return path if path.is_file() else None
    if path.is_file():
        return path
    return None


def _key(card: Dict[str, Any], p: Optional[Path]) -> str:
    try:
        st = p.stat() if p else None
        stamp = f"{st.st_mtime_ns}|{st.st_size}" if st else "none"
    except OSError:
        stamp = "gone"
    return hashlib.sha1(f"{card.get('id')}|{p}|{stamp}|{VERSION}".encode("utf-8", "ignore")).hexdigest()


def _paths(card: Dict[str, Any]) -> Dict[str, Path]:
    p = media_path(card)
    k = _key(card, p)
    d = cache_dir() / k[:2]
    return {"dir": d, "key": k, "image": d / (k + ".webp"), "strip": d / (k + ".strip.webp"), "json": d / (k + ".json"), "src": p}


def ready(card: Dict[str, Any]) -> bool:
    """Done: the details file exists (with or without an image)."""
    return _paths(card)["json"].exists()


def image_path(card: Dict[str, Any]) -> Optional[Path]:
    pp = _paths(card)
    return pp["image"] if pp["image"].exists() else None


def strip_path(card: Dict[str, Any]) -> Optional[Path]:
    pp = _paths(card)
    return pp["strip"] if pp["strip"].exists() else None


def details(card: Dict[str, Any]) -> Dict[str, Any]:
    """What is known about the file: {} until the pass has been."""
    pp = _paths(card)
    k = pp["key"]
    d = _DETAILS_CACHE.get(k)
    if d is not None:
        return d
    if not pp["json"].exists():
        return {}
    try:
        d = json.loads(pp["json"].read_text(encoding="utf-8"))
    except Exception:
        d = {}
    if len(_DETAILS_CACHE) > 4000:
        _DETAILS_CACHE.clear()
    _DETAILS_CACHE[k] = d
    return d


def status() -> Dict[str, Any]:
    with _LOCK:
        return {"pending": len(_QUEUE), "done": _STATE["done"], "failed": _STATE["failed"], "building": _STATE["building"]}


# ── the queue and its one worker ─────────────────────────────────────────────

def enqueue(cards: List[Dict[str, Any]], *, front: bool = False) -> int:
    """Queue every card without a preview; newest first. Returns how many were added."""
    n = 0
    with _LOCK:
        for c in cards:
            cid = c.get("id")
            if not cid or cid in _QUEUED or ready(c):
                continue
            _QUEUED.add(cid)
            (_QUEUE.appendleft if front else _QUEUE.append)(c)
            n += 1
    if n and not os.environ.get("FRIDAY_TESTING"):
        _start_worker()
    return n


def _start_worker() -> None:
    global _WORKER
    with _LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return
        _WORKER = threading.Thread(target=_worker, name="media-previews", daemon=True)
        _WORKER.start()


def _ram_mib() -> Optional[int]:
    try:
        from agent_friday.services.machine_monitor import _ram_available_mib
        return _ram_available_mib()
    except Exception:
        return None


def _room(floor: int) -> bool:
    r = _ram_mib()
    return r is None or r >= floor


def _worker() -> None:
    while True:
        with _LOCK:
            c = _QUEUE.popleft() if _QUEUE else None
        if c is None:
            _close_browser()
            _after_pass()
            return
        while not _room(RAM_FLOOR_MIB):
            time.sleep(RAM_WAIT_S)
        _run_one(c)
        time.sleep(PAUSE_S)


def _after_pass() -> None:
    """When the previews are done, the audio and video go on to the local
    transcriber (services/media_transcripts.py), the same one-at-a-time way."""
    try:
        from agent_friday.services import media_index as mi, media_transcripts as mt
        if mt.available():
            mt.enqueue([c for c in mi.query(view="all", sort="newest", limit=100000)["cards"] if c.get("kind") in mt.AV_KINDS])
    except Exception:
        pass


def _run_one(c: Dict[str, Any]) -> None:
    with _LOCK:
        _STATE["building"] = c.get("id")
    try:
        build(c)
        with _LOCK:
            _STATE["done"] += 1
    except Exception as e:  # one bad file never stops the pass
        with _LOCK:
            _STATE["failed"] += 1
            _STATE["last"] = f"{c.get('id')}: {str(e)[:160]}"
    finally:
        with _LOCK:
            _QUEUED.discard(c.get("id"))
            _STATE["building"] = None


def ensure_all(cards: Optional[List[Dict[str, Any]]] = None, *, sync: Optional[bool] = None, timeout: float = 60.0) -> Dict[str, Any]:
    """Queue the cards (or everything the index holds) and, when synchronous,
    build them inline; otherwise wait up to ``timeout`` for the worker."""
    if cards is None:
        from agent_friday.services import media_index as mi
        cards = mi.query(view="all", limit=100000)["cards"]
    if sync is None:
        sync = bool(os.environ.get("FRIDAY_TESTING"))
    enqueue(cards)
    if sync:
        while True:
            with _LOCK:
                c = _QUEUE.popleft() if _QUEUE else None
            if c is None:
                break
            _run_one(c)
        # A card the background worker already holds is skipped by the queue,
        # so the pass is done only when that build has finished too.
        end = time.time() + timeout
        while time.time() < end and (_QUEUE or _STATE["building"]):
            time.sleep(0.05)
        _close_browser()
    else:
        end = time.time() + timeout
        while time.time() < end and (_QUEUE or _STATE["building"]):
            time.sleep(0.05)
    return status()


# ── building one preview ─────────────────────────────────────────────────────

def build(card: Dict[str, Any]) -> Dict[str, Any]:
    """Make the preview and the details for one card and write them to the cache."""
    pp = _paths(card)
    p = pp["src"]
    det: Dict[str, Any] = {"id": card.get("id"), "kind": card.get("kind"), "version": VERSION, "built": time.time()}
    img = None
    if p is not None:
        st = p.stat()
        det.update({"bytes": st.st_size, "suffix": p.suffix.lower(), "filename": p.name})
        kind = card.get("kind") or "file"
        suffix = p.suffix.lower()
        try:
            if kind == "imageset" or (card.get("extra") or {}).get("files"):
                img = _mosaic(card, p, det)
            elif kind in IMAGE_KINDS or suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
                img = _image(p, det)
            elif kind in VIDEO_KINDS or suffix in (".mp4", ".webm", ".mov", ".mkv"):
                img = _video(p, det, pp)
            elif kind in AUDIO_KINDS or suffix in (".mp3", ".wav", ".m4a", ".ogg", ".flac"):
                img = _audio(p, det)
            elif suffix in OFFICE_SUFFIXES:
                img = _office(p, det)
            elif suffix == ".pdf":
                img = _pdf(p, det)
            elif kind == "page" or suffix in (".html", ".htm"):
                img = _page(p, det)
            elif suffix in TEXT_SUFFIXES or kind in ("draft", "article", "doc"):
                img = _text(p, det)
        except Exception as e:
            det["error"] = str(e)[:200]
    _provenance_details(card, det)
    if img is not None:
        img = _fit(img, SIZE)
        det["dhash"] = _dhash(img)
        det["preview"] = [img.width, img.height]
    pp["dir"].mkdir(parents=True, exist_ok=True)
    if img is not None:
        _write_webp(img, pp["image"])
    tmp = pp["json"].with_suffix(".json.tmp")
    tmp.write_text(json.dumps(det, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, pp["json"])
    _DETAILS_CACHE.pop(pp["key"], None)
    if det.get("text"):
        try:
            from agent_friday.services import media_index as mi
            mi.enrich_text(card["id"], text=det["text"])
        except Exception:
            pass
    return det


def _fit(img, size: int):
    from PIL import Image
    im = img
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGB")
    if max(im.size) > size:
        im = im.copy()
        im.thumbnail((size, size), Image.LANCZOS)
    return im


def _write_webp(img, out: Path) -> None:
    tmp = out.with_suffix(".webp.tmp")
    img.save(tmp, "WEBP", quality=82, method=4)
    os.replace(tmp, out)


def _dhash(img) -> str:
    """A 9x8 difference hash: 64 bits as 16 hex digits. Near duplicates differ in few bits."""
    g = img.convert("L").resize((9, 8))
    px = list(g.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            left, right = px[row * 9 + col], px[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except Exception:
        return 64


# ── kinds ────────────────────────────────────────────────────────────────────

def _open_image(p: Path):
    from PIL import Image, ImageOps
    im = Image.open(p)
    try:
        im.draft("RGB", (SIZE * 2, SIZE * 2))
    except Exception:
        pass
    try:
        im.seek(0)
    except Exception:
        pass
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if "A" in im.getbands() or im.mode == "P" else "RGB")
    return im


def _image(p: Path, det: Dict[str, Any]):
    from PIL import Image
    with Image.open(p) as probe:
        det["width"], det["height"] = probe.size
        det["format"] = probe.format
    return _open_image(p)


def _mosaic(card: Dict[str, Any], p: Path, det: Dict[str, Any]):
    """A 2x2 of the set's first four files; a single file is a plain image."""
    from PIL import Image
    files = [Path(str(f)) for f in ((card.get("extra") or {}).get("files") or [])]
    files = [f if f.is_absolute() else p.parent / f for f in files]
    files = [f for f in files if f.is_file()][:4] or [p]
    det["count"] = len(files)
    if len(files) == 1:
        return _image(files[0], det)
    cell = SIZE // 2
    out = Image.new("RGB", (cell * 2, cell * 2), (12, 14, 20))
    for i, f in enumerate(files):
        try:
            im = _open_image(f).convert("RGB")
            im.thumbnail((cell, cell), Image.LANCZOS)
            x, y = (i % 2) * cell + (cell - im.width) // 2, (i // 2) * cell + (cell - im.height) // 2
            out.paste(im, (x, y))
        except Exception:
            continue
    return out


def ffmpeg_exe() -> Optional[str]:
    try:
        from agent_friday.services.timeline_engine import ffmpeg_exe as _ff
        exe = _ff()
        if exe:
            return exe
    except Exception:
        pass
    import shutil
    return shutil.which("ffmpeg")


def ffprobe_exe() -> Optional[str]:
    import shutil
    exe = ffmpeg_exe()
    if exe:
        cand = Path(exe).with_name("ffprobe" + (".exe" if exe.lower().endswith(".exe") else ""))
        if cand.exists():
            return str(cand)
    return shutil.which("ffprobe")


def _run(argv: List[str], timeout: float = 20.0) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, timeout=timeout, creationflags=_SUBPROCESS_FLAGS)


def _probe(p: Path, det: Dict[str, Any]) -> float:
    exe = ffprobe_exe()
    if not exe:
        return 0.0
    try:
        r = _run([exe, "-v", "error", "-show_entries", "format=duration:stream=width,height,codec_type", "-of", "json", str(p)], timeout=15)
        d = json.loads(r.stdout.decode("utf-8", "ignore") or "{}")
    except Exception:
        return 0.0
    dur = 0.0
    try:
        dur = float((d.get("format") or {}).get("duration") or 0)
    except Exception:
        dur = 0.0
    if dur:
        det["duration_s"] = round(dur, 2)
    for s in d.get("streams") or []:
        if s.get("codec_type") == "video" and s.get("width"):
            det["width"], det["height"] = int(s["width"]), int(s["height"])
            break
    return dur


def _video(p: Path, det: Dict[str, Any], pp: Dict[str, Path]):
    from PIL import Image
    exe = ffmpeg_exe()
    dur = _probe(p, det)
    if not exe:
        return None
    poster = None
    for ss in ("1.5", "0"):
        r = _run([exe, "-v", "error", "-ss", ss, "-i", str(p), "-frames:v", "1", "-vf", f"scale={SIZE}:-2", "-f", "image2pipe", "-vcodec", "png", "-"], timeout=20)
        if r.returncode == 0 and r.stdout:
            poster = Image.open(io.BytesIO(r.stdout)).convert("RGB")
            break
    # the hover-scrub strip: STRIP_FRAMES frames spread over the whole length, one row
    if dur and dur > 0.5:
        step = max(dur / STRIP_FRAMES, 0.05)
        r = _run([exe, "-v", "error", "-i", str(p), "-vf", f"fps=1/{step:.4f},scale=192:-2,tile={STRIP_FRAMES}x1", "-frames:v", "1",
                  "-f", "image2pipe", "-vcodec", "png", "-"], timeout=40)
        if r.returncode == 0 and r.stdout:
            try:
                strip = Image.open(io.BytesIO(r.stdout)).convert("RGB")
                pp["dir"].mkdir(parents=True, exist_ok=True)
                _write_webp(strip, pp["strip"])
                det["strip"] = STRIP_FRAMES
            except Exception:
                pass
    return poster


def _audio(p: Path, det: Dict[str, Any]):
    """Waveform peaks as numbers (for the page) and as a drawn image (for the grid)."""
    from PIL import Image, ImageDraw
    exe = ffmpeg_exe()
    _probe(p, det)
    peaks: List[float] = []
    if exe:
        r = _run([exe, "-v", "error", "-i", str(p), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], timeout=60)
        if r.returncode == 0 and r.stdout:
            import numpy as np
            a = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32)
            if a.size:
                if not det.get("duration_s"):
                    det["duration_s"] = round(a.size / 8000.0, 2)
                n = PEAKS
                chunk = max(1, a.size // n)
                a = np.abs(a[: chunk * n]).reshape(n, chunk) if a.size >= n else np.abs(a).reshape(1, -1)
                m = a.max(axis=1)
                top = float(m.max()) or 1.0
                peaks = [round(float(x) / top, 3) for x in m]
    det["peaks"] = peaks
    if not peaks:
        return None
    W, H = SIZE, SIZE // 2
    img = Image.new("RGB", (W, H), (12, 14, 20))
    d = ImageDraw.Draw(img)
    bw = W / len(peaks)
    for i, v in enumerate(peaks):
        hh = max(2, int(v * (H - 16)))
        x0 = int(i * bw) + 1
        d.rectangle([x0, (H - hh) // 2, max(x0 + 1, int((i + 1) * bw) - 1), (H + hh) // 2], fill=(0, 229, 255))
    return img


def _first_paragraph(slide_xml: str) -> str:
    """The first paragraph on a slide that has words in it: the slide's own
    heading, not the heading and everything under it. The runs of a paragraph
    are joined as written, because a run break is a change of style; a soft
    line break is a space."""
    for para in re.findall(r"<a:p[ >].*?</a:p>", slide_xml, re.S):
        para = re.sub(r"<a:br\b[^>]*>", "<a:t> </a:t>", para)
        text = _clean("".join(re.findall(r"<a:t>([^<]*)</a:t>", para)))
        if text:
            return text
    return ""


def _pptx_text(p: Path, det: Dict[str, Any]) -> Tuple[str, List[str]]:
    """The first slide's title line and every slide's text, from the zip; the slide count."""
    first, allt = "", []
    try:
        with zipfile.ZipFile(p) as z:
            names = sorted([n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)], key=lambda n: int(re.findall(r"\d+", n)[-1]))
            det["pages"] = len(names)
            for i, n in enumerate(names):
                xml = z.read(n).decode("utf-8", "ignore")
                words = " ".join(t for t in re.findall(r"<a:t>([^<]*)</a:t>", xml)).strip()
                allt.append(words)
                if i == 0:
                    first = _first_paragraph(xml)
    except Exception:
        pass
    return first, allt


def _docx_text(p: Path) -> str:
    try:
        with zipfile.ZipFile(p) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        paras = re.split(r"</w:p>", xml)
        return "\n".join(" ".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", para)).strip() for para in paras).strip()
    except Exception:
        return ""


def _office(p: Path, det: Dict[str, Any]):
    """The first page as the office tool renders it; else a text card of its words."""
    text = ""
    if p.suffix.lower() == ".pptx":
        first, allt = _pptx_text(p, det)
        text = "\n\n".join(allt)
        if first:
            det["title_guess"] = first[:120]
    elif p.suffix.lower() == ".docx":
        text = _docx_text(p)
        head = text.split("\n", 1)[0].strip() if text else ""
        if head:
            det["title_guess"] = head[:120]
    if text:
        det["text"] = text[:20000]
        det["snippet"] = text[:400]
    img = None
    try:
        from agent_friday.services import office_engine
        if office_engine.available() and _room(RAM_FLOOR_MIB):
            png, _note = office_engine.screenshot(p, page="1", width=1024)
            if png:
                from PIL import Image
                img = Image.open(io.BytesIO(png)).convert("RGB")
    except Exception:
        img = None
    if img is None and (det.get("snippet") or det.get("title_guess")):
        img = _text_card(det.get("title_guess") or "", det.get("snippet") or "")
    return img


def _pdf(p: Path, det: Dict[str, Any]):
    raw = b""
    try:
        raw = p.read_bytes()
        det["pages"] = len(re.findall(rb"/Type\s*/Page[^s]", raw)) or None
    except Exception:
        pass
    text = ""
    try:
        from agent_friday.services.file_extraction import extract_text
        res = extract_text(p)
        text = res.text or ""
    except Exception:
        text = ""
    if text:
        det["text"] = text[:20000]
        det["snippet"] = text[:400]
        det["title_guess"] = text.strip().split("\n", 1)[0][:120]
    return _text_card(det.get("title_guess") or p.stem, text[:400]) if text else None


def _page(p: Path, det: Dict[str, Any]):
    """A screenshot of the first viewport, from a headless browser with no
    network: the only request allowed is the file itself; everything else is refused."""
    raw = p.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", raw, re.S | re.I)
    title = _clean(m.group(1)) if m else (_clean(h1.group(1)) if h1 else "")
    if title:
        det["title_guess"] = title[:120]
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    text = _clean(re.sub(r"<[^>]+>", " ", body))
    if text:
        det["text"] = text[:20000]
        det["snippet"] = text[:400]
    if not _room(BROWSER_FLOOR_MIB):
        det["browser"] = "deferred: the machine is short of memory; the page's words stand in"
        return _text_card(title or p.stem, text[:400])
    try:
        br = _browser()
    except Exception as e:
        det["browser"] = f"unavailable: {str(e)[:120]}"
        return _text_card(title or p.stem, text[:400])
    if br is None:
        return _text_card(title or p.stem, text[:400])
    from PIL import Image
    ctx = br.new_context(viewport={"width": 1280, "height": 800}, offline=True, java_script_enabled=True)
    try:
        allowed = p.resolve().as_uri().lower()

        def gate(route):
            if route.request.url.lower() == allowed:
                return route.continue_()
            return route.abort()
        ctx.route("**/*", gate)
        page = ctx.new_page()
        page.goto(p.resolve().as_uri(), wait_until="load", timeout=8000)
        png = page.screenshot(type="png")
        det["browser"] = "chromium, offline"
        return Image.open(io.BytesIO(png)).convert("RGB")
    finally:
        try:
            ctx.close()
        except Exception:
            pass


def _browser():
    """One headless Chromium for the batch, started on first need, closed when the queue drains."""
    with _LOCK:
        if _BROWSER.get("browser"):
            return _BROWSER["browser"]
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        try:
            br = pw.chromium.launch(headless=True)
        except Exception:
            pw.stop()
            raise
        _BROWSER.update({"pw": pw, "browser": br})
        return br


def _close_browser() -> None:
    with _LOCK:
        br, pw = _BROWSER.pop("browser", None), _BROWSER.pop("pw", None)
    for x in (br, pw):
        try:
            if x is not None:
                (x.close if hasattr(x, "close") else x.stop)()
        except Exception:
            pass


def _text(p: Path, det: Dict[str, Any]):
    raw = p.read_text(encoding="utf-8", errors="ignore")
    text = raw
    title = ""
    if p.suffix.lower() in (".html", ".htm"):
        return _page(p, det)
    m = re.search(r"^\s*#\s+(.+)$", raw, re.M)
    if m:
        title = m.group(1).strip()
    elif raw.strip():
        title = raw.strip().split("\n", 1)[0].strip()[:120]
    if title:
        det["title_guess"] = title[:120]
    det["text"] = text[:20000]
    det["snippet"] = re.sub(r"^\s*#.+\n", "", text, count=1).strip()[:400]
    det["words"] = len(text.split())
    return _text_card(title, det["snippet"])


def _clean(s: str) -> str:
    import html as _h
    return re.sub(r"\s+", " ", _h.unescape(s or "")).strip()


def _text_card(title: str, body: str):
    """A rendered card of the first words: the document's own text, on the brand's dark face."""
    from PIL import Image, ImageDraw, ImageFont
    W, H = SIZE, int(SIZE * 0.66)
    img = Image.new("RGB", (W, H), (12, 14, 20))
    d = ImageDraw.Draw(img)
    font_t = font_b = None
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            font_t = ImageFont.truetype(name, 26)
            font_b = ImageFont.truetype(name, 15)
            break
        except OSError:
            continue
    if font_t is None:
        font_t = font_b = ImageFont.load_default()
    y = 22
    if title:
        d.text((24, y), title[:60], fill=(236, 240, 248), font=font_t)
        y += 44
    words, line, lines = (body or "").split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > 58:
            lines.append(line); line = w
        else:
            line = (line + " " + w).strip()
        if len(lines) >= 9:
            break
    if line and len(lines) < 9:
        lines.append(line)
    for ln in lines:
        d.text((24, y), ln, fill=(170, 182, 200), font=font_b)
        y += 22
    d.rectangle([0, H - 4, W, H], fill=(0, 229, 255))
    return img


def _provenance_details(card: Dict[str, Any], det: Dict[str, Any]) -> None:
    """Which model or tool, the prompt, the sources: from the sidecar, the
    episode record, or the card itself."""
    det["maker"] = card.get("maker") or ""
    det["sources"] = list(card.get("sources") or [])
    prompt = ""
    try:
        if card.get("source_kind") == "creation":
            from agent_friday.services.creative_engine import creation_metadata
            meta = creation_metadata(Path(card["path"]).name) or {}
            prompt = str(meta.get("prompt") or "")
            if meta.get("model") or meta.get("api_model"):
                det["model"] = str(meta.get("model") or meta.get("api_model"))
            if meta.get("title"):
                det["title_guess"] = str(meta["title"])[:120]
            if meta.get("seed_image"):
                det["sources"].append(str(meta["seed_image"]))
        elif card.get("source_kind") == "episode":
            rec = json.loads((Path(card["path"]) / "episode.json").read_text(encoding="utf-8"))
            if rec.get("title"):
                det["title_guess"] = str(rec["title"])[:120]
            det["sources"] = [str(s.get("title") or s.get("kind") or "") for s in (rec.get("sources") or [])] or det["sources"]
            det["model"] = str(rec.get("voice_engine") or "local voice")
    except Exception:
        pass
    if not prompt:
        for s in det["sources"]:
            if str(s).startswith("prompt: "):
                prompt = str(s)[8:]
                break
    det["prompt"] = prompt
    det["sources"] = [s for s in det["sources"] if not str(s).startswith("prompt: ")]


def forget(card: Dict[str, Any]) -> None:
    """Drop a card's cached preview (the file changed or went away)."""
    pp = _paths(card)
    for k in ("image", "strip", "json"):
        try:
            pp[k].unlink()
        except OSError:
            pass
    _DETAILS_CACHE.pop(pp["key"], None)
