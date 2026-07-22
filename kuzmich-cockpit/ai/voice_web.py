#!/usr/bin/env python3
"""Small standalone web UI for V3 speech playback."""
from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from speak_bluetooth import (
    DEFAULT_CACHE_DIR,
    DEFAULT_EQ,
    DEFAULT_NATIVE_PLAYER,
    DEFAULT_PHRASES_FILE,
    DEFAULT_SINK,
    DEFAULT_TTS_URL,
    _play_wav,
    load_phrases,
    phrase_cache_path,
    speak_bluetooth,
)


HTML = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kuzmich Voice</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, -apple-system, Segoe UI, sans-serif; }
    body { margin: 0; background: #101214; color: #f3f5f7; }
    main { max-width: 860px; margin: 0 auto; padding: 22px; }
    h1 { margin: 0 0 18px; font-size: 24px; font-weight: 650; }
    section { margin: 16px 0; padding: 16px; background: #191d21; border: 1px solid #2a3036; border-radius: 8px; }
    label { display: block; margin: 0 0 8px; color: #b8c0c8; font-size: 14px; }
    textarea, select, input { width: 100%; box-sizing: border-box; background: #0f1215; color: #f3f5f7; border: 1px solid #343c45; border-radius: 6px; padding: 10px; font: inherit; }
    textarea { min-height: 110px; resize: vertical; }
    .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
    .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    button { border: 0; border-radius: 6px; padding: 10px 14px; background: #2f7dd3; color: white; font: inherit; cursor: pointer; }
    button.secondary { background: #343c45; }
    button:disabled { opacity: .55; cursor: default; }
    .check { display: flex; align-items: center; gap: 8px; color: #d6dce2; }
    .check input { width: auto; }
    #status { min-height: 22px; color: #9fd28f; }
    #status.err { color: #ff8f8f; }
    @media (max-width: 720px) { .grid { grid-template-columns: 1fr 1fr; } }
  </style>
</head>
<body>
<main>
  <h1>Kuzmich Voice</h1>

  <section>
    <label for="text">Текст</label>
    <textarea id="text">Кузьмич готов говорить.</textarea>
    <div class="row" style="margin-top:12px">
      <button id="speakText">Сказать текст</button>
      <button id="refresh" class="secondary">Обновить фразы</button>
    </div>
  </section>

  <section>
    <div class="grid">
      <div>
        <label for="output">Вывод</label>
        <select id="output">
          <option value="native" selected>Родной динамик</option>
          <option value="bluetooth">Bluetooth</option>
        </select>
      </div>
      <div>
        <label for="gain">Усиление dB</label>
        <input id="gain" type="number" min="-20" max="20" step="1" value="15">
      </div>
      <div>
        <label for="volume">Громкость</label>
        <input id="volume" type="number" min="0" max="100" step="1" value="100">
      </div>
      <div>
        <label>&nbsp;</label>
        <label class="check"><input id="eq" type="checkbox" checked> bass-cut</label>
      </div>
    </div>
    <div class="grid" style="margin-top:12px">
      <div>
        <label for="highpass">High-pass Hz</label>
        <input id="highpass" type="number" min="40" max="400" step="10" value="150">
      </div>
      <div>
        <label for="bassGain">Бас dB</label>
        <input id="bassGain" type="number" min="-18" max="12" step="1" value="-6">
      </div>
      <div>
        <label for="lowMidGain">Низ-середина dB</label>
        <input id="lowMidGain" type="number" min="-18" max="12" step="1" value="-3">
      </div>
      <div>
        <label>&nbsp;</label>
        <button id="resetEq" class="secondary" type="button">Сброс EQ</button>
      </div>
    </div>
    <div class="grid" style="margin-top:12px">
      <div>
        <label for="bassFreq">Бас Hz</label>
        <input id="bassFreq" type="number" min="60" max="220" step="10" value="130">
      </div>
      <div>
        <label for="bassQ">Бас Q</label>
        <input id="bassQ" type="number" min="0.4" max="3" step="0.1" value="1.1">
      </div>
      <div>
        <label for="lowMidFreq">Низ-середина Hz</label>
        <input id="lowMidFreq" type="number" min="180" max="500" step="10" value="250">
      </div>
      <div>
        <label for="lowMidQ">Низ-середина Q</label>
        <input id="lowMidQ" type="number" min="0.4" max="3" step="0.1" value="1.0">
      </div>
    </div>
  </section>

  <section>
    <label for="phrase">Готовая фраза</label>
    <select id="phrase"></select>
    <div class="row" style="margin-top:12px">
      <button id="speakPhrase">Сказать фразу</button>
      <button id="prepare" class="secondary">Пересобрать кэш</button>
    </div>
  </section>

  <section>
    <div id="status"></div>
  </section>
</main>
<script>
const $ = (id) => document.getElementById(id);

function payload(extra = {}) {
  return {
    output: $("output").value,
    amplification_db: Number($("gain").value || 15),
    volume: Number($("volume").value || 100),
    eq: $("eq").checked,
    highpass_hz: Number($("highpass").value || 150),
    bass_freq: Number($("bassFreq").value || 130),
    bass_q: Number($("bassQ").value || 1.1),
    bass_gain_db: Number($("bassGain").value || -6),
    lowmid_freq: Number($("lowMidFreq").value || 250),
    lowmid_q: Number($("lowMidQ").value || 1.0),
    lowmid_gain_db: Number($("lowMidGain").value || -3),
    ...extra,
  };
}

function status(text, err=false) {
  const el = $("status");
  el.textContent = text;
  el.classList.toggle("err", err);
}

async function api(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body || {}),
  });
  const data = await res.json();
  if (!res.ok || !data.ok) throw new Error(data.error || res.statusText);
  return data;
}

async function loadPhrases() {
  const res = await fetch("/api/phrases");
  const data = await res.json();
  const select = $("phrase");
  select.innerHTML = "";
  for (const [id, text] of Object.entries(data.phrases || {})) {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = `${id}: ${text}`;
    select.appendChild(opt);
  }
  status(`Фраз: ${select.options.length}`);
}

$("speakText").onclick = async () => {
  try {
    $("speakText").disabled = true;
    status("Говорю...");
    await api("/api/speak", payload({text: $("text").value.trim()}));
    status("Готово");
  } catch (e) {
    status(String(e), true);
  } finally {
    $("speakText").disabled = false;
  }
};

$("speakPhrase").onclick = async () => {
  try {
    $("speakPhrase").disabled = true;
    status("Говорю...");
    await api("/api/speak", payload({phrase_id: $("phrase").value}));
    status("Готово");
  } catch (e) {
    status(String(e), true);
  } finally {
    $("speakPhrase").disabled = false;
  }
};

$("prepare").onclick = async () => {
  try {
    $("prepare").disabled = true;
    status("Генерирую кэш...");
    const data = await api("/api/prepare", payload({}));
    status(`Готово: ${data.count}`);
  } catch (e) {
    status(String(e), true);
  } finally {
    $("prepare").disabled = false;
  }
};

$("refresh").onclick = loadPhrases;
$("resetEq").onclick = () => {
  $("eq").checked = true;
  $("highpass").value = 150;
  $("bassFreq").value = 130;
  $("bassQ").value = 1.1;
  $("bassGain").value = -6;
  $("lowMidFreq").value = 250;
  $("lowMidQ").value = 1.0;
  $("lowMidGain").value = -3;
  status("EQ сброшен");
};
loadPhrases();
</script>
</body>
</html>
"""


class VoiceHandler(BaseHTTPRequestHandler):
    server_version = "KuzmichVoice/1.0"

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/voice.html"):
            body = HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/phrases":
            try:
                phrases = load_phrases(DEFAULT_PHRASES_FILE)
            except Exception as exc:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
                return
            self._json(HTTPStatus.OK, {"ok": True, "phrases": phrases})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            data = self._read_json()
            if path == "/api/speak":
                self._handle_speak(data)
                return
            if path == "/api/prepare":
                self._handle_prepare(data)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

    def _playback_args(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "output": str(data.get("output") or "native"),
            "sink": DEFAULT_SINK,
            "volume": int(data.get("volume") or 100),
            "iface": "eth0",
            "native_player": DEFAULT_NATIVE_PLAYER,
        }

    def _filter(self, data: dict[str, Any]) -> str:
        if not data.get("eq", True):
            return ""

        def number(name: str, default: float, low: float, high: float) -> float:
            try:
                value = float(data.get(name, default))
            except (TypeError, ValueError):
                value = default
            return max(low, min(high, value))

        highpass = number("highpass_hz", 150.0, 40.0, 400.0)
        bass_freq = number("bass_freq", 130.0, 60.0, 220.0)
        bass_q = number("bass_q", 1.1, 0.4, 3.0)
        bass_gain = number("bass_gain_db", -6.0, -18.0, 12.0)
        lowmid_freq = number("lowmid_freq", 250.0, 180.0, 500.0)
        lowmid_q = number("lowmid_q", 1.0, 0.4, 3.0)
        lowmid_gain = number("lowmid_gain_db", -3.0, -18.0, 12.0)
        return (
            f"highpass=f={highpass:.0f},"
            f"equalizer=f={bass_freq:.0f}:t=q:w={bass_q:.2f}:g={bass_gain:.1f},"
            f"equalizer=f={lowmid_freq:.0f}:t=q:w={lowmid_q:.2f}:g={lowmid_gain:.1f}"
        )

    def _handle_speak(self, data: dict[str, Any]) -> None:
        phrase_id = str(data.get("phrase_id") or "").strip()
        output_args = self._playback_args(data)
        if phrase_id:
            wav_path = phrase_cache_path(DEFAULT_CACHE_DIR, phrase_id)
            if not wav_path.exists():
                raise FileNotFoundError(f"phrase cache not found: {wav_path}")
            _play_wav(wav_path, **output_args)
            self._json(HTTPStatus.OK, {"ok": True, "wav": str(wav_path), "cached": True})
            return
        text = str(data.get("text") or "").strip()
        if not text:
            raise ValueError("empty text")
        wav_path = speak_bluetooth(
            text,
            output=output_args["output"],
            sink=output_args["sink"],
            volume=output_args["volume"],
            amplification_db=float(data.get("amplification_db") or 15.0),
            ffmpeg_filter=self._filter(data),
            iface=output_args["iface"],
            native_player=output_args["native_player"],
        )
        self._json(HTTPStatus.OK, {"ok": True, "wav": str(wav_path), "cached": False})

    def _handle_prepare(self, data: dict[str, Any]) -> None:
        from speak_bluetooth import prepare_phrase_cache

        prepared = prepare_phrase_cache(
            phrases_file=DEFAULT_PHRASES_FILE,
            cache_dir=DEFAULT_CACHE_DIR,
            tts_url=DEFAULT_TTS_URL,
            voice="",
            volume=int(data.get("volume") or 100),
            amplification_db=float(data.get("amplification_db") or 15.0),
            ffmpeg_filter=self._filter(data),
            timeout_s=15.0,
            library_dir=Path("/home/unitree/teleop/app/web_admin/data/audio_player/library"),
        )
        self._json(HTTPStatus.OK, {"ok": True, "count": len(prepared)})

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.client_address[0]} - {fmt % args}", flush=True)


def main() -> int:
    host = os.environ.get("VOICE_WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("VOICE_WEB_PORT", "8090"))
    server = ThreadingHTTPServer((host, port), VoiceHandler)
    print(f"voice web listening on http://{host}:{port}/voice.html", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
