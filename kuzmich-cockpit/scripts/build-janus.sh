#!/bin/bash
# Build Janus 1.4.1 for Unitree G1 camera streaming
# Run this on WSL/Ubuntu
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
JANUS_SRC="$PROJECT_DIR/janus"
JANUS_CONF_SRC="$PROJECT_DIR/janus-conf"

echo "=== Building Janus 1.4.1 ==="

# Install build dependencies
sudo apt-get update
sudo apt-get install -y \
    libmicrohttpd-dev libjansson-dev libssl-dev \
    libglib2.0-dev libopus-dev libogg-dev libcurl4-openssl-dev \
    liblua5.3-dev libconfig-dev libnice-dev libwebsockets-dev \
    libsrtp2-dev \
    gengetopt pkg-config cmake automake autoconf libtool

# Remove old build artifacts
echo "Cleaning old build..."
cd "$JANUS_SRC"
make clean 2>/dev/null || true
make distclean 2>/dev/null || true

# Run autogen.sh
echo "Running autogen.sh..."
./autogen.sh

# Configure with minimal plugins
echo "Configuring Janus..."
./configure \
    --prefix=/usr/local \
    --enable-websockets \
    --enable-rest \
    --disable-rabbitmq \
    --disable-nanomsg \
    --disable-mqtt \
    --disable-unix-sockets \
    --disable-data-channels \
    --disable-plugin-audiobridge \
    --disable-plugin-recordplay \
    --disable-plugin-sip \
    --disable-plugin-sipre \
    --disable-plugin-nosip \
    --disable-plugin-videocall \
    --disable-plugin-voicemail \
    --disable-plugin-textroom \
    --disable-plugin-lua \
    --disable-plugin-duktape

# Build
echo "Building..."
make -j$(nproc)

# Install
echo "Installing..."
sudo make install

# Verify installation
echo ""
echo "=== Verifying installation ==="
echo "Binary: $(which janus)"
echo "Plugins: $(ls /usr/local/lib/janus/plugins/*.so 2>/dev/null | wc -l) .so files"
echo "Transports: $(ls /usr/local/lib/janus/transports/*.so 2>/dev/null | wc -l) .so files"

echo ""
echo "=== Janus 1.4.1 installed ==="
echo "Start: janus -F $JANUS_CONF_SRC -P /usr/local/lib/janus/plugins"
