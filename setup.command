#!/bin/bash
# ============================================================
# Highlight Comedy Cutter - Setup FFmpeg & Claude Code cho macOS
# ============================================================

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

echo ""
echo "========================================"
echo "  Highlight Comedy Cutter - Setup"
echo "========================================"
echo ""

# ── 1. Check Homebrew (can de cai FFmpeg & Node) ──
install_brew() {
    echo "--- Kiem tra Homebrew ---"
    if command -v brew &>/dev/null; then
        info "Homebrew da co"
    else
        warn "Dang cai Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        if [[ -f /opt/homebrew/bin/brew ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
            echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
        fi
        info "Homebrew da cai xong"
    fi
}

NEED_BREW=false

# ── 2. Check FFmpeg ──
echo "--- Kiem tra FFmpeg ---"
if command -v ffmpeg &>/dev/null; then
    info "FFmpeg da co: $(ffmpeg -version 2>&1 | head -1)"
else
    warn "Chua co FFmpeg"
    NEED_BREW=true
fi

# ── 3. Check Claude Code CLI ──
echo ""
echo "--- Kiem tra Claude Code CLI ---"
if command -v claude &>/dev/null; then
    CLAUDE_VER=$(claude --version 2>/dev/null | head -1)
    info "Claude Code CLI da co: $CLAUDE_VER"
else
    warn "Chua co Claude Code CLI"
    NEED_BREW=true
fi

# ── 4. Cai dat neu can ──
if [[ "$NEED_BREW" == true ]]; then
    echo ""
    install_brew

    if ! command -v ffmpeg &>/dev/null; then
        echo ""
        warn "Dang cai FFmpeg..."
        brew install ffmpeg
        info "FFmpeg da cai xong"
    fi

    if ! command -v claude &>/dev/null; then
        echo ""
        warn "Dang cai Claude Code CLI..."
        brew install claude-code
        info "Claude Code CLI da cai xong"
    fi
fi

# ── 5. Ket qua ──
echo ""
echo "========================================"
echo "  SETUP HOAN TAT!"
echo "========================================"
echo ""
if command -v claude &>/dev/null; then
    info "Claude Code CLI: $(claude --version 2>/dev/null | head -1)"
    warn "Neu chua dang nhap, hay chay 'claude' trong Terminal."
fi
if command -v ffmpeg &>/dev/null; then
    info "FFmpeg: $(ffmpeg -version 2>&1 | head -1)"
fi
echo ""
