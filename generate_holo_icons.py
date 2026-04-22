#!/usr/bin/env python3
"""
Gemini Design Refinement — Holographic Dock Icons
Generates premium holographic-style SVG dock icons for Friday Desktop.
Each icon uses prismatic gradients (cyan→purple→magenta), glow filters,
and geometric line-art inspired by sci-fi HUD interfaces.
"""
import os, json
from pathlib import Path

ASSETS_DIR = Path(__file__).parent / "assets" / "icons"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

# Shared SVG defs for holographic styling
DEFS = '''  <defs>
    <linearGradient id="hg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#00d4ff"/>
      <stop offset="40%" stop-color="#7b61ff"/>
      <stop offset="100%" stop-color="#ff00ff"/>
    </linearGradient>
    <linearGradient id="hg2" x1="100%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#00ffcc"/>
      <stop offset="50%" stop-color="#00d4ff"/>
      <stop offset="100%" stop-color="#7b61ff"/>
    </linearGradient>
    <filter id="gl">
      <feGaussianBlur stdDeviation="0.7" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>'''

def svg(inner):
    return f'<svg viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg" fill="none">\n{DEFS}\n{inner}\n</svg>'

def s(d, sw="1.5"):
    """Stroke helper — holographic gradient + glow."""
    return f'stroke="url(#hg)" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round" filter="url(#gl)" {d}'

