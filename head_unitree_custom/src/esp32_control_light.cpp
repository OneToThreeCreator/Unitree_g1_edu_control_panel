/*
  esp32_control_light.cpp
  ═══════════════════════════════════════════════════════════════════════
  Light-прошивка «Управление умной головой Кузьмича» для ESP32-C3.
  Добавлено автоматическое моргание с настраиваемой периодичностью,
  инверсия управления правым серво, обновлены калибровки.
*/

#include <Arduino.h>
#include <WiFi.h>
#include <ESPAsyncWebServer.h>
#include <WebSocketsServer.h>
#include <Adafruit_NeoPixel.h>
#include <ArduinoJson.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <ArduinoOTA.h>
#include <esp_task_wdt.h>
#include <LittleFS.h>

#if ENABLE_SERVOS
  #include <ESP32Servo.h>
#endif

#include "config.h"

// ═══════════════════════════════════════════════════════════════════════
//  Globals
// ═══════════════════════════════════════════════════════════════════════

AsyncWebServer    server(80);
WebSocketsServer  webSocket(81);
Adafruit_NeoPixel strip(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);
Preferences       prefs;

// ── LED state ──────────────────────────────────────────────────────────
String   currentAnimation = DEFAULT_ANIMATION;
uint32_t staticColor      = DEFAULT_COLOR;
uint32_t bgColor          = DEFAULT_COLOR2;
int      ledBrightness    = DEFAULT_BRIGHTNESS;
int      animationSpeed   = DEFAULT_SPEED;
unsigned long lastAnimStep = 0;
int      animStep          = 0;

// ── Servo state ────────────────────────────────────────────────────────
#if ENABLE_SERVOS
Servo servo1;
Servo servo2;
int   servo1Angle   = 90;
int   servo2Angle   = 90;
bool  servo1Enabled = true;
bool  servo2Enabled = true;
int   servoSpeed    = DEFAULT_SERVO_SPEED;
#endif

// ── Auto blink state ──────────────────────────────────────────────────
bool  autoBlinkEnabled   = DEFAULT_AUTO_BLINK_ENABLED;
unsigned long autoBlinkInterval = DEFAULT_AUTO_BLINK_INTERVAL;
unsigned long lastBlinkTime = 0;

// ── Misc runtime ───────────────────────────────────────────────────────
unsigned long lastStateChange  = 0;
bool          stateDirty       = false;
unsigned long lastHeartbeat    = 0;
int           wsClientCount    = 0;

// ── Hardening ──────────────────────────────────────────────────────────
struct WsClientInfo {
  unsigned long firstMsgTime = 0;
  int           msgCount     = 0;
  unsigned long lastMsgTime  = 0;
  bool          active       = false;
};
WsClientInfo wsClients[8];

struct HttpRateEntry {
  IPAddress     ip;
  unsigned long windowStart = 0;
  int           count       = 0;
};
HttpRateEntry httpRate[16];
const int HTTP_RATE_MAX = 30;
const unsigned long HTTP_RATE_WINDOW = 1000;

unsigned long lastRebootRequest = 0;

// ── Servo macro scheduler ─────────────────────────────────────────────
#if ENABLE_SERVOS
struct MacroStep {
  unsigned long delayMs;
  uint8_t  which;          // 1, 2 или 3 (= оба)
  int      angle;
};
const int MACRO_MAX_STEPS = 24; // увеличили для новых макросов
struct MacroState {
  MacroStep steps[MACRO_MAX_STEPS];
  int       stepCount = 0;
  int       curStep   = 0;
  bool      active    = false;
  unsigned long stepStartTime = 0;
  String    name = "";
};
MacroState macro;
#endif

// ═══════════════════════════════════════════════════════════════════════
//  Forward declarations
// ═══════════════════════════════════════════════════════════════════════

void loadState();
void saveState();
void markStateDirty();
uint32_t colorWheel(byte wheelPos);
void updateAnimation();
void sendState(uint8_t clientNum);
void broadcastState();
void handleWebSocketMessage(uint8_t clientNum, const char* payload);
void onWebSocketEvent(uint8_t clientNum, WStype_t type, uint8_t* payload, size_t length);
void setupHTTP();
void setupOTA();
void setupWiFi();
void setupWDT();
bool wsRateLimitOk(uint8_t clientNum);
bool httpRateLimitOk(AsyncWebServerRequest* req);
void wsResetClient(uint8_t clientNum);
#if ENABLE_SERVOS
void startMacro(const String& name);
void updateMacro();
void stopMacro();
void attachServos();
void setServo(int which, int angle);
void enableServo(int which, bool enabled);
#endif

// ═══════════════════════════════════════════════════════════════════════
//  NVS state persistence
// ═══════════════════════════════════════════════════════════════════════

void loadState() {
  prefs.begin("state", true);
  currentAnimation = prefs.getString("anim",    DEFAULT_ANIMATION);
  ledBrightness    = prefs.getInt   ("bright",  DEFAULT_BRIGHTNESS);
  animationSpeed   = prefs.getInt   ("speed",   DEFAULT_SPEED);
  staticColor      = prefs.getUInt  ("color",   DEFAULT_COLOR);
  bgColor          = prefs.getUInt  ("color2",  DEFAULT_COLOR2);
#if ENABLE_SERVOS
  servo1Angle      = prefs.getInt   ("s1angle", 90);
  servo2Angle      = prefs.getInt   ("s2angle", 90);
  servo1Enabled    = prefs.getBool  ("s1en",    true);
  servo2Enabled    = prefs.getBool  ("s2en",    true);
  servoSpeed       = prefs.getInt   ("svspd",   DEFAULT_SERVO_SPEED);
#endif
  autoBlinkEnabled = prefs.getBool  ("autoblink", DEFAULT_AUTO_BLINK_ENABLED);
  autoBlinkInterval= prefs.getULong ("blinkint", DEFAULT_AUTO_BLINK_INTERVAL);
  prefs.end();
}

void saveState() {
  prefs.begin("state", false);
  prefs.putString("anim",   currentAnimation);
  prefs.putInt   ("bright", ledBrightness);
  prefs.putInt   ("speed",  animationSpeed);
  prefs.putUInt  ("color",  staticColor);
  prefs.putUInt  ("color2", bgColor);
#if ENABLE_SERVOS
  prefs.putInt   ("s1angle", servo1Angle);
  prefs.putInt   ("s2angle", servo2Angle);
  prefs.putBool  ("s1en",    servo1Enabled);
  prefs.putBool  ("s2en",    servo2Enabled);
  prefs.putInt   ("svspd",   servoSpeed);
#endif
  prefs.putBool  ("autoblink", autoBlinkEnabled);
  prefs.putULong ("blinkint",  autoBlinkInterval);
  prefs.end();
  stateDirty = false;
  Serial.println(F("[NVS] state saved"));
}

inline void markStateDirty() {
  stateDirty = true;
  lastStateChange = millis();
}

// ═══════════════════════════════════════════════════════════════════════
//  LED animations (без изменений)
// ═══════════════════════════════════════════════════════════════════════

uint32_t colorWheel(byte wheelPos) {
  wheelPos = 255 - wheelPos;
  if (wheelPos < 85)  return strip.Color(255 - wheelPos * 3, 0, wheelPos * 3);
  if (wheelPos < 170) {
    wheelPos -= 85;
    return strip.Color(0, wheelPos * 3, 255 - wheelPos * 3);
  }
  wheelPos -= 170;
  return strip.Color(wheelPos * 3, 255 - wheelPos * 3, 0);
}

