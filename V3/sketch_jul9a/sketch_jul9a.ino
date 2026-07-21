
#include <Adafruit_NeoPixel.h>

#define LED_PIN 4
#define LED_COUNT 48   // 2 кольца по 24 светодиода

Adafruit_NeoPixel strip(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);

void setAll(uint8_t r, uint8_t g, uint8_t b) {
  for (int i = 0; i < LED_COUNT; i++) {
    strip.setPixelColor(i, strip.Color(r, g, b));
  }
  strip.show();
}

void setup() {
  strip.begin();
  strip.setBrightness(80);
  strip.clear();
  strip.show();
}

void loop() {
  setAll(255, 0, 0);   // красный
  delay(500);

  setAll(0, 0, 0);     // выкл
  delay(300);

  setAll(0, 255, 0);   // зеленый
  delay(500);

  setAll(0, 0, 0);
  delay(300);

  setAll(0, 0, 255);   // синий
  delay(500);

  setAll(0, 0, 0);
  delay(300);

  setAll(255, 255, 255); // белый
  delay(500);

  setAll(0, 0, 0);
  delay(700);
}
