# Installation Guide

Complete setup guide for Agent Friday Desktop on a fresh machine.

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| **Python** | 3.10+ | 3.11 or 3.12 recommended |
| **pip** | Latest | Comes with Python |
| **Git** | Any | For cloning the repo |
| **Node.js** | 18+ | Only needed for Playwright tests |
| **Ollama** | Latest | Optional — for local model routing |

### Optional Build Tools (for Headroom compression)

Headroom's native Rust core delivers 60-95% token compression. Without it, Friday works fine but skips compression.

| Requirement | Notes |
|-------------|-------|
| **Rust toolchain** | `rustup` — needed to compile `headroom._core` |
| **MSVC Build Tools** | Windows only — `cl.exe`/`link.exe` from Visual Studio Build Tools |

---

## Step 1: Clone the Repository

```bash
git clone https://github.com/FutureSpeakAI/Agent-Friday.git
cd Agent-Friday
```

---

## Unsigned-script warnings (Windows SmartScreen · PowerShell · macOS Gatekeeper)

Agent Friday's installers are open-source scripts, not code-signed binaries, so
a fresh OS may warn you before running them. This is expected for any unsigned
script — here's how to proceed safely. (Always read a script before running it;
ours are short and plain-text.)

### Windows — PowerShell execution policy

If `.\install.ps1` fails with *"running scripts is disabled on this system"*,
run it once with a bypass scoped to that single command (it does **not** change
your machine's policy):

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

If you downloaded the repo as a ZIP, Windows may mark files as "blocked." Clear
that flag first:

```powershell
Get-ChildItem -Recurse . | Unblock-File
```

### Windows — SmartScreen ("Windows protected your PC")

If you run a packaged executable (a PyInstaller build) and SmartScreen shows a
blue dialog, click **More info → Run anyway**. SmartScreen flags any executable
that hasn't yet built up download reputation; running from source with
`python server.py` avoids the prompt entirely.

### macOS — Gatekeeper ("cannot be opened because the developer cannot be verified")

For the `install.sh` script there is no Gatekeeper prompt — run it normally. If
you ever run a downloaded **app bundle** and Gatekeeper blocks it, either
right-click the app → **Open** (then confirm), or clear the quarantine flag:

```bash
xattr -d com.apple.quarantine /path/to/AgentFriday
```

When in doubt, the source install (`python server.py`) never triggers any of
these warnings, because you're running your own Python on scripts you can read.

---

## Step 2: Create a Virtual Environment (Recommended)

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

---

## Step 3: Install Dependencies

The recommended path uses `pyproject.toml`, which also installs the `friday`
console command and every optional capability group (voice, creative, Google,
local embeddings/memory, compression, federation):

```bash
pip install -e ".[all]"
```

Prefer a leaner install? `pip install -e .` lands just the core dependencies
(server + UI + Anthropic/Gemini paths); the heavier extras stay out and the
features that need them degrade gracefully. The one-line installers
(`install.ps1` / `install.bat` / `install.sh`) run the `.[all]` path for you
and fall back to `requirements.txt` automatically if it errors.

`requirements.txt` remains as a direct fallback:

```bash
pip install -r requirements.txt
```

The core install includes:

| Package | Purpose |
|---------|---------|
| `flask` | Web server |
| `anthropic` | Claude API client |
| `google-genai` | Gemini API (TTS, creative, voice) |
| `rich` | Terminal formatting |
| `colorama` | Windows terminal colors |
| `pyautogui` | OS control (Ring 3 features) |
| `beautifulsoup4` | HTML parsing for web search |
| `requests` | HTTP requests |
| `pyyaml` | Skill file parsing |
| `sentence-transformers` | Embeddings for semantic context pruning |
| `headroom-ai[all]` | Context compression (optional native core) |

If `headroom-ai` fails to build (missing Rust/MSVC), Friday will still run — compression is disabled gracefully.

---

## Step 4: Configure API Keys

Friday needs at least one API key. **Never commit keys to the repository.**

### Option A: Environment Variables

```bash
# Windows (cmd)
set ANTHROPIC_API_KEY=sk-ant-...
set GEMINI_API_KEY=AIza...

# Windows (PowerShell)
$env:ANTHROPIC_API_KEY = "sk-ant-..."
$env:GEMINI_API_KEY = "AIza..."

# macOS / Linux
export ANTHROPIC_API_KEY=sk-ant-...
export GEMINI_API_KEY=AIza...
```

### Option B: Setup Wizard

On first run, Friday's setup wizard (in-browser) lets you enter API keys. They are saved to `~/.friday/settings.json` (local only, never committed).

### Option C: Settings File

Create or edit `~/.friday/settings.json`:

```json
{
  "anthropic_api_key": "sk-ant-...",
  "gemini_api_key": "AIza..."
}
```

### Key Sources

| Key | Source | Required |
|-----|--------|----------|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com/) | Yes |
| `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com/) | Optional (for TTS, creative, voice) |

### Optional: Authentication for Remote Access

If you plan to expose Friday via a tunnel (e.g., Cloudflare):

```bash
set FRIDAY_USERNAME=your-email
set FRIDAY_PASSWORD=your-password
set FRIDAY_SECRET_KEY=a-random-secret-string
```

Loopback (localhost) access is always auto-authenticated regardless of these settings.

---

## Step 5: Install Ollama (Optional)

Ollama enables local model routing — required for vault access to private data.

1. Download from [ollama.com](https://ollama.com/)
2. Install and start the Ollama service
3. Pull a model:

```bash
ollama pull qwen3:14b    # general purpose (8+ GB VRAM)
ollama pull qwen3:8b     # lighter alternative (6+ GB VRAM)
ollama pull qwen3:4b     # minimal (runs on CPU)
```

Friday auto-detects Ollama at `http://localhost:11434`. To use a different URL, set it in `~/.friday/settings.json`:

