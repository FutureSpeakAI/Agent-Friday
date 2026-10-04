"""Hard caps for reading a document. A document that breaks one is reported as
"couldn't read: <reason>", never silently skipped."""
from __future__ import annotations

MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_PAGES = 2000
MAX_BLOCKS = 50_000
MAX_SECTIONS = 5_000
MAX_TEXT_BYTES = 10 * 1024 * 1024
MAX_ZIP_MEMBERS = 10_000
MAX_ZIP_UNCOMPRESSED = 100 * 1024 * 1024
OCR_SECONDS_PER_DOC = 60.0

# A rendered page image: longest edge in pixels and total pixels.
MAX_RENDER_EDGE = 2400
MAX_RENDER_PIXELS = 12_000_000


class CapExceeded(Exception):
    """A cap was hit. `str(e)` is the plain reason shown to the owner."""
