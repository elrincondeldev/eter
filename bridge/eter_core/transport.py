"""Links to a node. Every transport carries one JSON message per line (docs/protocol.md §1)."""

import asyncio
import queue
import time
from collections.abc import Callable, Coroutine
from typing import Any, Protocol

import serial
from serial.tools import list_ports


class Transport(Protocol):
    def write_line(self, line: str) -> None: ...

    def read_line(self, timeout_s: float) -> str | None:
        """Next complete line without the newline, or None if none arrived in time.
        Raises OSError (e.g. ConnectionError) once the link is lost."""
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


class WebSocketTransport:
    """WebSocket link to a node that connected to the bridge (the node is the client).

    The connection lives on an asyncio loop while Node calls come from worker threads: the
    server's receive loop feeds incoming messages in, and writes are scheduled on the loop.
    """

    def __init__(
        self, loop: asyncio.AbstractEventLoop, send: Callable[[str], Coroutine[Any, Any, None]]
    ) -> None:
        self._loop = loop
        self._send = send
        self._inbox: queue.Queue[str | None] = queue.Queue()
        self._closed = False

    def feed(self, message: str) -> None:
        self._inbox.put(message)

    def close(self) -> None:
        self._closed = True
        self._inbox.put(None)

    def write_line(self, line: str) -> None:
        if self._closed:
            raise ConnectionError("the node disconnected")
        future = asyncio.run_coroutine_threadsafe(self._send(line), self._loop)
        try:
            future.result(timeout=10)
        except Exception as error:
            raise ConnectionError(f"the node disconnected ({error})") from error

    def read_line(self, timeout_s: float) -> str | None:
        try:
            line = self._inbox.get(timeout=max(timeout_s, 0))
        except queue.Empty:
            return None
        if line is None:
            self._inbox.put(None)  # keep reporting the closed link
            raise ConnectionError("the node disconnected")
        return line


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
