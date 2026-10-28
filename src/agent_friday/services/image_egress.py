"""Strip an image's metadata before its bytes leave for a cloud model.

A photo carries more than its pixels: EXIF (GPS position, camera make and
serial, the time it was taken, sometimes the owner's name), XMP, IPTC,
comments and embedded thumbnails. A vision model needs the pixels only, so
every path that sends image bytes to a cloud model sends them through
`strip_metadata`.

The pixels are kept, turned upright first (the EXIF orientation is applied,
since it is dropped with the rest). The format is kept. Nothing is resized.

When the image cannot be read here (a format Pillow cannot open, a corrupt
file), the bytes go out unchanged and the result says so, so the egress
ledger row is honest about it.
"""
from __future__ import annotations

import io
from typing import Tuple

#: Formats re-encoded in place. Anything else is sent unchanged (and said so).
_REENCODE = {"JPEG", "PNG", "WEBP", "TIFF", "BMP", "GIF"}
#: `Image.info` keys that carry metadata rather than pixels.
_META_KEYS = ("exif", "xmp", "XML:com.adobe.xmp", "comment", "photoshop", "icc_profile")


def _has_metadata(im) -> bool:
    try:
        if len(im.getexif()) > 0:
            return True
    except Exception:
        pass
    return any(im.info.get(k) for k in _META_KEYS)


def strip_metadata(data: bytes) -> Tuple[bytes, str]:
    """(bytes to send, what happened): "stripped", "no_metadata" or
    "not_stripped:<why>". Pure; never raises."""
    if not data:
        return data, "not_stripped:empty"
    try:
        from PIL import Image, ImageOps
    except Exception:
        return data, "not_stripped:no_pillow"
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = (im.format or "").upper()
            if fmt not in _REENCODE:
                return data, "not_stripped:format_%s" % (fmt.lower() or "unknown")
            had = _has_metadata(im)
            if fmt == "GIF" and getattr(im, "is_animated", False):
                # GIF carries no EXIF or GPS; re-encoding an animation is not
                # worth the risk of breaking it. Its comment, if any, stays.
                return data, "not_stripped:animated_gif" if had else "no_metadata"
            im.load()
            upright = ImageOps.exif_transpose(im)
            # A fresh image holds only pixels: no info, no EXIF, no profile.
            clean = Image.new(upright.mode, upright.size)
            if upright.mode == "P":
                clean.putpalette(upright.getpalette() or [])
            clean.paste(upright)
            kwargs = {}
            if "transparency" in upright.info:
                kwargs["transparency"] = upright.info["transparency"]
            if fmt == "JPEG":
                kwargs = {"quality": 95}
                if clean.mode not in ("RGB", "L", "CMYK"):
                    clean = clean.convert("RGB")
            out = io.BytesIO()
            clean.save(out, format=fmt, **kwargs)
            return out.getvalue(), ("stripped" if had else "no_metadata")
    except Exception as e:
        return data, "not_stripped:%s" % type(e).__name__
