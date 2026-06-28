#!/usr/bin/env bash
set -euo pipefail

# Guard against environment leakage when the installer is launched from another
# Python-driven session. A pre-set PYTHONPATH can force pip/entrypoints to import
# a different checkout than the one being installed.
if [ -n "${PYTHONPATH:-}" ]; then
    echo "⚠ Ignoring inherited PYTHONPATH during install to avoid module shadowing"
    unset PYTHONPATH
fi
if [ -n "${PYTHONHOME:-}" ]; then
    echo "⚠ Ignoring inherited PYTHONHOME during install"
    unset PYTHONHOME
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

# Environment detection
is_termux() {
    [ -n "${TERMUX_VERSION:-}" ] || [[ "${PREFIX:-}" == *"com.termux/files/usr"* ]]
}

if is_termux; then
    TERMUX_MODE=true
else
    TERMUX_MODE=false
fi

if [ -t 0 ]; then
    IS_INTERACTIVE=true
else
    IS_INTERACTIVE=false
fi

log_info() { echo -e "${CYAN}→${NC} $1"; }
log_success() { echo -e "${GREEN}✓${NC} $1"; }
log_warn() { echo -e "${YELLOW}⚠${NC} $1"; }
log_error() { echo -e "${RED}✗${NC} $1"; }

prompt_read() {
    local prompt="$1"
    local default="${2:-}"
    local varname="$3"
    local answer=""

    if [ "$IS_INTERACTIVE" = true ]; then
        if [ -n "$default" ]; then
            printf "%s [%s]: " "$prompt" "$default" >&2
            IFS= read -r answer || answer=""
            answer="${answer:-$default}"
        else
            printf "%s: " "$prompt" >&2
            IFS= read -r answer || answer=""
        fi
    elif [ -r /dev/tty ] && [ -w /dev/tty ]; then
        if [ -n "$default" ]; then
            printf "%s [%s]: " "$prompt" "$default" > /dev/tty
            IFS= read -r answer < /dev/tty || answer=""
            answer="${answer:-$default}"
        else
            printf "%s: " "$prompt" > /dev/tty
            IFS= read -r answer < /dev/tty || answer=""
        fi
    else
        answer="$default"
    fi

    eval "$varname=\$answer"
}

echo -e "${CYAN}${BOLD}"
cat <<'EOF'
┌─────────────────────────────────────────────────────────┐
│              vtx-coding-agent installer                 │
├─────────────────────────────────────────────────────────┤
│  Minimalist coding agent harness                       │
└─────────────────────────────────────────────────────────┘
EOF
echo -e "${NC}"
echo ""

# Verify Python
if ! command -v python3 >/dev/null 2>&1; then
    if ! command -v python >/dev/null 2>&1; then
        log_error "Python 3.12+ is required but was not found on PATH."
        if [ "$TERMUX_MODE" = true ]; then
            log_info "Install via: pkg install python"
        fi
        exit 1
    fi
    PYTHON_CMD="python"
else
    PYTHON_CMD="python3"
fi

PYTHON_PATH="$(command -v "$PYTHON_CMD")"
PYTHON_VER="$("$PYTHON_PATH" --version 2>/dev/null || echo unknown)"
log_success "Python found: $PYTHON_VER"

if [ "$TERMUX_MODE" = true ]; then
    log_info "Termux detected"
fi

# Ensure pip is available
if ! "$PYTHON_PATH" -m pip --version >/dev/null 2>&1; then
    log_info "pip not found, bootstrapping..."
    if ! "$PYTHON_PATH" -m ensurepip --upgrade >/dev/null 2>&1; then
        if [ "$TERMUX_MODE" = true ]; then
            log_warn "ensurepip failed. Try: pkg install python-pip"
        else
            log_warn "ensurepip failed. Install pip manually."
        fi
    fi
fi

# Detect active virtual environment
ACTIVE_VENV=""
if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "${VIRTUAL_ENV}/bin/python" ]; then
    ACTIVE_VENV="$VIRTUAL_ENV"
fi

# Step 1: Choose installation source
echo -e "${CYAN}${BOLD}Choose installation source:${NC}"
echo "  1) Stable version from PyPI (recommended)"
echo "  2) Latest from GitHub (main branch)"
echo ""
prompt_read "Enter choice" "1" SOURCE_CHOICE

