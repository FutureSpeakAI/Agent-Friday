# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Agent Friday (onefile). Excludes the heavy optional ML
# stack (torch / sentence-transformers / transformers / headroom) — the app
# degrades gracefully without them (semantic context pruning + Headroom
# compression fall back to no-ops). Run: pyinstaller AgentFriday.spec
import os
from PyInstaller.utils.hooks import collect_submodules

datas = [
    ('index.html', '.'),
    ('SELF.md', '.'),
    ('VOICE_DEMO.md', '.'),   # private-repo only; filtered out below when absent
    ('friday_live.html', '.'),
    ('friday_live_sw.js', '.'),
    ('friday_live_manifest.json', '.'),
    ('requirements.txt', '.'),
    ('static', 'static'),
    ('assets', 'assets'),
    ('skills', 'skills'),
    ('optional-skills', 'optional-skills'),
]
# Some bundled files exist only in the private working copy (gitignored in the
# public repo). Skip whatever is absent so the build works from either tree.
datas = [(src, dest) for (src, dest) in datas if os.path.exists(src)]

hiddenimports = [
    # local modules (some are imported inside try/except — pin them explicitly)
    'model_router', 'ollama_manager', 'notifications', 'notifications_engine',
    'cognitive_memory', 'context_compressor', 'context_pruner', 'epistemic_engine',
    # 'liquid_ui' intentionally NOT bundled — experimental, unwired (see its docstring)
    'dynamic_rings', 'proof_of_integrity', 'vault_access',
    'vault_crypto', 'vault_encrypt_migrate', 'voice_personality', 'skill_capture',
    'skill_registry', 'skillopt_engine', 'setup_wizard', 'friday_cli',
    # third-party that hooks can miss
    'flask_sock', 'feedparser', 'bs4', 'yaml', 'requests', 'colorama',
    'pyautogui', 'pynput', 'pynput.keyboard', 'pynput.mouse',
]
hiddenimports += collect_submodules('anthropic')
hiddenimports += collect_submodules('google.genai')

excludes = [
    'torch', 'torchvision', 'torchaudio', 'sentence_transformers', 'transformers',
    'scipy', 'sklearn', 'matplotlib', 'tensorflow', 'headroom', 'headroom_ai',
    'tokenizers', 'safetensors', 'accelerate', 'datasets', 'sympy',
]

_icon = 'assets/icons/futurespeak.ico'
icon = _icon if os.path.exists(_icon) else None

a = Analysis(
    ['server.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AgentFriday',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    icon=icon,
)
