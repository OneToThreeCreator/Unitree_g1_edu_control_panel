#!/bin/bash
# Start Kuzmich Cockpit with LiveKit SFU
# Usage: bash start.sh [--dry-run]
#
# This script starts:
#   1. LiveKit SFU server (port 7880)
#   2. Python FastAPI backend (port 8080)
#
# For video publishing, use GStreamer + whipsender.py separately,
# or run lk with --publish-demo manually.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

DRY_RUN="${COCKPIT_DRY_RUN:-true}"
if [ "$1" = "--no-dry-run" ]; then
    DRY_RUN="false"
fi

echo "============================================"
echo "  Kuzmich Cockpit + LiveKit SFU"
echo "============================================"
echo ""

# ---- Kill previous instances ----
echo "[1/3] Stopping previous instances..."
pkill -f "livekit-server" 2>/dev/null || true
pkill -f "uvicorn backend.app:app" 2>/dev/null || true
sleep 1

# ---- Start LiveKit ----
echo "[2/3] Starting LiveKit server on :7880..."
./livekit-bin/livekit-server --config livekit.yaml --dev > /tmp/livekit.log 2>&1 &
LIVEKIT_PID=$!
sleep 1

if kill -0 $LIVEKIT_PID 2>/dev/null; then
    echo "      LiveKit running (PID=$LIVEKIT_PID)"
else
    echo "      ERROR: LiveKit failed to start. Check /tmp/livekit.log"
    exit 1
fi

# ---- Start Python backend ----
echo "[3/3] Starting Python backend on :8080 (DRY_RUN=$DRY_RUN)..."
COCKPIT_DRY_RUN=$DRY_RUN python3 -m uvicorn backend.app:app \
    --host 0.0.0.0 --port 8080 --log-level info > /tmp/cockpit.log 2>&1 &
BACKEND_PID=$!
sleep 2

if kill -0 $BACKEND_PID 2>/dev/null; then
    echo "      Backend running (PID=$BACKEND_PID)"
else
    echo "      ERROR: Backend failed to start. Check /tmp/cockpit.log"
    kill $LIVEKIT_PID 2>/dev/null
    exit 1
fi

echo ""
echo "============================================"
echo "  All services started!"
echo "============================================"
echo ""
echo "  Frontend:    http://localhost:8080"
echo "  Viewer:      http://localhost:8080/viewer.html"
echo "  LiveKit:     ws://localhost:7880"
echo ""
echo "  Logs:"
echo "    LiveKit:   /tmp/livekit.log"
echo "    Backend:   /tmp/cockpit.log"
echo ""
echo "  To stop: pkill -f livekit-server && pkill -f uvicorn"
echo ""
echo "  Press Ctrl+C to stop all..."
echo ""

# ---- Wait for Ctrl+C ----
cleanup() {
    echo ""
    echo "Stopping..."
    kill $BACKEND_PID 2>/dev/null
    kill $LIVEKIT_PID 2>/dev/null
    echo "Done."
    exit 0
}

trap cleanup SIGINT SIGTERM
wait
