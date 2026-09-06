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

## Supported installation paths

| Method | Supported | Platforms | Notes |
|---|---|---|---|
| **Windows installer zip** (`AgentFriday-Setup-<version>.zip`) | **Yes — primary** | Windows 10/11 | Embedded CPython, source payload, wheelhouse. No Python, Git or terminal needed. Installs Ollama and sizes a local model to your GPU, or recommends a cloud key. Take the newest zip; every older one is superseded. |
| **Source checkout** (`pip install -e .`) | **Yes** | Windows, macOS, Linux | The developer path and the only path on macOS/Linux. Feature differences by platform are in the README. |
| **Wheel** (`python -m build`, `pip install agent_friday-*.whl`) | Yes, for the application and bundled seed skills | Windows, macOS, Linux | Not published to PyPI; build it yourself. CI verifies the wheel carries the seed skills' data files. |
| **One-line installers** (`scripts/install.sh`, `install.ps1`, `install.bat`) | Yes, as a convenience over the source path | Linux/macOS/WSL2, Windows | They clone this repository and run `friday setup`. Read them before piping anything to a shell. |
| **`AgentFriday.exe`** (PyInstaller) | **No** | — | The recipe (`AgentFriday.spec`) is kept for reference. The last published binary is from July 2026 and predates current privacy fixes; do not use it. Any `.exe` in a checkout's `dist/` is that same build. |

### The Windows installer

