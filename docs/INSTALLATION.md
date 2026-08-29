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

## Option 0: Download the Packaged App (No Python Required)

The fastest path on Windows needs no Python, Git, or terminal at all: download
the `AgentFriday-Setup-*.zip` attached to the
[latest release](https://github.com/FutureSpeakAI/Agent-Friday/releases/latest),
unzip it anywhere, and double-click **Install Agent Friday.cmd**. SmartScreen
may warn on first launch — see the SmartScreen note below for the one-click
bypass.

> ⚠️ **Take the newest zip, and do not go back for an older one.** Every
> `5.6.x` before `5.6.5` is superseded — `5.6.1` was tagged a few hours before
> the API-key pre-flight it was written to add, so the published `5.6.1` zip
> promises a self-repair loop it cannot verify a key for, and `5.6.0` is older
> still. See [RELEASE_NOTES.md](../RELEASE_NOTES.md).

> 🔴 **Upgrading from any version before 5.6.5? Read this.** Installers
> `5.6.0`–`5.6.4` did not replace Friday's own files when an install already
> existed — the copy step short-circuited, while the installer still wrote the
> new version number and reported success. An install that reached its version
> by upgrading has been running the code it *first* installed. Running the
> `5.6.5` installer over the top repairs it from any prior version and keeps
> everything under `~/.friday`. If you connected a credentialed MCP server
> (Airtable, Gmail, GitHub, Slack) while on an affected install, rotate that
> credential — it was stored and served in plaintext. See
> [CHANGELOG.md](../CHANGELOG.md) under 5.6.5.

> ⚠️ **The releases page also carries an `AgentFriday.exe`. It is not current.**
> The newest published `AgentFriday.exe` is **v5.4.0, built 6 July 2026**. It
> predates every egress-gate fix made on 24–25 August — among them the
> classifier gaining its first phone/address/account-number regexes, the wiki
> context section that failed *open* on a classifier miss, and the voice path
> that failed open at its strongest verdict — and it predates 5.5.0 entirely.
> The `dist/AgentFriday.exe` in a checkout is that same 6 July build.
>
> **Do not treat either as a current build.** Use the installer zip above, or
> run from source.

The steps that follow are the from-source path, recommended for developers and
anyone who wants to read or modify the code they run.

### The two packaged builds are not the same product, privacy-wise

This matters more than a packaging detail usually would, so it is stated up
front rather than buried:

| | **`AgentFriday.exe`** (PyInstaller, one file) | **`AgentFriday-Setup-*.zip`** (Windows installer) |
|---|---|---|
| What it is | A single frozen binary | An embedded CPython plus a source payload and a wheelhouse |
| Sensitivity classifier | **Layers 1a + 1b only** — regex and keyword | Layers 1a + 1b, **plus Layer 3** (embeddings) if the memory tier installs |
| `sentence-transformers` | **Excluded on purpose** (pulls torch: over 4 GB measured, against a ~152 MB binary) | Installed by the *memory* tier (~2.5 GB, announced and skippable) |
| `presidio-analyzer` | Not bundled | Installed by the *recommended* tier — but **observe-only**, see below |
| PDF extraction | Bundled (`pdfplumber` pinned in the spec) | Installed by the *recommended* tier |

Neither build is "the weakened one" by accident. The `.exe` trades Layer 3 for
not shipping a 4+ GB tensor library, which is the right trade for a desktop
download. What is **not** acceptable is claiming otherwise, so Friday tells you
which layers are live at every boot — see the next section.

### What a fresh install actually reports

On first run Friday probes its own privacy layers and prints the result. A
healthy source install prints something like:

```
  Privacy layers: Sensitivity classifier: 3/4 layers active (source checkout). DEGRADED - not running: presidio.
```

and, when anything is inactive, a boxed notice you are meant to read:

```
  ╔════════════════════════════════════════════════════════════╗
  ║  NOTICE: SENSITIVITY CLASSIFIER IS RUNNING DEGRADED       ║
  ║  inactive: presidio                                       ║
  ║  Egress decisions use the remaining layers only.          ║
  ╚════════════════════════════════════════════════════════════╝
```

**Seeing `presidio` listed as inactive is expected and correct**, even after an
installer run that installed it. Presidio is deliberately not enforced — the
reasoning is in [THREAT_MODEL.md](../THREAT_MODEL.md#1-cloud-side-exposure-of-sensitive-data),
and the short version is that measurement found it *worse* than the regex it
would supplement while escalating half of ordinary conversation.

### Nothing downloads a model behind your back

Two things worth stating explicitly, because both are common in this class of
tool and neither happens here:

- **Presidio's ~590 MB spaCy model is never fetched.** `AnalyzerEngine()` — the
  call that would pull it — is only constructed when you opt in with
  `FRIDAY_PRESIDIO_ENFORCE=1` or `FRIDAY_PRESIDIO_SHADOW=1`. Both default to
  off, so a normal install never constructs one.
- **The embedding model is lazy and announced.** `all-MiniLM-L6-v2` arrives on
  first use, into `%USERPROFILE%\.cache\huggingface`, and the installer warns
  about the ~2.5 GB memory tier before starting it and lets you skip it.

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

If `.\scripts\install.ps1` fails with *"running scripts is disabled on this system"*,
run it once with a bypass scoped to that single command (it does **not** change
your machine's policy):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
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

For the `scripts/install.sh` script there is no Gatekeeper prompt — run it normally. If
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
features that need them degrade gracefully. Of the one-line installers,
`scripts\install.bat` runs the `.[all]` path for you and falls back to
`requirements.txt` automatically if it errors; `scripts\install.ps1` and
`scripts/install.sh` install from `requirements.txt` directly.

`requirements.txt` remains as a direct fallback:

```bash
pip install -r requirements.txt
```

The full install (`.[all]` or `requirements.txt`) includes:

| Package | Purpose |
|---------|---------|
| `flask` | Web server |
| `flask-sock` | WebSocket support (live voice, real-time updates) |
| `anthropic` | Claude API client |
| `google-genai` | Gemini API (TTS, creative, voice) |
| `rich` | Terminal formatting |
| `colorama` | Windows terminal colors |
| `pyautogui` | OS control (Ring 3 features) — extra: `windows` |
| `beautifulsoup4` | HTML parsing for web search |
| `requests` | HTTP requests |
| `pyyaml` | Skill file parsing |
| `pdfplumber` | PDF text extraction for file reading and `search_files` — **core since 5.6.0** |
| `sentence-transformers` | Embeddings for semantic context pruning **and Layer 3 of the egress classifier** — extra: `local` |
| `headroom-ai[all]` | Context compression (optional native core) — extra: `compression` |

A lean `pip install -e .` covers everything above **except** the rows marked
with an extra — those arrive only via `.[all]`, their named extra, or
`requirements.txt`.

If `headroom-ai` fails to build (missing Rust/MSVC), Friday will still run — compression is disabled gracefully.

**`.docx` reading needs no dependency at all.** A `.docx` is a zip archive of
XML, and Friday reads it with the standard library. There is deliberately no
`python-docx` in any requirements file; please don't add one.

---

## Step 4: Configure API Keys

Cloud keys are **optional in principle, and asked about in practice.** Friday can chat with no key at all through a local model on Ollama, but since 5.6.1 the Windows installer asks which way you want to run her and recommends the key on a graphics card too small to hold a model comfortably — so a zero-key install is a choice you make, not the default you fall into. Add a key only to upgrade reasoning (Anthropic) or unlock voice/creative (Gemini). **Keys are stored encrypted per provider under `~/.friday/providers/keys/` (vault-passphrase or Windows DPAPI protection).** One honest caveat: the setup wizard has historically also written keys and the vault passphrase as plaintext `SET` lines into launch scripts, and those plaintext values *override* the encrypted store at import. Treat any `start.bat` or `friday_startup.vbs` on your machine as containing live secrets. See KNOWN_ISSUES.md §7.

### Option A: Setup Wizard (Recommended)

Run `friday setup`, or use the first-run wizard that opens in your browser. Either way, keys are immediately encrypted and stored in the credential store, and `friday setup` also arms the vault passphrase. This is the safest path — a hand-edited `start.bat` with a plaintext key is **not** recommended.

### Option B: Environment Variables

Set keys as environment variables before starting the server. Friday reads them at startup and (optionally) stores them in the encrypted credential store.

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

### Key Sources

| Key | Source | Required |
|-----|--------|----------|
| *(none)* | Ollama + a local model | Fully local, zero keys — offered by the installer when your card has room |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com/) | Optional (sharper reasoning) |
| `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com/) | Optional (TTS, creative, voice) |
| `OPENROUTER_API_KEY` | [openrouter.ai](https://openrouter.ai/) | Optional (hundreds of models via one key) |

Other OpenAI-compatible providers (Groq, Mistral, DeepSeek, xAI, Together,
Fireworks, and more) can be added with their own keys through `friday setup`
or **Settings → Providers**.

### Vault Encryption with FRIDAY_PASSWORD

Friday's sovereign vault stores private notes, contacts, and sensitive data. By default the vault is encrypted with a machine key. For stronger protection, set `FRIDAY_PASSWORD` before the first run:

```bash
# Windows (cmd)
set FRIDAY_PASSWORD=your-passphrase

# macOS / Linux
export FRIDAY_PASSWORD=your-passphrase
```

Without `FRIDAY_PASSWORD`, vault data is protected by a machine-local key (adequate for personal use on a trusted machine). With it, the vault is encrypted with Argon2id-derived AES-256-GCM using your passphrase — stronger against physical access scenarios.

### Optional: Authentication for Remote Access

If you plan to expose Friday via a tunnel (e.g., Cloudflare):

```bash
set FRIDAY_USERNAME=your-email
set FRIDAY_PASSWORD=your-password   # also encrypts the vault
set FRIDAY_SECRET_KEY=a-random-secret-string
```

Loopback (localhost) access is always auto-authenticated regardless of these settings.

---

## Step 5: Install Ollama (Optional)

Ollama enables local model routing — required for vault access to private data.

1. Download from [ollama.com](https://ollama.com/)
2. Install and start the Ollama service
3. Pull a model — or better, let Friday choose one. `friday models` reads your
   RAM, VRAM and disk and tells you the largest brain that fits, with the
   arithmetic behind anything it refuses; `friday models --install` then pulls
   exactly that. The Windows installer runs the same planner, so if you used it
   you can skip this step.

   The legacy `scripts/install.{sh,ps1,bat}` do something different and older:
   they pull `gemma3:4b` unconditionally, without consulting the planner. That
   model has **no native tool calling** — prefer `qwen3:4b`, which is smaller
   (2.5 GB against 3.3 GB) and keeps its tools.

   To pull one by hand instead:

```bash
ollama pull qwen3:4b     # 2.50 GB; needs a  ~7 GB card
ollama pull qwen3:8b     # 5.23 GB; needs a ~10 GB card
ollama pull gemma4:12b   # 7.56 GB; needs a ~12 GB card
ollama pull qwen3:14b    # 9.28 GB; needs a ~13 GB card
ollama pull qwen3:32b    # 20.20 GB; needs a ~24 GB card
```

   Those card sizes are the model's own footprint plus 2.5 GB for the desktop.
   A model's footprint is its weights plus about 1.7 GB of runtime overhead —
   KV cache at Friday's tool-seat context, the multimodal projector, and CUDA's
   own context. That 1.7 GB is measured: `gemma4:12b` occupies 8,745 MiB of a
   12 GB card against 7,023 MiB of weights.

   Two of these rungs are measured and three are arithmetic. `gemma4:12b` has
   been run and timed on a 12 GB card; `qwen3:14b` and `qwen3:32b` have not
   been run here, so their fit is calculated rather than observed. The table in
   `services/model_plan.py` marks which is which.

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

Set the key via environment variable, or run `friday setup` to store it encrypted in the credential store. Restart the server after changing.

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

The context pruner downloads the `all-MiniLM-L6-v2` model (~90 MB) on first use. This is a one-time download, and it is not the 2.5 GB memory tier — that is a separate, announced, skippable step in the Windows installer. If behind a proxy, set `HTTP_PROXY`/`HTTPS_PROXY` environment variables.

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
