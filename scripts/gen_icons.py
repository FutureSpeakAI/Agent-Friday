"""Generate 16 holographic dock icons + splash screen for Friday Desktop.

Style: consistent cyan/purple/magenta holographic neon line-art on dark background,
matching the Friday Desktop holographic 3D OS aesthetic.
"""
import os, sys, io, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    print("ERROR: GEMINI_API_KEY not set"); sys.exit(1)

from google import genai
from google.genai import types
from PIL import Image

client = genai.Client(api_key=API_KEY)

ASSETS = Path(r"C:\Users\user\Projects\friday-desktop\assets")
ICONS_DIR = ASSETS / "icons"
SPLASH_DIR = ASSETS / "splash"
PALETTE_DIR = ASSETS / "palette"

# Nano Banana 2 first for speed, pro as fallback
MODELS = [
    "gemini-3.1-flash-image-preview",  # Nano Banana 2
    "gemini-3-pro-image-preview",      # Nano Banana Pro
    "gemini-2.5-flash-image",
]

# Consistent style preamble applied to every icon prompt
STYLE = (
    "Holographic neon line-art icon, minimalist vector style, glowing cyan outline "
    "(#00d4ff) with subtle magenta (#ff0080) and purple (#7c3aed) accent glow, "
    "thin 2px stroke, dark near-black transparent background, centered subject, "
    "symmetrical, flat shading, soft outer glow, sci-fi HUD aesthetic like Iron Man's "
    "JARVIS interface, 32x32 pixel app icon composition, no text, no labels."
)

ICONS = [
    ("home",        "a simple house silhouette with pitched roof and single door"),
    ("family",      "three stylized figures standing together, one taller two shorter"),
    ("coparent",    "two adult figures with a child between them holding hands"),
    ("health",      "a heart shape with an ECG pulse line crossing through it"),
    ("finance",     "a stack of coins with a subtle dollar glyph overlaid"),
    ("career",      "a briefcase with a single horizontal handle"),
    ("futurespeak", "a stylized rocket ship lifting off with a trailing plume"),
    ("contacts",    "two overlapping human silhouette heads / address-book figure"),
    ("draft",       "a quill pen writing on a single line"),
    ("content",     "a document page with three short horizontal text lines"),
    ("news",        "a folded newspaper with masthead lines"),
    ("wiki",        "an open book with visible page spread"),
    ("trust",       "two interlocking chain links"),
    ("studio",      "an artist's paint palette with brush"),
    ("code",        "angle brackets enclosing a forward slash (</>)"),
    ("system",      "a desktop monitor with a small power symbol on screen"),
]

SPLASH_PROMPT = (
    "A cinematic holographic boot screen for 'FRIDAY Desktop OS' — "
    "a glowing wireframe sphere floating in dark space, concentric rings rotating around it, "
    "streams of cyan data particles, subtle purple and magenta accents, "
    "deep black background with starfield, volumetric light rays, "
    "bloom post-processing, sci-fi HUD frame, Iron Man JARVIS aesthetic, "
    "Three.js 3D scene style, cinematic depth of field, no text."
)

PALETTE_PROMPT = (
    "A minimalist design system color palette card on a dark near-black background. "
    "Four large horizontal color swatches stacked vertically, each with a thin neon glow: "
    "cyan #00d4ff, purple #7c3aed, magenta #ff0080, green #00ff80. "
    "Each swatch is a clean rectangle with the hex code displayed in a thin monospace font "
    "(JetBrains Mono style) in white beside it. Holographic HUD aesthetic, "
    "thin corner brackets framing the composition, subtle grid lines in background. "
    "Styled like a Figma design token card for the Friday Desktop OS brand."
)


def generate(prompt: str, aspect: str = "1:1", retries: int = 2) -> bytes | None:
    full_prompt = f"Generate a detailed, high-quality image: {prompt}"
    for attempt in range(retries + 1):
        for m in MODELS:
            try:
                r = client.models.generate_content(
                    model=m,
                    contents=full_prompt,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"],
                        image_config=types.ImageConfig(aspect_ratio=aspect),
                    ),
                )
                if r.candidates:
                    for c in r.candidates:
                        if c.content and c.content.parts:
                            for p in c.content.parts:
                                if p.inline_data and p.inline_data.mime_type and p.inline_data.mime_type.startswith("image/"):
                                    return p.inline_data.data
            except Exception as e:
                msg = str(e)
                if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
                    time.sleep(3)
                    continue
                # try next model
        time.sleep(1)
    return None


def gen_icon(key: str, subject: str) -> tuple[str, bool, str]:
    prompt = f"{STYLE} Subject: {subject}."
    out = ICONS_DIR / f"{key}.png"
    data = generate(prompt, "1:1")
    if not data:
        return (key, False, "no data")
    try:
        img = Image.open(io.BytesIO(data))
        # Normalize to 256x256 (downscale at render time)
        img = img.resize((256, 256), Image.LANCZOS)
        img.save(out, "PNG", optimize=True)
        return (key, True, str(out))
    except Exception as e:
        return (key, False, f"save error: {e}")


def main():
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    SPLASH_DIR.mkdir(parents=True, exist_ok=True)
    PALETTE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Generating {len(ICONS)} icons in parallel (max 4 concurrent)...")
    results = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(gen_icon, k, s): k for k, s in ICONS}
        for fut in as_completed(futures):
            key, ok, info = fut.result()
            status = "OK " if ok else "FAIL"
            print(f"  [{status}] {key}: {info}")
            results.append((key, ok))

    print("\nGenerating splash screen...")
    data = generate(SPLASH_PROMPT, "16:9")
    if data:
        out = SPLASH_DIR / "boot.png"
        Image.open(io.BytesIO(data)).save(out, "PNG", optimize=True)
        print(f"  [OK ] splash -> {out}")
    else:
        print("  [FAIL] splash")

    print("\nGenerating palette card...")
    data = generate(PALETTE_PROMPT, "1:1")
    if data:
        out = PALETTE_DIR / "palette.png"
        Image.open(io.BytesIO(data)).save(out, "PNG", optimize=True)
        print(f"  [OK ] palette -> {out}")
    else:
        print("  [FAIL] palette")

    good = sum(1 for _, ok in results if ok)
    print(f"\nIcons: {good}/{len(ICONS)} generated")


if __name__ == "__main__":
    main()
