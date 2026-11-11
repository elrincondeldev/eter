# eter

An open-source harness that gives AI agents access to the radio spectrum through cheap hardware.

A **node** (reference: ESP32-S3 + CC1101 433 MHz) captures signals. A **bridge** (Python) decodes and stores them and exposes them through MCP, a CLI, a library and an optional REST API.

> Status: pre-development. Phase 1 (CC1101 wiring and live RSSI) done; next is Phase 2 (firmware v0.1 + node protocol v0).

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
