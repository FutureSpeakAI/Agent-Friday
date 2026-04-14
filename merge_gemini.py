import os

# Read source files
gemini = open(r'C:\Users\user\OneDrive\Documents\gemini-friday-desktop-code.txt', 'r', encoding='utf-8').read()
app = open(r'C:\Users\user\Projects\friday-desktop\ui_parts\app.html', 'r', encoding='utf-8').read()

# Extract Gemini sections
lines = gemini.split('\n')

# CSS: everything between <style> and </style>
css_start = next(i for i,l in enumerate(lines) if '<style>' in l)
css_end = next(i for i,l in enumerate(lines) if '</style>' in l)
gemini_css = '\n'.join(lines[css_start:css_end+1])

# Body HTML: between <body> and first <script> (the HUD overlay elements)
body_start = next(i for i,l in enumerate(lines) if '<body' in l)
first_script = next(i for i,l in enumerate(lines) if i > body_start and '<script' in l)
gemini_body_html = '\n'.join(lines[body_start+1:first_script])

# Scripts: from MediaPipe scripts through end of main script
script_section_start = next(i for i,l in enumerate(lines) if 'mediapipe/camera_utils' in l)
# Find the closing </script> for the main script block
main_script_end = len(lines) - 1
for i in range(len(lines)-1, 0, -1):
    if '</script>' in lines[i]:
        main_script_end = i
        break
gemini_scripts = '\n'.join(lines[script_section_start:main_script_end+1])

# Build the merged head.html
head = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>FRIDAY Desktop \u2014 FutureSpeak.AI</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet"/>
''' + gemini_css + '''
</head>
'''

# Build the merged scene html
scene = '''<body>
''' + gemini_body_html + '''
<div id="ui-root" style="position:relative;z-index:10;height:100vh;pointer-events:none"></div>
<div style="position:fixed;bottom:8px;left:12px;z-index:1;font-family:Orbitron,monospace;font-size:10px;color:rgba(124,58,237,0.08);letter-spacing:.1em;pointer-events:none">FutureSpeak.AI</div>
''' + gemini_scripts + '''
<script>
// Wire fridayVibe API to Gemini's scene
window.fridayVibe = window.fridayVibe || {
    setMood: function(m) { if(typeof setMood === 'function') setMood(m); },
    nextStructure: function() { if(typeof nextStructure === 'function') nextStructure(); else if(typeof currentStructureIndex !== 'undefined') { currentStructureIndex = (currentStructureIndex + 1) % structureBuilders.length; switchStructure(currentStructureIndex); }},
    prevStructure: function() { if(typeof currentStructureIndex !== 'undefined' && typeof structureBuilders !== 'undefined') { currentStructureIndex = (currentStructureIndex - 1 + structureBuilders.length) % structureBuilders.length; switchStructure(currentStructureIndex); }},
    setStructure: function(i) { if(typeof switchStructure === 'function') switchStructure(i); },
    getStructure: function() { return {index: typeof currentStructureIndex !== 'undefined' ? currentStructureIndex : 0, name: 'Structure ' + (typeof currentStructureIndex !== 'undefined' ? currentStructureIndex : 0)}; },
    getMood: function() { return typeof currentMood !== 'undefined' ? currentMood : 'LISTENING'; }
};
</script>
'''

# Write the parts
open(r'C:\Users\user\Projects\friday-desktop\ui_parts\head.html', 'w', encoding='utf-8').write(head)
open(r'C:\Users\user\Projects\friday-desktop\ui_parts\styles_and_scene.html', 'w', encoding='utf-8').write(scene)
# app.html stays as-is

print(f'Merged: head={len(head)} scene={len(scene)} app={len(app)}')
print('Run: python build_ui.py')
