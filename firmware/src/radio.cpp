#include "radio.h"

#include <RadioLib.h>
#include <SPI.h>

namespace radio {
namespace {

// CC1101 -> ESP32-S3 wiring (PROPOSAL.md §7.3)
constexpr int PIN_SCK = 12;
constexpr int PIN_MISO = 13;
constexpr int PIN_MOSI = 11;
constexpr int PIN_CSN = 10;
constexpr int PIN_GDO0 = 4;
constexpr int PIN_GDO2 = 5;

// Receiver filter width. Narrow for sweeps (sharper peaks), wide for pulses (cheap remotes are
// often 50-100 kHz off their nominal frequency).
constexpr float SCAN_BW_KHZ = 58;
constexpr float PULSES_BW_KHZ = 270;

// After each retune: calibration (~0.8 ms) plus time for the RSSI reading to settle.
constexpr uint32_t SETTLE_US = 1500;

// Frame detection (docs/protocol.md §4, rx_pulses).
// 10 dB let noise spikes through with the 270 kHz filter (hundreds of false triggers in 10 s).
constexpr int16_t THRESHOLD_MARGIN_DB = 15;
constexpr uint32_t SILENCE_US = 10000;
constexpr uint32_t PRE_US = 2000;
constexpr uint32_t MAX_FRAME_US = 2000000;
constexpr size_t MAX_DURATIONS = 4000;
constexpr size_t MIN_PULSES = 8;

// Edge ring buffer filled by the GDO0 interrupt. Size must be a power of two.
constexpr uint32_t RING = 8192;

CC1101 cc = new Module(PIN_CSN, PIN_GDO0, RADIOLIB_NC, PIN_GDO2);

volatile uint32_t ring_t[RING];
volatile uint8_t ring_level[RING];
volatile uint32_t ring_head = 0;
uint32_t durations[MAX_DURATIONS];

// In async mode GDO0 is the demodulated signal: high = carrier on. Record every edge.
void IRAM_ATTR onEdge() {
  uint32_t i = ring_head & (RING - 1);
  ring_t[i] = micros();
  ring_level[i] = digitalRead(PIN_GDO0);
  ring_head = ring_head + 1;
}

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

// Write a CC1101 configuration register directly over SPI (RadioLib keeps its writer private).
void writeRegister(uint8_t addr, uint8_t value) {
  SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));
  digitalWrite(PIN_CSN, LOW);
  uint32_t start = millis();
  while (digitalRead(PIN_MISO) == HIGH && millis() - start < 10) {
  }
  SPI.transfer(addr);
  SPI.transfer(value);
  digitalWrite(PIN_CSN, HIGH);
  SPI.endTransaction();
}

int16_t readRssi() { return (int16_t)lroundf(cc.getRSSI()); }

// Turns the edges recorded between from_us and to_us into pulse/gap durations.
// Returns false if the result is too short to be a real transmission.
bool buildFrame(uint32_t from_us, uint32_t to_us, Frame& frame) {
  noInterrupts();
  uint32_t head = ring_head;
  interrupts();
  uint32_t tail = head > RING ? head - RING : 0;

  size_t n = 0;
  bool started = false;
  uint32_t prev_t = 0;
  uint8_t prev_level = 0;
  for (uint32_t k = tail; k < head; k++) {
    uint32_t i = k & (RING - 1);
    uint32_t t = ring_t[i];
    uint8_t level = ring_level[i];
    if ((int32_t)(t - from_us) < 0) continue;
    if ((int32_t)(t - to_us) > 0) break;
    if (!started) {
      // Start at the first rising edge so the sequence begins with a pulse.
      if (level == 1) {
        started = true;
        prev_t = t;
        prev_level = 1;
      }
      continue;
    }
    if (level == prev_level) continue;  // missed an edge: keep extending the current run
    if (n == MAX_DURATIONS) {
      frame.truncated = true;
      break;
    }
    durations[n++] = t - prev_t;
    prev_t = t;
    prev_level = level;
  }
  if (n % 2 == 0 && n > 0) n--;  // drop a trailing gap: frames end with a pulse

  frame.t_us = durations;
  frame.len = n;
  return (n + 1) / 2 >= MIN_PULSES;
}

}  // namespace

