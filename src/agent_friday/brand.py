"""Friday's brand, as values Python can read.

This module is the single source of truth for the palette, the faces and the
type scale. The UI carries a `:root` block of `--fr-*` custom properties in
`index.html` and `ui_parts/head.html`; that block is `css_root_block()` below,
byte for byte, and `scripts/check_brand_tokens.py` fails when either file
differs from it (`--write` regenerates both). The prose half is
`docs/brand/BRAND.md`.

The values here are the ones the product already ships. Nothing in this module
is a proposal.

Rules that live here because they are about meaning, not looks:

* Cyan is the brand. The holographic triad is cyan, violet, magenta.
* Decoration never borrows a status hue. Category accents, lane dots and
  account badges draw from the CAT_* and ACCOUNT_* hues; the guard fails a
  category property that points at a status token.
* A live microphone is the brand (cyan), not a failure.
* Four hues are reserved for status. A failure is ERROR, a refusal is DENY,
  amber (WARN) means only "needs you", OK means done or connected.
  Violet is the colour of work in progress.
* FutureSpeak amber is an accent only inside the "FutureSpeak.AI" wordmark.
  It shares its value with WARN, so anywhere else amber reads as "needs you".
* The product is Agent Friday(TM) wherever the brand shows. Her own name is the
  one the user chose (settings `agent_name`) and belongs to conversation. The
  mark is added to text as it is displayed (`tm`), never to what is stored, what
  a model reads or what is spoken (`spoken` takes it back off for audio).

Stdlib only, no imports from the rest of the package, so build tooling can
load this file directly.
"""
from __future__ import annotations

import json
import re

# -- identity ----------------------------------------------------------------
CYAN = "#00d4ff"
CYAN_SOFT = "rgba(0,212,255,0.12)"
VIOLET = "#7b61ff"
MAGENTA = "#ff00ff"
TRIAD = (CYAN, VIOLET, MAGENTA)
#: The lighter violet the news and message workspaces already use.
VIOLET_SOFT = "#a78bfa"

# -- scoped category hues the UI already ships (news, message lanes) ---------
CAT_TEAL = "#2dd4bf"
CAT_PINK = "#f472b6"
CAT_BLUE = "#60a5fa"
#: A warm neutral that is deliberately not amber.
CAT_SAND = "#d6c7a1"

# -- reserved status hues ----------------------------------------------------
OK = "#00ff80"
#: "Needs you": waiting on an approval or a decision. Never "running".
WARN = "#f59e0b"
#: A rule refused, or you said no.
DENY = "#ff0080"
#: Something failed.
ERROR = "#ef4444"
#: Disconnected, idle, unknown: a state with no opinion.
NEUTRAL = "#7a8699"
#: The amber in the "FutureSpeak.AI" wordmark. Same value as WARN by design.
WORDMARK_AMBER = WARN

# -- surfaces and inks -------------------------------------------------------
SURFACE = "#0a0e1a"
GLASS = "rgba(10,14,26,0.75)"
GLASS_BLUR = "blur(16px) saturate(1.2)"
GLASS_EDGE = "rgba(255,255,255,0.06)"
TEXT = "rgba(255,255,255,0.86)"
TEXT_LABEL = "rgba(255,255,255,0.78)"
TEXT_DIM = "rgba(255,255,255,0.46)"
TEXT_FAINT = "rgba(255,255,255,0.3)"

# Standalone pages Friday generates (Studio showcase) carry their own ground.
PAGE_BG = "#07080d"
PAGE_PANEL = "#10121c"
PAGE_TEXT = "#e8e8f0"
PAGE_MUTED = "#a8a8b4"

#: Hues that tell one connected account from another, in assignment order.
#: Account colour is decoration and holds no status hue.
ACCOUNT_PINK = "#ec4899"
ACCOUNT_PURPLE = "#a855f7"
ACCOUNT_TEAL = "#14b8a6"
ACCOUNT_SILVER = "#cbd5e1"
ACCOUNT_PALETTE = (CYAN, ACCOUNT_PURPLE, ACCOUNT_TEAL, ACCOUNT_PINK, CAT_BLUE, CAT_SAND, ACCOUNT_SILVER)
#: Status hues an earlier palette handed to accounts, mapped to the palette slot
#: each one occupied. A saved account record carrying one shows that slot instead.
RETIRED_ACCOUNT_HUES = {"#22c55e": 2, OK: 2, WARN: 3, ERROR: 6}

