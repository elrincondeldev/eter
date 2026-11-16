# eter

An open-source harness that gives AI agents access to the radio spectrum through cheap hardware.

A **node** (reference: ESP32-S3 + CC1101 433 MHz) captures signals. A **bridge** (Python) decodes and stores them and exposes them through MCP, a CLI, a library and an optional REST API.

> Status: pre-development. Phase 3 (bridge + MCP) done: Claude Code can scan and listen through the node. Next is Phase 4 (decoding). Pending first: check OOK pulse quality with a real OOK device (doorbell, RF plug).

## Use it from Claude Code

The repo's `.mcp.json` registers the `eter` MCP server. Open Claude Code in the repo root, approve the `eter` server, and ask things like *"what is on the air between 430 and 440 MHz?"*.

| Tool | What it does | Needs |
|---|---|---|
| `node_status` | Connected node, firmware, radios and bands | — |
| `identify_frequency` | Typical uses of a frequency (Europe) | — |
| `query_events` | History of scans and listens | — |
| `scan_spectrum` | Sweep a range: noise floor and peaks | a radio with `rssi_scan` |
| `listen` | Capture on/off transmissions on one frequency | a radio with `rx_pulses` |

The same operations are available as a CLI that prints JSON:

```
uv run --directory bridge eter status
uv run --directory bridge eter scan --start 430 --end 440
uv run --directory bridge eter listen --freq 433.92 --seconds 10
uv run --directory bridge eter identify 446.1
uv run --directory bridge eter events --since 60
```

Configuration (environment variables):

| Variable | Default | Meaning |
|---|---|---|
| `ETER_PORT` | the only `/dev/*usbmodem*` / `ttyACM*` | Serial port of the node |
| `ETER_SIM` | — | Use the simulator with this captures directory instead of hardware (absolute path, e.g. `$PWD/captures`) |
| `ETER_DB` | `~/.eter/eter.db` | SQLite history |

Only one program can use the node's USB port at a time: close the serial monitor and `node_probe.py` before using the MCP server or the CLI.

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