ICONS = {
    # ─── LIFE ───
    "home": svg(f'''  <path {s('d="M16 4L3 15h4v12h7v-8h4v8h7V15h4L16 4z"', "1.4")}/>
  <line x1="13" y1="24" x2="13" y2="19" stroke="url(#hg2)" stroke-width="1" opacity="0.5"/>
  <line x1="19" y1="24" x2="19" y2="19" stroke="url(#hg2)" stroke-width="1" opacity="0.5"/>
  <rect x="13" y="17" width="6" height="3" stroke="url(#hg2)" stroke-width="0.8" fill="none" opacity="0.4"/>'''),

    "family": svg(f'''  <circle cx="10" cy="8" r="3" {s("")}/>
  <circle cx="22" cy="8" r="3" {s("")}/>
  <circle cx="16" cy="18" r="2.5" {s("")}/>
  <path {s('d="M4 17c0-3 2.5-5 6-5s6 2 6 5"', "1.2")}/>
  <path {s('d="M16 17c0-3 2.5-5 6-5s6 2 6 5"', "1.2")}/>
  <line x1="16" y1="21" x2="16" y2="28" stroke="url(#hg2)" stroke-width="1" opacity="0.6"/>'''),

    "coparent": svg(f'''  <circle cx="7" cy="9" r="3" {s("")}/>
  <circle cx="25" cy="9" r="3" {s("")}/>
  <circle cx="16" cy="14" r="2.5" {s("")}/>
  <path {s('d="M3 18c0-2.5 1.8-4 4-4"', "1.2")}/>
  <path {s('d="M29 18c0-2.5-1.8-4-4-4"', "1.2")}/>
  <path d="M7 18L16 24L25 18" stroke="url(#hg2)" stroke-width="1" stroke-dasharray="2 1.5" fill="none" opacity="0.5" filter="url(#gl)"/>
  <line x1="16" y1="17" x2="16" y2="24" stroke="url(#hg)" stroke-width="1" opacity="0.5"/>'''),

    "health": svg(f'''  <path {s('d="M16 28L4 16c-3-3-3-8 1-10s7 0 9 2l2 2 2-2c2-2 5-4 9-2s4 7 1 10L16 28z"', "1.3")}/>
  <polyline points="6,18 12,18 14,13 16,22 18,16 20,18 26,18" stroke="url(#hg2)" stroke-width="1.2" fill="none" filter="url(#gl)"/>'''),

    "finance": svg(f'''  <rect x="4" y="22" width="4" height="6" rx="1" {s("", "1.2")}/>
  <rect x="10" y="17" width="4" height="11" rx="1" {s("", "1.2")}/>
  <rect x="16" y="12" width="4" height="16" rx="1" {s("", "1.2")}/>
  <rect x="22" y="6" width="4" height="22" rx="1" {s("", "1.2")}/>
  <path d="M4 22L10 17L16 12L22 6" stroke="url(#hg2)" stroke-width="1" stroke-dasharray="2 1" fill="none" opacity="0.5" filter="url(#gl)"/>
  <circle cx="26" cy="4" r="1.5" stroke="url(#hg)" stroke-width="1" fill="none" filter="url(#gl)"/>'''),

    # ─── WORK ───
    "career": svg(f'''  <rect x="6" y="12" width="20" height="14" rx="2" {s("", "1.3")}/>
  <path {s('d="M11 12V9a5 5 0 0110 0v3"', "1.3")}/>
  <path d="M16 17v4" stroke="url(#hg2)" stroke-width="1.2" filter="url(#gl)"/>
  <circle cx="16" cy="17" r="1.5" stroke="url(#hg2)" stroke-width="1" fill="none" filter="url(#gl)"/>'''),

    "futurespeak": svg(f'''  <path {s('d="M16 3L10 13h4v7l6-10h-4V3z"', "1.2")}/>
  <circle cx="16" cy="16" r="12" stroke="url(#hg2)" stroke-width="0.8" stroke-dasharray="3 2" fill="none" opacity="0.3" filter="url(#gl)"/>
  <circle cx="16" cy="16" r="8" stroke="url(#hg)" stroke-width="0.6" stroke-dasharray="1.5 2" fill="none" opacity="0.25"/>
  <path d="M8 26l3-3M24 26l-3-3" stroke="url(#hg2)" stroke-width="1" opacity="0.4" filter="url(#gl)"/>'''),

    "contacts": svg(f'''  <circle cx="16" cy="16" r="2.5" {s("")}/>
  <circle cx="6" cy="8" r="2" {s("", "1.2")}/>
  <circle cx="26" cy="8" r="2" {s("", "1.2")}/>
  <circle cx="6" cy="24" r="2" {s("", "1.2")}/>
  <circle cx="26" cy="24" r="2" {s("", "1.2")}/>
  <line x1="8" y1="9.5" x2="14" y2="14.5" stroke="url(#hg2)" stroke-width="0.8" opacity="0.5" filter="url(#gl)"/>
  <line x1="24" y1="9.5" x2="18" y2="14.5" stroke="url(#hg2)" stroke-width="0.8" opacity="0.5" filter="url(#gl)"/>
  <line x1="8" y1="22.5" x2="14" y2="17.5" stroke="url(#hg2)" stroke-width="0.8" opacity="0.5" filter="url(#gl)"/>
  <line x1="24" y1="22.5" x2="18" y2="17.5" stroke="url(#hg2)" stroke-width="0.8" opacity="0.5" filter="url(#gl)"/>'''),

    "draft": svg(f'''  <path {s('d="M22 4L8 18l-2 8 8-2L28 10 22 4z"', "1.3")}/>
  <line x1="18" y1="8" x2="24" y2="14" stroke="url(#hg2)" stroke-width="1" opacity="0.4"/>
  <path d="M6 26h20" stroke="url(#hg2)" stroke-width="0.8" stroke-dasharray="2 2" opacity="0.3" filter="url(#gl)"/>'''),

    "content": svg(f'''  <rect x="6" y="3" width="20" height="26" rx="2" {s("", "1.3")}/>
  <line x1="10" y1="9" x2="22" y2="9" stroke="url(#hg2)" stroke-width="1" opacity="0.6" filter="url(#gl)"/>
  <line x1="10" y1="14" x2="20" y2="14" stroke="url(#hg2)" stroke-width="1" opacity="0.5"/>
  <line x1="10" y1="19" x2="18" y2="19" stroke="url(#hg2)" stroke-width="1" opacity="0.4"/>
  <line x1="10" y1="24" x2="16" y2="24" stroke="url(#hg2)" stroke-width="1" opacity="0.3"/>'''),

    # ─── SYSTEM ───
    "news": svg(f'''  <rect x="4" y="5" width="24" height="22" rx="2" {s("", "1.3")}/>
  <line x1="8" y1="10" x2="24" y2="10" stroke="url(#hg2)" stroke-width="1.5" opacity="0.7" filter="url(#gl)"/>
  <line x1="8" y1="15" x2="16" y2="15" stroke="url(#hg2)" stroke-width="0.8" opacity="0.4"/>
  <line x1="8" y1="19" x2="18" y2="19" stroke="url(#hg2)" stroke-width="0.8" opacity="0.35"/>
  <line x1="8" y1="23" x2="14" y2="23" stroke="url(#hg2)" stroke-width="0.8" opacity="0.3"/>
  <rect x="19" y="14" width="5" height="5" rx="0.5" stroke="url(#hg2)" stroke-width="0.8" fill="none" opacity="0.4"/>
  <path d="M2 9a4 4 0 013-3M2 13a8 8 0 017-7" stroke="url(#hg)" stroke-width="0.7" opacity="0.3" filter="url(#gl)"/>'''),

    "wiki": svg(f'''  <path {s('d="M5 4v24l3-2 3 2 3-2 3 2 3-2 3 2 3-2 3 2V4l-3 2-3-2-3 2-3-2-3 2-3-2-3 2L5 4z"', "1.2")}/>
  <line x1="10" y1="11" x2="22" y2="11" stroke="url(#hg2)" stroke-width="0.8" opacity="0.5"/>
  <line x1="10" y1="16" x2="22" y2="16" stroke="url(#hg2)" stroke-width="0.8" opacity="0.4"/>
  <line x1="10" y1="21" x2="18" y2="21" stroke="url(#hg2)" stroke-width="0.8" opacity="0.35"/>
  <circle cx="23" cy="7" r="2" stroke="url(#hg)" stroke-width="0.7" fill="none" opacity="0.4" filter="url(#gl)"/>'''),

    "trust": svg(f'''  <path {s('d="M16 3l10 5v8c0 6-4 10-10 13C10 26 6 22 6 16V8l10-5z"', "1.3")}/>
  <polyline points="11,16 15,20 22,12" stroke="url(#hg2)" stroke-width="1.5" fill="none" filter="url(#gl)"/>'''),

    "studio": svg(f'''  <circle cx="16" cy="16" r="11" {s("", "1.2")}/>
  <circle cx="11" cy="11" r="2.5" stroke="#00d4ff" stroke-width="1.2" fill="none" filter="url(#gl)"/>
  <circle cx="21" cy="11" r="2.5" stroke="#7b61ff" stroke-width="1.2" fill="none" filter="url(#gl)"/>
  <circle cx="11" cy="21" r="2.5" stroke="#ff00ff" stroke-width="1.2" fill="none" filter="url(#gl)"/>
  <circle cx="21" cy="21" r="2.5" stroke="#00ff88" stroke-width="1.2" fill="none" filter="url(#gl)"/>
  <circle cx="16" cy="16" r="1" stroke="url(#hg)" stroke-width="1" fill="none"/>'''),

    "code": svg(f'''  <polyline points="10,8 4,16 10,24" {s("", "1.5")}/>
  <polyline points="22,8 28,16 22,24" {s("", "1.5")}/>
  <line x1="18" y1="6" x2="14" y2="26" stroke="url(#hg2)" stroke-width="1.2" opacity="0.6" filter="url(#gl)"/>
  <rect x="14" y="15" width="4" height="2" rx="0.5" fill="url(#hg)" opacity="0.3">
    <animate attributeName="opacity" values="0.3;0.7;0.3" dur="1.2s" repeatCount="indefinite"/>
  </rect>'''),

    "system": svg(f'''  <rect x="4" y="4" width="24" height="18" rx="2" {s("", "1.3")}/>
  <line x1="12" y1="22" x2="12" y2="27" stroke="url(#hg2)" stroke-width="1.2" filter="url(#gl)"/>
  <line x1="20" y1="22" x2="20" y2="27" stroke="url(#hg2)" stroke-width="1.2" filter="url(#gl)"/>
  <line x1="8" y1="27" x2="24" y2="27" stroke="url(#hg)" stroke-width="1.5" filter="url(#gl)"/>
  <line x1="8" y1="10" x2="14" y2="10" stroke="url(#hg2)" stroke-width="0.8" opacity="0.5"/>
  <line x1="8" y1="13" x2="20" y2="13" stroke="url(#hg2)" stroke-width="0.8" opacity="0.4"/>
  <line x1="8" y1="16" x2="12" y2="16" stroke="url(#hg2)" stroke-width="0.8" opacity="0.35"/>
  <circle cx="22" cy="10" r="1" fill="url(#hg)" opacity="0.5">
    <animate attributeName="opacity" values="0.3;0.8;0.3" dur="2s" repeatCount="indefinite"/>
  </circle>'''),
}