```json
{
  "ollama_url": "http://localhost:11434"
}
```

---

## Step 6: First Run

```bash
python server.py
```

Friday starts on port 3000 by default. Open your browser to:

```
http://localhost:3000
```

On first launch:
1. The setup wizard guides you through API key configuration
2. Friday creates `~/.friday/` with default settings
3. The holographic UI loads with the Genesis Lattice visualization

---

## Directory Structure After First Run

```
~/.friday/
├── settings.json           # Configuration
├── personality.json        # Personality evolution
├── trust_graph.json        # Relationship map
├── epistemic_scores.json   # Epistemic calibration
├── privacy_shield.json     # PII watchlist
├── memory/                 # Long-term memory
├── skills/                 # Learnable skills (YAML)
├── skillopt/               # SkillOpt engine data
├── wiki/                   # Personal wiki
├── vault/                  # Governance key + access logs
├── audio-cache/            # TTS cache
└── vibe-code-logs/         # Coding session logs
```

---

## Troubleshooting

### "ANTHROPIC_API_KEY is not set"

Set the key via environment variable, setup wizard, or `~/.friday/settings.json`. Restart the server after changing.

### Headroom compression shows "0% saved"

The Headroom native Rust core (`headroom._core`) isn't installed. This requires:
- **Rust toolchain**: Install via [rustup.rs](https://rustup.rs/)
- **Windows**: MSVC Build Tools (`cl.exe`/`link.exe`) from Visual Studio Build Tools
- Then: `pip install headroom-ai[all] --force-reinstall`

Friday works without it — compression falls back to passthrough.

### Ollama not detected

1. Confirm Ollama is running: `ollama list`
2. Check the URL (default `http://localhost:11434`)
3. Pull at least one model: `ollama pull qwen3:8b`
4. Check `GET /api/ollama/status` for diagnostics

### sentence-transformers download on first chat

The context pruner downloads the `all-MiniLM-L6-v2` model (~80MB) on first use. This is a one-time download. If behind a proxy, set `HTTP_PROXY`/`HTTPS_PROXY` environment variables.

### Port 3000 already in use

Friday handles this automatically: if port 3000 is busy, it scans the next ten
ports, binds the first free one, and prints the actual URL it chose, e.g.
`Note: port 3000 was busy — using 3001 instead.` Open the URL it prints.

To pin a specific port yourself, set `FRIDAY_PORT` before launching:

```bash
# Windows (Command Prompt)
set FRIDAY_PORT=3001 && python server.py

# Windows (PowerShell)
$env:FRIDAY_PORT = "3001"; python server.py

# macOS / Linux
FRIDAY_PORT=3001 python server.py
```

If no port in the 3000–3010 range is free, Friday exits with a clear message
rather than a raw traceback.

### flask-sock not installed

WebSocket features (live voice, real-time updates) require `flask-sock`:

```bash
pip install flask-sock
```

Friday will start without it but `/ws/live` will be disabled.

---

## Updating

```bash
git pull origin main
pip install -r requirements.txt --upgrade
python server.py
```

Settings and data in `~/.friday/` are preserved across updates.
