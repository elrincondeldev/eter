# eter

An open-source harness that gives AI agents access to the radio spectrum through cheap hardware.

A **node** (reference: ESP32-S3 + CC1101 433 MHz) captures signals. A **bridge** (Python) decodes and stores them and exposes them through MCP, a CLI, a library and an optional REST API.

> Status: pre-development. Phase 2 (firmware 0.1.0 + [node protocol v0](docs/protocol.md)) done; next is Phase 3 (bridge + MCP). Pending: check OOK pulse quality with a real OOK device (doorbell, RF plug) before Phase 4.

## Try the node

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
| `captures/` | Recorded sample captures for tests and the simulator |

## Development

```
pio run -d firmware                  # build firmware
pio run -d firmware -t upload        # flash
pio device monitor -b 115200         # serial monitor
cd bridge && uv run pytest           # tests
cd bridge && uv run ruff check . && uv run mypy .
```
