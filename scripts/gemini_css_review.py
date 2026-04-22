"""Ask Gemini 2.5 Pro to review head.html CSS and suggest holographic-aesthetic refinements."""
import os, sys
from pathlib import Path

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    print("ERROR: GEMINI_API_KEY not set"); sys.exit(1)

from google import genai
from google.genai import types

client = genai.Client(api_key=API_KEY)

head_path = Path(r"C:\Users\user\Projects\friday-desktop\.claude\worktrees\relaxed-ishizaka-30a75b\ui_parts\head.html")
css_source = head_path.read_text(encoding="utf-8")

prompt = f"""You are a senior product-design consultant reviewing CSS for **Friday Desktop** — a holographic 3D desktop OS inspired by Iron Man's JARVIS and Minority Report.

**Design system constraints** (do not break these):
- Core colors: cyan #00d4ff, purple #7c3aed, magenta #ff0080, green #00ff80, dark base #030308
- Fonts: Orbitron (headers), Inter (body), JetBrains Mono (data)
- Three.js 3D scene fills the background with bloom
- Glassmorphism on floating windows (blur, low-alpha backgrounds, subtle borders)
- 16 dock workspaces grouped Life / Work / System

**Your task:** Propose concrete, surgical CSS improvements that make the UI more polished and visually cohesive. Focus on:
1. **Subtle holographic scanline / chromatic aberration** as optional body::after overlay (very low opacity — must not harm readability)
2. **Refined glassmorphism** on .fwin — layered inset glow, better edge light catch
3. **Dock button polish** — cleaner hover/active states, stronger holographic feel, icon-image support via background-image so we can swap emojis for generated PNGs
4. **Focus-visible keyboard accessibility** — currently missing; add consistent focus rings using cyan
5. **Reduced-motion support** — respect `prefers-reduced-motion: reduce`
6. **Boot splash overlay class** — a new `.boot-splash` fullscreen overlay that fades out after 1.5s, uses the generated splash asset as background, with a small Orbitron caption

**Output format — CRITICAL:**
Return ONLY valid CSS that I can APPEND verbatim to the existing <style> block. No markdown fences, no commentary, no `<style>` tags. Keep it under ~120 lines. Make every rule load-bearing.

Here is the current head.html for context:

```html
{css_source}
```

Begin CSS now:"""

print("Asking Gemini 2.5 Pro for CSS refinements...\n", file=sys.stderr)
r = client.models.generate_content(
    model="gemini-2.5-pro",
    contents=prompt,
    config=types.GenerateContentConfig(temperature=0.6),
)
print(f"finish_reason: {r.candidates[0].finish_reason if r.candidates else 'no candidates'}", file=sys.stderr)
print(f"usage: {r.usage_metadata}", file=sys.stderr)
out = r.text or ""
if not out and r.candidates:
    # Surface parts manually
    parts = r.candidates[0].content.parts if r.candidates[0].content else []
    out = "".join(p.text or "" for p in parts)
# Strip markdown fences if present
if out.startswith("```"):
    lines = out.split("\n")
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    out = "\n".join(lines)

target = Path(r"C:\Users\user\Projects\friday-desktop\.claude\worktrees\relaxed-ishizaka-30a75b\scripts\gemini_css_suggestions.css")
target.write_text(out, encoding="utf-8")
print(f"Saved suggestions to {target}")
print(f"Length: {len(out)} chars")
