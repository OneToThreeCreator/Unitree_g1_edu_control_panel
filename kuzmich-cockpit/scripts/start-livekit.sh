#!/bin/bash
# Start LiveKit server for Unitree G1 camera streaming
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LIVEKIT_BIN="$PROJECT_DIR/livekit-bin/livekit-server"
LIVEKIT_CONF="$PROJECT_DIR/livekit.yaml"

echo "Starting LiveKit server..."
echo "WebSocket: ws://127.0.0.1:7880"
echo "Config: $LIVEKIT_CONF"
echo ""
echo "Dev keys: API Key=devkey, API Secret=secret"

# Kill any running livekit-server
killall livekit-server 2>/dev/null || true

exec "$LIVEKIT_BIN" --config "$LIVEKIT_CONF" --dev 2>&1
