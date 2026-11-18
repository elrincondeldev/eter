// Links to the bridge (docs/protocol.md §1): USB serial (NDJSON lines) and a WebSocket client
// (one JSON message per text message). Commands from both end up in one stream.
#pragma once
#include <Arduino.h>

namespace transport {

constexpr size_t MAX_MESSAGE = 512;  // bridge -> node

enum class Channel : uint8_t { USB, WS };

struct Message {
  Channel from;
  bool overflow;  // longer than MAX_MESSAGE: text is empty
  char text[MAX_MESSAGE + 1];
};

void begin();

// Next complete incoming message from any link. Returns false if there is none.
bool poll(Message& message);

// Sends one message (without newline) on a link. A WebSocket send is dropped if not connected.
void send(Channel to, const String& text);

// Starts the WebSocket client towards `uri` (replacing any previous one). It reconnects by
// itself when the connection drops, but not after the bridge closes it.
void connect(const String& uri);
// A client exists and is connected or reconnecting by itself.
bool wsStarted();
bool wsConnected();
// True once after every new WebSocket connection (the node must then send hello).
bool takeWsConnected();

}  // namespace transport
