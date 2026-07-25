#!/bin/bash
# Build Janus 1.4.1 locally for Unitree G1 camera streaming
# No sudo needed — everything stays in the project directory
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
JANUS_SRC="$PROJECT_DIR/janus"
JANUS_CONF_SRC="$PROJECT_DIR/janus-conf"
JANUS_LOCAL="$PROJECT_DIR/janus-local"

echo "=== Building Janus 1.4.1 (local) ==="

# Install build dependencies
sudo apt-get update
sudo apt-get install -y \
    libmicrohttpd-dev libjansson-dev libssl-dev \
    libglib2.0-dev libopus-dev libogg-dev libcurl4-openssl-dev \
    liblua5.3-dev libconfig-dev libnice-dev libwebsockets-dev \
    libsrtp2-dev \
    gengetopt pkg-config cmake automake autoconf libtool

# Clean old build
echo "Cleaning old build..."
cd "$JANUS_SRC"
make clean 2>/dev/null || true
make distclean 2>/dev/null || true

# Run autogen.sh
echo "Running autogen.sh..."
./autogen.sh

# Configure — install locally, no sudo
echo "Configuring Janus..."
./configure \
    --prefix="$JANUS_LOCAL" \
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

# Install locally (no sudo)
echo "Installing locally to $JANUS_LOCAL..."
make install

echo ""
echo "=== Janus 1.4.1 installed locally ==="
echo "Binary: $JANUS_LOCAL/bin/janus"
echo "Plugins: $JANUS_LOCAL/lib/janus/plugins/"
echo "Configs: $JANUS_CONF_SRC/"
echo ""
echo "To start: bash scripts/start-janus.sh"
