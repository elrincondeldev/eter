# eter

An open-source harness that gives AI agents access to the radio spectrum through cheap hardware.

A **node** (reference: ESP32-S3 + CC1101 433 MHz) captures signals. A **bridge** (Python) decodes and stores them and exposes them through MCP, a CLI, a library and an optional REST API.

> Status: pre-development. Phase 4 (decoding) done: rtl_433 decoding, devices, pulse analysis and background monitoring, checked on the reference node. Phase 5 (Wi-Fi, BLE and running 24/7) in progress: nodes connect over Wi-Fi.

## Run the bridge

The bridge is a program on your computer that owns the nodes. Start it and leave it running:

```
uv run --directory bridge eter serve          # Wi-Fi nodes only
uv run --directory bridge eter serve --usb    # also the node plugged in over USB
```

Nodes connect to it over Wi-Fi (port 8765; it announces itself on the local network as `_eter._tcp`, so nodes find it). Claude Code and the CLI talk to it at `http://127.0.0.1:8766/mcp`, which only this computer can reach. macOS may ask whether Python can accept incoming connections: allow it, or Wi-Fi nodes cannot connect.

### Put a node on Wi-Fi

1. Flash the firmware and power the node. With no Wi-Fi saved, it opens a Wi-Fi network called `eter-xxxxxx` for 10 minutes.
2. Join that network from your phone. A setup page opens (if not, browse to `192.168.4.1`). Choose your Wi-Fi network and enter its password. Leave *Bridge address* empty unless mDNS does not work on your network; in that case enter the computer's IP.
3. The node joins your Wi-Fi and connects to the bridge. The first time, it waits for your approval:

```
uv run --directory bridge eter nodes                        # nodes and their state
uv run --directory bridge eter nodes approve esp32s3-xxxxxx # pair it
uv run --directory bridge eter nodes forget esp32s3-xxxxxx  # unpair it
```

Approving gives the node a secret token, which it keeps. From then on it reconnects by itself after a Wi-Fi drop, a bridge restart or a reboot. Unapproved nodes are ignored.

To start over, hold the node's **BOOT** button for 3 seconds: it erases Wi-Fi, bridge address and token, and opens the setup network again.

## Use it from Claude Code

The repo's `.mcp.json` points Claude Code at the running bridge (`eter serve`). Open Claude Code in the repo root, approve the `eter` server, and ask things like *"what is on the air between 430 and 440 MHz?"*. The tools that need a radio appear once a node with that radio has connected.

| Tool | What it does | Needs |
|---|---|---|
| `node_status` | Connected node, firmware, radios and bands | — |
| `identify_frequency` | Typical uses of a frequency (Europe) | — |
| `query_events` | History of scans and listens | — |
| `scan_spectrum` | Sweep a range: noise floor and peaks | a radio with `rssi_scan` |
| `listen` | Capture on/off transmissions on one frequency and decode known devices | a radio with `rx_pulses` |
| `analyze_capture` | Pulse widths, modulation guess and bits of an earlier listen | a radio with `rx_pulses` |
| `list_devices` | Devices decoded so far, with their last data | — |
| `label_device` | Name a device and mark it as yours | — |
| `monitor` | Keep listening in the background (preset `sensors_433`) while the bridge runs | a radio with `rx_pulses` |

Decoding uses [rtl_433](https://github.com/merbanan/rtl_433) as an external program (macOS: `brew install rtl_433`). Without it, `listen` still captures frames but cannot name them.

The same operations are available as a CLI that prints JSON. When `eter serve` is running, the CLI goes through it; otherwise it opens the USB node itself:

```
uv run --directory bridge eter status
uv run --directory bridge eter scan --start 430 --end 440
uv run --directory bridge eter listen --freq 433.92 --seconds 10
uv run --directory bridge eter identify 446.1
uv run --directory bridge eter events --since 60
uv run --directory bridge eter analyze <event_id>
uv run --directory bridge eter devices
uv run --directory bridge eter label <device> "Living room thermometer" --owned
uv run --directory bridge eter monitor --minutes 10
```

Configuration (environment variables):

| Variable | Default | Meaning |
|---|---|---|
| `ETER_PORT` | the only `/dev/*usbmodem*` / `ttyACM*` | Serial port of the node |
| `ETER_SIM` | — | Use the simulator with this captures directory instead of hardware (absolute path, e.g. `$PWD/captures`) |
| `ETER_DB` | `~/.eter/eter.db` | SQLite history and paired nodes |
| `ETER_MCP_PORT` | `8766` | Port of the bridge's MCP server (on 127.0.0.1) |

Only one program can use the node's USB port at a time: close the serial monitor and `node_probe.py` before using `eter serve --usb` or the CLI over USB.

Without hardware, a simulated node can join a running bridge over Wi-Fi the same way: `uv run --directory bridge python -m eter_sim --captures $PWD/captures`.

## Try the node directly

With the reference node on USB (see `tools/node_probe.py --help`):

```
uv run tools/node_probe.py hello                     # identity and capabilities
uv run tools/node_probe.py scan --start 430 --end 440 # RSSI sweep + plot
uv run tools/node_probe.py listen --freq 433.92       # capture OOK pulse frames
```

## Layout

| Path | Contents |
|---|---|
| `firmware/` | PlatformIO project for the reference node |
| `bridge/` | Python bridge (`eter_core` + MCP/CLI/REST facades + `eter_sim`) |
| `docs/` | Node protocol, capabilities, wiring, regions |
| `captures/` | Curated captures for tests and the simulator (new recordings go to `tmp/captures/`) |
| `tools/` | Development tools (`node_probe.py`) |

## Development

```
pio run -d firmware                  # build firmware
pio run -d firmware -t upload        # flash
pio device monitor -b 115200         # serial monitor
cd bridge && uv run pytest           # tests
cd bridge && uv run ruff check . && uv run mypy .
```
