"""Draw the Agent Friday rocket mark from vector geometry, at every size.

One mark, drawn per size: the rocket fills the frame (about 92% of its height)
and each size band has its own stroke weight and detail:

  tiny  (16-24 px)  pixel-aligned silhouette, 2 px strokes, no glow, no smoke
  mid   (32-48 px)  medium detail, a faint glow
  full  (64-256 px) full detail with glow

Colours are the brand tokens from docs/brand/BRAND.md. Every size renders at
8x and is downsampled with LANCZOS. Run from the repository root:

    python scripts/brand/draw_rocket_icon.py

It rewrites the icon files in place (assets/, static/, the installer images).
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]

CYAN = (0x00, 0xD4, 0xFF)      # --fr-cyan
VIOLET = (0x7B, 0x61, 0xFF)    # --fr-violet
MAGENTA = (0xFF, 0x00, 0xFF)   # --fr-magenta
SURFACE = (0x0A, 0x0E, 0x1A)   # --fr-surface

SS = 8
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def band(size: int) -> str:
    return "tiny" if size <= 24 else "mid" if size <= 48 else "full"


def bez(p0, p1, p2, p3, n=24):
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                    u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]))
    return out


def mirror(pts):
    return [(1 - x, y) for x, y in pts]


class Geo:
    """Shapes in unit coordinates (0..1), x centred on 0.5."""

    def __init__(self, kind: str):
        self.nozzle = None
        self.puffs = []
        if kind == "tiny":
            # 16-unit grid, integer vertices: every edge lands on a pixel boundary.
            def g(pts):
                return [(x / 16, y / 16) for x, y in pts]
            self.hull = g([(8, 1), (5, 5), (5, 11), (11, 11), (11, 5)])
            self.fin_l = g([(5, 8), (2, 12), (2, 14), (5, 12)])
            self.fin_r = mirror(self.fin_l)
            self.flame = g([(7, 12), (8, 15), (9, 12)])
            self.window = (8 / 16, 6.5 / 16, 1.0 / 16)
            return
        nose_l = bez((0.5, 0.04), (0.43, 0.13), (0.37, 0.25), (0.37, 0.39))
        nose_r = mirror(nose_l)[::-1]
        self.hull = nose_l + [(0.37, 0.72), (0.63, 0.72)] + nose_r
        self.fin_l = [(0.37, 0.50), (0.215, 0.70), (0.215, 0.84), (0.37, 0.73)]
        self.fin_r = mirror(self.fin_l)
        self.nozzle = [(0.435, 0.72), (0.41, 0.80), (0.59, 0.80), (0.565, 0.72)]
        fl = bez((0.44, 0.80), (0.44, 0.88), (0.47, 0.93), (0.5, 0.965), 14)
        self.flame = fl + mirror(fl)[::-1]
        self.window = (0.5, 0.335, 0.075)


def render(size: int) -> Image.Image:
    kind = band(size)
    geo = Geo(kind)
    S = size * SS

    def P(pt):
        return (pt[0] * S, pt[1] * S)

    stroke = {"tiny": 2.0, "mid": 1.9, "full": 0.030 * size}[kind] * SS
    if kind == "full":
        stroke = max(stroke, 3.0 * SS)

    def mask():
        return Image.new("L", (S, S), 0)

    def poly(m, pts):
        ImageDraw.Draw(m).polygon([P(p) for p in pts], fill=255)

    def outline(m, pts, closed=True, w=None):
        w = stroke if w is None else w
        d = ImageDraw.Draw(m)
        pp = [P(p) for p in pts] + ([P(pts[0])] if closed else [])
        d.line(pp, fill=255, width=int(round(w)), joint="curve")
        r = w / 2
        for x, y in pp:
            d.ellipse((x - r, y - r, x + r, y + r), fill=255)

    hull_fill = mask()
    poly(hull_fill, geo.hull)
    hull_ln = mask()
    outline(hull_ln, geo.hull)
    fin_fill = mask()
    fin_ln = mask()
    for f in (geo.fin_l, geo.fin_r):
        poly(fin_fill, f)
        outline(fin_ln, f)
    flame_fill = mask()
    poly(flame_fill, geo.flame)
    flame_ln = mask()
    outline(flame_ln, geo.flame, w=stroke * 0.8)
    wx, wy, wr = geo.window
    box = ((wx - wr) * S, (wy - wr) * S, (wx + wr) * S, (wy + wr) * S)
    win_ln = mask()
    win_fill = mask()
    if kind == "tiny":
        ImageDraw.Draw(win_fill).rectangle(box, fill=255)
    else:
        ImageDraw.Draw(win_fill).ellipse(box, fill=255)
        ImageDraw.Draw(win_ln).ellipse(box, outline=255, width=int(round(stroke * 0.85)))
    noz_ln = mask()
    if geo.nozzle:
        outline(noz_ln, geo.nozzle, w=stroke * 0.85)
    puff_ln = mask()
    detail = mask()
    if size >= 128:
        outline(detail, [(0.5, 0.43), (0.5, 0.60)], closed=False, w=stroke * 0.55)
        outline(detail, [(0.37, 0.58), (0.63, 0.58)], closed=False, w=stroke * 0.55)

    glow_r = {"tiny": 0, "mid": 0.010, "full": 0.020 if size >= 128 else 0.014}[kind] * S
    glow_gain = {"tiny": 0, "mid": 0.45, "full": 0.6 if size >= 128 else 0.4}[kind]
    glow_srcs = [(hull_ln, CYAN), (fin_ln, MAGENTA), (flame_ln, VIOLET), (win_ln, CYAN),
                 (noz_ln, CYAN), (puff_ln, CYAN)]

    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    def paint(m, col, a=1.0):
        nonlocal canvas
        if a < 1:
            m = m.point(lambda v: int(v * a))
        solid = Image.new("RGBA", (S, S), col + (255,))
        solid.putalpha(m)
        canvas = Image.alpha_composite(canvas, solid)

    if glow_r:
        for m, col in glow_srcs:
            g = m.filter(ImageFilter.GaussianBlur(glow_r))
            g = g.point(lambda v: min(255, int(v * 3 * glow_gain)))
            paint(g, col, 0.9)
            g2 = m.filter(ImageFilter.GaussianBlur(glow_r * 0.4))
            paint(g2.point(lambda v: min(255, int(v * 1.4))), col, 0.5 * glow_gain)

    hf = {"tiny": 0.42, "mid": 0.18, "full": 0.10}[kind]
    paint(ImageChops.subtract(hull_fill, fin_fill), CYAN, hf)
    paint(fin_fill, MAGENTA, 0.55 if kind != "full" else 0.22)
    paint(flame_fill, VIOLET, 0.9 if kind == "tiny" else 0.45)
    paint(win_fill, VIOLET, 0.95 if kind == "tiny" else 0.35)
    paint(fin_ln, MAGENTA)
    paint(flame_ln, VIOLET if kind != "tiny" else MAGENTA)
    paint(hull_ln, CYAN)
    paint(noz_ln, CYAN)
    paint(puff_ln, CYAN)
    paint(detail, VIOLET)
    paint(win_ln, CYAN)

    return canvas.resize((size, size), Image.LANCZOS)


def save_ico(path: Path, sizes, frames):
    biggest = max(sizes)
    frames[biggest].save(path, format="ICO", sizes=[(s, s) for s in sizes],
                         append_images=[frames[s] for s in sizes if s != biggest])


def wizard(w: int, h: int, mark: int, y_frac: float) -> Image.Image:
    img = Image.new("RGB", (w, h), SURFACE)
    r = render(mark)
    img.paste(r, ((w - mark) // 2, int((h - mark) * y_frac)), r)
    return img


def main() -> int:
    frames = {s: render(s) for s in sorted(set(ICO_SIZES) | {180, 192, 512})}
    frames[256].save(ROOT / "assets/icons/futurespeak.png")
    save_ico(ROOT / "assets/icons/futurespeak.ico", ICO_SIZES, frames)
    (ROOT / "assets/friday.ico").write_bytes((ROOT / "assets/icons/futurespeak.ico").read_bytes())
    save_ico(ROOT / "static/favicon.ico", (16, 32, 48), frames)
    frames[16].save(ROOT / "static/favicon-16x16.png")
    frames[32].save(ROOT / "static/favicon-32x32.png")
    frames[180].save(ROOT / "static/apple-touch-icon.png")
    frames[192].save(ROOT / "static/android-chrome-192x192.png")
    frames[512].save(ROOT / "static/android-chrome-512x512.png")
    inst = ROOT / "packaging/windows/installer"
    inst.mkdir(parents=True, exist_ok=True)
    small = wizard(55, 58, 48, 0.5)
    small.save(inst / "wizard-small.bmp")
    small.save(inst / "wizard-small.png")
    wizard(164, 314, 128, 0.42).save(inst / "wizard.bmp")
    return 0


if __name__ == "__main__":
    sys.exit(main())
