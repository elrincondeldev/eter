// Wi-Fi for the node: setup portal, finding the bridge, and the settings kept across reboots.
//
// - No saved Wi-Fi (or it cannot connect): the node opens the access point "eter-xxxxxx" for
//   10 minutes. Join it from a phone; a setup page asks for the Wi-Fi network and, optionally,
//   the bridge address (empty = find it over mDNS, docs/protocol.md §1.1).
// - Holding the BOOT button for 3 s erases Wi-Fi, bridge address and pairing token, and restarts.
#pragma once
#include <Arduino.h>

namespace net {

using LogFn = void (*)(const char* level, const String& msg);

void begin(const char* node_id, LogFn log);

// Call from loop(): runs the portal, keeps Wi-Fi and the bridge connection alive.
void loop();

// Pairing token from the bridge (docs/protocol.md §1.2). Empty if not paired.
String token();
void setToken(const char* token);

// Wi-Fi signal strength, if connected.
bool wifiRssi(int& rssi_dbm);

}  // namespace net
