#!/usr/bin/env bash
# Maw installer — Linux & macOS
set -euo pipefail

# Windows is not supported natively — use WSL
if [[ "${OS:-}" == "Windows_NT" ]] || [[ "$(uname -s 2>/dev/null)" == MINGW* ]]; then
    echo "Windows is not supported. Please use WSL (Windows Subsystem for Linux)."
    echo "See: https://learn.microsoft.com/en-us/windows/wsl/install"
    exit 1
fi

OS_TYPE="$(uname -s)"  # Linux or Darwin

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

MAW_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$MAW_DIR/.venv"
BIN_DIR="$HOME/.local/bin"

ok()   { echo -e "  ${GREEN}✓${NC}  $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $1"; }
err()  { echo -e "  ${RED}✗${NC}  $1"; }
info() { echo -e "  ${BLUE}→${NC}  $1"; }
hdr()  { echo -e "\n${BOLD}[$1]${NC} $2"; }

echo ""
echo -e "${BOLD}${BLUE}  ███╗   ███╗ █████╗ ██╗    ██╗${NC}"
echo -e "${BOLD}${BLUE}  ████╗ ████║██╔══██╗██║    ██║${NC}"
echo -e "${BOLD}${BLUE}  ██╔████╔██║███████║██║ █╗ ██║${NC}"
echo -e "${BOLD}${BLUE}  ██║╚██╔╝██║██╔══██║██║███╗██║${NC}"
echo -e "${BOLD}${BLUE}  ██║ ╚═╝ ██║██║  ██║╚███╔███╔╝${NC}"
echo -e "${BOLD}${BLUE}  ╚═╝     ╚═╝╚═╝  ╚═╝ ╚══╝╚══╝ ${NC}"
echo ""
echo -e "  ${BOLD}local AI file agent${NC}  ·  installer"
echo ""
echo -e "  ${BLUE}────────────────────────────────${NC}"
echo ""