void animOff()         { strip.clear(); strip.show(); }
void animStatic()      { strip.fill(staticColor); strip.show(); }
void animRainbow() {
  for (int i = 0; i < LED_COUNT; i++)
    strip.setPixelColor(i, colorWheel((animStep + i * 256 / LED_COUNT) & 255));
  strip.show();
}
void animRainbowCycle() {
  for (int i = 0; i < LED_COUNT; i++)
    strip.setPixelColor(i, colorWheel(((animStep * 5) + i * 256 / LED_COUNT) & 255));
  strip.show();
}
void animChase() {
  strip.clear();
  for (int i = 0; i < 3; i++)
    strip.setPixelColor((animStep + i) % LED_COUNT, staticColor);
  for (int i = 0; i < LED_COUNT; i++)
    if (i != animStep % LED_COUNT && i != (animStep + 1) % LED_COUNT && i != (animStep + 2) % LED_COUNT)
      strip.setPixelColor(i, bgColor);
  strip.show();
}
void animBreathing() {
  float t = (sin(animStep * 0.08) + 1.0) * 0.5;
  uint8_t r = ((staticColor >> 16) & 0xFF) * t;
  uint8_t g = ((staticColor >> 8)  & 0xFF) * t;
  uint8_t b = ( staticColor        & 0xFF) * t;
  strip.fill(strip.Color(r, g, b));
  strip.show();
}
void animTheater() {
  strip.fill(bgColor);
  for (int i = animStep % 3; i < LED_COUNT; i += 3)
    strip.setPixelColor(i, staticColor);
  strip.show();
}
void animWipe() {
  int fc = animStep % (LED_COUNT * 2);
  if (fc < LED_COUNT) {
    for (int i = 0; i <= fc; i++)           strip.setPixelColor(i, staticColor);
    for (int i = fc + 1; i < LED_COUNT; i++) strip.setPixelColor(i, bgColor);
  } else {
    int c = fc - LED_COUNT;
    for (int i = 0; i < LED_COUNT - c - 1; i++) strip.setPixelColor(i, staticColor);
    for (int i = LED_COUNT - c - 1; i < LED_COUNT; i++) strip.setPixelColor(i, bgColor);
  }
  strip.show();
}
void animScanner() {
  strip.fill(bgColor);
  int pos = (sin(animStep * 0.12) + 1.0) * 0.5 * (LED_COUNT - 1);
  strip.setPixelColor(pos, staticColor);
  uint32_t dim = strip.Color(((staticColor >> 16) & 0xFF) / 3,
                              ((staticColor >> 8)  & 0xFF) / 3,
                              ( staticColor        & 0xFF) / 3);
  if (pos > 0)           strip.setPixelColor(pos - 1, dim);
  if (pos < LED_COUNT-1) strip.setPixelColor(pos + 1, dim);
  strip.show();
}
void animDualColor() {
  for (int i = 0; i < LED_COUNT; i++)
    strip.setPixelColor(i, (i + animStep) % 2 ? staticColor : bgColor);
  strip.show();
}

void updateAnimation() {
  if (millis() - lastAnimStep < (unsigned long)animationSpeed) return;
  lastAnimStep = millis();
  animStep++;
  strip.setBrightness(ledBrightness);
  if      (currentAnimation == "off")            animOff();
  else if (currentAnimation == "static")         animStatic();
  else if (currentAnimation == "rainbow")        animRainbow();
  else if (currentAnimation == "rainbow_cycle")  animRainbowCycle();
  else if (currentAnimation == "chase")          animChase();
  else if (currentAnimation == "breathing")      animBreathing();
  else if (currentAnimation == "theater")        animTheater();
  else if (currentAnimation == "wipe")           animWipe();
  else if (currentAnimation == "scanner")        animScanner();
  else if (currentAnimation == "dual")           animDualColor();
  else                                            animOff();
}

// ═══════════════════════════════════════════════════════════════════════
//  Servo helpers (с инверсией для правого глаза в UI)
// ═══════════════════════════════════════════════════════════════════════

#if ENABLE_SERVOS
void attachServos() {
  if (servo1Enabled) {
    servo1.attach(SERVO1_PIN, SERVO_MIN_US, SERVO_MAX_US);
    servo1.write(servo1Angle);
  }
  if (servo2Enabled) {
    servo2.attach(SERVO2_PIN, SERVO_MIN_US, SERVO_MAX_US);
    servo2.write(servo2Angle);
  }
}

void setServo(int which, int angle) {
  angle = constrain(angle, 0, 180);
  if (which == 1) {
    servo1Angle = angle;
    if (servo1Enabled) servo1.write(angle);
  } else {
    servo2Angle = angle;
    if (servo2Enabled) servo2.write(angle);
  }
  markStateDirty();
}

void enableServo(int which, bool enabled) {
  if (which == 1) {
    servo1Enabled = enabled;
    if (enabled) { servo1.attach(SERVO1_PIN, SERVO_MIN_US, SERVO_MAX_US); servo1.write(servo1Angle); }
    else         { servo1.detach(); }
  } else {
    servo2Enabled = enabled;
    if (enabled) { servo2.attach(SERVO2_PIN, SERVO_MIN_US, SERVO_MAX_US); servo2.write(servo2Angle); }
    else         { servo2.detach(); }
  }
  markStateDirty();
}

// ── Macro scheduler ────────────────────────────────────────────────────
void addStep(int idx, unsigned long d, uint8_t w, int a) {
  if (idx < MACRO_MAX_STEPS) {
    macro.steps[idx].delayMs = d;
    macro.steps[idx].which   = w;
    macro.steps[idx].angle   = a;
  }
}

unsigned long macroDelay() {
  int s = constrain(servoSpeed, 1, 100);
  return (unsigned long)map(s, 1, 100, 400, 50);
}

int eyeOpen(uint8_t which)   { return which == 1 ? EYE_LEFT_OPEN   : EYE_RIGHT_OPEN;   }
int eyeClosed(uint8_t which) { return which == 1 ? EYE_LEFT_CLOSED : EYE_RIGHT_CLOSED;  }
int eyeMid(uint8_t which)    { return (eyeOpen(which) + eyeClosed(which)) / 2;          }


void startMacro(const String& name) {
  macro.stepCount = 0;
  macro.curStep   = 0;
  macro.active    = false;
  macro.name      = name;

  const unsigned long d = macroDelay();
  int i = 0;

  if (name == "center") {
    addStep(i++, 0, 1, eyeMid(1));
    addStep(i++, 0, 2, eyeMid(2));
  }
  else if (name == "home" || name == "open") {
    addStep(i++, 0, 1, eyeOpen(1));
    addStep(i++, 0, 2, eyeOpen(2));
  }
  else if (name == "close") {
    addStep(i++, 0, 1, eyeClosed(1));
    addStep(i++, 0, 2, eyeClosed(2));
  }
  else if (name == "blink_left") {
    addStep(i++, 0, 1, eyeClosed(1));
    addStep(i++, d, 1, eyeOpen(1));
  }
  else if (name == "blink_right") {
    addStep(i++, 0, 2, eyeClosed(2));
    addStep(i++, d, 2, eyeOpen(2));
  }
  else if (name == "blink_both") {
    addStep(i++, 0, 1, eyeClosed(1));
    addStep(i++, 0, 2, eyeClosed(2));
    addStep(i++, d, 1, eyeOpen(1));
    addStep(i++, 0, 2, eyeOpen(2));
    addStep(i++, d, 1, eyeClosed(1));
    addStep(i++, 0, 2, eyeClosed(2));
    addStep(i++, d, 1, eyeOpen(1));
    addStep(i++, 0, 2, eyeOpen(2));
  }
  else if (name == "wink") {
    addStep(i++, 0, 1, eyeClosed(1));
    addStep(i++, d, 1, eyeOpen(1));
    addStep(i++, d, 2, eyeClosed(2));
    addStep(i++, d, 2, eyeOpen(2));
    addStep(i++, d, 1, eyeClosed(1));
    addStep(i++, 0, 2, eyeClosed(2));
    addStep(i++, d, 1, eyeOpen(1));
    addStep(i++, 0, 2, eyeOpen(2));
  }
  else if (name == "blink_single") {
    // Новая задержка, зависящая от скорости
    unsigned long blinkDelay = map(servoSpeed, 1, 100, 400, 80);
    addStep(i++, 0, 1, eyeClosed(1));
    addStep(i++, 0, 2, eyeClosed(2));
    addStep(i++, blinkDelay, 1, eyeOpen(1));
    addStep(i++, 0, 2, eyeOpen(2));
    addStep(i++, blinkDelay, 1, eyeOpen(1)); // пауза после открытия
    addStep(i++, 0, 2, eyeOpen(2));
  }
  else {
    Serial.printf("[Macro] unknown: %s\n", name.c_str());
    return;
  }

  macro.stepCount = i;
  macro.active = true;
  macro.stepStartTime = millis();
  Serial.printf("[Macro] start: %s (%d steps)\n", name.c_str(), macro.stepCount);
}


void updateMacro() {
  if (!macro.active) return;
  unsigned long now = millis();
  if (now - macro.stepStartTime < macro.steps[macro.curStep].delayMs) return;

  const MacroStep& s = macro.steps[macro.curStep];
  if (s.which == 3) {
    setServo(1, s.angle);
    setServo(2, s.angle);
  } else {
    setServo(s.which, s.angle);
  }

  macro.curStep++;
  macro.stepStartTime = now;
  if (macro.curStep >= macro.stepCount) {
    Serial.printf("[Macro] done: %s\n", macro.name.c_str());
    macro.active = false;
    macro.name = "";
  }
}

void stopMacro() {
  if (macro.active) {
    Serial.println(F("[Macro] stopped"));
  }
  macro.active = false;
  macro.name = "";
  macro.curStep = 0;
  macro.stepCount = 0;
}
#endif

