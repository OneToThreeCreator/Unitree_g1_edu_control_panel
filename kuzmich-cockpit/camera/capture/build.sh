#!/bin/bash
# Build script for camera-capture on Jetson Orin NX
# Run this ON THE JETSON

set -e

echo "=== Camera Capture Build ==="

# 1. Install Rust toolchain
if ! command -v cargo &> /dev/null; then
    echo "Installing Rust..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    source "$HOME/.cargo/env"
else
    source "$HOME/.cargo/env" 2>/dev/null || true
fi

# Set default toolchain if not configured
if ! rustc --version &> /dev/null; then
    echo "Setting default Rust toolchain..."
    rustup default stable
fi

echo "Rust: $(rustc --version)"
echo "Cargo: $(cargo --version)"

# 2. System dependencies
echo ""
echo "Installing system dependencies..."
sudo apt update

# GStreamer
sudo apt install -y \
    pkg-config \
    libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-plugins-good

# Jetson-specific GStreamer (nvv4l2 etc.)
# These come from NVIDIA JetPack, not apt — check if available
if dpkg -l | grep -q nvidia-l4t-gstreamer; then
    echo "NVIDIA GStreamer plugins found (JetPack)"
else
    echo "WARNING: NVIDIA GStreamer plugins not found."
    echo "Install JetPack GStreamer: sudo apt install nvidia-l4t-gstreamer"
fi

# GStreamer nvcodec — part of JetPack, not a separate apt package
# Check if nvv4l2h264enc is available
if gst-inspect-1.0 nvv4l2h264enc &> /dev/null; then
    echo "nvv4l2h264enc: OK"
elif gst-inspect-1.0 omxh264enc &> /dev/null; then
    echo "omxh264enc: OK (fallback)"
else
    echo "WARNING: No HW encoder found. Will try software fallback."
fi

# RealSense
if pkg-config --exists librealsense2; then
    echo "librealsense2: OK"
else
    echo "Installing librealsense2..."
    # Try apt first
    if sudo apt install -y librealsense2-dev 2>/dev/null; then
        echo "librealsense2 installed from apt"
    else
        echo "librealsense2 not in apt repos."
        echo "Install manually:"
        echo "  mkdir -p ~/repos && cd ~/repos"
        echo "  git clone https://github.com/IntelRealSense/librealsense.git"
        echo "  cd librealsense && mkdir build && cd build"
        echo "  cmake .. -DCMAKE_BUILD_TYPE=Release -DBUILD_EXAMPLES=false"
        echo "  make -j\$(nproc) && sudo make install"
        exit 1
    fi
fi

# Build dependencies
sudo apt install -y cmake clang libclang-dev

# 3. Build
echo ""
echo "Building camera-capture..."
cd "$(dirname "$0")"
cargo build --release 2>&1

echo ""
echo "=== Build complete ==="
echo "Binary: $(pwd)/target/release/camera-capture"
echo ""
echo "Run with:"
echo "  CAMERA_CONFIG=camera.toml ./target/release/camera-capture"
