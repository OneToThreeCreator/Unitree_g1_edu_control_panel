#!/bin/bash
# Build script for camera-capture + websocketsink on Jetson Orin NX
# Run this ON THE JETSON from project root

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
COCKPIT_DIR="$PROJECT_ROOT/kuzmich-cockpit"
CAPTURE_DIR="$COCKPIT_DIR/camera/capture"

echo "=== Camera Capture Build ==="

# 1. Install Rust toolchain
if ! command -v cargo &> /dev/null; then
    echo "Installing Rust..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
fi

if [ -f "$HOME/.cargo/env" ]; then
    . "$HOME/.cargo/env"
fi

if ! rustc --version &> /dev/null; then
    echo "Setting default Rust toolchain..."
    rustup default stable
    . "$HOME/.cargo/env"
fi

echo "Rust: $(rustc --version)"
echo "Cargo: $(cargo --version)"

# 2. System dependencies
echo ""
echo "Installing system dependencies..."
sudo apt update

sudo apt install -y \
    pkg-config \
    libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-plugins-good \
    cmake \
    libboost-system-dev

# Jetson GStreamer
if dpkg -l 2>/dev/null | grep -q nvidia-l4t-gstreamer; then
    echo "NVIDIA GStreamer plugins found (JetPack)"
else
    echo "WARNING: NVIDIA GStreamer plugins not found."
fi

# Check HW encoder
if gst-inspect-1.0 nvv4l2h265enc &> /dev/null 2>&1; then
    echo "nvv4l2h265enc: OK"
elif gst-inspect-1.0 nvv4l2h264enc &> /dev/null 2>&1; then
    echo "nvv4l2h264enc: OK"
elif gst-inspect-1.0 omxh264enc &> /dev/null 2>&1; then
    echo "omxh264enc: OK (fallback)"
else
    echo "WARNING: No HW encoder found."
fi

# RealSense
if pkg-config --exists librealsense2 2>/dev/null || [ -f "/usr/include/librealsense2/rs.h" ] || [ -f "/usr/local/include/librealsense2/rs.h" ]; then
    echo "librealsense2: OK"
else
    echo "librealsense2 not found. Install from official Intel repo:"
    echo "  sudo apt-key adv --keyserver keyserver.ubuntu.com --recv-key F6E65AC044F831AC80A06380C8B3A55A6F3EFCDE"
    echo "  sudo add-apt-repository \"deb http://realsense-hw-public.s3-us-west-2.amazonaws.com/Ubuntu/apt-repo focal main\""
    echo "  sudo apt update && sudo apt install -y librealsense2-dev"
    exit 1
fi

# 3. Build websocketsink GStreamer plugin
echo ""
echo "Building websocketsink..."
WS_DIR="$COCKPIT_DIR/gstwebsocketsink"
WS_BIN="$COCKPIT_DIR/gstwebsocketsink-bin"

if [ -d "$WS_DIR" ]; then
    cd "$WS_DIR"

    # Apply patches (idempotent)
    if grep -q "stdatomic.h" gstwebsocketsink.cpp; then
        echo "Patching websocketsink for GStreamer 1.16 compatibility..."
        sed -i 's/#include <stdatomic.h>/#include <atomic>/' gstwebsocketsink.cpp
        python3 -c "
import re
with open('gstwebsocketsink.cpp', 'r') as f:
    content = f.read()
match = re.search(r'(INCLUDES\n\*+/)\n(.*?)(\n/\*{10,})', content, re.DOTALL)
if match:
    lines = [l for l in match.group(2).strip().split('\n') if l.strip()]
    std_h, gst_h, ws_h = [], [], []
    for l in lines:
        if 'websocketpp' in l: ws_h.append(l)
        elif 'gst' in l.lower(): gst_h.append(l)
        else: std_h.append(l)
    new_block = '\n'.join(std_h + gst_h + ws_h) + '\n'
    content = content[:match.start(2)] + new_block + content[match.end(2):]
    with open('gstwebsocketsink.cpp', 'w') as f:
        f.write(content)
" 2>/dev/null || echo "Manual header reorder may be needed"
    fi

    mkdir -p build && cd build
    cmake .. 2>&1
    make -j$(nproc) 2>&1
    mkdir -p "$WS_BIN"
    cp *.so "$WS_BIN/" 2>/dev/null || true
    echo "websocketsink built: $WS_BIN"
else
    echo "WARNING: gstwebsocketsink/ not found (git submodule not initialized?)"
    echo "  Run: cd $COCKPIT_DIR && git submodule update --init"
fi

# 4. Build camera-capture (Rust)
echo ""
echo "Building camera-capture..."
cd "$CAPTURE_DIR"
cargo build --release 2>&1

echo ""
echo "=== Build complete ==="
echo "Binary: $CAPTURE_DIR/target/release/camera-capture"
echo "websocketsink: $WS_BIN/"
echo ""
echo "Run from kuzmich-cockpit/:"
echo "  GST_PLUGIN_PATH=gstwebsocketsink-bin \\"
echo "  CAMERA_CONFIG=camera.toml \\"
echo "  camera/capture/target/release/camera-capture"