# ─── Holographic CSS refinements ──────────────────────────────
HOLOGRAPHIC_CSS = '''/* ═══ GEMINI DESIGN PASS — Holographic Dock Refinements ═══
   Generated for Friday Desktop by Gemini design pipeline.
   Layers prismatic holographic effects over existing glassmorphism. */

/* Prismatic shimmer keyframes */
@keyframes holoShimmer {
  0%   { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}

@keyframes holoPulse {
  0%, 100% { filter: brightness(1) drop-shadow(0 0 2px rgba(0,212,255,0.2)); }
  50%      { filter: brightness(1.15) drop-shadow(0 0 6px rgba(123,97,255,0.35)); }
}

@keyframes holoScanline {
  0%   { transform: translateY(-100%); }
  100% { transform: translateY(100%); }
}

@keyframes prismaticBorder {
  0%   { border-color: rgba(0,212,255,0.25); }
  33%  { border-color: rgba(123,97,255,0.25); }
  66%  { border-color: rgba(255,0,255,0.2); }
  100% { border-color: rgba(0,212,255,0.25); }
}

/* ── Dock: holographic overlay ───────────────────────── */
.dock {
  background:
    linear-gradient(90deg,
      transparent 0%,
      rgba(0,212,255,0.03) 25%,
      rgba(123,97,255,0.04) 50%,
      rgba(255,0,255,0.03) 75%,
      transparent 100%
    ),
    rgba(3,3,8,0.75);
  background-size: 200% 100%, 100% 100%;
  animation: holoShimmer 8s linear infinite;
  border-top: 1px solid rgba(0,212,255,0.08);
  box-shadow:
    0 -1px 0 rgba(123,97,255,0.05),
    inset 0 1px 30px rgba(0,212,255,0.02);
}

/* Subtle scanline overlay on dock */
.dock::after {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; bottom: 0;
  background: repeating-linear-gradient(
    0deg,
    transparent,
    transparent 2px,
    rgba(0,212,255,0.008) 2px,
    rgba(0,212,255,0.008) 4px
  );
  pointer-events: none;
  z-index: 1;
}

/* ── Dock buttons: holographic treatment ─────────────── */
.dock-btn {
  position: relative;
  z-index: 2;
  animation: prismaticBorder 6s ease infinite;
}

.dock-btn:hover {
  background: rgba(0,212,255,0.06);
  border-color: rgba(123,97,255,0.3);
  box-shadow:
    0 0 12px rgba(0,212,255,0.1),
    0 0 24px rgba(123,97,255,0.06),
    inset 0 0 12px rgba(0,212,255,0.04);
}

.dock-btn.active {
  background:
    linear-gradient(135deg,
      rgba(0,212,255,0.08),
      rgba(123,97,255,0.06),
      rgba(255,0,255,0.04)
    );
  border-color: rgba(0,212,255,0.3);
  box-shadow:
    0 0 16px rgba(0,212,255,0.15),
    0 0 32px rgba(123,97,255,0.08),
    inset 0 0 16px rgba(0,212,255,0.05);
}

/* ── SVG icon styling ────────────────────────────────── */
.dock-btn .ico {
  display: flex;
  align-items: center;
  justify-content: center;
}

.dock-btn .ico img {
  width: 20px;
  height: 20px;
  filter: drop-shadow(0 0 2px rgba(0,212,255,0.4));
  transition: filter 0.3s ease, transform 0.3s ease;
}

.dock-btn:hover .ico img {
  filter:
    drop-shadow(0 0 4px rgba(0,212,255,0.6))
    drop-shadow(0 0 8px rgba(123,97,255,0.3));
  transform: scale(1.1);
}

.dock-btn.active .ico img {
  filter:
    drop-shadow(0 0 4px rgba(0,212,255,0.7))
    drop-shadow(0 0 10px rgba(123,97,255,0.4))
    drop-shadow(0 0 2px rgba(255,0,255,0.2));
  animation: holoPulse 3s ease-in-out infinite;
}

/* ── Active dot: prismatic ───────────────────────────── */
.dock-btn.active .dot {
  background: linear-gradient(90deg, #00d4ff, #7b61ff, #ff00ff);
  background-size: 200% 100%;
  animation: holoShimmer 3s linear infinite;
  box-shadow: 0 0 6px #00d4ff, 0 0 12px rgba(123,97,255,0.4);
}

/* ── Group separator: holographic line ───────────────── */
.dock-sep {
  width: 1px;
  margin: 8px 4px;
  background: linear-gradient(180deg,
    transparent,
    rgba(0,212,255,0.15) 30%,
    rgba(123,97,255,0.2) 50%,
    rgba(255,0,255,0.15) 70%,
    transparent
  );
}
'''

def main():
    print("═══ FRIDAY Gemini Design Refinement ═══")
    print(f"Writing {len(ICONS)} holographic dock icons...")

    manifest = {}
    for icon_id, svg_content in ICONS.items():
        out_path = ASSETS_DIR / f"{icon_id}.svg"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(svg_content)
        manifest[icon_id] = f"assets/icons/{icon_id}.svg"
        print(f"  ✓ {icon_id}.svg ({len(svg_content)} bytes)")

    # Write manifest
    manifest_path = ASSETS_DIR.parent / "icon-manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\n  Manifest → {manifest_path}")

    # Write CSS
    css_path = ASSETS_DIR.parent / "holographic.css"
    with open(css_path, "w", encoding="utf-8") as f:
        f.write(HOLOGRAPHIC_CSS)
    print(f"  CSS      → {css_path} ({len(HOLOGRAPHIC_CSS)} bytes)")

    print(f"\n═══ Done. {len(ICONS)} icons + CSS generated. ═══")


if __name__ == "__main__":
    main()