# -- type ----------------------------------------------------------------------
FONT_DISPLAY = "'Orbitron', sans-serif"
FONT_BODY = "'Inter', system-ui, sans-serif"
FONT_MONO = "'JetBrains Mono', ui-monospace, monospace"

#: The sizes the interface already uses most, in px.
TYPE_SCALE = {
    "2xs": "9px",
    "xs": "10px",
    "sm": "11px",
    "md": "12px",
    "base": "13px",
    "lg": "15px",
    "xl": "18px",
    "2xl": "26px",
}

#: How the top bar, the dock and the start screen's cluster come and go:
#: one duration and easing, so they move as one (unified-shell.md §10.3).
REVEAL_TIME = "0.35s"
REVEAL = REVEAL_TIME + " cubic-bezier(0.2, 0.8, 0.3, 1)"

#: Every `--fr-*` custom property, in the order the :root block declares them.
TOKENS = {
    "--fr-cyan": CYAN,
    "--fr-cyan-soft": CYAN_SOFT,
    "--fr-violet": VIOLET,
    "--fr-magenta": MAGENTA,
    "--fr-violet-soft": VIOLET_SOFT,
    "--fr-cat-teal": CAT_TEAL,
    "--fr-cat-pink": CAT_PINK,
    "--fr-cat-blue": CAT_BLUE,
    "--fr-cat-sand": CAT_SAND,
    "--fr-ok": OK,
    "--fr-warn": WARN,
    "--fr-deny": DENY,
    "--fr-error": ERROR,
    "--fr-neutral": NEUTRAL,
    "--fr-wordmark-amber": WORDMARK_AMBER,
    "--fr-surface": SURFACE,
    "--fr-glass": GLASS,
    "--fr-glass-blur": GLASS_BLUR,
    "--fr-glass-edge": GLASS_EDGE,
    "--fr-text": TEXT,
    "--fr-label": TEXT_LABEL,
    "--fr-dim": TEXT_DIM,
    "--fr-faint": TEXT_FAINT,
    "--fr-font-display": FONT_DISPLAY,
    "--fr-font-body": FONT_BODY,
    "--fr-font-mono": FONT_MONO,
    **{f"--fr-text-{step}": size for step, size in TYPE_SCALE.items()},
    "--fr-track-display": "0.06em",
    "--fr-track-label": "0.15em",
    "--fr-reveal": REVEAL,
    "--fr-reveal-time": REVEAL_TIME,
}

# -- names -------------------------------------------------------------------
#: The product and its maker as audio and models get them. Neither is shown
#: plain: shown text uses PRODUCT_NAME and MAKER_NAME, which carry the
#: trademark sign: the unregistered mark, never the registered one, since
#: neither mark is registered.
PRODUCT = "Agent Friday"
MAKER = "FutureSpeak.AI"
TRADEMARK = "\u2122"
PRODUCT_NAME = PRODUCT + TRADEMARK
MAKER_NAME = MAKER + TRADEMARK
#: The wordmark: the tray tooltip, the top bar, the About card.
PRODUCT_LOCKUP = f"{PRODUCT_NAME} by {MAKER_NAME}"
#: The mark on pages Friday publishes and on exports.
MADE_WITH = f"Made with {PRODUCT_NAME}"
#: Under the lockup in Settings' About card, and in the README. Its last words
#: name the company that owns both marks, so they are written plain.
TRADEMARK_NOTICE = f"{PRODUCT_NAME} and {MAKER_NAME} are trademarks of {MAKER}."


def from_product(what: str) -> str:
    """How written material credits the product: "The Briefing from Agent Friday\u2122"."""
    return f"{what} from {PRODUCT_NAME}"


#: Fenced code blocks and inline code: display text inside them is left as it is.
_CODE = re.compile(r"(```[\s\S]*?(?:```|$)|`[^`\n]*`)")
#: Not already marked, in any of the forms people type.
_UNMARKED = r"(?![ \t]*(?:\u2122|\u00ae|\((?:[tT][mM]|[rR])\)))"
#: The product's words, not an identifier or an address (agent_friday,
#: agent-friday), and not already marked.
_PRODUCT_WORDS = re.compile(
    r"(?<![\w-])(agent[ \t\u00a0]+friday)(?![\w-])" + _UNMARKED, re.IGNORECASE)
