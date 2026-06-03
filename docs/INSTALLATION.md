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
git clone https://github.com/FutureSpeakAI/friday-desktop.git
cd friday-desktop
```

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

```bash
pip install -r requirements.txt
```

This installs:

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

Set a different port:

```bash
set FRIDAY_PORT=3001
python server.py
```

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
