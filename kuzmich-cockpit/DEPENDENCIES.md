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

# Janus build dependencies
sudo apt-get install -y \
  libmicrohttpd-dev libjansson-dev libssl-dev \
  libglib2.0-dev libopus-dev libogg-dev libcurl4-openssl-dev \
  liblua5.3-dev libconfig-dev libnice-dev libwebsockets-dev \
  gengetopt pkg-config cmake automake autoconf libtool

# Build Janus
bash scripts/build-janus.sh
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

# Janus SFU
sudo apt-get install -y janus
```

### Jetson-specific encoders

| Encoder | Notes |
|---------|-------|
| `nvv4l2h265enc` | Hardware H.265 (V4L2 M2M) — default |
| `nvv4l2h264enc` | Hardware H.264 (V4L2 M2M) — fallback |
| `omxh265enc` | OpenMAX H.265 — older JetPack |
| `omxh264enc` | OpenMAX H.264 — older JetPack |

## Janus Gateway

Janus is built from source as a git submodule in `janus/`.

### Build
```bash
bash scripts/build-janus.sh
```

### Start
```bash
bash scripts/start-janus.sh
# Or manually:
janus -o
```

### Config files
- `janus-conf/janus.jcfg` — Main config
- `janus-conf/janus.plugin.streaming.jcfg` — Streaming plugin (H.265/H.264 mountpoints)
- `janus-conf/janus.transport.http.jcfg` — HTTP API
- `janus-conf/janus.transport.websockets.jcfg` — WebSocket transport

```bash
# Ubuntu/Debian
sudo apt-get install -y janus

# Or build from source (for latest version)
# https://github.com/meetecho/janus-gateway
```

### Janus config

Required in `/etc/janus/janus.jcfg`:
- Enable `videoroom` plugin
- Set STUN server
- Create room matching `janus_room_id` (default: 1234)

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
