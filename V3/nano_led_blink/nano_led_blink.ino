#include <FastLED.h>

#define DATA_PIN 6
#define SERVO_LEFT_PIN 9
#define SERVO_RIGHT_PIN 10
#define NUM_LEDS 18
#define RING_LEDS 16
#define CENTER_START 16
#define CENTER_COUNT 2
#define BRIGHTNESS 50
#define LED_TYPE WS2812B
#define COLOR_ORDER GRB

#define SERVO_CLOSED_ANGLE 45
#define SERVO_CENTER_ANGLE 90
#define SERVO_OPEN_ANGLE 135
#define SERVO_SMALL_MIN_ANGLE 75
#define SERVO_SMALL_MAX_ANGLE 105

CRGB leds[NUM_LEDS];
CRGB loadingColor = CRGB(0, 120, 255);
CRGB halfColor = CRGB(0, 120, 255);
uint8_t currentBrightness = BRIGHTNESS;
unsigned long loadingIntervalMs = 120;

char line[96];
uint8_t lineLen = 0;
uint8_t mode = 0; // 0 red, 1 primary, 2 rainbow, 3 off, 4 loading, 5 half, 6 manual, 7 loading rainbow, 8 anime friendly, 9 anime anger, 10 anime loading, 11 anime line, 12 anime loop
uint8_t colorIndex = 0;
uint8_t loadingIndex = 0;
uint8_t loadingHue = 0;
unsigned long lastStepMs = 0;
unsigned long animeLoopStartedMs = 0;
bool servoTestActive = false;
bool servoPulsesEnabled = false;
uint8_t servoMask = 3; // bit 0 left, bit 1 right
int servoAngle = SERVO_CLOSED_ANGLE;
int servoMinAngle = SERVO_CLOSED_ANGLE;
int servoMaxAngle = SERVO_OPEN_ANGLE;
int servoPulseUs = 1500;
int servoDirection = 1;
uint8_t servoPasses = 0;
unsigned long lastServoMs = 0;
unsigned long lastServoPulseMs = 0;

const CRGB primaryColors[] = {
  CRGB::Red,
  CRGB::Green,
  CRGB::Blue,
};

const CRGB rainbowColors[] = {
  CRGB::Red,
  CRGB(255, 80, 0),
  CRGB::Yellow,
  CRGB::Green,
  CRGB::Cyan,
  CRGB::Blue,
  CRGB::Purple,
  CRGB::Magenta,
};

void showSolid(const CRGB &color) {
  fill_solid(leds, NUM_LEDS, color);
  FastLED.show();
}

void setCenterLeds(const CRGB &color) {
  for (uint8_t i = CENTER_START; i < NUM_LEDS; i++) {
    leds[i] = color;
  }
}

void showLoadingFrame() {
  fill_solid(leds, NUM_LEDS, CRGB::Black);

  for (uint8_t eye = 0; eye < 2; eye++) {
    uint8_t base = eye * 8;
    uint8_t head = loadingIndex % 8;
    uint8_t tail1 = (head + 7) % 8;
    uint8_t tail2 = (head + 6) % 8;

    CRGB frameColor = loadingColor;
    if (mode == 7) {
      frameColor = CHSV(loadingHue, 255, 255);
    }
    CRGB tailColor1 = frameColor;
    CRGB tailColor2 = frameColor;
    tailColor1.nscale8(90);
    tailColor2.nscale8(28);

    leds[base + head] = frameColor;
    leds[base + tail1] = tailColor1;
    leds[base + tail2] = tailColor2;
  }

  FastLED.show();
}

void showAnimeLoadingFrame() {
  fill_solid(leds, NUM_LEDS, CRGB::Black);

  for (uint8_t eye = 0; eye < 2; eye++) {
    uint8_t base = eye * 8;
    uint8_t head = loadingIndex % 8;
    uint8_t tail1 = (head + 7) % 8;
    uint8_t tail2 = (head + 6) % 8;

    CRGB frameColor = CHSV(loadingHue, 255, 255);
    CRGB tailColor1 = frameColor;
    CRGB tailColor2 = frameColor;
    tailColor1.nscale8(90);
    tailColor2.nscale8(28);

    leds[base + head] = frameColor;
    leds[base + tail1] = tailColor1;
    leds[base + tail2] = tailColor2;
  }

  leds[CENTER_START] = CHSV(loadingHue + 64, 255, 255);
  leds[CENTER_START + 1] = CHSV(loadingHue + 160, 255, 255);
  FastLED.show();
}

