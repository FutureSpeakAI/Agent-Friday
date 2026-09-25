"""Where the page's typefaces come from.

The page always links /static/fonts/fonts.css, which declares Orbitron, Inter
and JetBrains Mono from files in static/fonts and falls back to system faces,
so it never needs the network for a font. Google Fonts is added to the page
only when BOTH hold:

  * the owner has not turned it off (`web_fonts_from_google`, Settings ->
    Privacy & Approvals), and
  * the font files are not in static/fonts. Once they are, Google Fonts is
    never requested, whatever the setting says.

Loading from Google Fonts reveals this PC's address to Google on every page
load; docs/user-guide/background-network.md says so.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

#: static/fonts/fonts.css is Google Fonts' own stylesheet for GOOGLE_FONTS_HREF
#: with each url() pointing at a copy under static/fonts/g/, so the page
#: renders exactly as it did from Google. All three families are under the SIL
#: Open Font License 1.1; NOTICE lists them and their source.
_LOCAL_URL = re.compile(r"url\(/static/fonts/(g/[A-Za-z0-9._-]+\.woff2)\)")


def font_files() -> list:
    """The font files fonts.css points at, relative to static/fonts."""
    try:
        css = (fonts_dir() / "fonts.css").read_text(encoding="utf-8")
    except OSError:
        return []
    return sorted(set(_LOCAL_URL.findall(css)))

GOOGLE_FONTS_HREF = ("https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900"
                     "&family=Inter:wght@300;400;500;600;700"
                     "&family=JetBrains+Mono:wght@400;500&display=swap")


def fonts_dir() -> Path:
    return Path(os.path.abspath("static")) / "fonts"


def vendored() -> bool:
    d = fonts_dir()
    files = font_files()
    return bool(files) and all((d / f).is_file() for f in files)


def use_google_fonts(settings: dict | None = None) -> bool:
    if vendored():
        return False
    if settings is None:
        try:
            from agent_friday.core import _load_settings
            settings = _load_settings() or {}
        except Exception:
            settings = {}
    return bool((settings or {}).get("web_fonts_from_google", True))


def head_html(settings: dict | None = None) -> str:
    """The <head> addition for the Google Fonts stylesheet, or ""."""
    if not use_google_fonts(settings):
        return ""
    # media="print" defers the render-blocking fetch; onload switches it on.
    return (f'<link href="{GOOGLE_FONTS_HREF}" rel="stylesheet" media="print" '
            'onload="this.media=\'all\'"/>')