int16_t begin(uint8_t& version) {
  pinMode(PIN_CSN, OUTPUT);
  digitalWrite(PIN_CSN, HIGH);
  pinMode(PIN_MISO, INPUT_PULLUP);
  SPI.begin(PIN_SCK, PIN_MISO, PIN_MOSI, PIN_CSN);

  version = readStatusRegister(0x31);
  if (version == 0x00 || version == 0xFF) return RADIOLIB_ERR_CHIP_NOT_FOUND;

  int16_t state = cc.begin(433.92);
  if (state == RADIOLIB_ERR_NONE) state = cc.setOOK(true);
  if (state == RADIOLIB_ERR_NONE) state = cc.standby();
  // Automatic gain control tuned for OOK reception (TI design note DN022). With the chip
  // defaults, the demodulated output on GDO0 is mostly noise even with a strong signal.
  writeRegister(RADIOLIB_CC1101_REG_AGCCTRL2, 0x07);
  writeRegister(RADIOLIB_CC1101_REG_AGCCTRL1, 0x00);
  writeRegister(RADIOLIB_CC1101_REG_AGCCTRL0, 0x91);
  return state;
}

int16_t scan(float start_mhz, float step_khz, int points, int16_t* rssi_dbm) {
  int16_t state = cc.setRxBandwidth(SCAN_BW_KHZ);
  for (int i = 0; i < points && state == RADIOLIB_ERR_NONE; i++) {
    state = cc.setFrequency(start_mhz + i * step_khz / 1000.0f);
    if (state == RADIOLIB_ERR_NONE) state = cc.receiveDirectAsync();
    delayMicroseconds(SETTLE_US);
    rssi_dbm[i] = readRssi();
  }
  cc.standby();
  return state;
}

ListenResult listen(float freq_mhz, uint32_t seconds, bool has_threshold, int16_t threshold_dbm,
                    const std::function<void(const Frame&)>& on_frame) {
  ListenResult result{};
  result.state = cc.setRxBandwidth(PULSES_BW_KHZ);
  if (result.state == RADIOLIB_ERR_NONE) result.state = cc.setFrequency(freq_mhz);
  if (result.state == RADIOLIB_ERR_NONE) result.state = cc.receiveDirectAsync();
  if (result.state != RADIOLIB_ERR_NONE) return result;
  delayMicroseconds(SETTLE_US);

  // Noise floor: average RSSI over ~50 ms.
  int32_t sum = 0;
  for (int i = 0; i < 50; i++) {
    sum += readRssi();
    delay(1);
  }
  result.noise_floor_dbm = sum / 50;
  result.threshold_dbm = has_threshold ? threshold_dbm : result.noise_floor_dbm + THRESHOLD_MARGIN_DB;

  ring_head = 0;
  attachInterrupt(digitalPinToInterrupt(PIN_GDO0), onEdge, CHANGE);

  bool in_frame = false;
  uint32_t start_us = 0, last_us = 0;
  int16_t peak = 0;
  uint32_t end_ms = millis() + seconds * 1000;
  auto finish = [&](uint32_t to_us, bool truncated) {
    Frame frame{nullptr, 0, peak, truncated};
    if (buildFrame(start_us - PRE_US, to_us, frame)) {
      result.frames++;
      on_frame(frame);
    } else {
      result.discarded++;
    }
    in_frame = false;
  };

  while ((int32_t)(millis() - end_ms) < 0) {
    int16_t rssi = readRssi();
    uint32_t now = micros();
    if (rssi >= result.threshold_dbm) {
      if (!in_frame) {
        in_frame = true;
        start_us = now;
        peak = rssi;
      }
      last_us = now;
      if (rssi > peak) peak = rssi;
    }
    if (in_frame && now - last_us > SILENCE_US) finish(last_us, false);
    if (in_frame && now - start_us > MAX_FRAME_US) finish(now, true);
  }
  if (in_frame) finish(micros(), true);

  detachInterrupt(digitalPinToInterrupt(PIN_GDO0));
  cc.standby();
  return result;
}

}  // namespace radio