void showAnimeFriendlyFrame() {
  fill_solid(leds, NUM_LEDS, CRGB::Black);

  CRGB ring = CRGB(0, 255, 60);
  ring.nscale8(28);
  for (uint8_t i = 0; i < RING_LEDS; i++) {
    leds[i] = ring;
  }

  const uint8_t pulse[] = {70, 120, 180, 255, 180, 120};
  CRGB center = CRGB(0, 255, 80);
  center.nscale8(pulse[loadingIndex % 6]);
  setCenterLeds(center);
  FastLED.show();
}

void showAnimeAngerFrame() {
  fill_solid(leds, NUM_LEDS, CRGB::Black);

  CRGB ring = CRGB(255, 0, 0);
  ring.nscale8(45);
  for (uint8_t i = 0; i < RING_LEDS; i++) {
    leds[i] = ring;
  }

  CRGB hot = CRGB(255, 0, 0);
  CRGB dim = CRGB(90, 0, 0);
  if (loadingIndex % 2 == 0) {
    leds[CENTER_START] = hot;
    leds[CENTER_START + 1] = dim;
  } else {
    leds[CENTER_START] = dim;
    leds[CENTER_START + 1] = hot;
  }
  FastLED.show();
}

void showAnimeLineFrame() {
  fill_solid(leds, NUM_LEDS, CRGB::Black);

  CRGB ring = CRGB(0, 80, 255);
  ring.nscale8(18);
  for (uint8_t i = 0; i < RING_LEDS; i++) {
    leds[i] = ring;
  }

  const CRGB colors[] = {
    CRGB(0, 180, 255),
    CRGB(255, 255, 255),
    CRGB(255, 0, 160),
    CRGB(0, 255, 120),
  };
  CRGB left = colors[loadingIndex % 4];
  CRGB right = colors[(loadingIndex + 1) % 4];
  leds[CENTER_START] = left;
  leds[CENTER_START + 1] = right;
  FastLED.show();
}

void showAnimeFrame(uint8_t animeMode) {
  if (animeMode == 8) {
    showAnimeFriendlyFrame();
  } else if (animeMode == 9) {
    showAnimeAngerFrame();
  } else if (animeMode == 10) {
    showAnimeLoadingFrame();
  } else if (animeMode == 11) {
    showAnimeLineFrame();
  }
}

void showHalf() {
  fill_solid(leds, NUM_LEDS, CRGB::Black);

  for (uint8_t eye = 0; eye < 2; eye++) {
    uint8_t base = eye * 8;
    leds[base + 0] = halfColor;
    leds[base + 1] = halfColor;
    leds[base + 2] = halfColor;
    leds[base + 3] = halfColor;
  }

  FastLED.show();
}

void setServos(int angle) {
  angle = constrain(angle, 0, 180);
  servoPulseUs = map(angle, 0, 180, 1000, 2000);
  servoPulsesEnabled = true;
}

void refreshServoPulses() {
  if (!servoPulsesEnabled) {
    return;
  }

  unsigned long now = millis();
  if (now - lastServoPulseMs < 20) {
    return;
  }
  lastServoPulseMs = now;

  if (servoMask & 1) digitalWrite(SERVO_LEFT_PIN, HIGH);
  if (servoMask & 2) digitalWrite(SERVO_RIGHT_PIN, HIGH);
  delayMicroseconds(servoPulseUs);
  digitalWrite(SERVO_LEFT_PIN, LOW);
  digitalWrite(SERVO_RIGHT_PIN, LOW);
}

