// eter reference node: node protocol v1 over USB serial and Wi-Fi (docs/protocol.md).
#include <Arduino.h>
#include <ArduinoJson.h>
#include <esp_system.h>
#include <esp_task_wdt.h>

#include "net.h"
#include "radio.h"
#include "transport.h"

using transport::Channel;

constexpr const char* FW_VERSION = "0.2.0";
constexpr int PROTOCOL_V = 1;
constexpr const char* RADIO_ID = "r433";
constexpr int MAX_POINTS = 1001;
// Restart if the main loop hangs this long. Long commands (rx_pulses: up to 60 s) feed the
// watchdog while they run.
constexpr uint32_t WATCHDOG_S = 30;

char node_id[16];
bool radio_ok = false;
int16_t spectrum[MAX_POINTS];
// Link the command being handled came from: its replies go back there.
Channel reply_to = Channel::USB;

void sendTo(Channel to, const JsonDocument& doc) {
  String text;
  serializeJson(doc, text);
  transport::send(to, text);
}

void send(const JsonDocument& doc) { sendTo(reply_to, doc); }

// Unsolicited messages go to USB and, when connected, to the bridge.
void broadcast(const JsonDocument& doc) {
  sendTo(Channel::USB, doc);
  if (transport::wsConnected()) sendTo(Channel::WS, doc);
}

// Message skeleton: {"v":0, "re":<id>, "type":...}. "re" only when the request had an "id".
JsonDocument message(const char* type, JsonVariantConst id) {
  JsonDocument doc;
  doc["v"] = PROTOCOL_V;
  if (!id.isNull()) doc["re"] = id;
  doc["type"] = type;
  return doc;
}

void sendError(JsonVariantConst id, const char* code, const String& msg) {
  JsonDocument doc = message("error", id);
  doc["code"] = code;
  doc["msg"] = msg;
  send(doc);
}

void sendLog(const char* level, const String& msg) {
  JsonDocument doc = message("log", JsonVariantConst());
  doc["level"] = level;
  doc["msg"] = msg;
  broadcast(doc);
}

// The pairing token only ever travels over the WebSocket (docs/protocol.md §1.2).
void sendHello(Channel to, JsonVariantConst id) {
  JsonDocument doc = message("hello", id);
  doc["node_id"] = node_id;
  doc["fw"] = FW_VERSION;
  if (to == Channel::WS) {
    String token = net::token();
    if (!token.isEmpty()) doc["token"] = token;
  }
  JsonObject caps = doc["capabilities"].to<JsonObject>();
  JsonArray radios = caps["radios"].to<JsonArray>();
  if (radio_ok) {
    JsonObject r = radios.add<JsonObject>();
    r["id"] = RADIO_ID;
    r["chip"] = "cc1101";
    JsonArray band = r["bands_mhz"].to<JsonArray>().add<JsonArray>();
    band.add(radio::BAND_MIN_MHZ);
    band.add(radio::BAND_MAX_MHZ);
    r["tuned_mhz"] = 433;
    r["modulations"].to<JsonArray>().add("ook");
    r["rx"] = true;
    r["tx"] = false;
    JsonArray features = r["features"].to<JsonArray>();
    features.add("rssi_scan");
    features.add("rx_pulses");
  }
  caps["builtin"].to<JsonArray>();
  sendTo(to, doc);
}

const char* resetReason() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON:
      return "poweron";
    case ESP_RST_SW:
      return "software";
    case ESP_RST_PANIC:
      return "panic";
    case ESP_RST_INT_WDT:
    case ESP_RST_TASK_WDT:
    case ESP_RST_WDT:
      return "watchdog";
    case ESP_RST_BROWNOUT:
      return "brownout";
    default:
      return "other";
  }
}

void handleStatus(JsonVariantConst id) {
  JsonDocument doc = message("status", id);
  doc["fw"] = FW_VERSION;
  doc["uptime_s"] = millis() / 1000;
  doc["heap_free"] = ESP.getFreeHeap();
  JsonArray radios = doc["radios"].to<JsonArray>();
  if (radio_ok) radios.add(RADIO_ID);
  doc["reset_reason"] = resetReason();
  int wifi_rssi;
  if (net::wifiRssi(wifi_rssi)) doc["wifi_rssi_dbm"] = wifi_rssi;
  send(doc);
}

