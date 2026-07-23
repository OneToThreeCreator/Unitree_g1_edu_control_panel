#!/bin/bash
# Build script for camera-capture on Jetson Orin NX
# Run this ON THE JETSON

set -e

echo "=== Camera Capture Build ==="

# 1. Install Rust toolchain
if ! command -v cargo &> /dev/null; then
    echo "Installing Rust..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
fi

# Source cargo env (use . instead of source for sh compatibility)
if [ -f "$HOME/.cargo/env" ]; then
    . "$HOME/.cargo/env"
fi

# Set default toolchain if not configured
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

# GStreamer dev libraries
sudo apt install -y \
    pkg-config \
    libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-plugins-good

# Jetson-specific GStreamer — comes from JetPack, check if available
if dpkg -l 2>/dev/null | grep -q nvidia-l4t-gstreamer; then
    echo "NVIDIA GStreamer plugins found (JetPack)"
else
    echo "WARNING: NVIDIA GStreamer plugins not found (nvidia-l4t-gstreamer)."
    echo "HW encoding may not work without JetPack GStreamer."
fi

# Check for HW encoder
if gst-inspect-1.0 nvv4l2h264enc &> /dev/null 2>&1; then
    echo "nvv4l2h264enc: OK"
elif gst-inspect-1.0 nvv4l2h265enc &> /dev/null 2>&1; then
    echo "nvv4l2h265enc: OK"
elif gst-inspect-1.0 omxh264enc &> /dev/null 2>&1; then
    echo "omxh264enc: OK (fallback)"
else
    echo "WARNING: No HW encoder found. Will try software fallback (libx264/libx265)."
fi

# RealSense
if pkg-config --exists librealsense2 2>/dev/null; then
    echo "librealsense2: OK"
else
    echo "librealsense2 not found. Trying apt..."
    if sudo apt install -y librealsense2-dev 2>/dev/null; then
        echo "librealsense2 installed from apt"
    else
        echo ""
        echo "ERROR: librealsense2-dev not in apt repos."
        echo "Install manually:"
        echo "  sudo apt install -y libusb-1.0-0 libusb-1.0-0-dev"
        echo "  cd /tmp && git clone https://github.com/IntelRealSense/librealsense.git"
        echo "  cd librealsense && mkdir build && cd build"
        echo "  cmake .. -DCMAKE_BUILD_TYPE=Release -DBUILD_EXAMPLES=false"
        echo "  make -j\$(nproc) && sudo make install"
        echo "  sudo ldconfig"
        echo ""
        echo "Then re-run this script."
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