#: The maker as its name is written, not a web or email address
#: (futurespeak.ai, name@futurespeak.ai, https://futurespeak.ai/...).
_MAKER_WORDS = re.compile(r"(?<![\w@/.-])(FutureSpeak\.AI)(?![\w/-])" + _UNMARKED)
#: Where "FutureSpeak.AI" names the company as owner, and stays plain.
_OWNER = re.compile(r"(?:trademarks? of|\u00a9|\(c\)|copyright)[ \t\d,\u2013-]*$", re.IGNORECASE)
_SPOKEN_MARK = re.compile(r"[ \t]*\u2122|(?:(?<=friday)|(?<=\.ai))[ \t]*\(tm\)", re.IGNORECASE)


def _mark_maker(part: str) -> str:
    return _MAKER_WORDS.sub(
        lambda m: m.group(1) if _OWNER.search(part[max(0, m.start() - 24):m.start()])
        else m.group(1) + TRADEMARK, part)


def her_name(agent_name) -> str:
    """Her name as the owner gave it (settings `agent_name`), as it is said
    and shown: a name stored in capitals ("AGENT FRIDAY", her default) reads
    in title case, the last word for the default. The page's fridayNameInText
    is the same rule."""
    n = str(agent_name or "").strip()
    if not n or n.upper() == "AGENT FRIDAY":
        return "Friday"
    if n == n.upper() and re.search(r"[A-Z]", n):
        return re.sub(r"(^|[\s-])(\S)", lambda m: m.group(1) + m.group(2).upper(), n.lower())
    return n


def tm(text: str) -> str:
    """`text` as it is displayed: "Agent Friday" and "FutureSpeak.AI" gain
    their trademark sign.

    Idempotent (a marked name is left alone, as are the (R) and (TM) forms),
    and code, fenced or inline, is not touched, nor is an address. Where
    "FutureSpeak.AI" names the owner ("trademarks of", a copyright line) it
    stays plain. For display only: stored text, text a model reads and text
    that is spoken stay plain.
    """
    text = "" if text is None else str(text)
    low = text.lower()
    if "friday" not in low and "futurespeak.ai" not in low:
        return text
    parts = _CODE.split(text)
    return "".join(p if i % 2 else _mark_maker(_PRODUCT_WORDS.sub(lambda m: m.group(1) + TRADEMARK, p))
                   for i, p in enumerate(parts))


def spoken(text: str) -> str:
    """`text` as it is spoken: the trademark signs come off, so the names are
    said "Agent Friday" and "FutureSpeak.AI" and never with a "T M"."""
    text = "" if text is None else str(text)
    return _SPOKEN_MARK.sub("", text) if ("\u2122" in text or "(" in text) else text


BEGIN_MARKER = "brand-tokens:begin"
END_MARKER = "brand-tokens:end"
NAMES_BEGIN_MARKER = "brand-names:begin"
NAMES_END_MARKER = "brand-names:end"


def css_root_block() -> str:
    """The exact text of the `:root` token block the UI files carry."""
    lines = [f"/* {BEGIN_MARKER} (generated from src/agent_friday/brand.py) */", ":root {"]
    lines += [f"    {name}: {value};" for name, value in TOKENS.items()]
    lines += ["}", f"/* {END_MARKER} */"]
    return "\n".join(lines)


#: What the page reads the names from (window.FRIDAY_BRAND).
PAGE_NAMES = {
    "product": PRODUCT,
    "name": PRODUCT_NAME,
    "maker": MAKER,
    "makerName": MAKER_NAME,
    "lockup": PRODUCT_LOCKUP,
    "madeWith": MADE_WITH,
    "notice": TRADEMARK_NOTICE,
    "mark": TRADEMARK,
}


def js_names_block() -> str:
    """The exact text of the names block the UI files carry."""
    return "\n".join([
        f"/* {NAMES_BEGIN_MARKER} (generated from src/agent_friday/brand.py) */",
        "window.FRIDAY_BRAND = " + json.dumps(PAGE_NAMES, ensure_ascii=True) + ";",
        f"/* {NAMES_END_MARKER} */",
    ])


__all__ = [n for n in dir() if n.isupper()] + ["css_root_block", "js_names_block", "tm", "spoken"]
