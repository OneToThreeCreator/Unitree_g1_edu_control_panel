#!/bin/bash
# Build script for camera-capture + websocketsink on Jetson Orin NX
# Run this ON THE JETSON

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COCKPIT_DIR="$(dirname "$SCRIPT_DIR")"

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
if pkg-config --exists librealsense2 2>/dev/null; then
    echo "librealsense2: OK"
else
    echo "librealsense2 not found. Install manually:"
    echo "  sudo apt install -y libusb-1.0-0 libusb-1.0-0-dev"
    echo "  cd /tmp && git clone https://github.com/IntelRealSense/librealsense.git"
    echo "  cd librealsense && mkdir build && cd build"
    echo "  cmake .. -DCMAKE_BUILD_TYPE=Release -DBUILD_EXAMPLES=false"
    echo "  make -j\$(nproc) && sudo make install && sudo ldconfig"
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
        # 1. Replace stdatomic.h with atomic
        sed -i 's/#include <stdatomic.h>/#include <atomic>/' gstwebsocketsink.cpp
        # 2. Reorder headers: standard C++ → GStreamer → websocketpp
        cat > /tmp/ws_headers_fix.py << 'PYEOF'
import re
with open("gstwebsocketsink.cpp", "r") as f:
    content = f.read()

# Find the INCLUDES section
match = re.search(r'(INCLUDES\n\*+/)\n(.*?)(\n/\*{10,})', content, re.DOTALL)
if match:
    header_block = match.group(2)
    # Split into lines
    lines = [l for l in header_block.strip().split('\n') if l.strip()]

    std_headers = []
    gst_headers = []
    ws_headers = []

    for l in lines:
        if 'websocketpp' in l:
            ws_headers.append(l)
        elif 'gst' in l.lower() or 'gstreamer' in l.lower():
            gst_headers.append(l)
        else:
            std_headers.append(l)

    new_block = '\n'.join(std_headers + gst_headers + ws_headers) + '\n'
    content = content[:match.start(2)] + new_block + content[match.end(2):]

with open("gstwebsocketsink.cpp", "w") as f:
    f.write(content)
print("Headers reordered")
PYEOF
        python3 /tmp/ws_headers_fix.py 2>/dev/null || echo "Manual header reorder may be needed"
    fi

    mkdir -p build && cd build
    cmake .. 2>&1
    make -j$(nproc) 2>&1
    mkdir -p "$WS_BIN"
    cp *.so "$WS_BIN/" 2>/dev/null || cp *.dylib "$WS_BIN/" 2>/dev/null || true
    echo "websocketsink built: $WS_BIN"
else
    echo "WARNING: gstwebsocketsink/ not found (git submodule not initialized?)"
    echo "  Run: cd $COCKPIT_DIR && git submodule update --init"
fi

# 4. Build camera-capture (Rust)
echo ""
echo "Building camera-capture..."
cd "$SCRIPT_DIR"
cargo build --release 2>&1

echo ""
echo "=== Build complete ==="
echo "Binary: $SCRIPT_DIR/target/release/camera-capture"
echo "websocketsink: $WS_BIN/"
echo ""
echo "Run with:"
echo "  GST_PLUGIN_PATH=$WS_BIN \\"
echo "  CAMERA_CONFIG=$COCKPIT_DIR/camera.toml \\"
echo "  $SCRIPT_DIR/target/release/camera-capture"