# ── 0. macOS: Homebrew check ──────────────────────────────────────────────────
if [[ "$OS_TYPE" == "Darwin" ]] && ! command -v brew &>/dev/null; then
    hdr "0/5" "Homebrew not found..."
    echo ""
    echo "  Homebrew is the standard package manager for macOS."
    echo "  Maw uses it to install Python and Ollama."
    echo "  It will be installed to /opt/homebrew (Apple Silicon) or /usr/local (Intel)."
    echo "  More info: https://brew.sh"
    echo ""
    read -r -p "  Install Homebrew? [Y/n] " choice
    if [[ ! "$choice" =~ ^[Nn]$ ]]; then
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        # Add brew to PATH for the rest of this script
        if [[ -f /opt/homebrew/bin/brew ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        elif [[ -f /usr/local/bin/brew ]]; then
            eval "$(/usr/local/bin/brew shellenv)"
        fi
        ok "Homebrew installed"
    else
        err "Homebrew is required on macOS. Exiting."
        exit 1
    fi
fi

# ── 1. Python check ───────────────────────────────────────────────────────────
hdr "1/5" "Checking Python..."

PYTHON=""
for cmd in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" &>/dev/null; then
        major=$("$cmd" -c "import sys; print(sys.version_info.major)")
        minor=$("$cmd" -c "import sys; print(sys.version_info.minor)")
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON="$cmd"
            version=$("$cmd" --version 2>&1)
            ok "Found $cmd ($version)"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    warn "Python 3.10+ not found."
    if command -v pacman &>/dev/null; then
        read -r -p "  Install Python via pacman? [Y/n] " choice
        if [[ ! "$choice" =~ ^[Nn]$ ]]; then
            sudo pacman -S --noconfirm python && PYTHON="python3" && ok "Python installed"
        else
            err "Python 3.10+ is required. Exiting."; exit 1
        fi
    elif command -v apt-get &>/dev/null; then
        read -r -p "  Install Python via apt? [Y/n] " choice
        if [[ ! "$choice" =~ ^[Nn]$ ]]; then
            sudo apt-get install -y python3 && PYTHON="python3" && ok "Python installed"
        else
            err "Python 3.10+ is required. Exiting."; exit 1
        fi
    elif command -v dnf &>/dev/null; then
        read -r -p "  Install Python via dnf? [Y/n] " choice
        if [[ ! "$choice" =~ ^[Nn]$ ]]; then
            sudo dnf install -y python3 && PYTHON="python3" && ok "Python installed"
        else
            err "Python 3.10+ is required. Exiting."; exit 1
        fi
    elif command -v brew &>/dev/null; then
        read -r -p "  Install Python via Homebrew? [Y/n] " choice
        if [[ ! "$choice" =~ ^[Nn]$ ]]; then
            brew install python3 && PYTHON="python3" && ok "Python installed"
        else
            err "Python 3.10+ is required. Exiting."; exit 1
        fi
    else
        err "Python 3.10+ is required. Install it from https://python.org"
        exit 1
    fi
fi

# ── 2. Ollama check ───────────────────────────────────────────────────────────
hdr "2/5" "Checking Ollama..."

if command -v ollama &>/dev/null; then
    ok "Ollama found ($(ollama --version 2>/dev/null || echo 'version unknown'))"
else
    warn "Ollama not found."
    echo ""
    echo "  Ollama runs AI models locally on your machine."
    echo "  It is required for Maw to work."
    echo ""
    if command -v pacman &>/dev/null; then
        read -r -p "  Install Ollama via pacman? [Y/n] " choice
        if [[ ! "$choice" =~ ^[Nn]$ ]]; then
            sudo pacman -S --noconfirm ollama && ok "Ollama installed"
        else
            err "Ollama is required. Exiting."; exit 1
        fi
    elif command -v brew &>/dev/null; then
        read -r -p "  Install Ollama via Homebrew? [Y/n] " choice
        if [[ ! "$choice" =~ ^[Nn]$ ]]; then
            brew install ollama && ok "Ollama installed"
        else
            err "Ollama is required. Exiting."; exit 1
        fi
    elif command -v apt-get &>/dev/null; then
        read -r -p "  Install Ollama via official installer? [Y/n] " choice
        if [[ ! "$choice" =~ ^[Nn]$ ]]; then
            curl -fsSL https://ollama.com/install.sh | sh && ok "Ollama installed"
        else
            err "Ollama is required. Exiting."; exit 1
        fi
    elif command -v dnf &>/dev/null; then
        read -r -p "  Install Ollama via official installer? [Y/n] " choice
        if [[ ! "$choice" =~ ^[Nn]$ ]]; then
            curl -fsSL https://ollama.com/install.sh | sh && ok "Ollama installed"
        else
            err "Ollama is required. Exiting."; exit 1
        fi
    elif command -v curl &>/dev/null; then
        read -r -p "  Install Ollama via official installer? [Y/n] " choice
        if [[ ! "$choice" =~ ^[Nn]$ ]]; then
            curl -fsSL https://ollama.com/install.sh | sh && ok "Ollama installed"
        else
            err "Ollama is required. Exiting."; exit 1
        fi
    else
        err "Cannot install Ollama. Install it from https://ollama.com"
        exit 1
    fi
fi

# Start Ollama if not already running
if curl -sf http://localhost:11434 &>/dev/null; then
    ok "Ollama is already running"
else
    info "Ollama is not running. Attempting to start it..."
    if [[ "$OS_TYPE" == "Darwin" ]]; then
        ollama serve &>/dev/null &
        sleep 3
    elif systemctl is-enabled ollama &>/dev/null 2>&1; then
        sudo systemctl start ollama
        sleep 2
    else
        ollama serve &>/dev/null &
        sleep 3
        info "To run Ollama automatically: sudo systemctl enable --now ollama"
    fi

    if curl -sf http://localhost:11434 &>/dev/null; then
        ok "Ollama started"
    else
        warn "Could not confirm Ollama is running. Continue anyway? [y/N]"
        read -r choice
        [[ "$choice" =~ ^[Yy]$ ]] || exit 1
    fi
fi

# ── 3. Python environment ─────────────────────────────────────────────────────
hdr "3/5" "Setting up Python environment..."

if [ ! -d "$VENV_DIR" ]; then
    info "Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
fi
ok "Virtual environment ready at $VENV_DIR"

info "Upgrading pip..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip

info "Installing core packages (requests, rich, prompt_toolkit)..."
"$VENV_DIR/bin/pip" install --quiet requests rich prompt_toolkit
ok "Core packages installed"

echo ""
echo "  RAG memory (per-folder file indexing) requires:"
echo "  • chromadb      — local vector database"
echo "  • sentence-transformers — local embeddings (~500MB + PyTorch)"
echo ""
read -r -p "  Install RAG packages? [y/N] " rag_choice
if [[ "$rag_choice" =~ ^[Yy]$ ]]; then
    info "Installing chromadb and sentence-transformers..."
    info "This may take several minutes (PyTorch is large)..."
    "$VENV_DIR/bin/pip" install --quiet chromadb sentence-transformers
    ok "RAG packages installed"
    echo ""
    info "Pre-downloading the embedding model (all-MiniLM-L6-v2)..."
    HF_HUB_DISABLE_IMPLICIT_TOKEN=1 "$VENV_DIR/bin/python" -c "
import os, sys, io, contextlib
os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN'] = '1'
import warnings; warnings.filterwarnings('ignore')
from sentence_transformers import SentenceTransformer
with open(os.devnull, 'w') as dn:
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = dn
    SentenceTransformer('all-MiniLM-L6-v2')
    sys.stdout, sys.stderr = old_out, old_err
"
    ok "Embedding model ready"
else
    warn "Skipping RAG. Maw will work, just without per-folder memory."
fi

# ── 4. Model picker ───────────────────────────────────────────────────────────
hdr "4/5" "Model selection..."

# Detect available RAM
if [[ "$OS_TYPE" == "Darwin" ]]; then
    RAM_BYTES=$(sysctl -n hw.memsize)
    RAM_GB=$((RAM_BYTES / 1024 / 1024 / 1024))
else
    RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    RAM_GB=$((RAM_KB / 1024 / 1024))
fi
info "System RAM: ~${RAM_GB}GB"
echo ""

if [ "$RAM_GB" -lt 8 ]; then
    echo "  Models recommended for your system (<8GB RAM):"
    echo "  ┌──────────────────────────────────────────────────────────────┐"
    echo "  │  [1] llama3.2:3b     2.0GB  ← recommended                   │"
    echo "  │  [2] phi3:mini       2.2GB  good at coding and reasoning     │"
    echo "  │  [3] gemma2:2b       1.6GB  very fast, smallest              │"
    echo "  └──────────────────────────────────────────────────────────────┘"
    DEFAULTS=("llama3.2:3b" "phi3:mini" "gemma2:2b")
    DEFAULT_MODEL="llama3.2:3b"
elif [ "$RAM_GB" -lt 20 ]; then
    echo "  Models recommended for your system (8–20GB RAM):"
    echo "  ┌──────────────────────────────────────────────────────────────┐"
    echo "  │  [1] llama3.1:8b     4.7GB  ← recommended, best quality     │"
    echo "  │  [2] mistral:7b      4.1GB  fast, great all-rounder         │"
    echo "  │  [3] llama3.2:3b     2.0GB  lighter, still good             │"
    echo "  └──────────────────────────────────────────────────────────────┘"
    DEFAULTS=("llama3.1:8b" "mistral:7b" "llama3.2:3b")
    DEFAULT_MODEL="llama3.1:8b"
else
    echo "  Models recommended for your system (20GB+ RAM):"
    echo "  ┌──────────────────────────────────────────────────────────────┐"
    echo "  │  [1] llama3.1:8b     4.7GB  ← reliable, fast                │"
    echo "  │  [2] qwen2.5:14b     8.9GB  smarter, great for complex tasks │"
    echo "  │  [3] mistral:7b      4.1GB  fast all-rounder                 │"
    echo "  └──────────────────────────────────────────────────────────────┘"
    DEFAULTS=("llama3.1:8b" "qwen2.5:14b" "mistral:7b")
    DEFAULT_MODEL="llama3.1:8b"
fi

# Show already-downloaded models
echo ""
EXISTING=$(ollama list 2>/dev/null | tail -n +2 | awk '{print $1}' || true)
if [ -n "$EXISTING" ]; then
    echo "  Already downloaded:"
    echo "$EXISTING" | while read -r m; do echo "    • $m"; done
else
    echo "  (no models downloaded yet)"
fi
echo ""

read -r -p "  Enter model name or [1/2/3] [default: $DEFAULT_MODEL]: " model_choice

case "$model_choice" in
    "1"|"") MODEL="${DEFAULTS[0]}" ;;
    "2")    MODEL="${DEFAULTS[1]}" ;;
    "3")    MODEL="${DEFAULTS[2]}" ;;
    *)      MODEL="$model_choice" ;;
