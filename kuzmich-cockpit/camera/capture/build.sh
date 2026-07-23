#!/bin/bash
# Build script for camera-capture on Jetson Orin NX
# Run this ON THE JETSON, not on Windows

set -e

echo "=== Camera Capture Build ==="
echo "Target: aarch64-unknown-linux-gnu"
echo ""

# Check if Rust is installed
if ! command -v cargo &> /dev/null; then
    echo "Installing Rust toolchain..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    source "$HOME/.cargo/env"
fi

echo "Rust version: $(rustc --version)"
echo "Cargo version: $(cargo --version)"
echo ""

# Install system dependencies
echo "Installing system dependencies..."
sudo apt update
sudo apt install -y \
    pkg-config \
    libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-nvcodec \
    librealsense2-dev \
    cmake \
    clang \
    libclang-dev

echo ""
echo "Building camera-capture..."
cd "$(dirname "$0")"
cargo build --release

echo ""
echo "Build complete: target/release/camera-capture"
echo ""
echo "Run with:"
echo "  CAMERA_CONFIG=camera.toml ./target/release/camera-capture"
