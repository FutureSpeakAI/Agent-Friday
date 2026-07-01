#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
#  Agent Friday — One-Line Installer (Linux / macOS / WSL2)
#  FutureSpeak.AI · Asimov's Mind
#
#  Usage:
#    curl -fsSL https://raw.githubusercontent.com/FutureSpeakAI/asimovs-mind/main/scripts/install.sh | bash
#
#  What this does:
#    1. Checks Python 3.10+
#    2. Clones the repo to ~/.friday-desktop
#    3. Creates a Python virtual environment
#    4. Installs all dependencies
#    5. Registers the `friday` CLI command in ~/.local/bin
#    6. Runs `friday setup` to configure your agent
# ─────────────────────────────────────────────────────────────────
set -e

REPO_URL="https://github.com/FutureSpeakAI/friday-desktop.git"
INSTALL_DIR="$HOME/.friday-desktop"
BIN_DIR="$HOME/.local/bin"
CLI_ENTRY="$BIN_DIR/friday"

# ── Colors ───────────────────────────────────────────────────────
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

info()    { echo -e "${CYAN}  ▸  $*${RESET}"; }
success() { echo -e "${GREEN}  ✓  $*${RESET}"; }
warn()    { echo -e "${YELLOW}  ⚠  $*${RESET}"; }
error()   { echo -e "${RED}  ✗  $*${RESET}"; exit 1; }
dim()     { echo -e "${DIM}     $*${RESET}"; }

echo ""
echo -e "${CYAN}${BOLD}"
cat << 'BANNER'
     ██████╗ ██╗██████╗  █████╗ ██╗   ██╗
    ██╔════╝ ██║██╔══██╗██╔══██╗╚██╗ ██╔╝
    ██║  ███╗██║██║  ██║███████║ ╚████╔╝
    ██║   ██║██║██║  ██║██╔══██║  ╚██╔╝
    ╚██████╔╝██║██████╔╝██║  ██║   ██║
     ╚═════╝ ╚═╝╚═════╝ ╚═╝  ╚═╝   ╚═╝
BANNER
echo -e "${RESET}"
echo -e "  ${MAGENTA}${BOLD}A S I M O V ' S   M I N D${RESET}  ${DIM}by FutureSpeak.AI${RESET}"
echo ""
echo -e "  ${DIM}Installing Agent Friday...${RESET}"
echo ""

# ── 1. Python check ──────────────────────────────────────────────
info "Checking Python..."

# Try python3 first, then python
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
        MAJOR=$(echo "$VER" | cut -d. -f1)
        MINOR=$(echo "$VER" | cut -d. -f2)
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 10 ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    error "Python 3.10+ not found.

  Install it from: https://www.python.org/downloads/

  On Ubuntu/Debian:   sudo apt install python3.11
  On macOS:           brew install python@3.11
  On WSL2:            sudo apt install python3.11"
fi

PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
success "Python $PY_VER  ($PYTHON)"

# ── 2. Git check ─────────────────────────────────────────────────
if ! command -v git &>/dev/null; then
    error "git not found. Install git and re-run.
  Ubuntu/Debian: sudo apt install git
  macOS:         brew install git"
fi
success "git $(git --version | awk '{print $3}')"

# ── 3. Clone or update ───────────────────────────────────────────
echo ""
if [ -d "$INSTALL_DIR/.git" ]; then
    info "Updating existing installation at $INSTALL_DIR..."
    git -C "$INSTALL_DIR" pull --ff-only origin main 2>&1 | sed 's/^/     /'
else
    info "Cloning Agent Friday to $INSTALL_DIR..."
    git clone --depth=1 "$REPO_URL" "$INSTALL_DIR" 2>&1 | sed 's/^/     /'
fi
success "Repository ready"

# ── 4. Virtual environment ───────────────────────────────────────
echo ""
VENV_DIR="$INSTALL_DIR/venv"
if [ ! -d "$VENV_DIR" ]; then
    info "Creating Python virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
fi
PYTHON_VENV="$VENV_DIR/bin/python"
PIP_VENV="$VENV_DIR/bin/pip"
success "Virtual environment ready"

# ── 5. Install dependencies ──────────────────────────────────────
echo ""
info "Installing dependencies..."
"$PIP_VENV" install --quiet --upgrade pip
"$PIP_VENV" install --quiet -r "$INSTALL_DIR/requirements.txt"

# Optional: pyautogui extras on Linux
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    "$PIP_VENV" install --quiet python-xlib 2>/dev/null || true
fi

