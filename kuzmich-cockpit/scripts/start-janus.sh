#!/bin/bash
# Start Janus for Unitree G1 camera streaming
# Run this on WSL/Ubuntu
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
JANUS_CONF="$PROJECT_DIR/janus-conf"

# Janus 1.x paths after make install
JANUS_BIN="/usr/local/bin/janus"
JANUS_PLUGINS="/usr/local/lib/janus/plugins"
JANUS_TRANSPORTS="/usr/local/lib/janus/transports"

echo "Starting Janus 1.4.1..."
echo "HTTP API: http://127.0.0.1:8088/janus"
echo "WebSocket: ws://127.0.0.1:8188"
echo "Configs: $JANUS_CONF"
echo "Plugins: $JANUS_PLUGINS"

# Kill any running Janus
sudo killall janus 2>/dev/null || true

# Start Janus with correct paths
exec "$JANUS_BIN" -F "$JANUS_CONF" -P "$JANUS_PLUGINS" -o
