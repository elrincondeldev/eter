"""Links to a node. Every transport carries NDJSON lines (docs/protocol.md §1)."""

import time
from typing import Protocol

import serial
from serial.tools import list_ports


class Transport(Protocol):
    def write_line(self, line: str) -> None: ...

    def read_line(self, timeout_s: float) -> str | None:
        """Next complete line without the newline, or None if none arrived in time."""
        ...


class SerialTransport:
    """USB serial link to a hardware node."""

    def __init__(self, port: str) -> None:
        self._serial = serial.Serial(port, 115200, timeout=0.2)
        self._serial.reset_input_buffer()
        self._buffer = b""

    def write_line(self, line: str) -> None:
        self._serial.write((line + "\n").encode())

    def read_line(self, timeout_s: float) -> str | None:
        deadline = time.monotonic() + timeout_s
        while not self._buffer.endswith(b"\n"):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._serial.timeout = remaining
            self._buffer += self._serial.readline()
        line, self._buffer = self._buffer, b""
        return line.decode(errors="replace").strip()


def find_serial_port() -> str:
    """The node's USB port, if exactly one candidate is plugged in."""
    ports = [
        p.device for p in list_ports.comports() if "usbmodem" in p.device or "ttyACM" in p.device
    ]
    if len(ports) != 1:
        found = [p.device for p in list_ports.comports()]
        raise ConnectionError(
            f"expected one node on USB, found {ports or 'none'} (all ports: {found}); set ETER_PORT"
        )
    return ports[0]