success "Dependencies installed"

# ── 6. Build UI ──────────────────────────────────────────────────
echo ""
info "Building UI..."
"$PYTHON_VENV" "$INSTALL_DIR/build_ui.py" > /dev/null
success "index.html built"

# ── 6.5 Bundled model (Ollama + Gemma) — zero cloud key needed ───
#  Friday runs a local Gemma model by default so chat works with NO API key.
#  This step is best-effort and never fails the install; skip it with
#  FRIDAY_SKIP_MODEL=1 or the --no-model flag.
echo ""
BUNDLED_MODEL="gemma3:4b"
if [ "${FRIDAY_SKIP_MODEL:-0}" = "1" ] || [[ " $* " == *" --no-model "* ]]; then
    warn "Skipping local model bootstrap (FRIDAY_SKIP_MODEL / --no-model)"
else
    info "Setting up local model ($BUNDLED_MODEL) for no-API-key chat..."
    ( set +e
      if ! command -v ollama &>/dev/null; then
          info "Installing Ollama..."
          if [[ "$OSTYPE" == "darwin"* ]] && command -v brew &>/dev/null; then
              brew install ollama
          else
              curl -fsSL https://ollama.com/install.sh | sh
          fi
      fi
      if command -v ollama &>/dev/null; then
          # Ensure a server is up (best-effort; harmless if one already runs).
          (ollama serve >/dev/null 2>&1 &) || true
          sleep 2
          if ollama list 2>/dev/null | grep -q "$BUNDLED_MODEL"; then
              success "$BUNDLED_MODEL already present"
          else
              info "Pulling $BUNDLED_MODEL (~3GB, one-time download)..."
              if ollama pull "$BUNDLED_MODEL"; then
                  success "$BUNDLED_MODEL ready — no cloud key required for chat"
              else
                  warn "Model pull failed — run 'ollama pull $BUNDLED_MODEL' later"
              fi
          fi
      else
          warn "Ollama unavailable — install later for offline chat: https://ollama.com"
      fi
    )
fi

# ── 7. Register CLI entry point ──────────────────────────────────
echo ""
info "Registering 'friday' command..."
mkdir -p "$BIN_DIR"

cat > "$CLI_ENTRY" << SCRIPT
#!/usr/bin/env bash
# Agent Friday CLI entry point
# Auto-generated by install.sh — do not edit
export FRIDAY_INSTALL_DIR="$INSTALL_DIR"
cd "\$FRIDAY_INSTALL_DIR"
exec "$PYTHON_VENV" "\$FRIDAY_INSTALL_DIR/friday_cli.py" "\$@"
SCRIPT

chmod +x "$CLI_ENTRY"
success "Created $CLI_ENTRY"

# ── 8. PATH check ────────────────────────────────────────────────
PATH_UPDATED=false
for PROFILE_FILE in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
    if [ -f "$PROFILE_FILE" ] && ! grep -q "$BIN_DIR" "$PROFILE_FILE"; then
        echo "" >> "$PROFILE_FILE"
        echo "# Agent Friday CLI" >> "$PROFILE_FILE"
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$PROFILE_FILE"
        PATH_UPDATED=true
        dim "Added $BIN_DIR to PATH in $PROFILE_FILE"
        break
    fi
done

# Also export for current session
export PATH="$BIN_DIR:$PATH"

# ── Done ─────────────────────────────────────────────────────────
echo ""
echo -e "  ${GREEN}${BOLD}╔══════════════════════════════════════════╗${RESET}"
echo -e "  ${GREEN}${BOLD}║  Agent Friday installed successfully!    ║${RESET}"
echo -e "  ${GREEN}${BOLD}╚══════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  ${CYAN}${BOLD}Next step:${RESET}  run the setup wizard"
echo ""
echo -e "  ${BOLD}    friday setup${RESET}"
echo ""

if $PATH_UPDATED; then
    warn "PATH updated — restart your shell or run:"
    echo -e "      ${DIM}source ~/.bashrc${RESET}  (or ~/.zshrc)"
    echo ""
fi

# ── Offer to run setup now ───────────────────────────────────────
read -r -p "  Run setup now? [Y/n] " REPLY
REPLY="${REPLY:-Y}"
if [[ "$REPLY" =~ ^[Yy] ]]; then
    echo ""
    exec "$PYTHON_VENV" "$INSTALL_DIR/setup_wizard.py"
fi
