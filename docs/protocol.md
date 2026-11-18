# Node protocol — v1

The contract between an eter **node** (radio hardware) and the **bridge**. Any hardware that speaks this protocol works with eter.

- **Version:** `1` (unstable: may change without notice until the first public release).
- **Status:** implemented by the reference firmware `0.2.0` (ESP32-S3 + CC1101 433 MHz) over USB serial and Wi-Fi.

## 1. Transports and encoding

The same JSON messages travel over two transports:

| Transport | Framing | Who connects | Pairing |
|---|---|---|---|
| **USB serial** (native USB CDC, 115200 baud setting; the actual speed is USB) | NDJSON: one JSON object per line, UTF-8, terminated by `\n` | The bridge opens the port | None: physical access is trust |
| **WebSocket** | One JSON object per text message (no newline) | **The node is the client**: it connects to `ws://<bridge>:8765/node` | Required (§1.2) |

- **Max message size, bridge → node:** 512 bytes. Longer lines/messages are rejected with `INVALID`. Node → bridge messages can be larger (a `pulses` message can reach ~25 KB).
- Every message carries `"v": 1`. A request with any other `v` is rejected with `VERSION`.
- A node can be on both transports at once. Each command is answered on the transport it came from. Unsolicited messages go to USB and, when connected and paired, to the WebSocket.

### 1.1 Discovery and reconnection (WebSocket)

- The bridge advertises the mDNS service **`_eter._tcp`** on its WebSocket port, with TXT records `path=/node` and `v=1`. The node looks it up after joining Wi-Fi. A bridge address entered in the node's setup portal (`host` or `host:port`) takes precedence over discovery.
- The node sends `hello` (§5) as its first message after every connection.
- The bridge pings every 20 s and drops a connection that does not answer within 20 s. The node reconnects by itself, and looks the bridge up again after 60 s without a connection (its address may have changed).
- The bridge keeps one connection per `node_id`: a new connection from the same node replaces the old one.

### 1.2 Pairing (WebSocket)

Unpaired nodes are ignored: the bridge sends them no commands and discards what they send.

