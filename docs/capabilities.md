# Node capabilities — v0

Every node announces what it can do in the `capabilities` object of its `hello` message ([protocol.md](protocol.md)). The bridge only offers the AI tools that some connected node supports: no capability, no tool.

## Schema

```json
{
  "radios": [
    {
      "id": "r433",
      "chip": "cc1101",
      "bands_mhz": [[387, 464]],
      "tuned_mhz": 433,
      "modulations": ["ook"],
      "rx": true,
      "tx": false,
      "features": ["rssi_scan", "rx_pulses"]
    }
  ],
  "builtin": []
}
```

### `radios[]`

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Unique per node. Commands address the radio by this id |
| `chip` | string | Radio chip, lowercase (`cc1101`, `sx1276`, …) |
| `bands_mhz` | `[[min, max], …]` | Frequency ranges the node accepts for this radio |
| `tuned_mhz` | number | Frequency the antenna/matching network is built for. Reception far from it is degraded |
| `modulations` | string[] | Modulations available for reception: `ook`, `2fsk`, `gfsk`, … |
| `rx` | bool | Can receive |
| `tx` | bool | Can transmit. Always `false` until TX is implemented (Phase 9) |
| `features` | string[] | Commands this radio supports (below) |

### Features

| Feature | Enables command |
|---|---|
| `rssi_scan` | `scan` |
| `rx_pulses` | `rx_pulses` |

### `builtin[]`

Non-radio-chip abilities of the node (e.g. `ble_scan`, `wifi_scan` on ESP32). Empty in v0.

## Reference node

The ESP32-S3 + CC1101 433 MHz node reports exactly the example above. The CC1101 chip also covers 300–348 and 779–928 MHz, but the reference module's antenna is matched for 433 MHz, so only the 387–464 MHz band is announced.
