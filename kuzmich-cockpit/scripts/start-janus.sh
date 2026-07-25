#!/bin/bash
# Start Janus for Unitree G1 camera streaming
# Run this on WSL/Ubuntu
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Starting Janus 1.4.1..."
echo "HTTP API: http://127.0.0.1:8088/janus"
echo "WebSocket: ws://127.0.0.1:8188"
echo "Configs: $PROJECT_DIR/janus-conf/"

# Copy configs if not in /etc/janus
if [ ! -f /etc/janus/janus.jcfg ]; then
    sudo mkdir -p /etc/janus
    sudo cp "$PROJECT_DIR/janus-conf/"*.jcfg /etc/janus/
    echo "Configs installed to /etc/janus/"
fi

# Kill any running Janus
sudo killall janus 2>/dev/null || true

# Start Janus
exec janus -o
