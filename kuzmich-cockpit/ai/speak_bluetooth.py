#!/usr/bin/env python3
"""Speak text through the robot's Bluetooth PulseAudio sink.

This intentionally avoids robot-admin native playback by requesting TTS with
play=false, then plays the generated WAV locally with paplay.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_TTS_URL = "http://127.0.0.1/api/audio/tts"
DEFAULT_LIBRARY_DIR = Path("/home/unitree/teleop/app/web_admin/data/audio_player/library")
DEFAULT_SINK = "bluez_sink.CC_C5_0A_78_A9_56.a2dp_sink"
DEFAULT_EQ = "highpass=f=150,equalizer=f=130:t=q:w=1.1:g=-6,equalizer=f=250:t=q:w=1.0:g=-3"
DEFAULT_NATIVE_PLAYER = "/home/unitree/g1_audio_play"
DEFAULT_PHRASES_FILE = Path(__file__).resolve().parent / "voice_phrases.json"
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / "voice_cache"


def _request_tts_track(
    *,
    text: str,
    tts_url: str,
    voice: str,
    volume: int,
    amplification_db: float,
    timeout_s: float,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "text": text,
        "play": False,
        "hardware_volume": int(volume),
        "amplification_db": float(amplification_db),
    }
    if voice:
        payload["voice"] = voice
    request = urllib.request.Request(
        tts_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        body = response.read().decode("utf-8", "replace")
    data = json.loads(body)
    if not data.get("ok", False):
        raise RuntimeError(f"TTS endpoint returned not-ok response: {data!r}")
    return data


def _track_wav_path(track: dict[str, Any], library_dir: Path) -> Path:
    for key in ("path", "wav_path", "file"):
        value = track.get(key)
        if value:
            path = Path(str(value))
            if path.exists():
                return path
    track_id = track.get("id") or track.get("track_id")
    if track_id:
        path = library_dir / f"{track_id}.wav"
        if path.exists():
            return path
    raise FileNotFoundError(f"Cannot resolve WAV path from track metadata: {track!r}")


def _filtered_wav(original: Path, ffmpeg_filter: str, output_path: Path | None = None) -> Path:
    if not ffmpeg_filter:
        return original
    out = output_path or Path(f"/tmp/speak_bluetooth_eq_{os.getpid()}.wav")
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(original),
            "-af",
            ffmpeg_filter,
            str(out),
        ],
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg EQ failed with rc={result.returncode}")
    return out


def _play_wav(
    wav_path: Path,
    *,
    output: str,
    sink: str,
    volume: int,
    iface: str,
    native_player: str,
) -> None:
    if output == "native":
        result = subprocess.run(
            [
                native_player,
                "--iface",
                iface,
                "--file",
                str(wav_path),
                "--volume",
                str(int(volume)),
            ],
            check=False,
        )
    elif output == "bluetooth":
        env = os.environ.copy()
        env.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")
        subprocess.run(["pactl", "set-default-sink", sink], env=env, check=False)
        result = subprocess.run(["paplay", str(wav_path)], env=env, check=False)
    else:
        raise ValueError(f"Unsupported output mode: {output}")
    if result.returncode != 0:
        raise RuntimeError(f"{output} playback failed with rc={result.returncode}")


def _append_volume_filter(ffmpeg_filter: str, gain_db: float) -> str:
    if abs(gain_db) < 1e-6:
        return ffmpeg_filter
    volume_filter = f"volume={float(gain_db)}dB"
    if not ffmpeg_filter:
        return volume_filter
    return f"{ffmpeg_filter},{volume_filter}"


def load_phrases(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("phrases"), dict):
        data = data["phrases"]
    if not isinstance(data, dict):
        raise ValueError(f"Phrase file must contain an object: {path}")
    phrases: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, str) and value.strip():
            phrases[str(key)] = value.strip()
    return phrases


def phrase_cache_path(cache_dir: Path, phrase_id: str) -> Path:
    safe_id = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in phrase_id)
    return cache_dir / f"{safe_id}.wav"


def prepare_phrase_cache(
    *,
    phrases_file: Path,
    cache_dir: Path,
    tts_url: str,
    voice: str,
    volume: int,
    amplification_db: float,
    ffmpeg_filter: str,
    timeout_s: float,
    library_dir: Path,
) -> dict[str, Path]:
    phrases = load_phrases(phrases_file)
    cache_dir.mkdir(parents=True, exist_ok=True)
    prepared: dict[str, Path] = {}
    combined_filter = _append_volume_filter(ffmpeg_filter, amplification_db)
    for phrase_id, text in phrases.items():
        data = _request_tts_track(
            text=text,
            tts_url=tts_url,
            voice=voice,
            volume=volume,
            amplification_db=0.0,
            timeout_s=timeout_s,
        )
        wav_path = _track_wav_path(data.get("track") or {}, library_dir)
        out_path = phrase_cache_path(cache_dir, phrase_id)
        if combined_filter:
            _filtered_wav(wav_path, combined_filter, output_path=out_path)
        else:
            out_path.write_bytes(wav_path.read_bytes())
        prepared[phrase_id] = out_path
    return prepared


def speak_bluetooth(
    text: str,
    *,
    output: str = "bluetooth",
    tts_url: str = DEFAULT_TTS_URL,
    sink: str = DEFAULT_SINK,
    voice: str = "",
    volume: int = 100,
    amplification_db: float = 0.0,
    ffmpeg_filter: str = DEFAULT_EQ,
    timeout_s: float = 15.0,
    library_dir: Path = DEFAULT_LIBRARY_DIR,
    iface: str = "eth0",
    native_player: str = DEFAULT_NATIVE_PLAYER,
) -> Path:
    normalized_text = text.replace("\\t", "\t")
    if "\t" in normalized_text:
        parts = [part.strip() for part in normalized_text.split("\t")]
        parts = [part for part in parts if part]
        if not parts:
            raise ValueError("empty text")
        last_path = Path("")
        for index, part in enumerate(parts):
            last_path = speak_bluetooth(
                part,
                output=output,
                tts_url=tts_url,
                sink=sink,
                voice=voice,
                volume=volume,
                amplification_db=amplification_db,
                ffmpeg_filter=ffmpeg_filter,
                timeout_s=timeout_s,
                library_dir=library_dir,
                iface=iface,
                native_player=native_player,
            )
            if index < len(parts) - 1:
                time.sleep(1.0)
        return last_path

    data = _request_tts_track(
        text=text,
        tts_url=tts_url,
        voice=voice,
        volume=volume,
        amplification_db=0.0,
        timeout_s=timeout_s,
    )
    wav_path = _track_wav_path(data.get("track") or {}, library_dir)
    playback_path = _filtered_wav(wav_path, _append_volume_filter(ffmpeg_filter, amplification_db))
    try:
        _play_wav(
            playback_path,
            output=output,
            sink=sink,
            volume=volume,
            iface=iface,
            native_player=native_player,
        )
        return wav_path
    finally:
        if playback_path != wav_path:
            try:
                playback_path.unlink()
            except OSError:
                pass


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Speak text through Bluetooth speaker via PulseAudio.")
    parser.add_argument("text", nargs="*", help="Text to speak. If omitted, stdin is used.")
    parser.add_argument("--output", choices=("bluetooth", "native"), default="bluetooth")
    parser.add_argument("--tts-url", default=DEFAULT_TTS_URL)
    parser.add_argument("--sink", default=os.environ.get("V3_TTS_PULSE_SINK", DEFAULT_SINK))
    parser.add_argument("--voice", default="")
    parser.add_argument("--volume", type=int, default=100)
    parser.add_argument("--amplification-db", type=float, default=0.0)
    parser.add_argument("--eq", default=os.environ.get("V3_TTS_FFMPEG_FILTER", DEFAULT_EQ))
    parser.add_argument("--no-eq", action="store_true")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    parser.add_argument("--iface", default="eth0")
    parser.add_argument("--native-player", default=DEFAULT_NATIVE_PLAYER)
    parser.add_argument("--phrases-file", type=Path, default=DEFAULT_PHRASES_FILE)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--phrase-id", default="", help="Speak a prepared phrase from cache.")
    parser.add_argument("--prepare-phrases", action="store_true", help="Generate local WAV cache for all phrases.")
    parser.add_argument("--list-phrases", action="store_true")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.list_phrases:
        try:
            for phrase_id, phrase_text in load_phrases(args.phrases_file).items():
                print(f"{phrase_id}: {phrase_text}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.prepare_phrases:
        try:
            prepared = prepare_phrase_cache(
                phrases_file=args.phrases_file,
                cache_dir=args.cache_dir,
                tts_url=args.tts_url,
                voice=args.voice,
                volume=args.volume,
                amplification_db=args.amplification_db,
                ffmpeg_filter="" if args.no_eq else args.eq,
                timeout_s=args.timeout,
                library_dir=args.library_dir,
            )
        except (OSError, RuntimeError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        for phrase_id, wav_path in prepared.items():
            print(f"OK: {phrase_id} -> {wav_path}")
        return 0
    if args.phrase_id:
        wav_path = phrase_cache_path(args.cache_dir, args.phrase_id)
        if not wav_path.exists():
            print(f"ERROR: phrase cache not found: {wav_path}", file=sys.stderr)
            print("Run with --prepare-phrases first.", file=sys.stderr)
            return 1
        try:
            _play_wav(
                wav_path,
                output=args.output,
                sink=args.sink,
                volume=args.volume,
                iface=args.iface,
                native_player=args.native_player,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"OK: {wav_path}")
        return 0

    text = " ".join(args.text).strip()
    if not text:
        text = sys.stdin.read().strip()
    if not text:
        print("No text provided", file=sys.stderr)
        return 2
    try:
        wav_path = speak_bluetooth(
            text,
            output=args.output,
            tts_url=args.tts_url,
            sink=args.sink,
            voice=args.voice,
            volume=args.volume,
            amplification_db=args.amplification_db,
            ffmpeg_filter="" if args.no_eq else args.eq,
            timeout_s=args.timeout,
            library_dir=args.library_dir,
            iface=args.iface,
            native_player=args.native_player,
        )
    except (OSError, RuntimeError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {wav_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