bool isToken(const char* token) {
  if (strlen(token) != 32) return false;
  for (const char* c = token; *c; c++) {
    if (!isdigit(*c) && (*c < 'a' || *c > 'f')) return false;
  }
  return true;
}

void handlePair(const JsonDocument& req, JsonVariantConst id) {
  if (reply_to != Channel::WS) {
    sendError(id, "UNSUPPORTED", "pair is only accepted from the bridge over Wi-Fi");
    return;
  }
  const char* token = req["token"] | "";
  if (!isToken(token)) {
    sendError(id, "INVALID", "token must be 32 lowercase hex characters");
    return;
  }
  net::setToken(token);
  sendLog("info", "paired with the bridge");
  send(message("paired", id));
}

bool inBand(float mhz) { return mhz >= radio::BAND_MIN_MHZ && mhz <= radio::BAND_MAX_MHZ; }

// Validates the "radio" parameter. Sends the error and returns false if it is not usable.
bool checkRadio(const JsonDocument& req, JsonVariantConst id) {
  if (!req["radio"].is<const char*>()) {
    sendError(id, "INVALID", "radio is required");
    return false;
  }
  if (strcmp(req["radio"], RADIO_ID) != 0) {
    sendError(id, "UNSUPPORTED", "unknown radio");
    return false;
  }
  if (!radio_ok) {
    sendError(id, "UNSUPPORTED", "radio r433 not available: CC1101 not responding");
    return false;
  }
  return true;
}

void handleScan(const JsonDocument& req, JsonVariantConst id) {
  if (!checkRadio(req, id)) return;
  if (!req["start_mhz"].is<float>() || !req["end_mhz"].is<float>() ||
      !req["step_khz"].is<float>()) {
    sendError(id, "INVALID", "start_mhz, end_mhz and step_khz are required numbers");
    return;
  }
  float start = req["start_mhz"];
  float end = req["end_mhz"];
  float step = req["step_khz"];
  if (!inBand(start) || !inBand(end)) {
    sendError(id, "OUT_OF_BAND", "start_mhz/end_mhz must be inside one band of the radio");
    return;
  }
  if (end <= start || step < 5 || step > 1000) {
    sendError(id, "INVALID", "need end_mhz > start_mhz and 5 <= step_khz <= 1000");
    return;
  }
  int points = (int)((end - start) * 1000.0f / step + 1e-3f) + 1;
  if (points > MAX_POINTS) {
    sendError(id, "INVALID", "too many points (max 1001)");
    return;
  }

  int16_t state = radio::scan(start, step, points, spectrum);
  if (state != 0) {
    sendError(id, "RADIO", String("RadioLib error ") + state);
    return;
  }
  JsonDocument doc = message("spectrum", id);
  doc["radio"] = RADIO_ID;
  doc["start_mhz"] = start;
  doc["step_khz"] = step;
  JsonArray rssi = doc["rssi_dbm"].to<JsonArray>();
  for (int i = 0; i < points; i++) rssi.add(spectrum[i]);
  send(doc);
}

