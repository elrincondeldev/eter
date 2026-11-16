"""Background listening: keep capturing on one frequency while nobody else needs the radio.

The node runs one command at a time and cannot abort one, so the monitor listens in short
chunks. Before each chunk it checks whether an AI/user request is waiting and, if so, lets it
go first. A request therefore waits at most one chunk.
"""

import threading
from typing import TYPE_CHECKING, Any

from eter_core.node import NodeError
from eter_core.storage import now_utc

if TYPE_CHECKING:
    from eter_core.eter import Eter

CHUNK_S = 5
PRESETS: dict[str, dict[str, Any]] = {
    "sensors_433": {
        "freq_mhz": 433.92,
        "description": "Weather stations, thermometers, door sensors and remotes at 433.92 MHz",
    },
}


class Monitor:
    def __init__(self, eter: "Eter") -> None:
        self._eter = eter
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._preset: str | None = None
        self._started: str | None = None
        self._chunks = 0
        self._frames = 0
        self._devices: set[str] = set()
        self._error: str | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, preset: str) -> dict[str, Any]:
        if preset not in PRESETS:
            raise NodeError("INVALID", f"unknown preset {preset!r}; available: {sorted(PRESETS)}")
        self._eter.require("rx_pulses")
        if self.running and preset == self._preset:
            return self.status()
        self.stop()
        self._preset, self._started, self._error = preset, now_utc(), None
        self._chunks = self._frames = 0
        self._devices = set()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(PRESETS[preset]["freq_mhz"],))
        self._thread.daemon = True
        self._thread.start()
        return self.status()

    def stop(self) -> dict[str, Any]:
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=CHUNK_S + 10)
            self._thread = None
        return self.status()

    def status(self) -> dict[str, Any]:
        status: dict[str, Any] = {"running": self.running, "presets": sorted(PRESETS)}
        if self._preset is not None:
            status |= {
                "preset": self._preset,
                "freq_mhz": PRESETS[self._preset]["freq_mhz"],
                "started": self._started,
                "chunks": self._chunks,
                "frames": self._frames,
                "devices_seen": sorted(self._devices),
                "error": self._error,
            }
        return status

    def _run(self, freq_mhz: float) -> None:
        while not self._stop.is_set():
            if self._eter.interactive_waiting:
                self._stop.wait(0.1)
                continue
            try:
                result = self._eter.capture(freq_mhz, CHUNK_S, None, kind="monitor")
            except NodeError as error:
                self._error = str(error)
                return
            self._chunks += 1
            self._frames += result["frames"]
            self._devices.update(d["device"] for d in result["devices"])
            self._stop.wait(0.05)