1. A node without a valid token connects and sends `hello` without `token`. The bridge records it as **pending** (`eter nodes` lists it).
2. The user approves it: `eter nodes approve <node_id>`.
3. The bridge generates a random token and sends it with the `pair` command (§4). The node stores it in non-volatile memory and answers `paired`. From then on its `hello` carries `"token"`, and the bridge stores only a hash of it.
4. A `hello` whose token does not match a paired node is answered with `error` `UNAUTHORIZED` (no `re`) and the connection is closed. To pair again (e.g. after the node's settings were erased): `eter nodes forget <node_id>`, then approve it again.

The token is never sent over USB.

## 2. Envelope

| Field | Direction | Required | Meaning |
|---|---|---|---|
| `v` | both | yes | Protocol version (`0`) |
| `type` | both | yes | Message type |
| `id` | bridge → node | no | Request id (integer). If present, every reply to that request carries it as `re` |
| `re` | node → bridge | when replying to a request with `id` | The `id` being answered |

## 3. Execution model

- The node runs **one command at a time**, whatever transport it came from. Commands are blocking: `scan` and `rx_pulses` take as long as the sweep or listening window lasts. Commands sent meanwhile wait and run afterwards.
- A command ends with exactly one **final** message: its result (`hello`, `status`, `spectrum`, `paired`), `done` (for `rx_pulses`), or `error`.
- `rx_pulses` may send any number of `pulses` messages before its final `done`.
- Unsolicited messages (no `re`): `hello` at boot and on every WebSocket connection, `log`, and the bridge's `error` `UNAUTHORIZED` (§1.2).

## 4. Commands (bridge → node)

All frequencies in MHz, steps in kHz, durations in seconds.

### `hello`

Ask the node to introduce itself. Reply: `hello` (§5).

```json
{"v":1,"id":1,"type":"hello"}
```

### `status`

Reply: `status` (§5).

```json
{"v":1,"id":2,"type":"status"}
```

### `scan`

RSSI sweep. At each step the node tunes, waits for the receiver to settle, and reads the instantaneous RSSI.

| Param | Type | Rules |
|---|---|---|
| `radio` | string | A radio `id` from capabilities |
| `start_mhz` | number | Inside one of the radio's `bands_mhz` |
| `end_mhz` | number | `> start_mhz`, same band |
| `step_khz` | number | `5`–`1000`; at most `1001` points in total |

Reply: `spectrum`.

```json
{"v":1,"id":3,"type":"scan","radio":"r433","start_mhz":430.0,"end_mhz":440.0,"step_khz":25}
```

Notes: a sweep takes roughly 1.5 ms per point (401 points ≈ 0.6 s). Readings are instantaneous, so short bursts (a remote pressed once) are easy to miss; this is a "peak radar", not a lab analyzer.

### `rx_pulses`

Listen on one frequency in OOK mode and capture the raw on/off timings of each transmission ("frame").

| Param | Type | Rules |
|---|---|---|
| `radio` | string | A radio `id` with feature `rx_pulses` |
| `freq_mhz` | number | Inside one of the radio's `bands_mhz` |
| `seconds` | number | `1`–`60` |
| `rssi_threshold_dbm` | number | Optional. Default: measured noise floor + 15 dB |

Frame detection:

1. The node measures the noise floor (average RSSI over ~50 ms) and sets the threshold (floor + 15 dB unless given).
2. A frame starts when RSSI goes above the threshold, and ends after **10 ms** below it. Edges from 2 ms before the start are included so the first pulse is not lost.
3. A frame is cut at **2 s** or **4000 durations** (marked `"truncated": true`).
4. Frames with fewer than **8 pulses** are discarded as noise.

Replies: zero or more `pulses`, then `done`.

```json
{"v":1,"id":4,"type":"rx_pulses","radio":"r433","freq_mhz":433.92,"seconds":10}
```

### `pair`

WebSocket only (over USB: `UNSUPPORTED`). Sent by the bridge after the user approves the node (§1.2). The node stores the token, replacing any previous one, and uses it in every later `hello`.

| Param | Type | Rules |
|---|---|---|
| `token` | string | 32 lowercase hex characters |

Reply: `paired`.

```json
{"v":1,"id":1,"type":"pair","token":"3f2a…"}
```

## 5. Messages (node → bridge)

### `hello`

Sent at boot, as the first message of every WebSocket connection, and in reply to `hello`.

```json
{"v":1,"type":"hello","node_id":"esp32s3-a1b2c3","fw":"0.2.0","token":"3f2a…","capabilities":{...}}
```

`token`: only over WebSocket, and only once the node is paired (§1.2).

`capabilities` is defined in [capabilities.md](capabilities.md).

### `status`

```json
{"v":1,"re":2,"type":"status","fw":"0.2.0","uptime_s":3600,"heap_free":210000,"radios":["r433"],
 "reset_reason":"poweron","wifi_rssi_dbm":-58}
```

- `reset_reason`: why the node last started: `poweron`, `software`, `panic`, `watchdog`, `brownout` or `other`. `watchdog` means it hung and restarted itself.
- `wifi_rssi_dbm`: Wi-Fi signal strength; absent when not on Wi-Fi.

### `spectrum`

```json
{"v":1,"re":3,"type":"spectrum","radio":"r433","start_mhz":430.0,"step_khz":25,"rssi_dbm":[-98,-97,-61]}
```

`rssi_dbm[i]` is the reading at `start_mhz + i * step_khz / 1000`. Integers, ~1 dB resolution.

### `pulses`

One captured frame.

```json
{"v":1,"re":4,"type":"pulses","radio":"r433","freq_mhz":433.92,"rssi_dbm":-55,"truncated":false,"t_us":[512,1480,498,1502]}
```

- `t_us`: durations in microseconds, alternating **pulse** (carrier on) and **gap** (carrier off). Always starts and ends with a pulse, so the length is odd.
- `rssi_dbm`: peak RSSI seen during the frame.

### `paired`

Reply to `pair`: the token is stored.

```json
{"v":1,"re":1,"type":"paired"}
```

### `done`

Final message of `rx_pulses`.

```json
{"v":1,"re":4,"type":"done","frames":3,"discarded":1,"noise_floor_dbm":-98,"threshold_dbm":-88}
```

- `frames`: `pulses` messages sent.
- `discarded`: transmissions detected by RSSI but dropped for having fewer than 8 pulses. A signal that keeps its carrier on all the time (e.g. FSK, which an OOK receiver sees as one long pulse) shows up here.

### `error`

Final message of a failed command.

```json
{"v":1,"re":3,"type":"error","code":"OUT_OF_BAND","msg":"start_mhz/end_mhz must be inside one band of the radio"}
```

| `code` | Meaning |
|---|---|
| `INVALID` | Not JSON, line too long, missing or invalid parameter |
| `VERSION` | Unsupported `v` |
| `UNSUPPORTED` | Unknown `type`, or unknown/unsupported `radio` |
| `OUT_OF_BAND` | Frequency outside the radio's `bands_mhz` |
| `RADIO` | The radio driver returned an error (`msg` has the details) |
| `UNAUTHORIZED` | Sent by the **bridge**: the node's token is not valid (§1.2) |

Reserved for later versions: `BUSY`, `TX_DISABLED`, `TX_NOT_ALLOWED`, `DUTY_CYCLE_EXCEEDED`.

### `log`

Human-readable diagnostics. Never needed to drive the node.

```json
{"v":1,"type":"log","level":"info","msg":"CC1101 ready (VERSION=0x14)"}
```

`level`: `debug`, `info`, `warn`, `error`.

## 6. Capture files

Tools that record node traffic (and `eter_sim`, which replays it) use the same format: an NDJSON file whose **first line is the node's `hello`**, followed by node → bridge messages exactly as received. A `re` field may be dropped.

- `captures/` holds curated, committed captures: the default data set of `eter_sim` and the tests.
- `tools/node_probe.py` writes new recordings to `tmp/captures/` (not committed). Review one before moving it into `captures/`.

Only commit captures of devices you own: frames from neighbours' devices can contain their identifiers. Anonymise `node_id`.

## 7. Changelog

| v | Changes |
|---|---|
| 0 | Initial: `hello`, `status`, `scan`, `rx_pulses`; USB serial |
| 1 | WebSocket transport (node is the client), mDNS discovery, pairing (`pair`/`paired`, `token` in `hello`, `UNAUTHORIZED`); `status` adds `reset_reason` and `wifi_rssi_dbm` |
