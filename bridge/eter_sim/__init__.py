"""eter simulator: a virtual node that answers protocol v0 from recorded captures.

Point it at a directory of capture files (docs/protocol.md §6). The first `hello` found becomes
the node's identity; `scan` replays recorded spectra; `rx_pulses` replays recorded frames.
"""

import json
from collections import deque
from pathlib import Path
from typing import Any

Message = dict[str, Any]
SIM_NOISE_DBM = -100


class SimTransport:
    def __init__(self, captures_dir: Path) -> None:
        self.hello: Message | None = None
        self.spectra: list[Message] = []
        self.frames: list[Message] = []
        for path in sorted(captures_dir.glob("*.ndjson")):
            for line in path.read_text().splitlines():
                message = json.loads(line)
                if message["type"] == "hello" and self.hello is None:
                    self.hello = message
                elif message["type"] == "spectrum":
                    self.spectra.append(message)
                elif message["type"] == "pulses":
                    self.frames.append(message)
        if self.hello is None:
            raise FileNotFoundError(f"no capture with a hello message in {captures_dir}")
        self._out: deque[str] = deque()

    def write_line(self, line: str) -> None:
        request = json.loads(line)
        base = {"v": 0, "re": request["id"]} if "id" in request else {"v": 0}
        for reply in self._answer(request):
            self._out.append(json.dumps({**reply, **base}))

    def read_line(self, timeout_s: float) -> str | None:
        return self._out.popleft() if self._out else None

    def _answer(self, request: Message) -> list[Message]:
        assert self.hello is not None
        kind = request.get("type")
        if kind == "hello":
            return [self.hello]
        radios = {r["id"]: r for r in self.hello["capabilities"]["radios"]}
        if kind == "status":
            return [
                {
                    "type": "status",
                    "fw": self.hello["fw"],
                    "uptime_s": 0,
                    "heap_free": 0,
                    "radios": list(radios),
                }
            ]
        if kind not in ("scan", "rx_pulses"):
            return [_error("UNSUPPORTED", "unknown type")]
        radio = radios.get(request.get("radio", ""))
        if radio is None:
            return [_error("UNSUPPORTED", "unknown radio")]
        freqs = (
            [request["start_mhz"], request["end_mhz"]] if kind == "scan" else [request["freq_mhz"]]
        )
        if not all(any(lo <= f <= hi for lo, hi in radio["bands_mhz"]) for f in freqs):
            return [_error("OUT_OF_BAND", "frequency outside the radio's bands")]
        if kind == "scan":
            return [self._scan(request)]
        return self._rx_pulses(request)

    def _scan(self, request: Message) -> Message:
        start, end, step = request["start_mhz"], request["end_mhz"], request["step_khz"]
        points = int((end - start) * 1000 / step + 1e-3) + 1
        rssi = [self._reading(start + i * step / 1000) for i in range(points)]
        return {
            "type": "spectrum",
            "radio": request["radio"],
            "start_mhz": start,
            "step_khz": step,
            "rssi_dbm": rssi,
        }

    def _reading(self, freq_mhz: float) -> int:
        """Nearest recorded point within half a step, else simulated noise."""
        for s in self.spectra:
            i = round((freq_mhz - s["start_mhz"]) * 1000 / s["step_khz"])
            if 0 <= i < len(s["rssi_dbm"]):
                value: int = s["rssi_dbm"][i]
                return value
        return SIM_NOISE_DBM

    def _rx_pulses(self, request: Message) -> list[Message]:
        frames = [f for f in self.frames if abs(f["freq_mhz"] - request["freq_mhz"]) <= 0.2]
        done = {
            "type": "done",
            "frames": len(frames),
            "discarded": 0,
            "noise_floor_dbm": SIM_NOISE_DBM,
            "threshold_dbm": SIM_NOISE_DBM + 15,
        }
        return [*frames, done]


def _error(code: str, msg: str) -> Message:
    return {"type": "error", "code": code, "msg": msg}
