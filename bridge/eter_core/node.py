"""A connected node: request/response over a transport (docs/protocol.md)."""

import json
import threading
import time
from typing import Any

from eter_core.transport import Transport

Message = dict[str, Any]
PROTOCOL_V = 1
FINAL_TYPES = {"hello", "status", "spectrum", "paired", "done", "error"}


class NodeError(Exception):
    """The node answered with an `error` message, or did not answer in time."""

    def __init__(self, code: str, msg: str) -> None:
        super().__init__(f"{code}: {msg}")
        self.code = code
        self.msg = msg


class Node:
    def __init__(self, transport: Transport, link: str = "usb") -> None:
        """`link`: how the node is reached, `usb`, `wifi` or `sim`."""
        self._transport = transport
        self.link = link
        self._next_id = 1
        # The node runs one command at a time; so do we.
        self._lock = threading.Lock()
        self.hello = self.request({"type": "hello"}, timeout_s=3)[-1]
        self.hello.pop("token", None)  # a secret: keep it out of everything we show or store

    @property
    def node_id(self) -> str:
        return str(self.hello["node_id"])

    @property
    def radios(self) -> list[Message]:
        radios: list[Message] = self.hello["capabilities"]["radios"]
        return radios

    def radio_with(self, feature: str) -> Message | None:
        return next((r for r in self.radios if feature in r["features"]), None)

    def request(self, msg: Message, timeout_s: float) -> list[Message]:
        """Send a command; return all its replies, the final one last. Raises NodeError."""
        try:
            return self._request(msg, timeout_s)
        except OSError as error:
            raise NodeError("DISCONNECTED", f"lost the link to the node: {error}") from error

    def _request(self, msg: Message, timeout_s: float) -> list[Message]:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            self._transport.write_line(json.dumps({"v": PROTOCOL_V, "id": request_id, **msg}))
            replies: list[Message] = []
            deadline = time.monotonic() + timeout_s
            while (remaining := deadline - time.monotonic()) > 0:
                line = self._transport.read_line(remaining)
                if not line:
                    continue
                try:
                    reply = json.loads(line)
                except ValueError:
                    continue  # boot noise from the chip, not protocol
                if reply.get("re") != request_id:
                    continue  # unsolicited: log, or hello after a reset
                if reply["type"] == "error":
                    raise NodeError(reply["code"], reply["msg"])
                replies.append(reply)
                if reply["type"] in FINAL_TYPES:
                    return replies
            raise NodeError("TIMEOUT", f"no reply to {msg['type']} within {timeout_s:.0f} s")
