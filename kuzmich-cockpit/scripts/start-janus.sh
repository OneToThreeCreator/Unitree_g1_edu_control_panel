#!/bin/bash
# Start Janus for Unitree G1 camera streaming
# Run this on WSL/Ubuntu
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
JANUS_CONF="$PROJECT_DIR/janus-conf"

echo "Starting Janus 1.4.1..."
echo "HTTP API: http://127.0.0.1:8088/janus"
echo "WebSocket: ws://127.0.0.1:8188"
echo "Configs: $JANUS_CONF"

# Kill any running Janus
sudo killall janus 2>/dev/null || true

# Start Janus with config directory (-F flag)
exec janus -F "$JANUS_CONF" -o
