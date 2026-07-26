#!/bin/bash
# Start GStreamer + lk publisher for LiveKit
# GStreamer encodes H.264 to a Unix socket, lk reads and publishes to LiveKit
#
# Usage: bash start-gst-lk.sh [--dry-run]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

DRY_RUN="${COCKPIT_DRY_RUN:-true}"
SOCKET="/tmp/gst-h264.sock"
LIVEKIT_URL="ws://localhost:7880"
LIVEKIT_KEY="devkey"
LIVEKIT_SECRET="secret"
ROOM="g1-camera"

# Clean up
rm -f "$SOCKET"
pkill -f "gst-launch.*videotestsrc" 2>/dev/null || true
pkill -f "lk room join" 2>/dev/null || true
sleep 1

echo "Starting GStreamer + lk publisher..."

if [ "$DRY_RUN" = "true" ]; then
    # DRY_RUN: videotestsrc
    w=640; h=480; fps=30

    # GStreamer: videotestsrc → x264enc → h264parse → video/x-h264,stream-format=byte-stream → multifilesink (socket)
    # Actually, use tcpserversink or udpsink, or just write to stdout and pipe to lk

    # Simpler: use lk's h264:// socket with GStreamer outputting to a socket
    # GStreamer → fdsink (stdout) | lk reads from stdin

    echo "  DRY_RUN: videotestsrc ${w}x${h}@${fps}fps"

    # Start GStreamer writing H.264 Annex-B to a pipe
    PIPE="/tmp/gst-h264-pipe"
    rm -f "$PIPE"
    mkfifo "$PIPE"

    gst-launch-1.0 -e \
        videotestsrc is-live=true ! \
        videoconvert ! video/x-raw,format=I420,width=$w,height=$h,framerate=$fps/1 ! \
        x264enc tune=zerolatency speed-preset=ultrafast key-int-max=30 ! \
        h264parse ! \
        filesink location="$PIPE" &
    GST_PID=$!
    sleep 1

    echo "  GStreamer PID: $GST_PID"
    echo "  Publishing to LiveKit room '$ROOM'..."

    # lk reads H.264 from the pipe
    ./livekit-bin/lk room join \
        --url "$LIVEKIT_URL" \
        --api-key "$LIVEKIT_KEY" --api-secret "$LIVEKIT_SECRET" \
        --identity "gst-publisher" \
        --publish "h264:///$PIPE" \
        --fps "$fps" \
        "$ROOM" &
    LK_PID=$!
    sleep 2

    if kill -0 $LK_PID 2>/dev/null; then
        echo "  lk publisher running (PID=$LK_PID)"
    else
        echo "  ERROR: lk failed to start"
        kill $GST_PID 2>/dev/null
        exit 1
    fi
else
    echo "  TODO: Real camera mode"
    exit 1
fi

echo ""
echo "============================================"
echo "  Streaming to LiveKit!"
echo "============================================"
echo ""
echo "  Open http://192.168.211.244:8080/viewer.html"
echo ""
echo "  Press Ctrl+C to stop..."

cleanup() {
    echo ""
    echo "Stopping..."
    kill $LK_PID 2>/dev/null
    kill $GST_PID 2>/dev/null
    rm -f "$PIPE"
    echo "Done."
    exit 0
}

trap cleanup SIGINT SIGTERM
wait
