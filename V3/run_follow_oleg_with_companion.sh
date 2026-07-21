#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}"
unset V3_TTS_PULSE_SINK
unset V3_TTS_FFMPEG_FILTER

if ! pgrep -af "kuzmich_companion.py" >/dev/null 2>&1; then
  : > voice_robot/kuzmich_companion.runtime.log
  setsid -f .venv_voice/bin/python voice_robot/kuzmich_companion.py \
    --config voice_robot/kuzmich.ini \
    > voice_robot/kuzmich_companion.runtime.log 2>&1 < /dev/null
  sleep 4
fi

exec .venv_face/bin/python follow_oleg_only.py eth0 \
  --real \
  --motion-backend udp \
  --stop-distance 1.00 \
  --max-time 90 \
  --lost-timeout 20 \
  --profile-every 10 \
  "$@"
