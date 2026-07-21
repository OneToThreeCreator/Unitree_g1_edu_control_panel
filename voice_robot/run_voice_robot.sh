#!/usr/bin/env bash
# =============================================================================
# Kuzmich companion — voice robot launcher
#
# Configuration lives in base.ini.  All runtime parameters (AI backend,
# voice, TTS, head) are read from there.  To reload config without restart:
#
#   kill -USR1 $(cat /tmp/kuzmich_companion.pid)
#
# To switch from local llama.cpp to an external API, edit base.ini:
#   [ai]  backend = openai
# then send SIGUSR1 — llama-server will be killed automatically.
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")"

# Audio EQ filter for TTS output (bypassed by paplay path, applied via ffmpeg)
unset V3_TTS_PULSE_SINK
export V3_TTS_FFMPEG_FILTER="highpass=f=150,equalizer=f=130:t=q:w=1.1:g=-20"

# Select Python interpreter
VOICE_PY="../.venv_voice/bin/python"
if [[ ! -x "$VOICE_PY" ]]; then
  VOICE_PY="../../V2/.venv_voice/bin/python"
fi

# Write PID file for easy SIGUSR1 reload
PIDFILE="/tmp/kuzmich_companion.pid"
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT

exec "$VOICE_PY" kuzmich_companion.py \
  --config base.ini \
  --log-file kuzmich_companion.log \
  "$@"