void stopServos() {
  servoTestActive = false;
  servoPulsesEnabled = false;
  digitalWrite(SERVO_LEFT_PIN, LOW);
  digitalWrite(SERVO_RIGHT_PIN, LOW);
}

void startServoTest(uint8_t mask, int minAngle = SERVO_CLOSED_ANGLE, int maxAngle = SERVO_OPEN_ANGLE) {
  servoMask = mask;
  servoTestActive = true;
  servoMinAngle = minAngle;
  servoMaxAngle = maxAngle;
  servoAngle = servoMinAngle;
  servoDirection = 1;
  servoPasses = 0;
  lastServoMs = 0;
  setServos(servoAngle);
}

void updateServos() {
  if (!servoTestActive) {
    return;
  }

  unsigned long now = millis();
  if (now - lastServoMs < 20) {
    return;
  }
  lastServoMs = now;

  servoAngle += servoDirection;

  if (servoAngle >= servoMaxAngle) {
    servoAngle = servoMaxAngle;
    servoDirection = -1;
    servoPasses++;
  } else if (servoAngle <= servoMinAngle) {
    servoAngle = servoMinAngle;
    servoDirection = 1;
    servoPasses++;
  }

  setServos(servoAngle);

  if (servoPasses >= 6) {
    servoTestActive = false;
    setServos(SERVO_CENTER_ANGLE);
    Serial.println("OK SERVOS DONE");
  }
}

void setMode(uint8_t newMode) {
  mode = newMode;
  colorIndex = 0;
  loadingIndex = 0;
  loadingHue = 0;
  lastStepMs = 0;
  animeLoopStartedMs = millis();

  if (mode == 0) {
    showSolid(CRGB::Red);
  } else if (mode == 3) {
    showSolid(CRGB::Black);
  } else if (mode == 4 || mode == 7) {
    showLoadingFrame();
  } else if (mode >= 8 && mode <= 11) {
    showAnimeFrame(mode);
  } else if (mode == 12) {
    colorIndex = 0;
    showAnimeFrame(8);
  } else if (mode == 5) {
    showHalf();
  }
}

void updateMode() {
  if (mode == 0 || mode == 3 || mode == 5 || mode == 6) {
    return;
  }

  unsigned long now = millis();
  unsigned long interval = 700;
  if (mode == 1) interval = 2000;
  if (mode == 4 || mode == 7) interval = loadingIntervalMs;
  if (mode >= 8 && mode <= 12) interval = loadingIntervalMs;

  if (now - lastStepMs < interval) {
    return;
  }

  lastStepMs = now;

  if (mode == 4 || mode == 7) {
    loadingIndex = (loadingIndex + 1) % 8;
    if (mode == 7) {
      loadingHue += 8;
    }
    showLoadingFrame();
    return;
  }

  if (mode >= 8 && mode <= 12) {
    loadingIndex++;
    loadingHue += 8;
    uint8_t animeMode = mode;
    if (mode == 12) {
      if (now - animeLoopStartedMs >= 3000) {
        colorIndex = (colorIndex + 1) % 4;
        animeLoopStartedMs = now;
        loadingIndex = 0;
      }
      animeMode = 8 + colorIndex;
    }
    showAnimeFrame(animeMode);
    return;
  }

  if (mode == 1) {
    showSolid(primaryColors[colorIndex]);
    colorIndex = (colorIndex + 1) % (sizeof(primaryColors) / sizeof(primaryColors[0]));
  } else if (mode == 2) {
    showSolid(rainbowColors[colorIndex]);
    colorIndex = (colorIndex + 1) % (sizeof(rainbowColors) / sizeof(rainbowColors[0]));
  }
}

