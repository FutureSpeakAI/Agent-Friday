# Pixel Office — Agent Visualization Layer

## Source
`C:\Users\user\Projects\agent-fridays-pixel-office` (fork of pablodelucca/pixel-agents)

## What It Is
A pixel art office environment where AI agents are visualized as animated characters.
Originally a VS Code extension; the relevant visual assets and rendering code live in `webview-ui/`.

## Key Directories
- `webview-ui/public/assets/furniture/` — Furniture sprites with manifest.json per item
- `webview-ui/public/assets/floors/` — Floor tile PNGs
- `webview-ui/public/assets/walls/` — Wall tile sets
- `webview-ui/public/characters.png` — 6 diverse character sprites (from JIK-A-4 Metro City pack)
- `webview-ui/public/Screenshot.jpg` — Reference screenshot

## Features Relevant to Vibe Mode
- Characters animate based on agent activity (typing, reading, waiting)
- Speech bubbles for agent-needs-input states
- Layout editor with HSB color control, undo/redo, export/import
- Expandable grid up to 64x64 tiles
- Persistent layouts saved as JSON
- External asset directory support for custom furniture packs

## Integration Notes
The pixel office is a canvas-based 2D rendering system, separate from the Three.js
holographic desktop. For Vibe Mode, these could coexist as tabs or overlays:
- "Holographic" view = Three.js 3D scene (DesktopViz)
- "Office" view = Pixel art 2D canvas (Pixel Office)
- Both share the same mood/personality data

## Asset Licensing
Character sprites: JIK-A-4 Metro City pack (check license)
Furniture/floors/walls: Open source, included in the repo under MIT
