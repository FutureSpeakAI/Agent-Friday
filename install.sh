#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  Agent Friday — Linux / macOS Installer
#  https://github.com/FutureSpeakAI/Agent-Friday
# ─────────────────────────────────────────────────────────────
set -euo pipefail

CYAN='\033[36m'; GREEN='\033[32m'; RED='\033[31m'; BOLD='\033[1m'; RESET='\033[0m'

banner() {
    echo ""
    echo -e "${CYAN}${BOLD}    ╔═══════════════════════════════════════════════╗${RESET}"
    echo -e "${CYAN}${BOLD}    ║           A G E N T   F R I D A Y             ║${RESET}"
    echo -e "${CYAN}${BOLD}    ║       Sovereign Personal AI Assistant         ║${RESET}"
    echo -e "${CYAN}${BOLD}    ║            by FutureSpeak.AI                  ║${RESET}"
    echo -e "${CYAN}${BOLD}    ╚═══════════════════════════════════════════════╝${RESET}"
    echo ""
}

command_exists() { command -v "$1" &>/dev/null; }

banner

# ── Python check ────────────────────────────────────────────
echo "[1/8] Checking Python..."
PYTHON=""
for cmd in python3 python; do
    if command_exists "$cmd"; then
        ver=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
        major=${ver%%.*}
        minor=${ver#*.}
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON="$cmd"
            echo "  Found Python $ver"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo -e "${RED}  Python 3.10+ is required but not found.${RESET}"
    echo "  Install via your package manager or https://www.python.org/downloads/"
    exit 1
fi

# ── Clone or update ─────────────────────────────────────────
INSTALL_DIR="$HOME/Agent-Friday"

echo "[2/8] Setting up project directory..."
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "  Existing install found — pulling latest..."
    cd "$INSTALL_DIR"
    git pull --ff-only 2>/dev/null || true
elif [ -d "$INSTALL_DIR" ]; then
    echo "  Directory exists but is not a git repo. Using as-is."
    cd "$INSTALL_DIR"
else
    if command_exists git; then
        echo "  Cloning repository..."
        git clone https://github.com/FutureSpeakAI/Agent-Friday.git "$INSTALL_DIR"
        cd "$INSTALL_DIR"
    else
        echo -e "${RED}  Git is not installed. Install it first:${RESET}"
        echo "  macOS: xcode-select --install"
        echo "  Ubuntu/Debian: sudo apt install git"
        echo "  Fedora: sudo dnf install git"
        exit 1
    fi
fi

# ── Virtual environment ─────────────────────────────────────
echo "[3/8] Creating Python virtual environment..."
if [ ! -d "venv" ]; then
    "$PYTHON" -m venv venv
fi
VENV_PYTHON="$INSTALL_DIR/venv/bin/python"
VENV_PIP="$INSTALL_DIR/venv/bin/pip"

# ── Dependencies ────────────────────────────────────────────
echo "[4/8] Installing dependencies..."
"$VENV_PIP" install --upgrade pip --quiet
if [ -f "requirements.txt" ]; then
    "$VENV_PIP" install -r requirements.txt --quiet
    echo "  Core dependencies installed."
else
    echo "  No requirements.txt found — skipping pip install."
fi

# ── API keys ────────────────────────────────────────────────
echo "[5/8] API key configuration..."
FRIDAY_DIR="$HOME/.friday"
mkdir -p "$FRIDAY_DIR"

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    echo ""
    echo "  Anthropic API key (required for Claude)."
    echo "  Get one at: https://console.anthropic.com/settings/keys"
    read -rp "  Enter your Anthropic API key (or press Enter to skip): " key
    if [ -n "$key" ]; then
        export ANTHROPIC_API_KEY="$key"
        echo "  Set ANTHROPIC_API_KEY for this session."
    fi
fi

if [ -z "${GEMINI_API_KEY:-}" ]; then
    echo ""
    echo "  Google Gemini API key (required for voice mode + creative)."
    echo "  Get one at: https://aistudio.google.com/apikey"
    read -rp "  Enter your Gemini API key (or press Enter to skip): " key
    if [ -n "$key" ]; then
        export GEMINI_API_KEY="$key"
        echo "  Set GEMINI_API_KEY for this session."
    fi
fi

# ── GPU detection + Ollama ──────────────────────────────────
echo "[6/8] Checking GPU and local model support..."
HAS_GPU=false

if command_exists nvidia-smi; then
    gpu_name=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    if [ -n "$gpu_name" ]; then
        HAS_GPU=true
        echo "  GPU detected: $gpu_name"
    fi
elif [ "$(uname)" = "Darwin" ]; then
    # macOS — Apple Silicon has unified GPU
    if sysctl -n machdep.cpu.brand_string 2>/dev/null | grep -q "Apple"; then
        HAS_GPU=true
        echo "  Apple Silicon detected (unified GPU)."
    fi
fi

if $HAS_GPU; then
    if command_exists ollama; then
        echo "  Ollama is already installed."
        echo "  Tip: run 'ollama pull llama3.2' for a local model."
    else
        echo ""
        echo "  GPU detected! Ollama enables local AI models for vault privacy."
        read -rp "  Install Ollama? (y/N): " install_ollama
        if [[ "$install_ollama" =~ ^[yY]$ ]]; then
            echo "  Installing Ollama..."
            curl -fsSL https://ollama.com/install.sh | sh
            echo "  Ollama installed. Run 'ollama pull llama3.2' after setup."
        fi
    fi
else
    echo "  No dedicated GPU detected. Ollama (local models) skipped."
    echo "  Cloud models will handle all requests."
fi

# ── Build UI ────────────────────────────────────────────────
echo "[7/8] Building UI..."
if [ -f "build_ui.py" ]; then
    "$VENV_PYTHON" build_ui.py
    echo "  UI built successfully."
else
    echo "  build_ui.py not found — skipping UI build."
fi

# ── Start script ────────────────────────────────────────────
echo "[8/8] Creating start script..."
START_SCRIPT="$INSTALL_DIR/start.sh"
cat > "$START_SCRIPT" << 'STARTEOF'
#!/usr/bin/env bash
cd "$(dirname "$0")"
source venv/bin/activate
python server.py
STARTEOF
chmod +x "$START_SCRIPT"

# Add API keys to start script if set
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    sed -i.bak '2a export ANTHROPIC_API_KEY="'"$ANTHROPIC_API_KEY"'"' "$START_SCRIPT" 2>/dev/null || \
    sed -i '' '2a\
export ANTHROPIC_API_KEY="'"$ANTHROPIC_API_KEY"'"' "$START_SCRIPT"
    rm -f "${START_SCRIPT}.bak"
fi
if [ -n "${GEMINI_API_KEY:-}" ]; then
    sed -i.bak '2a export GEMINI_API_KEY="'"$GEMINI_API_KEY"'"' "$START_SCRIPT" 2>/dev/null || \
    sed -i '' '2a\
export GEMINI_API_KEY="'"$GEMINI_API_KEY"'"' "$START_SCRIPT"
    rm -f "${START_SCRIPT}.bak"
fi

# macOS: create .command double-clickable alias
if [ "$(uname)" = "Darwin" ]; then
    ln -sf "$START_SCRIPT" "$INSTALL_DIR/Start Agent Friday.command"
    echo "  Double-click 'Start Agent Friday.command' to launch."
fi

# ── Done ────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}  Installation complete!${RESET}"
echo ""
echo "  To start Agent Friday:"
echo "    cd $INSTALL_DIR && ./start.sh"
echo ""
echo "  Friday will open at http://localhost:3000"
echo ""
echo "  First run? The setup wizard will guide you through"
echo "  API keys, model selection, and voice configuration."
echo ""
