#ifndef CONFIG_H
#define CONFIG_H

// ═══════════════════════════════════════════════════════════════════════
//  config.h — пины / фичи / лимиты для ESP32-C3 (DevKitM-1)
//  Значения можно переопределить -D флагами в platformio.ini.
// ═══════════════════════════════════════════════════════════════════════

// ─── Feature flags ────────────────────────────────────────────────────
#ifndef ENABLE_SERVOS
#define ENABLE_SERVOS  true
#endif

// ─── WiFi (режим STA — клиент сети SMITeleop) ────────────────────────
#ifndef STA_SSID
#define STA_SSID "SMITeleop"
#endif
#ifndef STA_PASS
#define STA_PASS "SMITSMIT"
#endif

// ─── Hostname для mDNS ────────────────────────────────────────────────
#ifndef MDNS_NAME
#define MDNS_NAME "esp32-control"
#endif

// ─── Пины (ESP32-C3 DevKitM-1) ────────────────────────────────────────
#ifndef LED_PIN
#define LED_PIN       10
#endif
#ifndef LED_COUNT
#define LED_COUNT     14
#endif

#if ENABLE_SERVOS
  #ifndef SERVO1_PIN
  #define SERVO1_PIN    4             // левый глаз
  #endif
  #ifndef SERVO2_PIN
  #define SERVO2_PIN    2             // правый глаз
  #endif
  #ifndef SERVO_MIN_US
  #define SERVO_MIN_US  500
  #endif
  #ifndef SERVO_MAX_US
  #define SERVO_MAX_US  2400
  #endif
  #ifndef SERVO_FREQ
  #define SERVO_FREQ    50
  #endif

  // Новые калибровочные углы (замерены пользователем)
  #ifndef EYE_LEFT_OPEN
  #define EYE_LEFT_OPEN    147
  #endif
  #ifndef EYE_LEFT_CLOSED
  #define EYE_LEFT_CLOSED   46
  #endif
  #ifndef EYE_RIGHT_OPEN
  #define EYE_RIGHT_OPEN   28
  #endif
  #ifndef EYE_RIGHT_CLOSED
  #define EYE_RIGHT_CLOSED  127
  #endif

  #ifndef DEFAULT_SERVO_SPEED
  #define DEFAULT_SERVO_SPEED 60
  #endif
#endif

// ─── Лимиты ───────────────────────────────────────────────────────────
#ifndef MAX_WS_CLIENTS
#define MAX_WS_CLIENTS    4
#endif
#ifndef STATE_SAVE_MS
#define STATE_SAVE_MS     1000
#endif
#ifndef WS_HEARTBEAT_MS
#define WS_HEARTBEAT_MS   30000
#endif

// ─── Значения по умолчанию (если NVS пуст) ────────────────────────────
#ifndef DEFAULT_ANIMATION
#define DEFAULT_ANIMATION  "rainbow_cycle"
#endif
#ifndef DEFAULT_BRIGHTNESS
#define DEFAULT_BRIGHTNESS 80
#endif
#ifndef DEFAULT_SPEED
#define DEFAULT_SPEED      30
#endif
#ifndef DEFAULT_COLOR
#define DEFAULT_COLOR      0xFF00FF
#endif
#ifndef DEFAULT_COLOR2
#define DEFAULT_COLOR2     0x000000
#endif

#ifndef DEFAULT_AUTO_BLINK_ENABLED
#define DEFAULT_AUTO_BLINK_ENABLED false
#endif
#ifndef DEFAULT_AUTO_BLINK_INTERVAL
#define DEFAULT_AUTO_BLINK_INTERVAL 3000   // мс
#endif

// Fallback AP (если STA не подключился — для отладки)
#ifndef AP_FALLBACK_SSID
#define AP_FALLBACK_SSID "Kuzmich"
#endif
#ifndef AP_FALLBACK_PASS
#define AP_FALLBACK_PASS "12345678"
#endif
#endif // CONFIG_H