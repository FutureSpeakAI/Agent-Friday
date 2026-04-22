@echo off
cd /d C:\Users\user\Projects\friday-desktop
git add assets/icons/*.svg assets/icon-manifest.json assets/holographic.css generate_holo_icons.py ui_parts/head.html ui_parts/app.html index.html
git commit -m "feat(gemini-design): holographic dock icons + prismatic CSS refinements

- Generated 16 holographic SVG dock icons (cyan/purple/magenta gradients, glow filters)
- Added prismatic shimmer animation on dock bar (8s cycle)
- Holographic hover/active effects with hue-shifting borders
- SVG icon glow effects with drop-shadow filters
- Scanline overlay on dock for sci-fi aesthetic
- Prismatic active dot animation
- Holographic group separators
- Emoji fallback on SVG load failure
- Rebuilt index.html with build_ui.py"
echo COMMIT DONE
