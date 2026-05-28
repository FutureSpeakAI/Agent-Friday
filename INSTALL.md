# Installation Guide — Agent Friday

> **Platform:** Windows 10/11 (primary) · macOS/Linux (partial support)  
> **Python:** 3.10 or later required

---

## One-Line Install *(recommended)*

**Linux / macOS / WSL2:**
```bash
curl -fsSL https://raw.githubusercontent.com/FutureSpeakAI/asimovs-mind/main/scripts/install.sh | bash
```

**Windows PowerShell:**
```powershell
iex (irm https://raw.githubusercontent.com/FutureSpeakAI/asimovs-mind/main/scripts/install.ps1)
```

This handles everything: Python check, repo clone, venv, pip install, `friday` command registration, and setup wizard. Jump straight to [Step 4 — Launch](#step-4--launch) after the one-liner completes.

---

## Manual Install

---

## Prerequisites

Before installing, make sure you have:

- **Python 3.10+** — [python.org/downloads](https://www.python.org/downloads/)
- **Git** — [git-scm.com](https://git-scm.com/)
- **An Anthropic API key** — [console.anthropic.com](https://console.anthropic.com) *(required for chat)*
- **A Google Gemini API key** — [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) *(optional, enables voice and creative generation)*

---

## Step 1 — Clone the Repository

```bash
git clone https://github.com/FutureSpeakAI/friday-desktop.git
cd friday-desktop
```

---

## Step 2 — Install Python Dependencies

```bash
pip install flask anthropic google-genai rich colorama pyautogui beautifulsoup4 requests pyyaml
```

**What each package does:**
| Package | Purpose |
|---------|---------|
| `flask` | Web server + API |
| `anthropic` | Claude SDK |
| `google-genai` | Gemini SDK (voice, images, music) |
| `rich` | Setup wizard terminal UI |
| `colorama` | Windows color support |
| `pyautogui` | Ring 3 OS control (mouse, keyboard) |
| `beautifulsoup4` | Web scraping for `browse_web` |
| `requests` | HTTP client for web search |
| `pyyaml` | Config file YAML support (`~/.friday/config.yaml`) |

> **Virtual environment** (recommended):
> ```bash
> python -m venv venv
> venv\Scripts\activate     # Windows
> # source venv/bin/activate  # macOS/Linux
> pip install flask anthropic google-genai rich colorama pyautogui beautifulsoup4 requests
> ```

---

## Step 3 — Run the Setup Wizard

The interactive setup wizard configures everything and creates your `start.bat`:

```bash
# Via the CLI (after install):
friday setup

# Full wizard:
friday setup

# Quick mode — just name + API keys, skip cosmetics:
friday setup --quick

# Direct (before CLI is registered):
python setup_wizard.py
python setup_wizard.py --quick
```

**The wizard covers:**
1. Agent name (default: AGENT FRIDAY)
2. Orchestrator model (Claude Opus 4.7 recommended)
3. Creative engine (Gemini 2.5 Flash recommended)
4. Anthropic API key
5. Gemini API key (optional)
6. TTS voice persona
7. Holographic scene preference
8. Summary and confirmation
9. Writes `~/.friday/settings.json` + `start.bat`
10. Optionally launches the server

**After the wizard**, settings are saved to `~/.friday/settings.json` and your `start.bat` will contain the API keys as environment variables.

---

## Step 4 — Launch

### Option A — Use the generated start.bat

```
start.bat
```

This sets your API keys as environment variables and launches `server.py`.

### Option B — Set environment variables manually

```bash
# Windows (Command Prompt)
SET ANTHROPIC_API_KEY=sk-ant-your-key-here
SET GEMINI_API_KEY=AIza-your-key-here
python server.py

# Windows (PowerShell)
$env:ANTHROPIC_API_KEY = "sk-ant-your-key-here"
$env:GEMINI_API_KEY = "AIza-your-key-here"
python server.py
```

### Option C — Web wizard (browser-based)

If you skip the CLI wizard, launch the server and open `http://localhost:5000`. If setup has not been completed, the UI will show a glassmorphism setup overlay where you can enter your keys and preferences directly in the browser.

---

## Step 5 — Open in Browser

With the server running:

```
http://localhost:5000
```

On first load you'll see the holographic Three.js scene with the Agent Friday desktop.

---

## Configuration

All settings live in `~/.friday/settings.json`. You can edit this file directly or use the Settings panel in the UI.

**Key settings:**
```json
{
  "agent_name": "AGENT FRIDAY",
  "orchestrator_model": "claude-opus-4-7",
  "tts_voice": "Aoede",
  "temperature": 0.7,
  "response_length": "standard",
  "communication_style": "professional",
  "context_logging_enabled": true
}
```

**Agent personality** — edit `~/.friday/agent-personality.txt` to customize how Friday talks to you. The default is a calm, perceptive professional assistant.

---

## Optional: Enable Computer Control (Ring 3)

To let Friday control your mouse and keyboard (take screenshots, click, type):

1. Open the Settings panel in the UI (gear icon in dock)
2. Enable **Computer Control** toggle
3. The top bar shows a blinking red CC indicator when active
4. Use the **Kill Switch** (red button, always visible) to suspend instantly

> **Security note:** Ring 3 is rate-limited to 20 actions/second. Destructive shell commands (`rm`, `del`, `format`, etc.) are hard-blocked at the governance gate regardless of ring.

---

## Optional: Personal Wiki

Agent Friday can read, search, and propose edits to a personal wiki:

1. Create a directory at `~/wiki/` (e.g., `C:\Users\YourName\wiki\`)
2. Add Markdown files organized into subdirectories:
   ```
   ~/wiki/
     professional/
       job-search.md
       resume.md
     family/
       ...
     identity/
       about-me.md
   ```
3. Friday auto-loads relevant wiki sections based on message keywords

---

## Optional: Remote Access via Cloudflare Tunnel

To access Agent Friday from outside your home network (securely, without opening ports):

1. [Install cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
2. Run a quick tunnel:
   ```bash
   cloudflared tunnel --url http://localhost:5000
   ```
3. Cloudflare prints a `https://something.trycloudflare.com` URL — open it on any device
4. For a persistent domain, set up a named tunnel in your Cloudflare dashboard

> **Security:** Set `FRIDAY_PASSWORD` to protect the UI when exposed to the internet:
> ```bash
> SET FRIDAY_PASSWORD=your-strong-password
> python server.py
> ```

---

## Optional: Build the UI

If you modify any files in `ui_parts/`, regenerate `index.html`:

```bash
python build_ui.py
```

The assembler concatenates `head.html`, `styles_and_scene.html`, and `app.html` into a single `index.html`. The server serves this file directly — no separate build step needed for the React components (Babel runs in the browser via CDN).

---

## Troubleshooting

**"ANTHROPIC_API_KEY is not set"**  
→ Make sure your key is set in the environment before running `server.py`, or run `python setup_wizard.py` to save it to `~/.friday/settings.json`.

**"Creative endpoints disabled"**  
→ No Gemini API key found. Set `GEMINI_API_KEY` or add `gemini_api_key` to `~/.friday/settings.json`.

**Server starts but UI is blank**  
→ Run `python build_ui.py` to regenerate `index.html`.

**pyautogui not working (Ring 3)**  
→ On Windows, pyautogui requires no additional setup. On macOS, grant accessibility permissions in System Preferences.

**Port 5000 already in use**  
→ Change the port: `python server.py --port 5001` (or set `FLASK_RUN_PORT=5001`).

**"google-genai not installed"**  
→ Run `pip install google-genai` and restart the server.

---

## Updating

```bash
git pull origin main
pip install -r requirements.txt  # if a requirements.txt is added
python build_ui.py
python server.py
```

---

*See `README.md` for feature overview and `ARCHITECTURE.md` for system design.*
