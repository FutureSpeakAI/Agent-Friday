"""remote_image: the one way a remote picture reaches Friday's page.

Model and tool output never makes the browser fetch from outside (the page CSP
admits only Friday's own origin for images), so a reply that embeds a remote
image shows a placeholder naming the host. When the owner clicks it, the page
asks here, Friday fetches the picture itself behind the SSRF guard and hands
back the bytes, and the page shows them from a blob: URL. The fetch carries no
cookie, token, referrer or other identity; it returns only raster images, and
only up to a size cap. The owner's click is the consent; nothing else calls
this.
"""
from __future__ import annotations

from urllib.parse import urlparse

from agent_friday.services import web_safety

MAX_BYTES = 5 * 1024 * 1024
TIMEOUT_S = 10

# SVG is a document, not a picture: it is refused whatever it claims to be.
_MAGIC = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),
}


class RemoteImageRefused(ValueError):
    """The picture will not be fetched or shown. The message is for the owner."""


def _looks_like(ctype: str, body: bytes) -> bool:
    if not any(body.startswith(m) for m in _MAGIC[ctype]):
        return False
    return ctype != "image/webp" or body[8:12] == b"WEBP"


def fetch_image(url: str) -> tuple[bytes, str]:
    """(bytes, content type) for an http(s) raster image, or RemoteImageRefused."""
    url = (url or "").strip()
    if urlparse(url).scheme.lower() not in ("http", "https"):
        raise RemoteImageRefused("only http and https pictures can be loaded")
    try:
        resp = web_safety.safe_get(
            url, timeout=TIMEOUT_S, stream=True,
            headers={"Accept": "image/png,image/jpeg,image/gif,image/webp"})
    except web_safety.UnsafeURLError as e:
        raise RemoteImageRefused(str(e)) from e
    except Exception as e:
        raise RemoteImageRefused("the host did not answer") from e
    try:
        if resp.status_code != 200:
            raise RemoteImageRefused(f"the host answered {resp.status_code}")
        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype not in _MAGIC:
            raise RemoteImageRefused("that address is not a PNG, JPEG, GIF or WebP picture")
        try:
            declared = int(resp.headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        if declared > MAX_BYTES:
            raise RemoteImageRefused("the picture is larger than 5 MB")
        chunks, size = [], 0
        for chunk in resp.iter_content(65536):
            size += len(chunk)
            if size > MAX_BYTES:
                raise RemoteImageRefused("the picture is larger than 5 MB")
            chunks.append(chunk)
        body = b"".join(chunks)
    finally:
        try:
            resp.close()
        except Exception:
            pass
    if not _looks_like(ctype, body):
        raise RemoteImageRefused("the file is not the kind of picture it says it is")
    return body, ctype
