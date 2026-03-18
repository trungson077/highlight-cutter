#!/bin/bash
# ============================================================
# Build Highlight Comedy Cutter thanh .app cho macOS
# Su dung conda env hien tai
# Output: thu muc "build_output" cung cap voi file nay
# ============================================================

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_OUTPUT="$SCRIPT_DIR/build_output"

echo ""
echo "========================================"
echo "  Build Highlight Comedy Cutter .app"
echo "========================================"
echo ""

# ── Kich hoat conda env ──
CONDA_BASE="$(conda info --base 2>/dev/null)"
if [[ -z "$CONDA_BASE" ]]; then
    error "Khong tim thay conda. Hay cai Miniconda/Anaconda truoc."
    exit 1
fi
source "$CONDA_BASE/etc/profile.d/conda.sh"

CONDA_ENV="tiktok_gen"
conda activate "$CONDA_ENV"
info "Conda env: $CONDA_ENV ($(python3 --version))"
info "Python: $(which python3)"

# ── Kiem tra cac package can thiet ──
echo ""
echo "--- Kiem tra dependencies ---"
for pkg in flet faster-whisper pyinstaller; do
    if pip show "$pkg" &>/dev/null; then
        info "$pkg da co"
    else
        warn "Dang cai $pkg..."
        pip install "$pkg"
        info "$pkg da cai xong"
    fi
done

# ── Build ──
echo ""
warn "Dang build .app (co the mat vai phut)..."

cd "$SCRIPT_DIR"
pyinstaller \
    --name "Highlight Comedy Cutter" \
    --windowed \
    --noconfirm \
    --clean \
    --distpath "$BUILD_OUTPUT" \
    --workpath "$SCRIPT_DIR/build_tmp" \
    --specpath "$SCRIPT_DIR/build_tmp" \
    --collect-all faster_whisper \
    --collect-all ctranslate2 \
    --collect-all flet \
    --hidden-import flet \
    --hidden-import faster_whisper \
    --hidden-import ctranslate2 \
    app.py

# Don dep thu muc tam
rm -rf "$SCRIPT_DIR/build_tmp"

echo ""
if [[ -d "$BUILD_OUTPUT/Highlight Comedy Cutter.app" ]]; then
    info "Build thanh cong!"
    echo ""
    echo "  Output: $BUILD_OUTPUT/"
    echo "  App:    $BUILD_OUTPUT/Highlight Comedy Cutter.app"
    echo ""
    echo "  LUU Y cho may dich:"
    echo "  - Van can cai FFmpeg:        brew install ffmpeg"
    echo "  - Van can cai Claude Code:   npm install -g @anthropic-ai/claude-code"
    echo "  - Lan dau chay can dang nhap Claude: chay 'claude' trong Terminal"
    echo "  - Whisper model tiny (~75MB) se tu tai lan dau su dung"
    echo ""
    open "$BUILD_OUTPUT"
else
    error "Build that bai. Xem log phia tren."
fi
