// Phase 1: check the CC1101 wiring, then print live RSSI at 433.92 MHz every 100 ms.
#include <Arduino.h>
#include <RadioLib.h>
#include <SPI.h>

// CC1101 -> ESP32-S3 wiring (PROPOSAL.md §7.3)
constexpr int PIN_SCK = 12;
constexpr int PIN_MISO = 13;
constexpr int PIN_MOSI = 11;
constexpr int PIN_CSN = 10;
constexpr int PIN_GDO0 = 4;
constexpr int PIN_GDO2 = 5;

constexpr float FREQ_MHZ = 433.92;

CC1101 radio = new Module(PIN_CSN, PIN_GDO0, RADIOLIB_NC, PIN_GDO2);

// Read a CC1101 status register directly over SPI (header byte: read + burst bits set).
uint8_t readStatusRegister(uint8_t addr) {
  SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));
  digitalWrite(PIN_CSN, LOW);
  // The chip pulls MISO low when it is ready. Give up after 10 ms (e.g. MISO not wired).
  uint32_t start = millis();
  while (digitalRead(PIN_MISO) == HIGH && millis() - start < 10) {
  }
  SPI.transfer(addr | 0xC0);
  uint8_t value = SPI.transfer(0x00);
  digitalWrite(PIN_CSN, HIGH);
  SPI.endTransaction();
  return value;
}

// Repeat the message so a serial monitor attached after boot still sees it.
void halt(const char* msg) {
  while (true) {
    Serial.println(msg);
    delay(1000);
  }
}

void setup() {
  Serial.begin(115200);
  delay(2000);  // give the USB serial monitor time to attach

  pinMode(PIN_CSN, OUTPUT);
  digitalWrite(PIN_CSN, HIGH);

  // Wiring check: with CSN low, a powered CC1101 pulls MISO low ("chip ready").
  pinMode(PIN_MISO, INPUT_PULLUP);
  delay(1);
  int miso_csn_high = digitalRead(PIN_MISO);
  digitalWrite(PIN_CSN, LOW);
  delay(1);
  int miso_csn_low = digitalRead(PIN_MISO);
  digitalWrite(PIN_CSN, HIGH);
  Serial.printf("MISO with CSN high=%d (expected 1), CSN low=%d (expected 0)\n", miso_csn_high,
                miso_csn_low);

  SPI.begin(PIN_SCK, PIN_MISO, PIN_MOSI, PIN_CSN);

  uint8_t partnum = readStatusRegister(0x30);
  uint8_t version = readStatusRegister(0x31);
  Serial.printf("PARTNUM=0x%02X (expected 0x00)\n", partnum);
  Serial.printf("VERSION=0x%02X (expected 0x14, 0x04, 0x17 or 0x03)\n", version);
  if (version == 0x00 || version == 0xFF) {
    halt("CC1101 not responding: check VCC, GND and the SPI wires.");
  }

  int16_t state = radio.begin(FREQ_MHZ);
  if (state == RADIOLIB_ERR_NONE) state = radio.setOOK(true);
  if (state == RADIOLIB_ERR_NONE) state = radio.receiveDirectAsync();
  if (state != RADIOLIB_ERR_NONE) {
    Serial.printf("RadioLib error %d\n", state);
    halt("Radio setup failed.");
  }
  Serial.printf("Listening at %.2f MHz (OOK). Press a 433 MHz remote nearby.\n", FREQ_MHZ);
}

void loop() {
  float rssi_dbm = radio.getRSSI();
  int bar = constrain(map((long)rssi_dbm, -110, -30, 0, 40), 0, 40);
  char bars[41];
  memset(bars, '#', bar);
  bars[bar] = '\0';
  // MARCSTATE 0x0D = RX. Anything else means the chip reset or left RX (power/wiring glitch).
  uint8_t marcstate = readStatusRegister(0x35) & 0x1F;
  Serial.printf("rssi_dbm=%6.1f marcstate=0x%02X %s\n", rssi_dbm, marcstate, bars);
  delay(100);
}