// ═══════════════════════════════════════════════════════════════════════
//  WebSocket state send (с инверсией для правого серво)
// ═══════════════════════════════════════════════════════════════════════

void sendState(uint8_t clientNum) {
  StaticJsonDocument<1024> doc;
  doc["type"] = "state";

  JsonObject features = doc.createNestedObject("features");
  features["servos"]  = (bool)ENABLE_SERVOS;

  doc["animation"]  = currentAnimation;
  char hex[8];
  snprintf(hex, sizeof(hex), "#%06X", staticColor);
  doc["color"]      = hex;
  snprintf(hex, sizeof(hex), "#%06X", bgColor);
  doc["color2"]     = hex;
  doc["brightness"] = ledBrightness;
  doc["speed"]      = animationSpeed;

#if ENABLE_SERVOS
  doc["servo1_angle"]   = servo1Angle;
  // Инверсия для правого: отображаем 180 - физический угол
  doc["servo2_angle"]   = 180 - servo2Angle;
  doc["servo1_enabled"] = servo1Enabled;
  doc["servo2_enabled"] = servo2Enabled;
  doc["servo_speed"]    = servoSpeed;
#endif

  doc["auto_blink_enabled"] = autoBlinkEnabled;
  doc["auto_blink_interval"] = autoBlinkInterval / 1000; // в секундах

  doc["heap_free"]  = ESP.getFreeHeap();
  doc["uptime_sec"] = (long)(millis() / 1000);
  doc["wifi_rssi"] = WiFi.RSSI();

  String response;
  serializeJson(doc, response);
  webSocket.sendTXT(clientNum, response);
}

void broadcastState() {
  StaticJsonDocument<1024> doc;
  doc["type"] = "state";
  doc["animation"]  = currentAnimation;
  char hex[8];
  snprintf(hex, sizeof(hex), "#%06X", staticColor);  doc["color"] = hex;
  snprintf(hex, sizeof(hex), "#%06X", bgColor);      doc["color2"] = hex;
  doc["brightness"] = ledBrightness;
  doc["speed"]      = animationSpeed;
#if ENABLE_SERVOS
  doc["servo1_angle"]   = servo1Angle;
  doc["servo2_angle"]   = 180 - servo2Angle; // инверсия
  doc["servo1_enabled"] = servo1Enabled;
  doc["servo2_enabled"] = servo2Enabled;
  doc["servo_speed"]    = servoSpeed;
#endif
  doc["auto_blink_enabled"] = autoBlinkEnabled;
  doc["auto_blink_interval"] = autoBlinkInterval / 1000;
  doc["heap_free"]    = ESP.getFreeHeap();
  doc["uptime_sec"]   = (long)(millis() / 1000);
  doc["wifi_rssi"] = WiFi.RSSI();
  String response;
  serializeJson(doc, response);
  webSocket.broadcastTXT(response);
}

// ═══════════════════════════════════════════════════════════════════════
//  WebSocket message handler
// ═══════════════════════════════════════════════════════════════════════

