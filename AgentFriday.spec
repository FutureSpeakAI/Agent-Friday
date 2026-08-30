# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Agent Friday (onefile). Excludes the heavy optional ML
# stack (torch / sentence-transformers / transformers / headroom). Run:
# pyinstaller AgentFriday.spec
#
# WHAT THE EXCLUSION ACTUALLY COSTS (corrected 2026-08-24). This comment used to
# say the app "degrades gracefully without them (semantic context pruning +
# Headroom compression fall back to no-ops)". That list was incomplete in a way
# that mattered: `sentence_transformers` ALSO backs Layer 3 of the sensitivity
# classifier (services/sensitivity_classifier.py::_embedding_tier), which is a
# PRIVACY control, not a performance optimisation. Excluding it means the frozen
# .exe classifies egress with Layers 1a+1b only - four regexes and two keyword
# lists - while the classifier docstring advertised four layers. Verified against
# build/AgentFriday/PYZ-00.toc: `sentence_transformers` 0 hits, `presidio` 0 hits,
# `sensitivity_classifier` present.
#
# The exclusion STAYS: sentence_transformers is 5 MB but pulls torch, measured at
# 4.4 GB on disk against a 152 MB .exe. Bundling it is not a sane trade for a
# desktop app. What changes is honesty, not size - services/privacy_layers.py
# probes the layers at startup and logs a WARNING naming each inactive one, so a
# packaged build can no longer claim protection it is not running. If you edit
# this list, re-run that self-check against the built artifact.
#
# STALENESS WARNING (2026-08-25). The TOC references above describe the build in
# build/AgentFriday/, which is dated 2026-07-06 and therefore predates every
# privacy and file module added since: file_extraction, file_grants, file_search,
# privacy_layers and presidio_shadow are all absent from that PYZ, and so is
# pdfplumber. Do NOT read that TOC as evidence about the current tree -- it is
# evidence about July. Verified instead by running collect_submodules directly
# against this spec's sys.path setup: 291 modules enumerated, all five present.
# Re-run the real build before making any claim about the .exe.
#
# THIS IS ALSO NOT THE SHIPPING INSTALLER. packaging/windows/ builds the actual
# 5.5.0 artifact (AgentFriday-Setup-5.5.0.zip) from a source payload plus an
# embedded CPython and a wheelhouse; it does not use PyInstaller at all. The two
# paths have DIFFERENT privacy properties -- the installer installs
# sentence-transformers (memory tier) and presidio-analyzer (recommended tier),
# neither of which this spec bundles. Check which one you are reasoning about
# before quoting a layer count.
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
    # Bundled career-pipeline skills moved from repo-root `skills/` into the
    # installable package at `src/agent_friday/seed/skills/` (PR-3, packaging).
    # Bundle at the SAME relative path inside the frozen tree
    # (`agent_friday/seed/skills`) so `skill_registry.BUNDLED_DIR` -
    # `Path(__file__).resolve().parent / "seed" / "skills"` from
    # `agent_friday/skill_registry.py` - still resolves correctly frozen.
    # NOTE: `datas` silently drops any entry whose source path does not exist
    # (see the filter two lines below) - this one was `('skills', 'skills')`
    # and would have gone missing with zero warning the moment the repo-root
    # `skills/` directory was deleted, had this line not moved with it.
    ('src/agent_friday/seed/skills', 'agent_friday/seed/skills'),
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
    # Privacy self-reporting. Pinned explicitly rather than left to
    # collect_submodules: if these silently fail to bundle, the build loses the
    # very check that tells you the build lost something.
    'agent_friday.services.privacy_layers', 'agent_friday.services.presidio_shadow',
    'agent_friday.services.sensitivity_classifier', 'agent_friday.services.egress_gate',
    'agent_friday.services.compaction', 'agent_friday.services.connectors',
    'agent_friday.cognitive_memory', 'agent_friday.epistemic_engine',
    'agent_friday.dynamic_rings', 'agent_friday.voice_personality',
    'agent_friday.skill_capture', 'agent_friday.skill_registry',
    'agent_friday.skillopt_engine', 'agent_friday.setup_wizard',
    'agent_friday.mcp_client', 'agent_friday.notifications_engine',
    'agent_friday.people_graph', 'agent_friday.source_trust_graph',
    # third-party that hooks can miss
    'flask_sock', 'feedparser', 'bs4', 'yaml', 'requests', 'colorama',
    # PDF text extraction. Pinned explicitly because the analyze-file route
    # imports it INSIDE a try/except, which collect_submodules cannot see --
    # exactly how it came to be absent from every environment until 2026-08-25.
    'pdfplumber', 'pdfminer', 'pdfminer.high_level',
    # NOTE: .docx extraction deliberately needs NO hiddenimport. file_extraction
    # ._extract_docx() reads the archive with stdlib zipfile + XML rather than
    # python-docx, so there is no third-party dependency to pin and none to add
    # to requirements.txt. Verified 2026-08-25 -- do not "fix" this by adding
    # python-docx.
    # Local file discovery + the grant ledger (WO-14 / WO-17). Verified on
    # 2026-08-25 that collect_submodules('agent_friday') DOES enumerate all
    # three (291 modules collected), so these lines are belt-and-braces rather
    # than a fix. They are here because collect_submodules returns [] silently
    # if the sys.path.insert above ever regresses, and the failure mode of
    # losing file_grants is that grants stop being read -- which fails safe on
    # egress but silently breaks a feature the user paid attention to.
    'agent_friday.services.file_extraction', 'agent_friday.services.file_grants',
    'agent_friday.services.file_search',
    # The capability preflight itself. If this fails to bundle, the build
    # loses the check that reports what else the build lost.
    'agent_friday.services.capability_preflight',
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
    # Tier-2 NeMo GPU voice (2026-08-25). These are NOT dependencies of this
    # build and never were -- nothing imports them. They arrive in the dev venv
    # only because services/voice_installer.py installs nemo_toolkit at the
    # USER'S request at runtime, and PyInstaller then sweeps whatever is sitting
    # in site-packages. Left unexcluded they are dead weight twice over: NeMo
    # requires torch, which is excluded above, so a frozen build could not run
    # Tier-2 voice even with NeMo bundled. services/nemo_voice.py only ever
    # probes for them with find_spec and degrades honestly when absent, which is
    # the correct answer in a frozen build.
    'nemo', 'nemo_toolkit', 'lightning', 'pytorch_lightning', 'lightning_fabric',
    'torchmetrics',
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
