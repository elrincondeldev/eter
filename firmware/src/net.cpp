#include "net.h"

#include <ESPmDNS.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiManager.h>

#include "transport.h"

namespace net {
namespace {

constexpr int BOOT_BUTTON = 0;
constexpr uint32_t RESET_HOLD_MS = 3000;
constexpr uint32_t PORTAL_TIMEOUT_S = 600;
constexpr uint32_t WIFI_CONNECT_TIMEOUT_S = 20;
constexpr uint32_t WIFI_RETRY_MS = 30000;
constexpr uint32_t BRIDGE_RETRY_MS = 10000;
// The WebSocket client reconnects to the same address by itself. After this long without a
// connection, look the bridge up again: its address may have changed.
constexpr uint32_t REDISCOVER_MS = 60000;
constexpr uint16_t DEFAULT_PORT = 8765;

const char* node_name = "";
LogFn log_fn = nullptr;
Preferences prefs;
WiFiManager wm;
char bridge_setting[64] = "";
WiFiManagerParameter bridge_param("bridge",
                                  "Bridge address (optional: host or host:port; empty = find "
                                  "it automatically)",
                                  bridge_setting, sizeof(bridge_setting) - 1);
bool mdns_started = false;
bool was_connected = false;
uint32_t pressed_since = 0;
uint32_t last_wifi_try = 0;
uint32_t last_bridge_try = 0;
uint32_t ws_lost_since = 0;

// Preferences logs an error on Serial for a missing key, which would mix with the protocol.
String setting(const char* key) { return prefs.isKey(key) ? prefs.getString(key, "") : String(); }

void saveBridgeSetting() {
  strlcpy(bridge_setting, bridge_param.getValue(), sizeof(bridge_setting));
  prefs.putString("bridge", bridge_setting);
  log_fn("info", String("bridge address saved: ") +
                     (bridge_setting[0] ? bridge_setting : "(find it automatically)"));
}

void checkResetButton() {
  if (digitalRead(BOOT_BUTTON) == HIGH) {
    pressed_since = 0;
    return;
  }
  if (pressed_since == 0) pressed_since = millis();
  if (millis() - pressed_since < RESET_HOLD_MS) return;
  log_fn("warn", "BOOT held: erasing Wi-Fi, bridge address and pairing token, restarting");
  prefs.clear();
  wm.resetSettings();
  delay(200);
  ESP.restart();
}

// "ws://host:port/node" from the portal setting, or from mDNS (_eter._tcp).
bool findBridge(String& uri) {
  if (bridge_setting[0]) {
    String host = bridge_setting;
    if (host.indexOf(':') < 0) host += ":" + String(DEFAULT_PORT);
    uri = "ws://" + host + "/node";
    return true;
  }
  if (!mdns_started) mdns_started = MDNS.begin(node_name);
  if (MDNS.queryService("eter", "tcp") < 1) return false;
  String path = MDNS.txt(0, "path");
  if (path.isEmpty()) path = "/node";
  uri = "ws://" + MDNS.IP(0).toString() + ":" + String(MDNS.port(0)) + path;
  return true;
}

}  // namespace

void begin(const char* node_id, LogFn log) {
  node_name = node_id;
  log_fn = log;
  pinMode(BOOT_BUTTON, INPUT_PULLUP);
  prefs.begin("eter", false);
  strlcpy(bridge_setting, setting("bridge").c_str(), sizeof(bridge_setting));
  bridge_param.setValue(bridge_setting, sizeof(bridge_setting) - 1);

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  wm.setDebugOutput(false);  // Serial carries the protocol
  wm.addParameter(&bridge_param);
  wm.setSaveParamsCallback(saveBridgeSetting);
  wm.setConfigPortalBlocking(false);
  wm.setConfigPortalTimeout(PORTAL_TIMEOUT_S);
  wm.setConnectTimeout(WIFI_CONNECT_TIMEOUT_S);
  wm.setTitle("eter node");

  String ap = String("eter-") + (strchr(node_id, '-') ? strchr(node_id, '-') + 1 : node_id);
  if (!wm.autoConnect(ap.c_str())) {  // success is logged by loop()
    log_fn("info", "Wi-Fi not configured or not reachable: setup portal open for 10 min on "
                   "the Wi-Fi network \"" + ap + "\"");
  }
}

void loop() {
  wm.process();
  checkResetButton();

  uint32_t now = millis();
  if (WiFi.status() != WL_CONNECTED) {
    was_connected = false;
    // Saved network unreachable (router off, out of range): keep trying once the portal is
    // closed. With nothing saved, wait for the portal or a reboot.
    if (!wm.getConfigPortalActive() && wm.getWiFiIsSaved() && now - last_wifi_try > WIFI_RETRY_MS) {
      last_wifi_try = now;
      WiFi.begin();
    }
    return;
  }
  if (!was_connected) {
    was_connected = true;
    // At full Wi-Fi transmit power the CC1101 failed most SPI register checks (RadioLib -16)
    // while Wi-Fi was busy: 13 of 15 back-to-back scans on the reference node, 0 of 15 at
    // 8.5 dBm. Probably the transmit bursts disturb the shared 3.3 V or the SPI wires. The
    // 433 MHz noise floor is the same either way.
    WiFi.setTxPower(WIFI_POWER_8_5dBm);
    log_fn("info", "Wi-Fi connected: " + WiFi.SSID() + ", IP " + WiFi.localIP().toString());
  }

  if (transport::wsConnected()) {
    ws_lost_since = 0;
    return;
  }
  if (ws_lost_since == 0) ws_lost_since = now;
  if (transport::wsStarted() && now - ws_lost_since < REDISCOVER_MS) return;
  if (last_bridge_try != 0 && now - last_bridge_try < BRIDGE_RETRY_MS) return;
  last_bridge_try = now;

  String uri;
  if (!findBridge(uri)) {
    log_fn("warn", "bridge not found on the network (is `eter serve` running?); retrying");
    return;
  }
  log_fn("info", "connecting to the bridge at " + uri);
  transport::connect(uri);
  ws_lost_since = now;
}

String token() { return setting("token"); }

void setToken(const char* value) { prefs.putString("token", value); }

bool wifiRssi(int& rssi_dbm) {
  if (WiFi.status() != WL_CONNECTED) return false;
  rssi_dbm = WiFi.RSSI();
  return true;
}

}  // namespace net
