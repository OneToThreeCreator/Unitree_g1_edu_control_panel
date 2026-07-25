#!/bin/bash
# Start Janus for Unitree G1 camera streaming
# Run this on WSL/Ubuntu
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
JANUS_CONF="$PROJECT_DIR/janus-conf"

# Find plugins folder
JANUS_PLUGINS=$(pkg-config --variable=pluginsdir gstreamer-1.0 2>/dev/null || echo "/usr/lib/x86_64-linux-gnu/janus/plugins")

echo "Starting Janus 1.4.1..."
echo "HTTP API: http://127.0.0.1:8088/janus"
echo "WebSocket: ws://127.0.0.1:8188"
echo "Configs: $JANUS_CONF"
echo "Plugins: $JANUS_PLUGINS"

# Kill any running Janus
sudo killall janus 2>/dev/null || true

# Start Janus with config directory (-F) and plugins folder (-P)
exec janus -F "$JANUS_CONF" -P "$JANUS_PLUGINS" -o
