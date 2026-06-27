#!/usr/bin/env python3
"""Friday Desktop OS — UI Assembler
Combines ui_parts/ into a single index.html
Run: python build_ui.py
"""
import os

parts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ui_parts')
output = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')

# Read and combine parts in order
parts = ['head.html', 'styles_and_scene.html', 'app.html']
combined = ''
for part in parts:
    path = os.path.join(parts_dir, part)
    with open(path, 'r', encoding='utf-8') as f:
        combined += f.read() + '\n'

# Write combined output
with open(output, 'w', encoding='utf-8') as f:
    f.write(combined)

print(f'Assembled {len(combined)} bytes from {len(parts)} parts -> {output}')
print(f'Parts: {", ".join(parts)}')
