#!/bin/bash
# Dev build — websocketsink only
# Usage: ./build-dev.sh [clean]

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
COCKPIT_DIR="$PROJECT_ROOT/kuzmich-cockpit"
WS_DIR="$COCKPIT_DIR/gstwebsocketsink"
WS_BIN="$COCKPIT_DIR/gstwebsocketsink-bin"

if [ ! -d "$WS_DIR" ]; then
    echo "ERROR: gstwebsocketsink/ not found."
    echo "  Run: cd $COCKPIT_DIR && git submodule update --init"
    exit 1
fi

cd "$WS_DIR"

# Clean build if requested
if [ "$1" = "clean" ]; then
    echo "Cleaning build/..."
    rm -rf build/
fi

# Apply patches (idempotent)
if grep -q "stdatomic.h" gstwebsocketsink.cpp 2>/dev/null; then
    echo "Patching websocketsink for GStreamer 1.16 compatibility..."
    sed -i 's/#include <stdatomic.h>/#include <atomic>/' gstwebsocketsink.cpp
    python3 -c "
import re
with open('gstwebsocketsink.cpp', 'r') as f:
    content = f.read()
match = re.search(r'(INCLUDES\n\*+/)\n(.*?)(\n/\*{10,})', content, re.DOTALL)
if match:
    lines = [l for l in match.group(2).strip().split('\n') if l.strip()]
    std_h, gst_h, ws_h = [], [], []
    for l in lines:
        if 'websocketpp' in l: ws_h.append(l)
        elif 'gst' in l.lower(): gst_h.append(l)
        else: std_h.append(l)
    new_block = '\n'.join(std_h + gst_h + ws_h) + '\n'
    content = content[:match.start(2)] + new_block + content[match.end(2):]
    with open('gstwebsocketsink.cpp', 'w') as f:
        f.write(content)
" 2>/dev/null || echo "Manual header reorder may be needed"
fi

# Build
mkdir -p build && cd build
cmake .. 2>&1
make -j$(nproc) 2>&1

# Copy output
mkdir -p "$WS_BIN"
cp *.so "$WS_BIN/" 2>/dev/null || true

echo ""
echo "=== websocketsink built ==="
echo "Output: $WS_BIN/"
echo ""
echo "Run with:"
echo "  GST_PLUGIN_PATH=$WS_BIN gst-inspect-1.0 websocketsink"