void handleWebSocketMessage(uint8_t clientNum, const char* payload) {
  StaticJsonDocument<512> doc;
  if (deserializeJson(doc, payload)) {
    Serial.println(F("[WS] JSON parse failed"));
    return;
  }
  String cmd = doc["cmd"] | "";

  if (cmd == "get_state")            { sendState(clientNum); return; }

  if (cmd == "led_animation") {
    currentAnimation = doc["name"] | "off";
    animStep = 0;
    markStateDirty();
    return;
  }
  if (cmd == "led_color") {
    String hex = doc["color"] | "#ff00ff";
    long rgb = strtol(hex.c_str() + 1, nullptr, 16);
    staticColor = strip.Color((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF);
    markStateDirty();
    return;
  }
  if (cmd == "led_color2") {
    String hex = doc["color"] | "#000000";
    long rgb = strtol(hex.c_str() + 1, nullptr, 16);
    bgColor = strip.Color((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF);
    markStateDirty();
    return;
  }
  if (cmd == "led_brightness") {
    ledBrightness = constrain((int)(doc["value"] | 80), 0, 255);
    markStateDirty();
    return;
  }
  if (cmd == "led_speed") {
    animationSpeed = constrain((int)(doc["value"] | 50), 10, 500);
    markStateDirty();
    return;
  }

#if ENABLE_SERVOS
  if (cmd == "servo") {
    int which = doc["which"] | 0;
    int angle = doc["angle"] | -1;
    if (which > 0 && angle >= 0) {
      if (which == 2) angle = 180 - angle; // инверсия
      setServo(which, angle);
    }
    return;
  }
  if (cmd == "servo_both") {
    int angle = doc["angle"] | -1;
    if (angle >= 0) {
      setServo(1, angle);
      setServo(2, 180 - angle); // инверсия для правого
    }
    return;
  }
  if (cmd == "servo_enable") {
    int which = doc["which"] | 0;
    bool en   = doc["enabled"] | false;
    if (which > 0) enableServo(which, en);
    return;
  }
  if (cmd == "servo_preset") {
    int which = doc["which"] | 0;
    int angle = doc["angle"] | -1;
    if (which > 0 && angle >= 0) {
      if (which == 2) angle = 180 - angle;
      setServo(which, angle);
    }
    return;
  }
  if (cmd == "servo_macro") {
    String name = doc["name"] | "";
    if (name.length() > 0) startMacro(name);
    return;
  }
  if (cmd == "servo_macro_stop") {
    stopMacro();
    return;
  }
  if (cmd == "servo_speed") {
    servoSpeed = constrain((int)(doc["value"] | DEFAULT_SERVO_SPEED), 1, 100);
    markStateDirty();
    return;
  }
  // Автоматическое моргание
  if (cmd == "auto_blink") {
    if (doc.containsKey("enabled")) {
      autoBlinkEnabled = doc["enabled"] | false;
      if (!autoBlinkEnabled) stopMacro(); // останавливаем текущий макрос
      markStateDirty();
    }
    if (doc.containsKey("interval")) {
      int sec = doc["interval"] | 3;
      if (sec < 1) sec = 1;
      if (sec > 10) sec = 10;
      autoBlinkInterval = sec * 1000UL;
      markStateDirty();
    }
    return;
  }
  if (cmd == "blink_now") {
    // Ручное моргание (один раз)
    if (!macro.active) {
      startMacro("blink_single");
    }
    return;
  }
#endif

#if !ENABLE_SERVOS
  if (cmd == "servo" || cmd == "servo_both" || cmd == "servo_enable" ||
      cmd == "servo_preset" || cmd == "servo_macro" || cmd == "servo_macro_stop" ||
      cmd == "servo_speed" || cmd == "auto_blink" || cmd == "blink_now") {
    StaticJsonDocument<128> err;
    err["type"]    = "error";
    err["message"] = "servos disabled in firmware";
    String r; serializeJson(err, r);
    webSocket.sendTXT(clientNum, r);
    return;
  }
#endif

  if (cmd == "reboot") {
    if (millis() - lastRebootRequest < 3000) {
      Serial.println(F("[CMD] reboot ignored (too soon)"));
      return;
    }
    lastRebootRequest = millis();
    Serial.println(F("[CMD] reboot in 500ms"));
    delay(500);
    ESP.restart();
    return;
  }

  Serial.printf("[WS] unknown cmd: %s\n", cmd.c_str());
}

// ═══════════════════════════════════════════════════════════════════════
//  WebSocket event handler (без изменений)
// ═══════════════════════════════════════════════════════════════════════

void onWebSocketEvent(uint8_t clientNum, WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_DISCONNECTED:
      if (wsClientCount > 0) wsClientCount--;
      wsResetClient(clientNum);
      Serial.printf("[WS] #%u disconnected (now %d)\n", clientNum, wsClientCount);
      break;
    case WStype_CONNECTED: {
      if (wsClientCount >= MAX_WS_CLIENTS) {
        Serial.printf("[WS] #%u rejected (limit %d)\n", clientNum, MAX_WS_CLIENTS);
        StaticJsonDocument<128> err;
        err["type"]    = "error";
        err["message"] = "max clients reached";
        String r; serializeJson(err, r);
        webSocket.sendTXT(clientNum, r);
        delay(50);
        webSocket.disconnect(clientNum);
        return;
      }
      if (ESP.getFreeHeap() < 30 * 1024) {
        Serial.printf("[WS] #%u rejected (low heap: %u)\n", clientNum, ESP.getFreeHeap());
        StaticJsonDocument<128> err;
        err["type"]    = "error";
        err["message"] = "low heap, try later";
        String r; serializeJson(err, r);
        webSocket.sendTXT(clientNum, r);
        delay(50);
        webSocket.disconnect(clientNum);
        return;
      }
      wsClientCount++;
      wsClients[clientNum].active = true;
      wsClients[clientNum].firstMsgTime = millis();
      wsClients[clientNum].msgCount = 0;
      Serial.printf("[WS] #%u connected (now %d, heap %u)\n",
                    clientNum, wsClientCount, ESP.getFreeHeap());
      sendState(clientNum);
      break;
    }
    case WStype_TEXT:
      if (length > 1024) {
        Serial.printf("[WS] #%u oversized payload (%u bytes), dropping\n", clientNum, length);
        StaticJsonDocument<128> err;
        err["type"]    = "error";
        err["message"] = "payload too large";
        String r; serializeJson(err, r);
        webSocket.sendTXT(clientNum, r);
        return;
      }
      if (!wsRateLimitOk(clientNum)) {
        Serial.printf("[WS] #%u rate-limited, dropping\n", clientNum);
        webSocket.disconnect(clientNum);
        return;
      }
      handleWebSocketMessage(clientNum, (const char*)payload);
      break;
    default: break;
  }
}

// ═══════════════════════════════════════════════════════════════════════
//  HTTP server (добавлена поддержка новых полей в /state)
// ═══════════════════════════════════════════════════════════════════════

extern const char INDEX_HTML[] PROGMEM;

void setupHTTP() {
  server.on("/", HTTP_GET, [](AsyncWebServerRequest* req) {
    if (!httpRateLimitOk(req)) { req->send(429, "text/plain", "rate limit"); return; }
    req->send(200, "text/html", INDEX_HTML);
  });

  server.on("/state", HTTP_GET, [](AsyncWebServerRequest* req) {
    if (!httpRateLimitOk(req)) { req->send(429, "text/plain", "rate limit"); return; }
    AsyncResponseStream* resp = req->beginResponseStream("application/json");
    resp->printf("{");
    resp->printf("\"animation\":\"%s\",", currentAnimation.c_str());
    resp->printf("\"brightness\":%d,", ledBrightness);
    resp->printf("\"speed\":%d,", animationSpeed);
    resp->printf("\"color\":\"#%06X\",", staticColor);
    resp->printf("\"color2\":\"#%06X\",", bgColor);
    resp->printf("\"features\":{\"servos\":%s},", ENABLE_SERVOS ? "true" : "false");
    resp->printf("\"servo1_angle\":%d,", servo1Angle);
    resp->printf("\"servo2_angle\":%d,", 180 - servo2Angle); // инверсия
    resp->printf("\"servo1_enabled\":%s,", servo1Enabled ? "true" : "false");
    resp->printf("\"servo2_enabled\":%s,", servo2Enabled ? "true" : "false");
    resp->printf("\"servo_speed\":%d,", servoSpeed);
    resp->printf("\"auto_blink_enabled\":%s,", autoBlinkEnabled ? "true" : "false");
    resp->printf("\"auto_blink_interval\":%u,", autoBlinkInterval / 1000);
    resp->printf("\"heap_free\":%u,", ESP.getFreeHeap());
    resp->printf("\"uptime_sec\":%lu,", millis() / 1000);
    resp->printf("\"wifi_rssi\":%d,", WiFi.RSSI());
    resp->printf("\"flash_size\":%u", ESP.getFlashChipSize());
    resp->printf("}");
    req->send(resp);
  });

  server.on("/team.jpg", HTTP_GET, [](AsyncWebServerRequest* req) {
    if (!httpRateLimitOk(req)) { req->send(429, "text/plain", "rate limit"); return; }
    if (LittleFS.exists("/team.jpg")) {
      AsyncWebServerResponse* r = req->beginResponse(LittleFS, "/team.jpg", "image/jpeg");
      r->addHeader("Cache-Control", "max-age=86400");
      req->send(r);
    } else {
      req->send(404, "text/plain", "team.jpg not uploaded");
    }
  });

  server.on("/reboot", HTTP_POST, [](AsyncWebServerRequest* req) {
    if (!httpRateLimitOk(req)) { req->send(429, "text/plain", "rate limit"); return; }
    if (millis() - lastRebootRequest < 3000) {
      req->send(429, "application/json", "{\"ok\":false,\"error\":\"cooldown\"}");
      return;
    }
    lastRebootRequest = millis();
    req->send(200, "application/json", "{\"ok\":true}");
    Serial.println(F("[HTTP] reboot in 500ms"));
    delay(500);
    ESP.restart();
  });

  server.onNotFound([](AsyncWebServerRequest* req) {
    req->send(404, "text/plain", "Not Found");
  });

  server.begin();
  Serial.println(F("[HTTP] server on :80"));
}

// ═══════════════════════════════════════════════════════════════════════
//  OTA (без изменений)
// ═══════════════════════════════════════════════════════════════════════

void setupOTA() {
  ArduinoOTA.setHostname(MDNS_NAME);
  ArduinoOTA.setPassword("ota-esp32");
  ArduinoOTA
    .onStart([]() { Serial.println(F("[OTA] start")); })
    .onEnd([]()   { Serial.println(F("\n[OTA] end")); })
    .onProgress([](unsigned int p, unsigned int t) {
      Serial.printf("[OTA] %u/%u (%u%%)\r", p, t, (p * 100) / t);
    })
    .onError([](ota_error_t e) {
      Serial.printf("[OTA] error %u\n", e);
    });
  ArduinoOTA.begin();
  Serial.println(F("[OTA] ready (password: ota-esp32)"));
}

// ═══════════════════════════════════════════════════════════════════════
//  WiFi + mDNS (без изменений)
// ═══════════════════════════════════════════════════════════════════════

void setupWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(STA_SSID, STA_PASS);
  Serial.printf("[WiFi] Connecting to %s", STA_SSID);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 40) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[WiFi] Connected! IP: %s  RSSI: %d dBm\n",
                  WiFi.localIP().toString().c_str(), WiFi.RSSI());
  } else {
    // Fallback to AP mode for debugging
    Serial.println("\n[WiFi] STA failed, starting AP fallback");
    WiFi.mode(WIFI_AP);
    WiFi.softAP(AP_FALLBACK_SSID, AP_FALLBACK_PASS);
    Serial.printf("[WiFi] AP SSID: %s\n", AP_FALLBACK_SSID);
    Serial.printf("[WiFi] AP IP:   %s\n", WiFi.softAPIP().toString().c_str());
  }

  if (!MDNS.begin(MDNS_NAME)) {
    Serial.println(F("[mDNS] failed"));
  } else {
    MDNS.addService("http", "tcp", 80);
    Serial.printf("[mDNS] http://%s.local\n", MDNS_NAME);
  }
}

// ═══════════════════════════════════════════════════════════════════════
//  Hardening (без изменений)
// ═══════════════════════════════════════════════════════════════════════

void setupWDT() {
  esp_task_wdt_init(10, true);
  esp_task_wdt_add(NULL);
  Serial.println(F("[WDT] watchdog 10s"));
}

void wdtFeed() {
  esp_task_wdt_reset();
}

bool wsRateLimitOk(uint8_t clientNum) {
  if (clientNum >= 8) return false;
  WsClientInfo& c = wsClients[clientNum];
  unsigned long now = millis();
  if (now - c.firstMsgTime > 1000) {
    c.firstMsgTime = now;
    c.msgCount = 0;
  }
  c.msgCount++;
  c.lastMsgTime = now;
  return c.msgCount <= 50;
}

void wsResetClient(uint8_t clientNum) {
  if (clientNum < 8) {
    wsClients[clientNum].active = false;
    wsClients[clientNum].msgCount = 0;
  }
}

bool httpRateLimitOk(AsyncWebServerRequest* req) {
  IPAddress ip = req->client()->remoteIP();
  unsigned long now = millis();
  HttpRateEntry* slot = nullptr;
  for (int i = 0; i < 16; i++) {
    if (httpRate[i].count == 0) { slot = &httpRate[i]; break; }
    if (httpRate[i].ip == ip)   { slot = &httpRate[i]; break; }
  }
  if (!slot) slot = &httpRate[0];
  if (now - slot->windowStart > HTTP_RATE_WINDOW) {
    slot->windowStart = now;
    slot->count = 0;
  }
  slot->ip = ip;
  slot->count++;
  return slot->count <= HTTP_RATE_MAX;
}

// ═══════════════════════════════════════════════════════════════════════
//  Setup / Loop
// ═══════════════════════════════════════════════════════════════════════

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println(F("\n=== Umnaya golova Kuzmicha (ESP32-C3) ==="));

  loadState();

  if (!LittleFS.begin(true)) {
    Serial.println(F("[FS] LittleFS mount failed"));
  } else {
    Serial.printf("[FS] LittleFS ok, team.jpg: %s\n",
                  LittleFS.exists("/team.jpg") ? "found" : "missing");
  }

  strip.begin();
  strip.setBrightness(ledBrightness);
  strip.fill(strip.Color(30, 30, 30));
  strip.show();
  Serial.println(F("[LED] ready"));

