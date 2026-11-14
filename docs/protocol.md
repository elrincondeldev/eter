# Node protocol — v0

The contract between an eter **node** (radio hardware) and the **bridge**. Any hardware that speaks this protocol works with eter.

- **Version:** `0` (unstable: may change without notice until `1`).
- **Status:** implemented by the reference firmware `0.1.0` (ESP32-S3 + CC1101 433 MHz) over USB serial.

## 1. Transport and encoding

- **Encoding:** NDJSON — one JSON object per line, UTF-8, terminated by `\n`.
- **v0 transport:** USB serial (native USB CDC, 115200 baud setting; the actual speed is USB). WebSocket comes in a later version with the same messages.
- **Max line length, bridge → node:** 512 bytes. Longer lines are rejected with `INVALID`.
- Every message carries `"v": 0`. A request with any other `v` is rejected with `VERSION`.

## 2. Envelope

| Field | Direction | Required | Meaning |
|---|---|---|---|
| `v` | both | yes | Protocol version (`0`) |
| `type` | both | yes | Message type |
| `id` | bridge → node | no | Request id (integer). If present, every reply to that request carries it as `re` |
| `re` | node → bridge | when replying to a request with `id` | The `id` being answered |

## 3. Execution model

- The node runs **one command at a time**. Commands are blocking: `scan` and `rx_pulses` take as long as the sweep or listening window lasts. Lines sent meanwhile wait in the serial buffer and run afterwards.
- A command ends with exactly one **final** message: its result (`hello`, `status`, `spectrum`), `done` (for `rx_pulses`), or `error`.
- `rx_pulses` may send any number of `pulses` messages before its final `done`.
- Unsolicited messages (no `re`): `hello` at boot, and `log`.

## 4. Commands (bridge → node)

All frequencies in MHz, steps in kHz, durations in seconds.

### `hello`

Ask the node to introduce itself. Reply: `hello` (§5).

```json
{"v":0,"id":1,"type":"hello"}
```

### `status`

Reply: `status` (§5).

```json
{"v":0,"id":2,"type":"status"}
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
{"v":0,"id":3,"type":"scan","radio":"r433","start_mhz":430.0,"end_mhz":440.0,"step_khz":25}
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
{"v":0,"id":4,"type":"rx_pulses","radio":"r433","freq_mhz":433.92,"seconds":10}
```

## 5. Messages (node → bridge)

### `hello`

Sent at boot and in reply to `hello`.

```json
{"v":0,"type":"hello","node_id":"esp32s3-a1b2c3","fw":"0.1.0","capabilities":{...}}
```

`capabilities` is defined in [capabilities.md](capabilities.md).

### `status`

```json
{"v":0,"re":2,"type":"status","fw":"0.1.0","uptime_s":3600,"heap_free":210000,"radios":["r433"]}
```

### `spectrum`

```json
{"v":0,"re":3,"type":"spectrum","radio":"r433","start_mhz":430.0,"step_khz":25,"rssi_dbm":[-98,-97,-61]}
```

`rssi_dbm[i]` is the reading at `start_mhz + i * step_khz / 1000`. Integers, ~1 dB resolution.

### `pulses`

One captured frame.

```json
{"v":0,"re":4,"type":"pulses","radio":"r433","freq_mhz":433.92,"rssi_dbm":-55,"truncated":false,"t_us":[512,1480,498,1502]}
```

- `t_us`: durations in microseconds, alternating **pulse** (carrier on) and **gap** (carrier off). Always starts and ends with a pulse, so the length is odd.
- `rssi_dbm`: peak RSSI seen during the frame.

### `done`

Final message of `rx_pulses`.

```json
{"v":0,"re":4,"type":"done","frames":3,"discarded":1,"noise_floor_dbm":-98,"threshold_dbm":-88}
```

- `frames`: `pulses` messages sent.
- `discarded`: transmissions detected by RSSI but dropped for having fewer than 8 pulses. A signal that keeps its carrier on all the time (e.g. FSK, which an OOK receiver sees as one long pulse) shows up here.

### `error`

Final message of a failed command.

```json
{"v":0,"re":3,"type":"error","code":"OUT_OF_BAND","msg":"start_mhz/end_mhz must be inside one band of the radio"}
```

| `code` | Meaning |
|---|---|
| `INVALID` | Not JSON, line too long, missing or invalid parameter |
| `VERSION` | Unsupported `v` |
| `UNSUPPORTED` | Unknown `type`, or unknown/unsupported `radio` |
| `OUT_OF_BAND` | Frequency outside the radio's `bands_mhz` |
| `RADIO` | The radio driver returned an error (`msg` has the details) |

Reserved for later versions: `BUSY`, `UNAUTHORIZED`, `TX_DISABLED`, `TX_NOT_ALLOWED`, `DUTY_CYCLE_EXCEEDED`.

### `log`

Human-readable diagnostics. Never needed to drive the node.

```json
{"v":0,"type":"log","level":"info","msg":"CC1101 ready (VERSION=0x14)"}
```

`level`: `debug`, `info`, `warn`, `error`.

## 6. Capture files

Tools that record node traffic (and `eter_sim`, which replays it) use the same format: an NDJSON file whose **first line is the node's `hello`**, followed by node → bridge messages exactly as received. Stored in `captures/`.

Only commit captures of devices you own: frames from neighbours' devices can contain their identifiers.

## 7. Changelog

| v | Changes |
|---|---|
| 0 | Initial: `hello`, `status`, `scan`, `rx_pulses`; USB serial |
