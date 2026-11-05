#include <Arduino.h>

void setup() {
  Serial.begin(115200);
}

void loop() {
  neopixelWrite(LED_RGB_PIN, 0, 0, 32);  // dim blue
  Serial.printf("hello (uptime_s=%lu, psram_bytes=%u)\n", millis() / 1000, ESP.getPsramSize());
  delay(500);
  neopixelWrite(LED_RGB_PIN, 0, 0, 0);
  delay(500);
}
