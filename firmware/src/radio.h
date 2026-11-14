// CC1101 access for the reference node: init, RSSI sweep and OOK pulse capture.
#pragma once
#include <Arduino.h>

#include <functional>

namespace radio {

// Band the node accepts (capabilities "bands_mhz"). The module is matched for 433 MHz.
constexpr float BAND_MIN_MHZ = 387.0;
constexpr float BAND_MAX_MHZ = 464.0;

// One captured transmission: alternating pulse/gap durations, starting and ending with a pulse.
struct Frame {
  const uint32_t* t_us;
  size_t len;
  int16_t peak_rssi_dbm;
  bool truncated;
};

struct ListenResult {
  int16_t state;  // RadioLib status code, 0 = OK
  int frames;
  int discarded;  // detected by RSSI but dropped for having too few pulses
  int16_t noise_floor_dbm;
  int16_t threshold_dbm;
};

// Checks the chip over SPI and configures it. `version` gets the CC1101 VERSION register.
// Returns a RadioLib status code (0 = OK).
int16_t begin(uint8_t& version);

// Reads RSSI at start_mhz + i * step_khz for i in [0, points). Returns a RadioLib status code.
int16_t scan(float start_mhz, float step_khz, int points, int16_t* rssi_dbm);

// Listens in OOK mode for `seconds` and calls on_frame for every frame detected.
ListenResult listen(float freq_mhz, uint32_t seconds, bool has_threshold, int16_t threshold_dbm,
                    const std::function<void(const Frame&)>& on_frame);

}  // namespace radio
