# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Agent Friday (onefile). Excludes the heavy optional ML
# stack (torch / sentence-transformers / transformers / headroom) — the app
# degrades gracefully without them (semantic context pruning + Headroom
# compression fall back to no-ops). Run: pyinstaller AgentFriday.spec
import os
import sys
# The package lives under src/ and is NOT pip-installed into site-packages in
# every build environment, so collect_submodules('agent_friday') below silently
# returns [] unless src is on sys.path first (collect_submodules import-fails →
# empty). That is exactly how the routes/* modules — imported dynamically at
# runtime and therefore invisible to PyInstaller's static analysis — went
# unbundled, 404'ing the entire API in the frozen .exe. Put src on the path so
# collect_submodules can actually enumerate the package. SPECPATH is the spec's
# own directory (injected by PyInstaller).
sys.path.insert(0, os.path.join(SPECPATH, 'src'))
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
    # agent_friday package and submodules (imported inside try/except — pin explicitly)
    'agent_friday', 'agent_friday.core', 'agent_friday.cli',
    'agent_friday.services.model_router', 'agent_friday.services.agent',
    'agent_friday.services.news_engine', 'agent_friday.services.voice_engine',
    'agent_friday.services.notifications', 'agent_friday.services.scheduler',
    'agent_friday.services.cost_meter', 'agent_friday.services.compaction',
    'agent_friday.services.tool_hooks', 'agent_friday.services.credential_store',
    'agent_friday.services.creative_engine', 'agent_friday.services.creative_pipeline',
    'agent_friday.services.creative_memory', 'agent_friday.services.content_credentials',
    'agent_friday.services.federation', 'agent_friday.services.federation_transport',
    'agent_friday.services.marketplace', 'agent_friday.services.economy',
    'agent_friday.services.moderation', 'agent_friday.services.defederation',
    'agent_friday.services.capability_router', 'agent_friday.services.demo_mode',
    'agent_friday.services.compaction', 'agent_friday.services.connectors',
    'agent_friday.cognitive_memory', 'agent_friday.epistemic_engine',
    'agent_friday.dynamic_rings', 'agent_friday.voice_personality',
    'agent_friday.skill_capture', 'agent_friday.skill_registry',
    'agent_friday.skillopt_engine', 'agent_friday.setup_wizard',
    'agent_friday.mcp_client', 'agent_friday.notifications_engine',
    'agent_friday.people_graph', 'agent_friday.source_trust_graph',
    # third-party that hooks can miss
    'flask_sock', 'feedparser', 'bs4', 'yaml', 'requests', 'colorama',
    'pyautogui', 'pynput', 'pynput.keyboard', 'pynput.mouse',
    'pynacl', 'nacl', 'nacl.signing', 'nacl.public',
]
hiddenimports += collect_submodules('agent_friday')
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
    ['src/agent_friday/server.py'],
    pathex=['src'],
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
