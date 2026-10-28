"""Strip an image's metadata before its bytes leave for a cloud model.

A photo carries more than its pixels: EXIF (GPS position, camera make and
serial, the time it was taken, sometimes the owner's name), XMP, IPTC,
comments and embedded thumbnails. A vision model needs the pixels only, so
every path that sends image bytes to a cloud model sends them through
`strip_metadata`.

The pixels are kept, turned upright first (the EXIF orientation is applied,
since it is dropped with the rest). The format is kept, except that a
multi-picture JPEG (MPO, as many phones write) goes as a plain JPEG of its
first picture. Nothing is resized.

A JPEG that Pillow cannot decode still has its metadata segments cut out at
the byte level. Any other image that cannot be read here goes unchanged, and
the result says so, so the egress ledger row is honest about it.
"""
from __future__ import annotations

import io
from typing import Tuple

#: Formats re-encoded in place (MPO is saved as JPEG).
_REENCODE = {"JPEG", "MPO", "PNG", "WEBP", "TIFF", "BMP", "GIF"}
#: `Image.info` keys that carry metadata rather than pixels.
_META_KEYS = ("exif", "xmp", "XML:com.adobe.xmp", "comment", "photoshop", "icc_profile")
#: The only application segments a JPEG keeps on the byte-level path: APP0
#: (JFIF) and APP14 (Adobe colour transform, which CMYK decoding needs). Every
#: other APPn (EXIF, XMP, ICC, MPF, IPTC, maker notes, JUMBF/C2PA) and COM goes.
_JPEG_KEEP_APP = {0xE0, 0xEE}


def _has_metadata(im) -> bool:
    try:
        if len(im.getexif()) > 0:
            return True
    except Exception:
        pass
    if any(im.info.get(k) for k in _META_KEYS):
        return True
    return bool(getattr(im, "text", None))     # PNG tEXt/iTXt/zTXt chunks


def _drop_segment(marker: int) -> bool:
    return marker == 0xFE or (0xE0 <= marker <= 0xEF and marker not in _JPEG_KEEP_APP)


def _scan_end(data: bytes, i: int) -> int:
    """Index of the first marker after the entropy-coded data starting at `i`
    (stuffed FF00 and restart markers are part of the data)."""
    n = len(data)
    while i + 1 < n:
        if data[i] == 0xFF:
            nxt = data[i + 1]
            if nxt == 0x00 or 0xD0 <= nxt <= 0xD7 or nxt == 0xFF:
                i += 2 if nxt != 0xFF else 1
                continue
            return i
        i += 1
    return n


def strip_jpeg_segments(data: bytes) -> bytes | None:
    """The first picture of a JPEG with every metadata segment removed and
    nothing after its end-of-image marker (a second picture, a motion clip,
    maker trailers), or None when it is not a JPEG this can walk. A file cut
    short ends where the data does. Pure."""
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    out = bytearray(b"\xff\xd8")
    i = 2
    n = len(data)
    while i < n:
        if i + 1 >= n or data[i] != 0xFF:
            return None if not out[2:] else bytes(out)
        marker = data[i + 1]
        if marker == 0xFF:                      # fill byte
            i += 1
            continue
        if marker == 0xD9:                      # end of image: stop, drop any trailer
            out += b"\xff\xd9"
            return bytes(out)
        if marker == 0xD8 or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            if marker == 0xD8:
                return None                     # a second start before an end: not walkable
            out += data[i:i + 2]
            i += 2
            continue
        if i + 4 > n:
            return bytes(out)
        seg_len = (data[i + 2] << 8) | data[i + 3]
        if seg_len < 2:
            return None
        seg_end = min(i + 2 + seg_len, n)
        if not _drop_segment(marker):
            out += data[i:seg_end]
        i = seg_end
        if marker == 0xDA:                      # start of scan: copy its coded data
            j = _scan_end(data, i)
            out += data[i:j]
            i = j
    return bytes(out)


def strip_metadata(data: bytes) -> Tuple[bytes, str]:
    """(bytes to send, what happened): "stripped", "no_metadata" or
    "not_stripped:<why>". Pure; never raises."""
    if not data:
        return data, "not_stripped:empty"
    try:
        out = _reencode(data)
        if out is not None:
            return out
    except Exception as e:
        why = type(e).__name__
    else:
        why = "unreadable"
    cut = None
    try:
        cut = strip_jpeg_segments(data)
    except Exception:
        cut = None
    if cut is not None:
        return cut, "stripped"
    return data, "not_stripped:%s" % why


def _reencode(data: bytes):
    """(bytes, label) from a Pillow re-encode, or None when Pillow cannot
    read the bytes. May raise; strip_metadata catches it."""
    try:
        from PIL import Image, ImageOps
    except Exception:
        return None
    with Image.open(io.BytesIO(data)) as im:
        fmt = (im.format or "").upper()
        if fmt not in _REENCODE:
            return data, "not_stripped:format_%s" % (fmt.lower() or "unknown")
        had = _has_metadata(im)
        if fmt == "GIF" and getattr(im, "is_animated", False):
            # GIF carries no EXIF or GPS; re-encoding an animation is not
            # worth the risk of breaking it. Its comment, if any, stays.
            return data, ("not_stripped:animated_gif" if had else "no_metadata")
        im.seek(0)
        im.load()
        try:
            upright = ImageOps.exif_transpose(im) or im
        except Exception:
            upright = im                       # malformed EXIF: pixels as stored
        # A fresh image holds only pixels: no info, no EXIF, no profile.
        clean = Image.new(upright.mode, upright.size)
        if upright.mode == "P":
            clean.putpalette(upright.getpalette() or [])
        clean.paste(upright)
        out_fmt = "JPEG" if fmt == "MPO" else fmt
        kwargs = {}
        if "transparency" in upright.info and out_fmt in ("PNG", "GIF"):
            kwargs["transparency"] = upright.info["transparency"]
        if out_fmt == "JPEG":
            kwargs = {"quality": 95}
            if clean.mode not in ("RGB", "L", "CMYK"):
                clean = clean.convert("RGB")
        elif out_fmt == "TIFF":
            kwargs = {"compression": "tiff_lzw"}
        buf = io.BytesIO()
        clean.save(buf, format=out_fmt, **kwargs)
        return buf.getvalue(), ("stripped" if had else "no_metadata")
