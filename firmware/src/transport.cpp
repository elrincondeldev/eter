#include "transport.h"

#include <esp_websocket_client.h>

namespace transport {
namespace {

// Big enough that node -> bridge messages (spectrum, pulses) normally fit in one frame.
// Allocated from PSRAM.
constexpr int WS_BUFFER = 32768;
constexpr int WS_PING_S = 10;
constexpr int WS_PONG_TIMEOUT_S = 30;
constexpr TickType_t SEND_TIMEOUT = pdMS_TO_TICKS(5000);

// USB line assembly.
Message usb_line;
size_t usb_len = 0;

// WebSocket messages arrive on the client's task and wait here for the main loop.
QueueHandle_t ws_inbox;
esp_websocket_client_handle_t ws = nullptr;
volatile bool ws_connected = false;
volatile bool ws_new_connection = false;
// The bridge closed the connection cleanly (e.g. it was restarted). The client does not
// reconnect by itself after that, unlike after a network error.
volatile bool ws_closed = false;

void onWsEvent(void*, esp_event_base_t, int32_t event_id, void* event_data) {
  auto* data = static_cast<esp_websocket_event_data_t*>(event_data);
  switch (event_id) {
    case WEBSOCKET_EVENT_CONNECTED:
      ws_connected = true;
      ws_new_connection = true;
      break;
    case WEBSOCKET_EVENT_DISCONNECTED:
      ws_connected = false;
      break;
    case WEBSOCKET_EVENT_CLOSED:
      ws_connected = false;
      ws_closed = true;
      break;
    case WEBSOCKET_EVENT_DATA: {
      // Text messages only; bridge -> node messages are small, so they come in one event.
      if (data->op_code != 0x1 || data->payload_offset != 0) break;
      static Message message;
      message.from = Channel::WS;
      message.overflow = data->payload_len > (int)MAX_MESSAGE || data->data_len < data->payload_len;
      size_t len = message.overflow ? 0 : data->data_len;
      memcpy(message.text, data->data_ptr, len);
      message.text[len] = '\0';
      xQueueSend(ws_inbox, &message, 0);  // dropped if the main loop is far behind
      break;
    }
    default:
      break;
  }
}

bool pollUsb(Message& out) {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      bool empty = usb_len == 0 && !usb_line.overflow;
      usb_line.text[usb_len] = '\0';
      usb_line.from = Channel::USB;
      if (!empty) out = usb_line;
      usb_len = 0;
      usb_line.overflow = false;
      if (!empty) return true;
    } else if (c == '\r') {
      continue;
    } else if (usb_len < MAX_MESSAGE) {
      usb_line.text[usb_len++] = c;
    } else {
      usb_line.overflow = true;
    }
  }
  return false;
}

}  // namespace

void begin() { ws_inbox = xQueueCreate(4, sizeof(Message)); }

bool poll(Message& message) {
  if (pollUsb(message)) return true;
  return xQueueReceive(ws_inbox, &message, 0) == pdTRUE;
}

void send(Channel to, const String& text) {
  if (to == Channel::USB) {
    Serial.write(text.c_str(), text.length());
    Serial.write('\n');
  } else if (ws != nullptr && ws_connected) {
    esp_websocket_client_send_text(ws, text.c_str(), text.length(), SEND_TIMEOUT);
  }
}

void connect(const String& uri) {
  if (ws != nullptr) {
    esp_websocket_client_stop(ws);
    esp_websocket_client_destroy(ws);
    ws = nullptr;
    ws_connected = false;
  }
  ws_closed = false;
  esp_websocket_client_config_t config = {};
  config.uri = uri.c_str();  // copied by esp_websocket_client_init
  config.buffer_size = WS_BUFFER;
  config.task_stack = 6144;
  config.ping_interval_sec = WS_PING_S;
  config.pingpong_timeout_sec = WS_PONG_TIMEOUT_S;
  ws = esp_websocket_client_init(&config);
  esp_websocket_register_events(ws, WEBSOCKET_EVENT_ANY, onWsEvent, nullptr);
  esp_websocket_client_start(ws);
}

bool wsStarted() { return ws != nullptr && !ws_closed; }

bool wsConnected() { return ws_connected; }

bool takeWsConnected() {
  if (!ws_new_connection) return false;
  ws_new_connection = false;
  return true;
}

}  // namespace transport
