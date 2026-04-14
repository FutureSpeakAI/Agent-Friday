#!/usr/bin/env python3
"""
Generate styles_and_scene.html from Gemini holographic desktop source.
Removes "Awaken The Core" overlay, auto-starts the scene, wires fridayVibe API.

Run: python write_scene.py
"""
import os
import re

GEMINI_SRC = os.path.expanduser(r"~\OneDrive\Documents\gemini-friday-desktop-code.txt")
OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_parts", "styles_and_scene.html")

def main():
    with open(GEMINI_SRC, "r", encoding="utf-8") as f:
        src = f.read()

    # --- Extract the <body>...</body> content from the Gemini source ---
    body_match = re.search(r"<body>(.*?)</body>", src, re.DOTALL)
    if not body_match:
        print("ERROR: Could not find <body> content in Gemini source")
        return
    body_content = body_match.group(1)

    # --- 1. REMOVE the interaction-prompt div entirely ---
    body_content = re.sub(
        r'\s*<div id="interaction-prompt">.*?</div>\s*</div>',
        '',
        body_content,
        flags=re.DOTALL
    )
    # Also remove if it's simpler format
    body_content = re.sub(
        r'\s*<div id="interaction-prompt"[^>]*>.*?</div>\s*',
        '\n',
        body_content,
        flags=re.DOTALL
    )

    # --- 2. In init(), remove the interaction-prompt click handler ---
    body_content = body_content.replace(
        "document.getElementById('interaction-prompt').addEventListener('click', setupAudio);",
        "// Auto-start audio (interaction-prompt removed)\n            setupAudio();"
    )

    # --- 3. In setupAudio(), remove the fade-out of interaction-prompt ---
    body_content = body_content.replace(
        "document.getElementById('interaction-prompt').classList.add('fade-out');",
        "// interaction-prompt removed - no fade needed"
    )

    # --- 4. Insert the #ui-root div before the Three.js script tags ---
    # Find where Three.js scripts start
    threejs_marker = '<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>'
    
    ui_root_html = """
    <!-- Friday OS React UI Layer -->
    <div id="ui-root" style="position:fixed;inset:0;z-index:60;pointer-events:none">
        <div id="ui-inner" style="pointer-events:auto;height:100vh"></div>
    </div>
    <div style="position:fixed;bottom:8px;left:12px;z-index:1;font-family:Orbitron,monospace;font-size:10px;color:rgba(124,58,237,0.08);letter-spacing:.1em;pointer-events:none">FutureSpeak.AI</div>

    <!-- MediaPipe -->
    <script src="https://cdn.jsdelivr.net/npm/@mediapipe/camera_utils/camera_utils.js" crossorigin="anonymous"></script>
    <script src="https://cdn.jsdelivr.net/npm/@mediapipe/face_detection/face_detection.js" crossorigin="anonymous"></script>
    <script src="https://cdn.jsdelivr.net/npm/@mediapipe/hands/hands.js" crossorigin="anonymous"></script>

    <!-- Three.js + Post-Processing -->
    """

    body_content = body_content.replace(threejs_marker, ui_root_html + threejs_marker)

    # --- 5. Remove any existing MediaPipe script tags that were in the body ---
    # (they've been moved to above Three.js in the ui_root_html block)
    # Remove duplicates that came from the original body
    body_content = re.sub(
        r'\s*<script src="https://cdn\.jsdelivr\.net/npm/@mediapipe/camera_utils/camera_utils\.js"[^>]*></script>',
        '',
        body_content,
        count=1  # Only remove the SECOND occurrence (first is the one we just added)
    )
    # Actually we need to be smarter. Let's count occurrences and only keep the first.
    # The MediaPipe scripts in original source are BEFORE the Three.js scripts.
    # We added them BEFORE Three.js in our ui_root_html block. So we have duplicates now.
    # Let's just remove the original ones (which are earlier in the file, before ui_root_html).
    
    # Actually, let me re-think. The original Gemini source has MediaPipe in <head>, not <body>.
    # Looking at the source: MediaPipe scripts are in the <head> (lines 238-240).
    # They are NOT in the <body>. So our body_content shouldn't have them. Good.
    # But our ui_root_html adds them. Perfect.

    # --- 6. After the main </script>, add fridayVibe API and keyboard handler ---
    fridayvibe_script = """
<script>
// === FRIDAY VIBE API ===
// Wire window.fridayVibe to Gemini's actual scene functions
window.fridayVibe = {
    setMood: function(m) {
        if (typeof setMood === 'function') setMood(m);
    },
    nextStructure: function() {
        if (typeof setEvolution === 'function' && typeof currentEvolutionIdx !== 'undefined' && typeof EVOLUTION_PATH !== 'undefined') {
            setEvolution((currentEvolutionIdx + 1) % EVOLUTION_PATH.length);
        }
    },
    prevStructure: function() {
        if (typeof setEvolution === 'function' && typeof currentEvolutionIdx !== 'undefined' && typeof EVOLUTION_PATH !== 'undefined') {
            setEvolution((currentEvolutionIdx - 1 + EVOLUTION_PATH.length) % EVOLUTION_PATH.length);
        }
    },
    setStructure: function(i) {
        if (typeof setEvolution === 'function') setEvolution(i);
    },
    getStructure: function() {
        return {
            index: typeof currentEvolutionIdx !== 'undefined' ? currentEvolutionIdx : 0,
            name: typeof EVOLUTION_PATH !== 'undefined' && typeof currentEvolutionIdx !== 'undefined'
                ? EVOLUTION_PATH[currentEvolutionIdx].name
                : 'Unknown'
        };
    },
    getMood: function() {
        return typeof currentMood !== 'undefined' ? currentMood : 'LISTENING';
    }
};

// === KEYBOARD CONTROLS ===
document.addEventListener('keydown', function(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) return;
    if (e.key === 'ArrowRight') { window.fridayVibe.nextStructure(); e.preventDefault(); }
    if (e.key === 'ArrowLeft')  { window.fridayVibe.prevStructure(); e.preventDefault(); }
});
</script>
"""

    # Find the last </script> before </body> end and append after it
    # The main script ends with: window.onload = init;\n    </script>
    last_script_end = body_content.rfind("</script>")
    if last_script_end != -1:
        insert_pos = last_script_end + len("</script>")
        body_content = body_content[:insert_pos] + "\n" + fridayvibe_script + "\n" + body_content[insert_pos:]

    # --- 7. Assemble final file ---
    output_content = "<body>\n" + body_content.strip() + "\n"
    # Don't close </body> - app.html comes after and build_ui.py will handle final tags

    # --- Write output ---
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(output_content)

    size = os.path.getsize(OUTPUT)
    print(f"Generated {OUTPUT}")
    print(f"Size: {size:,} bytes")

    # --- Verification ---
    with open(OUTPUT, "r", encoding="utf-8") as f:
        content = f.read()

    errors = []
    if "interaction-prompt" in content and "Awaken" in content:
        errors.append("FAIL: interaction-prompt 'Awaken The Core' still present")
    if "Awaken The Core" in content:
        errors.append("FAIL: 'Awaken The Core' text still present")
    if 'id="ui-root"' not in content:
        errors.append("FAIL: #ui-root div not found")
    if "z-index:60" not in content:
        errors.append("FAIL: z-index:60 not found on ui-root")
    if "pointer-events:none" not in content:
        errors.append("FAIL: pointer-events:none not found on ui-root")
    if "pointer-events:auto" not in content:
        errors.append("FAIL: pointer-events:auto not found on ui-inner")
    if "window.onload = init" not in content:
        errors.append("FAIL: window.onload = init not found")
    if "window.fridayVibe" not in content:
        errors.append("FAIL: fridayVibe API not found")
    if "setEvolution" not in content:
        errors.append("FAIL: setEvolution not found in fridayVibe API")
    if "EVOLUTION_PATH" not in content:
        errors.append("FAIL: EVOLUTION_PATH not found")
    if "function animate()" not in content:
        errors.append("FAIL: animate() function not found")
    if "function init()" not in content:
        errors.append("FAIL: init() function not found")
    if "buildAllStructures" not in content:
        errors.append("FAIL: buildAllStructures not found")
    if "setupAudio" not in content:
        errors.append("FAIL: setupAudio not found")

    if errors:
        print("\n=== VERIFICATION ERRORS ===")
        for e in errors:
            print(f"  {e}")
    else:
        print("\n=== ALL VERIFICATION CHECKS PASSED ===")
        print("  - No 'Awaken The Core' overlay")
        print("  - #ui-root div present with z-index:60")
        print("  - window.onload = init (auto-start)")
        print("  - fridayVibe API wired to setEvolution/EVOLUTION_PATH")
        print("  - All core functions present (init, animate, buildAllStructures)")
        print("\nReady! Run: python build_ui.py")


if __name__ == "__main__":
    main()