#if ENABLE_SERVOS
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  servo1.setPeriodHertz(SERVO_FREQ);
  servo2.setPeriodHertz(SERVO_FREQ);
  attachServos();
  Serial.println(F("[Servo] ready"));
#else
  Serial.println(F("[Servo] disabled in firmware"));
#endif

  setupWiFi();
  setupHTTP();
  setupOTA();
  setupWDT();

  WiFi.setTxPower(WIFI_POWER_17dBm);

  webSocket.begin();
  webSocket.onEvent(onWebSocketEvent);
  Serial.println(F("[WS] server on :81"));

  Serial.println(F("=== Ready ==="));
}

void loop() {
  webSocket.loop();
  ArduinoOTA.handle();
  updateAnimation();
#if ENABLE_SERVOS
  updateMacro();
  // Автоматическое моргание
  if (autoBlinkEnabled && !macro.active) {
    if (millis() - lastBlinkTime >= autoBlinkInterval) {
      startMacro("blink_single");
      lastBlinkTime = millis();
    }
  }
#endif
  wdtFeed();

  if (stateDirty && (millis() - lastStateChange > STATE_SAVE_MS)) {
    saveState();
  }

  if (millis() - lastHeartbeat > 5000) {
    lastHeartbeat = millis();
    if (wsClientCount > 0) {
      StaticJsonDocument<256> doc;
      doc["type"]         = "heartbeat";
      doc["heap_free"]    = ESP.getFreeHeap();
      doc["uptime_sec"]   = (long)(millis() / 1000);
      doc["wifi_rssi"] = WiFi.RSSI();
      String r; serializeJson(doc, r);
      webSocket.broadcastTXT(r);
    }
  }

  yield();
}

// ═══════════════════════════════════════════════════════════════════════
//  Embedded HTML (обновлён: добавлен блок «Моргание»)
// ═══════════════════════════════════════════════════════════════════════