void handleRxPulses(const JsonDocument& req, JsonVariantConst id) {
  if (!checkRadio(req, id)) return;
  if (!req["freq_mhz"].is<float>() || !req["seconds"].is<float>()) {
    sendError(id, "INVALID", "freq_mhz and seconds are required numbers");
    return;
  }
  float freq = req["freq_mhz"];
  float seconds = req["seconds"];
  if (!inBand(freq)) {
    sendError(id, "OUT_OF_BAND", "freq_mhz must be inside one band of the radio");
    return;
  }
  if (seconds < 1 || seconds > 60) {
    sendError(id, "INVALID", "seconds must be between 1 and 60");
    return;
  }
  bool has_threshold = !req["rssi_threshold_dbm"].isNull();
  if (has_threshold && !req["rssi_threshold_dbm"].is<float>()) {
    sendError(id, "INVALID", "rssi_threshold_dbm must be a number");
    return;
  }
  int16_t threshold = has_threshold ? (int16_t)lroundf(req["rssi_threshold_dbm"].as<float>()) : 0;

  radio::ListenResult result =
      radio::listen(freq, (uint32_t)lroundf(seconds), has_threshold, threshold,
                    [&](const radio::Frame& frame) {
                      JsonDocument doc = message("pulses", id);
                      doc["radio"] = RADIO_ID;
                      doc["freq_mhz"] = freq;
                      doc["rssi_dbm"] = frame.peak_rssi_dbm;
                      doc["truncated"] = frame.truncated;
                      JsonArray t = doc["t_us"].to<JsonArray>();
                      for (size_t i = 0; i < frame.len; i++) t.add(frame.t_us[i]);
                      send(doc);
                    });
  if (result.state != 0) {
    sendError(id, "RADIO", String("RadioLib error ") + result.state);
    return;
  }
  JsonDocument doc = message("done", id);
  doc["frames"] = result.frames;
  doc["discarded"] = result.discarded;
  doc["noise_floor_dbm"] = result.noise_floor_dbm;
  doc["threshold_dbm"] = result.threshold_dbm;
  send(doc);
}

void handleMessage(const char* text) {
  JsonDocument req;
  if (deserializeJson(req, text) != DeserializationError::Ok || !req.is<JsonObject>()) {
    sendError(JsonVariantConst(), "INVALID", "not a JSON object");
    return;
  }
  JsonVariantConst id = req["id"];
  JsonVariantConst v = req["v"];
  if (!v.isNull() && v != PROTOCOL_V) {
    sendError(id, "VERSION", "unsupported protocol version");
    return;
  }
  const char* type = req["type"] | "";
  if (strcmp(type, "hello") == 0) {
    sendHello(reply_to, id);
  } else if (strcmp(type, "pair") == 0) {
    handlePair(req, id);
  } else if (strcmp(type, "status") == 0) {
    handleStatus(id);
  } else if (strcmp(type, "scan") == 0) {
    handleScan(req, id);
  } else if (strcmp(type, "rx_pulses") == 0) {
    handleRxPulses(req, id);
  } else {
    sendError(id, "UNSUPPORTED", "unknown type");
  }
}

void setup() {
  Serial.begin(115200);
  transport::begin();
  esp_task_wdt_init(WATCHDOG_S, true);
  esp_task_wdt_add(nullptr);

  // node_id from the last 3 bytes of the factory MAC address.
  uint64_t mac = ESP.getEfuseMac();
  snprintf(node_id, sizeof(node_id), "esp32s3-%02x%02x%02x", (uint8_t)(mac >> 24),
           (uint8_t)(mac >> 32), (uint8_t)(mac >> 40));

  uint8_t version = 0;
  int16_t state = radio::begin(version);
  radio_ok = state == 0;
  if (radio_ok) {
    sendLog("info", String("CC1101 ready (VERSION=0x") + String(version, HEX) + ")");
  } else {
    sendLog("error", String("CC1101 not available (VERSION=0x") + String(version, HEX) +
                         ", RadioLib " + state + "): check VCC, GND and the SPI wires");
  }
  sendHello(Channel::USB, JsonVariantConst());
  net::begin(node_id, sendLog);
}

void loop() {
  esp_task_wdt_reset();
  net::loop();
  if (transport::takeWsConnected()) {
    sendHello(Channel::WS, JsonVariantConst());  // must be the first message (protocol §1.1)
    sendLog("info", net::token().isEmpty()
                        ? String("connected to the bridge; if this node is new, approve it "
                                 "there: eter nodes approve ") + node_id
                        : String("connected to the bridge"));
  }
  transport::Message msg;
  if (!transport::poll(msg)) return;
  reply_to = msg.from;
  if (msg.overflow) {
    sendError(JsonVariantConst(), "INVALID", "message too long (max 512 bytes)");
  } else {
    handleMessage(msg.text);
  }
  reply_to = Channel::USB;
}