esac

# Pull the model if not already present
MODEL_BASE="${MODEL%%:*}"
if echo "$EXISTING" | grep -q "^${MODEL_BASE}"; then
    ok "Model $MODEL is already downloaded"
else
    info "Pulling $MODEL from Ollama (this may take several minutes)..."
    ollama pull "$MODEL"
    ok "Model $MODEL downloaded"
fi

# Write the chosen model into agent.py
if [[ "$OS_TYPE" == "Darwin" ]]; then
    sed -i '' "s/^MODEL = .*/MODEL = \"$MODEL\"/" "$MAW_DIR/agent.py"
else
    sed -i "s/^MODEL = .*/MODEL = \"$MODEL\"/" "$MAW_DIR/agent.py"
fi
ok "Set agent to use model: $MODEL"

# ── 5. Install global command ─────────────────────────────────────────────────
hdr "5/5" "Installing the 'maw' command..."

mkdir -p "$BIN_DIR"

cat > "$BIN_DIR/maw" << WRAPPER
#!/usr/bin/env bash
exec "$VENV_DIR/bin/python" "$MAW_DIR/agent.py" "\$@"
WRAPPER

chmod +x "$BIN_DIR/maw"
ok "Created $BIN_DIR/maw"

# Add ~/.local/bin to PATH if it's not already there
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
    if [ -f "$rc" ]; then
        if grep -q ".local/bin" "$rc"; then
            ok "$rc already has ~/.local/bin in PATH"
        else
            printf '\n# Added by Maw installer\n%s\n' "$PATH_LINE" >> "$rc"
            ok "Added ~/.local/bin to PATH in $rc"
        fi
    fi
done

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "  ${BLUE}────────────────────────────────${NC}"
echo ""
echo -e "  ${BOLD}${GREEN}Installation complete!${NC}"
echo ""

# Quick smoke test
if "$VENV_DIR/bin/python" "$MAW_DIR/agent.py" --help &>/dev/null; then
    ok "Maw runs correctly"
else
    warn "Something may be off. Test manually: $VENV_DIR/bin/python $MAW_DIR/agent.py --help"
fi

echo ""
echo -e "  ${BOLD}Next steps:${NC}"

if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo "    source ~/.zshrc        # reload PATH (or open a new terminal)"
fi

echo "    maw                    # start Maw in any folder"
echo "    maw --help             # show help"
echo "    maw reset              # wipe memory for the current folder"
echo ""
echo -e "  ${BOLD}Tip:${NC} Run 'maw' from any project folder — it scopes itself there."
echo ""
