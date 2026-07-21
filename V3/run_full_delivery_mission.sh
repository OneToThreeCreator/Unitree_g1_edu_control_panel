#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

IFACE="eth0"
if [[ $# -gt 0 ]]; then
  IFACE="$1"
  shift
fi

if pgrep -f "kuzmich_companion.py" >/dev/null 2>&1; then
  pkill -f "kuzmich_companion.py" || true
  sleep 1
fi

if systemctl --no-pager is-active --quiet teleop-bridge.service 2>/dev/null; then
  echo "Stopping teleop-bridge.service to free RealSense camera..."
  printf '123\n' | sudo -S systemctl stop teleop-bridge.service >/tmp/teleop_bridge_stop.log 2>&1 || true
  sleep 1
fi
pkill -f "[c]amera_h265_sender" || true
sleep 0.5

pkill -f "^/home/unitree/g1_sdk_udp_receiver_fsm801_cpp " || true
pkill -f "[u]dp_cmd_vel_raw_bridge.py" || true
pkill -f "[g]1_cmd_vel_bridge.py" || true
pkill -f "[g]1_arm_keyframe_player" || true
sleep 0.5
setsid -f /home/unitree/g1_sdk_udp_receiver_fsm801_cpp \
  --iface "$IFACE" \
  --udp-port 15100 \
  --fsm -1 \
  --max-linear-x 0.18 \
  --max-linear-y 0.00 \
  --max-angular-z 0.18 \
  --send-rate-hz 20 \
  --cmd-timeout-s 0.90 \
  > /home/unitree/yolo_cup_project/V3/g1_udp_receiver.runtime.log 2>&1 < /dev/null
sleep 2

if ! pgrep -f "g1_arm_keyframe_player" >/dev/null 2>&1; then
  setsid -f /home/unitree/g1_arm_keyframe_player \
    --iface "$IFACE" \
    --udp-port 15001 \
    --config /home/unitree/arm_player.cfg \
    > /home/unitree/yolo_cup_project/V3/g1_arm_keyframe_player.runtime.log 2>&1 < /dev/null
  sleep 1
fi

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}"
unset V3_TTS_PULSE_SINK
export V3_TTS_FFMPEG_FILTER="highpass=f=150,equalizer=f=130:t=q:w=1.1:g=-20"

exec .venv_face/bin/python itog_v3.py "$IFACE" \
  --real \
  --motion-backend udp \
  --udp-port 15100 \
  --no-conversation \
  --keep-external-processes \
  --follow-oleg-after-grasp \
  --start-companion-after-delivery \
  --img-size 960 \
  --max-time 0 \
  --follow-oleg-max-time 0 \
  --coffee-request-text "ищу кофе" \
  --cup-initial-search-vx 0.18 \
  --follow-oleg-initial-search-vx 0.18 \
  --stop-distance 0.42 \
  --z-tolerance 0.02 \
  --target-x -0.08 \
  --approach-y-min -0.38 \
  --approach-y-max -0.04 \
  --post-grasp-backup-s 3.0 \
  --post-grasp-backup-vx -0.14 \
  --post-grasp-turn-right-deg 101 \
  --post-grasp-turn-vyaw 0.18 \
  --post-grasp-turn-from-start-zero \
  --follow-oleg-stop-distance 1.00 \
  --follow-oleg-left-turn-after-s 10 \
  --follow-oleg-left-turn-deg 15 \
  --follow-oleg-left-turn-vyaw 0.18 \
  --follow-oleg-text "я к вам уже иду" \
  --follow-oleg-reached-text "здравствуйте хозяин , вот ваше кофе" \
  --follow-oleg-take-coffee-text "заберите кофе, подставьте ваши руки под мои" \
  --follow-oleg-take-coffee-repeat 1 \
  --follow-oleg-take-coffee-repeat-delay 3 \
  --follow-oleg-release-delay 5 \
  --post-delivery-right-turn-deg 60 \
  --post-delivery-left-turn-deg 30 \
  --post-delivery-turn-vyaw 0.18 \
  --post-delivery-right-text "здравствуйте ксения леонидовна, да здравствует минсельхоз россии, слава роботам!" \
  --post-delivery-left-text "" \
  --post-delivery-right-speech-wait 8 \
  --post-delivery-left-speech-wait 0 \
  --companion-python /home/unitree/yolo_cup_project/V3/.venv_voice/bin/python \
  --companion-ready-text "Кузьмич выполнил свою задачу и готов поговорить" \
  --left-first-finger-close-angle 700 \
  --tts-volume 100 \
  --tts-amplification-db 15 \
  --tts-timeout 12 \
  "$@"