Download the zip from the
[latest release](https://github.com/FutureSpeakAI/Agent-Friday/releases/latest),
unzip it anywhere, and double-click **Install Agent Friday.cmd**. SmartScreen
may warn on first launch; see the note below. Everything installs per-user
under `%LOCALAPPDATA%\AgentFriday`; no administrator rights are used.

Upgrading over an existing install keeps everything under `~/.friday`. Two
older installer defects are worth knowing if your install predates them:
5.6.0–5.6.4 did not replace application files on upgrade (running the current
installer repairs it), and 5.6.5 deleted a vault passphrase that lived only in
`start.bat` (since 5.7.0 the passphrase lives in the OS keychain and a
DPAPI-wrapped file that no installer touches). Both are in
[KNOWN_ISSUES.md](../../KNOWN_ISSUES.md).

### What a packaged install runs, privacy-wise

The sensitivity classifier declares four layers; which ones run depends on the
install. The installer's *memory* tier adds the embedding layer
(`sentence-transformers`, about 2.5 GB, announced and skippable); its
*recommended* tier installs Presidio, which runs observe-only and reports as
inactive by design. On first run Friday probes its own layers and prints the
result, with a boxed `SENSITIVITY CLASSIFIER IS RUNNING DEGRADED` notice when
anything declared is not running. Seeing `presidio` listed as inactive is
expected. The reasoning is in the
[threat model](../security/threat-model.md).

Nothing downloads a model behind your back: Presidio's spaCy model is only
fetched under `FRIDAY_PRESIDIO_ENFORCE=1` or `FRIDAY_PRESIDIO_SHADOW=1`, and
the embedding model (`all-MiniLM-L6-v2`) arrives on first use after the
installer has warned about the memory tier.

The steps that follow are the from-source path.

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

Cloud keys are **optional in principle, and asked about in practice.** Friday
can chat with no key at all through a local model on Ollama; the Windows
installer asks which way you want to run her and recommends a key on a graphics
card too small to hold a model comfortably. Add a key to upgrade reasoning
(Anthropic) or unlock voice and creative work (Gemini).

Where a key ends up depends on how you enter it:

| Entered through | Stored in | Protection |
|---|---|---|
| **Settings → Providers** in the running app | `~/.friday/providers/keys/<provider>.key` | Encrypted: vault key (Argon2id → AES-256-GCM) when a vault passphrase is set, otherwise Windows DPAPI, otherwise plaintext with a one-time warning. |
| **`friday setup`** (the command-line wizard) | `~/.friday/settings.json`, `~/.friday/config.yaml`, and a `start.bat` launcher in the checkout | **Plaintext.** These files are outside the repository or gitignored, but treat them as containing live secrets. |
| Environment variables | your shell or system environment | Wins over every stored copy. |

The vault passphrase is never written to a launch script: `friday setup` and
`friday vault-setup` store it in the OS keychain and a DPAPI-wrapped file
under `~/.friday/security/`. Full detail: [SECURITY.md](../../SECURITY.md).

### Option A: Settings → Providers (recommended)

Start Friday, open **Settings → Providers**, and paste the key. It is verified
against the provider and stored in the encrypted store. `friday setup` is the
terminal alternative for a first run; it configures routing, the vault
passphrase, and voice as well, but writes provider keys in plaintext as shown
above.

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
   model has **no native tool calling** — prefer `gemma4:e2b`, which is
   smaller (7.2 GB against gemma3:4b's 3.3 GB download, but far lighter once
   loaded — 1.77 GiB VRAM measured) and keeps its tools.

   To pull one by hand instead (the Gemma 4 family — Qwen was removed from
   the ladder entirely on 2026-09-03, in favor of Gemma 4 as a placeholder
   until FutureSpeak's own model ships):

```bash
ollama pull gemma4:e2b   # 7.2 GB;  needs a  ~5 GB card
ollama pull gemma4:e4b   # 9.6 GB;  needs a  ~6 GB card
ollama pull gemma4:12b   # 7.6 GB;  needs a ~11 GB card
ollama pull gemma4:26b   # 19.0 GB; needs a ~20 GB card
```

   Those card sizes are the model's own footprint plus 2.5 GB for the desktop.
   A model's footprint is its weights plus runtime overhead — KV cache at
   Friday's tool-seat context, the multimodal projector, and CUDA's own
   context. `gemma4:12b`'s is measured directly: 7,718 MiB of a 12 GB card.

   All four rungs have real measurements behind them, though not the same
   ones: `gemma4:e2b` and `gemma4:12b` have both a measured VRAM footprint and
   a measured tool-calling score; `gemma4:e4b`'s VRAM is measured at a smaller
   context than its tool-seat width; `gemma4:26b` is an MoE (26B total / 4B
   active) whose measured footprint used a hybrid GPU+CPU split this simple
   ladder can't represent, so its card-size figure here is the conservative
   full-residency number, not the smaller one that actually worked in
   production. The table in `services/model_plan.py` has the full detail.

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

## The local model ladder

Which local model you get is decided by your hardware, not by a default in a
configuration file. The planner takes the largest brain that fits your card:

| Your card | You get | Download | What it is |
|---|---|---|---|
| 5 GB | `gemma4:e2b` | 7.2 GB | The smallest seat that keeps its tools — quick lookups, formatting, status checks |
| 6 GB | `gemma4:e4b` | 9.6 GB | A solid everyday model |
| 11 GB | `gemma4:12b` | 7.6 GB | Measured at 49–54 tok/s, fully resident on a 12 GB card — the model Friday is tuned against |
| 20 GB+ | `gemma4:26b` | 19.0 GB | The largest offered — an MoE, closest to a cloud model for tool use |

"Your card" is the whole card: 2.5 GB comes off it for the desktop, and each
model's own KV cache, projector and CUDA context are counted inside its
footprint. `friday models` shows what your machine can hold and the arithmetic
behind anything it refuses.

Size is capability, not just speed. On published function-calling benchmarks a
4B model scores in the low 80s on single-call syntax and in the teens on
multi-turn exchanges, and the failure is invisible: the model keeps talking
fluently while losing the thread of a multi-step job. That is why an 8 GB card
defaults to a cloud key rather than a local model, and why every model in the
table calls tools natively — the planner refuses to select a tool-incapable
model at any tier, and re-checks that flag against the daemon after every
install rather than trusting a table. A model can still *narrate* a tool call
it never made; `tool_integrity.find_pseudo_toolcalls` catches that after the
fact rather than preventing it.

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
3. Pull at least one model: `ollama pull gemma4:e2b`
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