case "$SOURCE_CHOICE" in
    1) INSTALL_SOURCE="pypi" ;;
    2) INSTALL_SOURCE="github" ;;
    *) log_error "Invalid choice"; exit 1 ;;
esac

# Step 2: Choose installation target
INSTALL_TARGET=""
PIP_PYTHON="$PYTHON_PATH"
MANAGED_VENV="$HOME/.vtx/venv"

if [ -n "$ACTIVE_VENV" ]; then
    log_info "Active virtual environment detected: $ACTIVE_VENV"
    INSTALL_TARGET="active_venv"
    PIP_PYTHON="$ACTIVE_VENV/bin/python"
else
    echo ""
    echo -e "${CYAN}${BOLD}Choose installation target:${NC}"
    echo "  1) Managed venv at ~/.vtx/venv (isolated, recommended)"
    echo "  2) Global install (user/system Python)"
    echo ""
    prompt_read "Enter choice" "1" TARGET_CHOICE

    case "$TARGET_CHOICE" in
        1) INSTALL_TARGET="managed" ;;
        2) INSTALL_TARGET="global" ;;
        *) log_error "Invalid choice"; exit 1 ;;
    esac
fi

# Create managed venv if requested
if [ "$INSTALL_TARGET" = "managed" ]; then
    if [ -d "$MANAGED_VENV" ] && [ -x "$MANAGED_VENV/bin/python" ]; then
        log_info "Using existing managed venv: $MANAGED_VENV"
    else
        log_info "Creating managed virtual environment: $MANAGED_VENV"
        "$PYTHON_PATH" -m venv "$MANAGED_VENV"
        log_success "Virtual environment created"
    fi
    PIP_PYTHON="$MANAGED_VENV/bin/python"
fi

# Upgrade pip
log_info "Upgrading pip..."
"$PIP_PYTHON" -m pip install --upgrade pip >/dev/null

# Step 3: Install the package
if [ "$INSTALL_SOURCE" = "github" ]; then
    if ! command -v git >/dev/null 2>&1; then
        log_warn "Git is not installed. GitHub source requires git for 'pip install git+https://...'"
        log_info "Install git first, or switch to the PyPI option."
        if [ "$TERMUX_MODE" = true ]; then
            log_info "  pkg install git"
        fi
    fi
    log_info "Installing latest from GitHub (main branch)..."
    "$PIP_PYTHON" -m pip install --upgrade "git+https://github.com/OEvortex/vtx-coding-agent.git@main"
else
    log_info "Installing stable version from PyPI..."
    "$PIP_PYTHON" -m pip install --upgrade vtx-coding-agent
fi

# Step 4: Post-install linking
if [ "$INSTALL_TARGET" = "managed" ]; then
    if [ "$TERMUX_MODE" = true ]; then
        COMMAND_LINK_DIR="${PREFIX:-/data/data/com.termux/files/usr}/bin"
    else
        COMMAND_LINK_DIR="$HOME/.local/bin"
    fi

    mkdir -p "$COMMAND_LINK_DIR"
    ln -sf "$MANAGED_VENV/bin/vtx" "$COMMAND_LINK_DIR/vtx"

    case ":${PATH}:" in
        *":${COMMAND_LINK_DIR}:"*)
            log_success "Vtx installed successfully. Run 'vtx' to start." ;;
        *)
            echo ""
            log_warn "${COMMAND_LINK_DIR} is not on your PATH."
            echo -e "  Add it: export PATH=\"${COMMAND_LINK_DIR}:\$PATH\""
            if [ "$TERMUX_MODE" = false ]; then
                echo -e "  Or append to shell config: echo 'export PATH=\"${COMMAND_LINK_DIR}:\$PATH\"' >> ~/.bashrc"
            fi ;;
    esac
else
    echo ""
    if [ "$TERMUX_MODE" = true ]; then
        COMMAND_LINK_DIR="${PREFIX:-/data/data/com.termux/files/usr}/bin"
    else
        COMMAND_LINK_DIR="$HOME/.local/bin"
    fi

    if [ -f "$COMMAND_LINK_DIR/vtx" ]; then
        log_success "Vtx installed successfully. Run 'vtx' to start."
    else
        log_warn "Installation completed, but 'vtx' was not found at $COMMAND_LINK_DIR/vtx"
        log_info "Add ${COMMAND_LINK_DIR} to your PATH and try again."
    fi
fi

echo ""
echo -e "${GREEN}${BOLD}✨ Done!${NC}"