const char INDEX_HTML[] PROGMEM = R"HTML(
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Управление умной головой Кузьмича</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  :root {
    --bg-0: #060608;
    --glass: rgba(255,255,255,0.04);
    --glass-bd: rgba(255,255,255,0.08);
    --text: #e8e8f0;
    --text-dim: #8888a0;
    --accent: #a855f7;
    --accent-2: #ec4899;
    --accent-3: #06b6d4;
    --success: #10b981;
    --warn: #f59e0b;
    --danger: #ef4444;
    --radius: 16px;
  }
  html, body {
    min-height: 100vh; background: var(--bg-0); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', system-ui, sans-serif;
    overflow-x: hidden;
  }
  body {
    background:
      radial-gradient(circle at 0% 0%, rgba(168,85,247,0.18), transparent 40%),
      radial-gradient(circle at 100% 0%, rgba(236,72,153,0.15), transparent 40%),
      radial-gradient(circle at 50% 100%, rgba(6,182,212,0.12), transparent 50%),
      var(--bg-0);
    padding: 16px; padding-bottom: 80px;
  }
  .orbs { position: fixed; inset: 0; z-index: -1; pointer-events: none; overflow: hidden; }
  .orb { position: absolute; border-radius: 50%; filter: blur(80px); opacity: 0.35; animation: float 20s ease-in-out infinite; }
  .orb:nth-child(1) { width: 400px; height: 400px; background: #a855f7; top: -100px; left: -100px; }
  .orb:nth-child(2) { width: 350px; height: 350px; background: #ec4899; top: 30%; right: -120px; animation-delay: -5s; }
  .orb:nth-child(3) { width: 300px; height: 300px; background: #06b6d4; bottom: -80px; left: 30%; animation-delay: -10s; }
  @keyframes float {
    0%,100% { transform: translate(0,0) scale(1); }
    33% { transform: translate(30px, -50px) scale(1.1); }
    66% { transform: translate(-20px, 30px) scale(0.9); }
  }
  header { text-align: center; margin-bottom: 24px; padding-top: 16px; }
  header h1 {
    font-size: clamp(1.6rem, 4vw, 2.2rem); font-weight: 800;
    background: linear-gradient(135deg, #a855f7 0%, #ec4899 50%, #06b6d4 100%);
    -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
    letter-spacing: -0.02em; margin-bottom: 6px;
  }
  header p { color: var(--text-dim); font-size: 0.85rem; font-weight: 500; }
  .status-pill {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 6px 14px; background: var(--glass); border: 1px solid var(--glass-bd);
    border-radius: 99px; font-size: 0.78rem; margin-top: 10px; backdrop-filter: blur(10px);
  }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--danger); transition: background 0.3s; }
  .status-dot.connected { background: var(--success); box-shadow: 0 0 12px var(--success); animation: pulse 2s ease-in-out infinite; }
  @keyframes pulse { 50% { transform: scale(1.3); } }
  .grid { display: grid; grid-template-columns: 1fr; gap: 16px; max-width: 1100px; margin: 0 auto; }
  @media (min-width: 768px) {
    .grid { grid-template-columns: 1fr 1fr; }
    .grid .led-card, .grid .metrics-card { grid-column: 1 / -1; }
  }
  .card {
    background: var(--glass); border: 1px solid var(--glass-bd);
    border-radius: var(--radius); padding: 20px; backdrop-filter: blur(20px);
  }
  .card-title { font-size: 1.1rem; font-weight: 700; margin-bottom: 4px; }
  .card-subtitle { color: var(--text-dim); font-size: 0.78rem; margin-bottom: 16px; }
  .hidden { display: none !important; }
  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 6px;
    background: rgba(245,158,11,0.15); color: var(--warn);
    font-size: 0.68rem; font-weight: 600; margin-left: 8px; vertical-align: middle;
  }
  .anim-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 16px; }
  .anim-btn {
    padding: 10px 4px; background: var(--glass); border: 1px solid var(--glass-bd);
    border-radius: 10px; color: var(--text); font-size: 0.78rem; font-weight: 600;
    cursor: pointer; transition: all 0.2s; text-transform: capitalize;
  }
  .anim-btn:hover { border-color: var(--accent); background: rgba(168,85,247,0.1); }
  .anim-btn.active {
    background: linear-gradient(135deg, var(--accent), var(--accent-2));
    border-color: transparent; color: white; box-shadow: 0 4px 16px rgba(168,85,247,0.4);
  }
  .control-row { margin-bottom: 14px; }
  .control-label {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 6px; font-size: 0.85rem; color: var(--text-dim); font-weight: 500;
  }
  .control-value { color: var(--accent-3); font-weight: 700; font-variant-numeric: tabular-nums; }
  input[type="range"] {
    -webkit-appearance: none; width: 100%; height: 6px; border-radius: 3px;
    background: rgba(255,255,255,0.08); outline: none;
  }
  input[type="range"]::-webkit-slider-thumb {
    -webkit-appearance: none; width: 22px; height: 22px; border-radius: 50%;
    background: linear-gradient(135deg, var(--accent), var(--accent-2));
    cursor: pointer; box-shadow: 0 0 12px rgba(168,85,247,0.6); border: 2px solid white;
  }
  input[type="range"]::-moz-range-thumb {
    width: 22px; height: 22px; border-radius: 50%; border: 2px solid white;
    background: linear-gradient(135deg, var(--accent), var(--accent-2)); cursor: pointer;
  }
  .color-row { display: flex; gap: 12px; align-items: center; margin-bottom: 12px; }
  .color-picker-wrap { display: flex; flex-direction: column; align-items: center; gap: 4px; }
  .color-picker-wrap span { font-size: 0.7rem; color: var(--text-dim); }
  input[type="color"] {
    width: 56px; height: 56px; border: none; border-radius: 12px; cursor: pointer;
    background: transparent; padding: 0;
  }
  input[type="color"]::-webkit-color-swatch-wrapper { padding: 0; }
  input[type="color"]::-webkit-color-swatch { border: 2px solid var(--glass-bd); border-radius: 12px; }
  .btn {
    padding: 8px 16px; background: var(--glass); border: 1px solid var(--glass-bd);
    border-radius: 10px; color: var(--text); font-size: 0.85rem; font-weight: 600;
    cursor: pointer; transition: all 0.2s;
  }
  .btn:hover { border-color: var(--accent); background: rgba(168,85,247,0.1); }
  .btn.danger { color: var(--danger); }
  .btn.danger:hover { border-color: var(--danger); background: rgba(239,68,68,0.1); }
  .toggle {
    position: relative; width: 44px; height: 24px; background: rgba(255,255,255,0.08);
    border-radius: 12px; cursor: pointer; transition: background 0.2s;
  }
  .toggle::after {
    content: ''; position: absolute; top: 2px; left: 2px; width: 20px; height: 20px;
    border-radius: 50%; background: white; transition: transform 0.2s;
  }
  .toggle.on { background: var(--success); }
  .toggle.on::after { transform: translateX(20px); }
  .toggle-row { display: flex; justify-content: space-between; align-items: center; }
  .preset-row { display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px; margin-top: 8px; }
  .preset-btn {
    padding: 6px 2px; background: var(--glass); border: 1px solid var(--glass-bd);
    border-radius: 8px; color: var(--text-dim); font-size: 0.72rem; font-weight: 600;
    cursor: pointer; transition: all 0.15s;
  }
  .preset-btn:hover { border-color: var(--accent-3); color: var(--accent-3); background: rgba(6,182,212,0.08); }
  .macro-grid {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 14px;
    padding-top: 14px; border-top: 1px solid var(--glass-bd);
  }
  .macro-btn {
    padding: 10px 4px; background: linear-gradient(135deg, rgba(168,85,247,0.15), rgba(236,72,153,0.15));
    border: 1px solid rgba(168,85,247,0.3); border-radius: 10px;
    color: var(--text); font-size: 0.76rem; font-weight: 600; cursor: pointer;
    transition: all 0.2s; text-transform: capitalize;
  }
  .macro-btn:hover { border-color: var(--accent); box-shadow: 0 4px 16px rgba(168,85,247,0.3); }
  .macro-btn.danger { background: rgba(239,68,68,0.1); border-color: rgba(239,68,68,0.3); color: var(--danger); }
  .macro-btn.danger:hover { border-color: var(--danger); }
  .sync-row {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 12px; padding: 8px 12px; background: rgba(255,255,255,0.03);
    border-radius: 10px; border: 1px solid var(--glass-bd);
  }
  .sync-row span { font-size: 0.82rem; color: var(--text-dim); }
  .metrics-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }
  .metric { background: var(--glass); border: 1px solid var(--glass-bd); border-radius: 12px; padding: 12px; }
  .metric-label { font-size: 0.7rem; color: var(--text-dim); margin-bottom: 4px; }
  .metric-value { font-size: 1.1rem; font-weight: 700; color: var(--accent-3); font-variant-numeric: tabular-nums; }
  .toast {
    position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%) translateY(80px);
    padding: 12px 20px; background: rgba(20,20,28,0.95); border: 1px solid var(--glass-bd);
    border-radius: 12px; color: var(--text); font-size: 0.85rem; backdrop-filter: blur(20px);
    transition: transform 0.3s, opacity 0.3s; opacity: 0; z-index: 1000; max-width: 90vw;
  }
  .toast.show { transform: translateX(-50%) translateY(0); opacity: 1; }
  .toast.error { border-color: var(--danger); }
  .toast.success { border-color: var(--success); }
  .footer-info { text-align: center; color: var(--text-dim); font-size: 0.72rem; margin-top: 24px; }
  .palette { margin: 6px 0 14px; padding: 12px; background: rgba(255,255,255,0.03); border: 1px solid var(--glass-bd); border-radius: 12px; }
  .palette-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; font-size: 0.82rem; color: var(--text-dim); font-weight: 500; }
  .palette-swatch { width: 40px; height: 24px; border-radius: 8px; border: 2px solid var(--glass-bd); background: #ff00ff; }
  .ch-r { color: #ff5a5a; } .ch-g { color: #4ade80; } .ch-b { color: #60a5fa; }
  .slider-r::-webkit-slider-thumb { background: linear-gradient(135deg,#ff5a5a,#ff0000); box-shadow: 0 0 12px rgba(255,90,90,0.6); }
  .slider-g::-webkit-slider-thumb { background: linear-gradient(135deg,#4ade80,#16a34a); box-shadow: 0 0 12px rgba(74,222,128,0.6); }
  .slider-b::-webkit-slider-thumb { background: linear-gradient(135deg,#60a5fa,#2563eb); box-shadow: 0 0 12px rgba(96,165,250,0.6); }
  .slider-r::-moz-range-thumb { background: #ff3b3b; } .slider-g::-moz-range-thumb { background: #22c55e; } .slider-b::-moz-range-thumb { background: #3b82f6; }
  /* Блок моргания */
  .blink-section {
    margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--glass-bd);
  }
  .blink-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  .blink-row .toggle { flex-shrink: 0; }
  .team-photo-wrap { max-width: 1100px; margin: 32px auto 0; text-align: center; }
  .team-photo {
    width: 100%; max-width: 480px; height: auto; border-radius: var(--radius);
    border: 1px solid var(--glass-bd); box-shadow: 0 8px 32px rgba(0,0,0,0.4);
  }
  .signature {
    max-width: 1100px; margin: 20px auto 0; text-align: center;
    padding: 18px; background: var(--glass); border: 1px solid var(--glass-bd);
    border-radius: var(--radius); backdrop-filter: blur(20px);
  }
  .signature .love {
    font-size: 0.95rem; font-weight: 700;
    background: linear-gradient(135deg,#a855f7,#ec4899,#06b6d4);
    -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
    margin-bottom: 6px;
  }
  .signature .team { color: var(--text-dim); font-size: 0.82rem; line-height: 1.5; }
</style>
</head>
<body>
<div class="orbs"><div class="orb"></div><div class="orb"></div><div class="orb"></div></div>

<header>
  <h1>Управление умной головой Кузьмича</h1>
  <p>Светодиоды &middot; Сервоприводы &middot; WebSocket &middot; mDNS &middot; OTA</p>
  <div class="status-pill">
    <span class="status-dot" id="statusDot"></span>
    <span id="statusText">Отключено</span>
  </div>
</header>

<div class="grid">

  <!-- LED Card -->
  <div class="card led-card">
    <div class="card-title">Светящиеся глаза</div>
    <div class="card-subtitle">LED управление &middot; GPIO10 &middot; 2&times; WS2812B панели (14 диодов)</div>

    <div class="anim-grid" id="animGrid">
      <button class="anim-btn" data-anim="off">Выкл</button>
      <button class="anim-btn" data-anim="static">Статичный</button>
      <button class="anim-btn" data-anim="rainbow">Радуга</button>
      <button class="anim-btn" data-anim="rainbow_cycle">Радуга цикл</button>
      <button class="anim-btn" data-anim="chase">Бегущий</button>
      <button class="anim-btn" data-anim="breathing">Дыхание</button>
      <button class="anim-btn" data-anim="theater">Театр</button>
      <button class="anim-btn" data-anim="wipe">Заливка</button>
      <button class="anim-btn" data-anim="scanner">Сканер</button>
      <button class="anim-btn" data-anim="dual">Двойной</button>
    </div>

    <div class="color-row">
      <div class="color-picker-wrap">
        <input type="color" id="colorPicker" value="#ff00ff">
        <span>Основной</span>
      </div>
      <div class="color-picker-wrap">
        <input type="color" id="color2Picker" value="#000000">
        <span>Фон</span>
      </div>
    </div>

    <div class="palette">
      <div class="palette-head">
        <span>Палитра основного цвета</span>
        <span class="palette-swatch" id="paletteSwatch"></span>
      </div>
      <div class="control-row">
        <div class="control-label">
          <span class="ch-r">Красный (R)</span>
          <span class="control-value" id="rVal">255</span>
        </div>
        <input type="range" id="rSlider" class="slider-r" min="0" max="255" value="255">
      </div>
      <div class="control-row">
        <div class="control-label">
          <span class="ch-g">Зелёный (G)</span>
          <span class="control-value" id="gVal">0</span>
        </div>
        <input type="range" id="gSlider" class="slider-g" min="0" max="255" value="0">
      </div>
      <div class="control-row">
        <div class="control-label">
          <span class="ch-b">Синий (B)</span>
          <span class="control-value" id="bVal">255</span>
        </div>
        <input type="range" id="bSlider" class="slider-b" min="0" max="255" value="255">
      </div>
    </div>

    <div class="control-row">
      <div class="control-label">
        <span>Яркость</span>
        <span class="control-value" id="brightnessVal">80</span>
      </div>
      <input type="range" id="brightness" min="0" max="255" value="80" data-throttle="true">
    </div>

    <div class="control-row">
      <div class="control-label">
        <span>Скорость анимации</span>
        <span class="control-value" id="speedVal">30</span>
      </div>
      <input type="range" id="speed" min="10" max="500" value="30" data-throttle="true">
    </div>
  </div>

  <!-- Servo Card -->
  <div class="card servo-card hidden" id="servoCard">
    <div class="card-title">Лючки глаз <span id="servoBadge"></span></div>
    <div class="card-subtitle">Серво &middot; GPIO4 — левый глаз &middot; GPIO2 — правый глаз (инвертирован)</div>

    <div class="sync-row">
      <span>Синхронный режим (оба глаза вместе)</span>
      <div class="toggle" id="syncToggle"></div>
    </div>

    <div class="control-row">
      <div class="control-label">
        <span>Скорость сервоприводов</span>
        <span class="control-value" id="servoSpeedVal">60</span>
      </div>
      <input type="range" id="servoSpeed" min="1" max="100" value="60">
    </div>

    <div class="control-row">
      <div class="toggle-row">
        <span>Левый глаз</span>
        <div class="toggle on" id="servo1Toggle"></div>
      </div>
      <div class="control-label">
        <span>Угол века</span>
        <span class="control-value"><span id="servo1Val">90</span>&deg;</span>
      </div>
      <input type="range" id="servo1" min="0" max="180" value="90" data-throttle="true">
      <div class="preset-row">
        <button class="preset-btn" data-servo="1" data-angle="0">0&deg;</button>
        <button class="preset-btn" data-servo="1" data-angle="45">45&deg;</button>
        <button class="preset-btn" data-servo="1" data-angle="90">90&deg;</button>
        <button class="preset-btn" data-servo="1" data-angle="135">135&deg;</button>
        <button class="preset-btn" data-servo="1" data-angle="180">180&deg;</button>
      </div>
    </div>

    <div class="control-row">
      <div class="toggle-row">
        <span>Правый глаз (инвертирован)</span>
        <div class="toggle on" id="servo2Toggle"></div>
      </div>
      <div class="control-label">
        <span>Угол века</span>
        <span class="control-value"><span id="servo2Val">90</span>&deg;</span>
      </div>
      <input type="range" id="servo2" min="0" max="180" value="90" data-throttle="true">
      <div class="preset-row">
        <button class="preset-btn" data-servo="2" data-angle="0">0&deg;</button>
        <button class="preset-btn" data-servo="2" data-angle="45">45&deg;</button>
        <button class="preset-btn" data-servo="2" data-angle="90">90&deg;</button>
        <button class="preset-btn" data-servo="2" data-angle="135">135&deg;</button>
        <button class="preset-btn" data-servo="2" data-angle="180">180&deg;</button>
      </div>
    </div>

    <div class="macro-grid">
      <button class="macro-btn" data-macro="blink_left">Подмигнуть левым</button>
      <button class="macro-btn" data-macro="blink_right">Подмигнуть правым</button>
      <button class="macro-btn" data-macro="blink_both">Моргнуть обоими</button>
      <button class="macro-btn" data-macro="wink">Подмаргивание</button>
      <button class="macro-btn" data-macro="open">Открыть глаза</button>
      <button class="macro-btn" data-macro="close">Закрыть глаза</button>
      <button class="macro-btn" data-macro="center">Центр</button>
      <button class="macro-btn" data-macro="home">Домой</button>
      <button class="macro-btn danger" data-macro="stop">Стоп</button>
    </div>

    <!-- Блок моргания -->
    <div class="blink-section">
      <div class="blink-row">
        <span style="font-weight:600;">Автоматическое моргание</span>
        <div class="toggle" id="autoBlinkToggle"></div>
      </div>
      <div class="control-row">
        <div class="control-label">
          <span>Интервал между морганиями (сек)</span>
          <span class="control-value" id="blinkIntervalVal">3</span>
        </div>
        <input type="range" id="blinkInterval" min="1" max="10" value="3">
      </div>
      <button class="btn" id="blinkNowBtn" style="width:100%;">Моргнуть сейчас</button>
    </div>
  </div>

  <!-- Metrics Card -->
  <div class="card metrics-card">
    <div class="card-title">Отладочная информация</div>
    <div class="card-subtitle">Метрики ESP32 в реальном времени</div>
    <div class="metrics-grid">
      <div class="metric"><div class="metric-label">Свободно памяти</div><div class="metric-value" id="mHeap">— КБ</div></div>
      <div class="metric"><div class="metric-label">Время работы</div><div class="metric-value" id="mUptime">—</div></div>
      <div class="metric"><div class="metric-label">Wi-Fi сигнал</div><div class="metric-value" id="mClients">—</div></div>
      <div class="metric"><div class="metric-label">Размер флеш-памяти</div><div class="metric-value" id="mFlash">— МБ</div></div>
    </div>
    <div style="margin-top: 16px; display: flex; gap: 8px; flex-wrap: wrap;">
      <button class="btn" id="refreshBtn">Обновить</button>
      <button class="btn danger" id="rebootBtn">Перезагрузка</button>
    </div>
  </div>

</div>

<div class="footer-info">
  Сеть: <strong>SMITeleop</strong> (STA-режим) · <strong>AP fallback: Kuzmich / 12345678</strong> &middot;
  <a href="http://esp32-control.local" style="color:var(--accent-3)">esp32-control.local</a>
</div>

<div class="team-photo-wrap">
  <img class="team-photo" src="/team.jpg" alt="Команда Агроинженеры">
</div>

<div class="signature">
  <div class="love">Сделано с любовью на агрохакатоне 2026</div>
  <div class="team">Команда «Агроинженеры»: Николай, Леонид, Ярослав, Ярослав Дикий и Матвей</div>
</div>

<div class="toast" id="toast"></div>

<script>
const ANIMATIONS = ['off','static','rainbow','rainbow_cycle','chase','breathing','theater','wipe','scanner','dual'];

let ws = null;
let wsReconnectDelay = 500;
let lastSendTimes = {};
let pendingSends = {};
let rafScheduled = false;
let syncMode = false;

function connect() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = proto + '//' + location.hostname + ':81';
  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    wsReconnectDelay = 500;
    setStatus(true);
    showToast('Подключено', 'success');
    sendCmd({cmd: 'get_state'});
  };
  ws.onclose = () => { setStatus(false); ws = null; scheduleReconnect(); };
  ws.onerror = () => { if (ws) ws.close(); };
  ws.onmessage = (e) => {
    try { handleMessage(JSON.parse(e.data)); }
    catch (err) { console.warn('parse error', err); }
  };
}

function scheduleReconnect() {
  showToast('Реконнект через ' + (wsReconnectDelay/1000) + 'с…', 'error');
  setTimeout(() => {
    wsReconnectDelay = Math.min(wsReconnectDelay * 1.5, 8000);
    connect();
  }, wsReconnectDelay);
}

function sendCmd(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function flushPending() {
  rafScheduled = false;
  const now = Date.now();
  for (const k in pendingSends) {
    const last = lastSendTimes[k] || 0;
    if (now - last >= 40) {
      sendCmd(pendingSends[k]);
      lastSendTimes[k] = now;
      delete pendingSends[k];
    }
  }
  if (Object.keys(pendingSends).length > 0) {
    rafScheduled = true;
    requestAnimationFrame(flushPending);
  }
}

function sendThrottled(key, obj) {
  pendingSends[key] = obj;
  if (!rafScheduled) {
    rafScheduled = true;
    requestAnimationFrame(flushPending);
  }
}

function handleMessage(msg) {
  if (msg.type === 'state') applyState(msg);
  else if (msg.type === 'heartbeat') updateMetrics(msg);
  else if (msg.type === 'error') showToast(msg.message || 'Ошибка', 'error');
}

function applyState(s) {
  if (s.features) {
    const card = document.getElementById('servoCard');
    if (!s.features.servos) card.classList.add('hidden');
    else {
      card.classList.remove('hidden');
      document.getElementById('servoBadge').innerHTML = '<span class="badge">включено</span>';
    }
  }
  if (s.animation) {
    document.querySelectorAll('.anim-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.anim === s.animation));
  }
  if (s.color)  { document.getElementById('colorPicker').value  = s.color; syncSlidersFromHex(s.color); }
  if (s.color2) document.getElementById('color2Picker').value = s.color2;
  if (typeof s.servo_speed === 'number') {
    document.getElementById('servoSpeed').value = s.servo_speed;
    document.getElementById('servoSpeedVal').textContent = s.servo_speed;
  }
  if (typeof s.brightness === 'number') {
    document.getElementById('brightness').value = s.brightness;
    document.getElementById('brightnessVal').textContent = s.brightness;
  }
  if (typeof s.speed === 'number') {
    document.getElementById('speed').value = s.speed;
    document.getElementById('speedVal').textContent = s.speed;
  }
  if (typeof s.servo1_angle === 'number') {
    document.getElementById('servo1').value = s.servo1_angle;
    document.getElementById('servo1Val').textContent = s.servo1_angle;
  }
  if (typeof s.servo2_angle === 'number') {
    // Уже инвертирован от сервера
    document.getElementById('servo2').value = s.servo2_angle;
    document.getElementById('servo2Val').textContent = s.servo2_angle;
  }
  if (typeof s.servo1_enabled === 'boolean')
    document.getElementById('servo1Toggle').classList.toggle('on', s.servo1_enabled);
  if (typeof s.servo2_enabled === 'boolean')
    document.getElementById('servo2Toggle').classList.toggle('on', s.servo2_enabled);
  // Автоматическое моргание
  if (typeof s.auto_blink_enabled === 'boolean') {
    document.getElementById('autoBlinkToggle').classList.toggle('on', s.auto_blink_enabled);
  }
  if (typeof s.auto_blink_interval === 'number') {
    const sec = s.auto_blink_interval;
    document.getElementById('blinkInterval').value = sec;
    document.getElementById('blinkIntervalVal').textContent = sec;
  }
  if (typeof s.flash_size === 'number')
    document.getElementById('mFlash').textContent = (s.flash_size / 1048576).toFixed(0) + ' МБ';
  updateMetrics(s);
}

function updateMetrics(s) {
  if (typeof s.heap_free === 'number')
    document.getElementById('mHeap').textContent = (s.heap_free / 1024).toFixed(1) + ' КБ';
  if (typeof s.uptime_sec === 'number')
    document.getElementById('mUptime').textContent = formatUptime(s.uptime_sec);
  if (typeof s.wifi_rssi === 'number')
    document.getElementById('mClients').textContent = s.wifi_rssi + ' dBm';
}

function formatUptime(sec) {
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (d > 0) return d + 'd ' + h + 'h';
  if (h > 0) return h + 'h ' + m + 'm';
  if (m > 0) return m + 'm ' + s + 's';
  return s + 's';
}

function setStatus(connected) {
  document.getElementById('statusDot').classList.toggle('connected', connected);
  document.getElementById('statusText').textContent = connected ? 'Подключено' : 'Отключено';
}

let toastTimer = null;
function showToast(text, kind) {
  const t = document.getElementById('toast');
  t.textContent = text;
  t.className = 'toast show' + (kind ? ' ' + kind : '');
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.className = 'toast', 2500);
}

document.querySelectorAll('.anim-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    sendCmd({cmd: 'led_animation', name: btn.dataset.anim});
    document.querySelectorAll('.anim-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  });
});

// ── Цвет + RGB палитра ──────────────────────────────────────────────
function clamp255(v) { return Math.max(0, Math.min(255, v | 0)); }
function toHex(r, g, b) {
  return '#' + [r, g, b].map(x => clamp255(x).toString(16).padStart(2, '0')).join('');
}
function syncSlidersFromHex(hex) {
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  document.getElementById('rSlider').value = r; document.getElementById('rVal').textContent = r;
  document.getElementById('gSlider').value = g; document.getElementById('gVal').textContent = g;
  document.getElementById('bSlider').value = b; document.getElementById('bVal').textContent = b;
  document.getElementById('paletteSwatch').style.background = hex;
}
function pushColorFromSliders() {
  const r = parseInt(document.getElementById('rSlider').value);
  const g = parseInt(document.getElementById('gSlider').value);
  const b = parseInt(document.getElementById('bSlider').value);
  document.getElementById('rVal').textContent = r;
  document.getElementById('gVal').textContent = g;
  document.getElementById('bVal').textContent = b;
  const hex = toHex(r, g, b);
  document.getElementById('colorPicker').value = hex;
  document.getElementById('paletteSwatch').style.background = hex;
  sendThrottled('color', {cmd: 'led_color', color: hex});
}

document.getElementById('colorPicker').addEventListener('input', (e) => {
  syncSlidersFromHex(e.target.value);
  sendThrottled('color', {cmd: 'led_color', color: e.target.value});
});
document.getElementById('color2Picker').addEventListener('input', (e) =>
  sendThrottled('color2', {cmd: 'led_color2', color: e.target.value}));
['rSlider', 'gSlider', 'bSlider'].forEach(id =>
  document.getElementById(id).addEventListener('input', pushColorFromSliders));

document.getElementById('servoSpeed').addEventListener('input', (e) => {
  document.getElementById('servoSpeedVal').textContent = e.target.value;
  sendThrottled('servospeed', {cmd: 'servo_speed', value: parseInt(e.target.value)});
});

document.getElementById('brightness').addEventListener('input', (e) => {
  document.getElementById('brightnessVal').textContent = e.target.value;
  sendThrottled('brightness', {cmd: 'led_brightness', value: parseInt(e.target.value)});
});
document.getElementById('speed').addEventListener('input', (e) => {
  document.getElementById('speedVal').textContent = e.target.value;
  sendThrottled('speed', {cmd: 'led_speed', value: parseInt(e.target.value)});
});

document.getElementById('servo1').addEventListener('input', (e) => {
  const v = parseInt(e.target.value);
  document.getElementById('servo1Val').textContent = v;
  if (syncMode) {
    document.getElementById('servo2').value = v;
    document.getElementById('servo2Val').textContent = v;
    sendThrottled('servo_both', {cmd: 'servo_both', angle: v});
  } else sendThrottled('servo1', {cmd: 'servo', which: 1, angle: v});
});
document.getElementById('servo2').addEventListener('input', (e) => {
  const v = parseInt(e.target.value);
  document.getElementById('servo2Val').textContent = v;
  if (syncMode) {
    document.getElementById('servo1').value = v;
    document.getElementById('servo1Val').textContent = v;
    sendThrottled('servo_both', {cmd: 'servo_both', angle: v});
  } else sendThrottled('servo2', {cmd: 'servo', which: 2, angle: v});
});

document.getElementById('servo1Toggle').addEventListener('click', (e) => {
  const on = !e.currentTarget.classList.contains('on');
  e.currentTarget.classList.toggle('on', on);
  sendCmd({cmd: 'servo_enable', which: 1, enabled: on});
});
document.getElementById('servo2Toggle').addEventListener('click', (e) => {
  const on = !e.currentTarget.classList.contains('on');
  e.currentTarget.classList.toggle('on', on);
  sendCmd({cmd: 'servo_enable', which: 2, enabled: on});
});

document.getElementById('syncToggle').addEventListener('click', (e) => {
  syncMode = !syncMode;
  e.currentTarget.classList.toggle('on', syncMode);
  showToast(syncMode ? 'Sync ON — оба серво вместе' : 'Sync OFF');
});

document.querySelectorAll('.preset-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const which = parseInt(btn.dataset.servo);
    const angle = parseInt(btn.dataset.angle);
    if (syncMode) {
      sendCmd({cmd: 'servo_both', angle: angle});
      document.getElementById('servo1').value = angle;
      document.getElementById('servo2').value = angle;
      document.getElementById('servo1Val').textContent = angle;
      document.getElementById('servo2Val').textContent = angle;
    } else {
      sendCmd({cmd: 'servo_preset', which: which, angle: angle});
      document.getElementById('servo' + which).value = angle;
      document.getElementById('servo' + which + 'Val').textContent = angle;
    }
  });
});

document.querySelectorAll('.macro-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const name = btn.dataset.macro;
    if (name === 'stop') { sendCmd({cmd: 'servo_macro_stop'}); showToast('Макрос остановлен'); }
    else { sendCmd({cmd: 'servo_macro', name: name}); showToast('Макрос: ' + name); }
  });
});

// ── Автоматическое моргание ──────────────────────────────────────────
document.getElementById('autoBlinkToggle').addEventListener('click', (e) => {
  const on = !e.currentTarget.classList.contains('on');
  e.currentTarget.classList.toggle('on', on);
  sendCmd({cmd: 'auto_blink', enabled: on});
  showToast(on ? 'Автоморгание включено' : 'Автоморгание выключено');
});

document.getElementById('blinkInterval').addEventListener('input', (e) => {
  const sec = parseInt(e.target.value);
  document.getElementById('blinkIntervalVal').textContent = sec;
  sendThrottled('blinkinterval', {cmd: 'auto_blink', interval: sec});
});

document.getElementById('blinkNowBtn').addEventListener('click', () => {
  sendCmd({cmd: 'blink_now'});
  showToast('Моргание');
});

document.getElementById('refreshBtn').addEventListener('click', () => {
  sendCmd({cmd: 'get_state'}); showToast('Запрошено состояние');
});
document.getElementById('rebootBtn').addEventListener('click', () => {
  if (confirm('Перезагрузить ESP32?')) { sendCmd({cmd: 'reboot'}); showToast('Перезагрузка…'); }
});

fetch('/state').then(r => r.json()).then(s => applyState(s)).catch(() => {});
connect();
</script>
</body>
</html>
)HTML";