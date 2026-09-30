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
* Four hues are reserved for status. A failure is ERROR, a refusal is DENY,
  amber (WARN) means only "needs you", OK means done or connected.
  Violet is the colour of work in progress.
* FutureSpeak amber is an accent only inside the "FutureSpeak.AI" wordmark.
  It shares its value with WARN, so anywhere else amber reads as "needs you".

Stdlib only, no imports from the rest of the package, so build tooling can
load this file directly.
"""
from __future__ import annotations

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
#: The two purple/pink/teal members are account-only decoration; the rest are
#: tokens. Account colour is decoration and must never carry a status.
ACCOUNT_PINK = "#ec4899"
ACCOUNT_PURPLE = "#a855f7"
ACCOUNT_TEAL = "#14b8a6"
ACCOUNT_PALETTE = (CYAN, ACCOUNT_PURPLE, OK, WARN, ACCOUNT_PINK, ACCOUNT_TEAL, ERROR)

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

#: Every `--fr-*` custom property, in the order the :root block declares them.
TOKENS = {
    "--fr-cyan": CYAN,
    "--fr-cyan-soft": CYAN_SOFT,
    "--fr-violet": VIOLET,
    "--fr-magenta": MAGENTA,
    "--fr-violet-soft": VIOLET_SOFT,
    "--fr-cat-teal": CAT_TEAL,
    "--fr-cat-pink": CAT_PINK,
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
}

BEGIN_MARKER = "brand-tokens:begin"
END_MARKER = "brand-tokens:end"


def css_root_block() -> str:
    """The exact text of the `:root` token block the UI files carry."""
    lines = [f"/* {BEGIN_MARKER} (generated from src/agent_friday/brand.py) */", ":root {"]
    lines += [f"    {name}: {value};" for name, value in TOKENS.items()]
    lines += ["}", f"/* {END_MARKER} */"]
    return "\n".join(lines)


__all__ = [n for n in dir() if n.isupper()] + ["css_root_block"]
