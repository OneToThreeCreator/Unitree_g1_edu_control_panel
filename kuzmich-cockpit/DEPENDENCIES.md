# System Dependencies

## Ubuntu / WSL (dev machine)

```bash
# GStreamer core + plugins
sudo apt-get install -y \
  gstreamer1.0-tools \
  gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly \
  gstreamer1.0-libav \
  gstreamer1.0-x

# Python
sudo apt-get install -y python3 python3-pip python3-venv

# Python packages (inside venv)
pip install -r requirements.txt
```

### Key GStreamer plugins

| Plugin | Package | Provides |
|--------|---------|----------|
| `x264enc` | `gstreamer1.0-plugins-ugly` | H.264 software encoder |
| `x265enc` | `gstreamer1.0-plugins-bad` | H.265 software encoder |
| `jpegenc` | `gstreamer1.0-plugins-good` | JPEG encoder (MJPEG) |
| `videotestsrc` | `gstreamer1.0-plugins-good` | Test video source |
| `uvch264src` | `gstreamer1.0-plugins-bad` | UVC H.264 cameras |

## Jetson (production)

```bash
# JetPack includes GStreamer with NVIDIA plugins
# Additional packages if building from rootfs:
sudo apt-get install -y \
  libgstreamer1.0-dev \
  libgstreamer-plugins-bad1.0-dev \
  nvidia-l4t-gstreamer
```

### Jetson-specific encoders

| Encoder | Notes |
|---------|-------|
| `nvv4l2h265enc` | Hardware H.265 (V4L2 M2M) — default |
| `nvv4l2h264enc` | Hardware H.264 (V4L2 M2M) — fallback |
| `omxh265enc` | OpenMAX H.265 — older JetPack |
| `omxh264enc` | OpenMAX H.264 — older JetPack |

## LiveKit SFU

LiveKit is installed as a pre-built binary in `livekit-bin/`.

### Install

```bash
# Download livekit-server (linux-amd64 for dev, linux-arm64 for Jetson)
curl -sSL https://get.livekit.io | bash
# Binary lands at /usr/local/bin/livekit-server

# Install CLI tool (optional, for testing)
curl -sSL https://get.livekit.io/cli | bash
# Binary lands at /usr/local/bin/lk
```

### Start

```bash
bash scripts/start-livekit.sh
# Or manually:
livekit-server --dev
# Dev keys: API Key=devkey, API Secret=secret
# Listening on: ws://localhost:7880
```

### Config

`livekit.yaml` — LiveKit server configuration:
```yaml
port: 7880
rtc:
  port_range_start: 50000
  port_range_end: 60000
  tcp_port: 7881
  use_external_ip: false
keys:
  devkey: secret
logging:
  level: info
```

### Test with CLI

```bash
# Publish test video to a room
lk room join \
  --url ws://localhost:7880 \
  --api-key devkey --api-secret secret \
  --identity test-publisher \
  --publish-demo \
  test-room
```

## GStreamer 1.16 Note (Jetson JetPack 5)

**`whipsink` from gst-plugins-rs requires GStreamer >= 1.20 and is NOT available on Jetson.**

Instead, we use:
- `webrtcbin` (available since GStreamer 1.14) with Python WHIP signaling
- Or RTMP fallback via `flvmux` + `rtmp2sink` (H.264 only)

## Custom GStreamer Plugin

`gstwebsocketsink/` — custom WebSocket sink for streaming video to browsers.

Build:
```bash
cd gstwebsocketsink
mkdir build && cd build
cmake .. && make
# Install to gstwebsocketsink-bin/
```

Requires: `libgstreamer1.0-dev`, `libboost-system-dev`, `cmake`
