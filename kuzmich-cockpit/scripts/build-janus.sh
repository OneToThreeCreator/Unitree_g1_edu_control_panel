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

# Run autogen.sh in source directory
echo "Running autogen.sh..."
cd "$JANUS_SRC"
./autogen.sh

# Configure with minimal plugins
echo "Configuring Janus..."
./configure \
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

echo ""
echo "=== Janus 1.4.1 installed ==="
echo "Binary: /usr/local/bin/janus"
echo "Configs: $JANUS_CONF_SRC/"
echo ""
echo "To start: janus -F $JANUS_CONF_SRC -o"