void handleLine(char *cmd) {
  int a, b, c, d, e;

  if (sscanf(cmd, "BRIGHTNESS %d", &a) == 1) {
    currentBrightness = constrain(a, 0, 255);
    FastLED.setBrightness(currentBrightness);
    FastLED.show();
    Serial.println("OK BRIGHTNESS");
    return;
  }

  if (sscanf(cmd, "RGB %d %d %d", &a, &b, &c) == 3) {
    mode = 6;
    showSolid(CRGB(constrain(a, 0, 255), constrain(b, 0, 255), constrain(c, 0, 255)));
    Serial.println("OK RGB");
    return;
  }

  if (sscanf(cmd, "PIXEL %d %d %d %d", &a, &b, &c, &d) == 4) {
    if (a < 0 || a >= NUM_LEDS) {
      Serial.println("ERR PIXEL");
      return;
    }
    mode = 6;
    leds[a] = CRGB(constrain(b, 0, 255), constrain(c, 0, 255), constrain(d, 0, 255));
    FastLED.show();
    Serial.println("OK PIXEL");
    return;
  }

  if (sscanf(cmd, "FILL %d %d %d %d %d", &a, &b, &c, &d, &e) == 5) {
    if (a < 0 || a >= NUM_LEDS || b < 1) {
      Serial.println("ERR FILL");
      return;
    }
    mode = 6;
    int endIndex = min(NUM_LEDS, a + b);
    for (int i = a; i < endIndex; i++) {
      leds[i] = CRGB(constrain(c, 0, 255), constrain(d, 0, 255), constrain(e, 0, 255));
    }
    FastLED.show();
    Serial.println("OK FILL");
    return;
  }

  if (strcmp(cmd, "CLEAR") == 0) {
    mode = 6;
    showSolid(CRGB::Black);
    Serial.println("OK CLEAR");
    return;
  }

  if (sscanf(cmd, "LOADING_COLOR %d %d %d", &a, &b, &c) == 3) {
    loadingColor = CRGB(constrain(a, 0, 255), constrain(b, 0, 255), constrain(c, 0, 255));
    if (mode == 4) showLoadingFrame();
    Serial.println("OK LOADING_COLOR");
    return;
  }

  if (sscanf(cmd, "LOADING_SPEED %d", &a) == 1) {
    loadingIntervalMs = constrain(a, 30, 2000);
    Serial.println("OK LOADING_SPEED");
    return;
  }

  if (sscanf(cmd, "HALF_COLOR %d %d %d", &a, &b, &c) == 3) {
    halfColor = CRGB(constrain(a, 0, 255), constrain(b, 0, 255), constrain(c, 0, 255));
    if (mode == 5) showHalf();
    Serial.println("OK HALF_COLOR");
    return;
  }

  if (strcmp(cmd, "RED") == 0) {
    setMode(0);
    Serial.println("OK RED");
    return;
  }

  if (strcmp(cmd, "PRIMARY") == 0) {
    setMode(1);
    updateMode();
    Serial.println("OK PRIMARY");
    return;
  }

  if (strcmp(cmd, "RAINBOW") == 0) {
    setMode(2);
    updateMode();
    Serial.println("OK RAINBOW");
    return;
  }

  if (strcmp(cmd, "OFF") == 0) {
    setMode(3);
    Serial.println("OK OFF");
    return;
  }

  if (strcmp(cmd, "LOADING") == 0) {
    setMode(4);
    Serial.println("OK LOADING");
    return;
  }

  if (strcmp(cmd, "LOADING_RAINBOW") == 0) {
    setMode(7);
    Serial.println("OK LOADING_RAINBOW");
    return;
  }

  if (strcmp(cmd, "CENTER_GREEN") == 0) {
    mode = 6;
    setCenterLeds(CRGB(0, 255, 0));
    FastLED.show();
    Serial.println("OK CENTER_GREEN");
    return;
  }

  if (strcmp(cmd, "CENTER_RED") == 0) {
    mode = 6;
    setCenterLeds(CRGB(255, 0, 0));
    FastLED.show();
    Serial.println("OK CENTER_RED");
    return;
  }

  if (strcmp(cmd, "CENTER_WHITE") == 0) {
    mode = 6;
    setCenterLeds(CRGB(255, 255, 255));
    FastLED.show();
    Serial.println("OK CENTER_WHITE");
    return;
  }

  if (strcmp(cmd, "CENTER_OFF") == 0) {
    mode = 6;
    setCenterLeds(CRGB::Black);
    FastLED.show();
    Serial.println("OK CENTER_OFF");
    return;
  }

  if (strcmp(cmd, "ANIME_FRIENDLY") == 0) {
    setMode(8);
    Serial.println("OK ANIME_FRIENDLY");
    return;
  }

  if (strcmp(cmd, "ANIME_ANGER") == 0) {
    setMode(9);
    Serial.println("OK ANIME_ANGER");
    return;
  }

  if (strcmp(cmd, "ANIME_LOADING") == 0) {
    setMode(10);
    Serial.println("OK ANIME_LOADING");
    return;
  }

  if (strcmp(cmd, "ANIME_LINE") == 0) {
    setMode(11);
    Serial.println("OK ANIME_LINE");
    return;
  }

  if (strcmp(cmd, "ANIME_LOOP") == 0) {
    setMode(12);
    Serial.println("OK ANIME_LOOP");
    return;
  }

  if (strcmp(cmd, "HALF") == 0) {
    setMode(5);
    Serial.println("OK HALF");
    return;
  }

  if (strcmp(cmd, "SERVOS") == 0) {
    startServoTest(3);
    Serial.println("OK SERVOS");
    return;
  }

  if (strcmp(cmd, "SERVOS_30") == 0) {
    startServoTest(3, SERVO_SMALL_MIN_ANGLE, SERVO_SMALL_MAX_ANGLE);
    Serial.println("OK SERVOS_30");
    return;
  }

  if (strcmp(cmd, "SERVO_LEFT") == 0) {
    startServoTest(1);
    Serial.println("OK SERVO_LEFT");
    return;
  }

  if (strcmp(cmd, "SERVO_RIGHT") == 0) {
    startServoTest(2);
    Serial.println("OK SERVO_RIGHT");
    return;
  }

  if (strcmp(cmd, "CENTER") == 0) {
    servoTestActive = false;
    servoMask = 3;
    setServos(SERVO_CENTER_ANGLE);
    Serial.println("OK CENTER");
    return;
  }

  if (strcmp(cmd, "OPEN") == 0) {
    servoTestActive = false;
    servoMask = 3;
    setServos(SERVO_OPEN_ANGLE);
    Serial.println("OK OPEN");
    return;
  }

  if (strcmp(cmd, "CLOSE") == 0) {
    servoTestActive = false;
    servoMask = 3;
    setServos(SERVO_CLOSED_ANGLE);
    Serial.println("OK CLOSE");
    return;
  }

  if (strcmp(cmd, "SERVO_OFF") == 0) {
    stopServos();
    Serial.println("OK SERVO_OFF");
    return;
  }

  Serial.println("ERR");
}

