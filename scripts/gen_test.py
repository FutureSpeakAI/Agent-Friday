"""Quick Gemini image gen test — verifies SDK + API key work before batch generation."""
import os, sys, io
from pathlib import Path

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    print("ERROR: GEMINI_API_KEY not set"); sys.exit(1)

from google import genai
from google.genai import types
from PIL import Image

client = genai.Client(api_key=API_KEY)

MODELS = [
    "gemini-3-pro-image-preview",
    "gemini-3.1-flash-image-preview",
    "gemini-2.5-flash-image",
]

prompt = "A single glowing cyan holographic home icon, minimalist line-art, dark transparent background, 32x32 app icon style, neon outline"

for m in MODELS:
    try:
        print(f"Trying {m}...")
        r = client.models.generate_content(
            model=m,
            contents=f"Generate a detailed, high-quality image: {prompt}",
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
                image_config=types.ImageConfig(aspect_ratio="1:1"),
            ),
        )
        if r.candidates:
            for c in r.candidates:
                if c.content and c.content.parts:
                    for p in c.content.parts:
                        if p.inline_data and p.inline_data.mime_type and p.inline_data.mime_type.startswith("image/"):
                            out = Path(r"C:\Users\user\Projects\friday-desktop\assets\icons\_test_home.png")
                            Image.open(io.BytesIO(p.inline_data.data)).save(out, "PNG")
                            print(f"OK via {m} -> {out}")
                            sys.exit(0)
        print(f"  {m}: no image in response")
    except Exception as e:
        print(f"  {m} failed: {e}")

print("FAIL: all models failed")
sys.exit(2)