void setup() {
  Serial.begin(115200);
  pinMode(SERVO_LEFT_PIN, OUTPUT);
  pinMode(SERVO_RIGHT_PIN, OUTPUT);
  stopServos();
  FastLED.addLeds<LED_TYPE, DATA_PIN, COLOR_ORDER>(leds, NUM_LEDS);
  FastLED.setBrightness(BRIGHTNESS);
  FastLED.setMaxPowerInVoltsAndMilliamps(5, 300);
  setMode(0);
  Serial.println("Nano ready. Commands: RED, PRIMARY, RAINBOW, LOADING, LOADING_RAINBOW, ANIME_FRIENDLY, ANIME_ANGER, ANIME_LOADING, ANIME_LINE, ANIME_LOOP, CENTER_GREEN, CENTER_RED, CENTER_WHITE, CENTER_OFF, HALF, OFF, CLEAR, RGB, BRIGHTNESS, PIXEL, FILL, LOADING_COLOR, LOADING_SPEED, HALF_COLOR, SERVOS, SERVOS_30, SERVO_LEFT, SERVO_RIGHT, CENTER, OPEN, CLOSE, SERVO_OFF");
}

void loop() {
  updateMode();
  updateServos();
  refreshServoPulses();

  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (lineLen > 0) {
        line[lineLen] = 0;
        handleLine(line);
        lineLen = 0;
      }
    } else if (lineLen < sizeof(line) - 1) {
      line[lineLen++] = c;
    }
  }
}
