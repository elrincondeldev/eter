"""Wi-Fi nodes: the WebSocket server they connect to, pairing, and mDNS advertisement.

Contract: docs/protocol.md §1.1 (discovery, reconnection) and §1.2 (pairing).
"""

import asyncio
import json
import logging
import secrets
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from zeroconf import IPVersion
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

from eter_core.eter import Eter
from eter_core.node import PROTOCOL_V, Node, NodeError
from eter_core.transport import WebSocketTransport

log = logging.getLogger("eter.nodes")

NODE_PORT = 8765
NODE_PATH = "/node"
SERVICE_TYPE = "_eter._tcp.local."
PING_S = 20  # the bridge pings; a node that misses a pong for this long is dropped
HELLO_TIMEOUT_S = 10
APPROVAL_POLL_S = 1.0
PAIR_TIMEOUT_S = 5


class NodeServer:
    """Accepts Wi-Fi nodes. Paired ones are attached to `eter`; unpaired ones are ignored until
    the user approves them (`eter nodes approve`)."""

    def __init__(self, eter: Eter, host: str = "0.0.0.0", port: int = NODE_PORT) -> None:
        self._eter = eter
        self._host = host
        self.port = port
        self._links: dict[str, ServerConnection] = {}

    async def run(self, ready: asyncio.Event | None = None) -> None:
        async with serve(
            self._handle,
            self._host,
            self.port,
            ping_interval=PING_S,
            ping_timeout=PING_S,
            max_size=2**20,
        ) as server:
            self.port = next(iter(server.sockets)).getsockname()[1]
            log.info("waiting for Wi-Fi nodes on ws://%s:%d%s", self._host, self.port, NODE_PATH)
            if ready is not None:
                ready.set()
            await server.serve_forever()

    async def _handle(self, ws: ServerConnection) -> None:
        if ws.request is None or ws.request.path != NODE_PATH:
            await ws.close(1008, "unknown path")
            return
        address = str(ws.remote_address[0])
        hello = await _first_message(ws)
        if (
            hello is None
            or hello.get("type") != "hello"
            or not isinstance(hello.get("node_id"), str)
        ):
            await ws.close(1008, "expected hello")
            return
        if hello.get("v") != PROTOCOL_V:
            await _reject(
                ws, "VERSION", f"the bridge speaks protocol v{PROTOCOL_V}; update the node"
            )
            return
        node_id: str = hello["node_id"]
        store = self._eter.store
        if await asyncio.to_thread(store.node_state, node_id) == "paired":
            token = hello.get("token")
            if not (isinstance(token, str) and store.token_matches(node_id, token)):
                log.warning(
                    "node %s at %s: wrong pairing token, rejected. If you reset it, pair it "
                    "again: eter nodes forget %s, then eter nodes approve %s",
                    node_id,
                    address,
                    node_id,
                    node_id,
                )
                await _reject(ws, "UNAUTHORIZED", "invalid token: the node must be paired again")
                return
        state = await asyncio.to_thread(
            store.node_connected, node_id, address, str(hello.get("fw"))
        )
        if state != "paired" and not await self._pair(ws, node_id, address):
            return
        await self._serve(ws, node_id, address)

    async def _pair(self, ws: ServerConnection, node_id: str, address: str) -> bool:
        """Ignore the node until the user approves it, then give it a token."""
        log.info(
            "node %s at %s is waiting for approval: eter nodes approve %s",
            node_id,
            address,
            node_id,
        )
        store = self._eter.store
        discard = asyncio.create_task(_discard_messages(ws))
        try:
            while (state := await asyncio.to_thread(store.node_state, node_id)) == "pending":
                await asyncio.wait([discard], timeout=APPROVAL_POLL_S)
                if discard.done():
                    return False  # it went away before being approved
        finally:
            discard.cancel()
            with suppress(asyncio.CancelledError):
                await discard
        if state != "approved":
            await ws.close(1008, "not approved")  # forgotten meanwhile
            return False
        token = secrets.token_hex(16)
        await ws.send(json.dumps({"v": PROTOCOL_V, "id": 1, "type": "pair", "token": token}))
        try:
            async with asyncio.timeout(PAIR_TIMEOUT_S):
                async for message in ws:
                    reply = json.loads(message)
                    if reply.get("re") == 1 and reply.get("type") == "paired":
                        break
                else:
                    return False
        except (TimeoutError, ValueError, ConnectionClosed):
            log.warning("node %s did not confirm pairing", node_id)
            await ws.close()
            return False
        await asyncio.to_thread(store.set_paired, node_id, token)
        log.info("node %s paired", node_id)
        return True

    async def _serve(self, ws: ServerConnection, node_id: str, address: str) -> None:
        """Attach a paired node to eter for as long as the connection lasts."""
        if (old := self._links.get(node_id)) is not None:
            await old.close(1000, "replaced by a newer connection")
        self._links[node_id] = ws
        transport = WebSocketTransport(asyncio.get_running_loop(), ws.send)
        reader = asyncio.create_task(_feed(ws, transport))
        try:
            try:
                node = await asyncio.to_thread(Node, transport, "wifi")
            except NodeError as error:
                log.warning("node %s at %s did not answer hello: %s", node_id, address, error)
                return
            self._eter.attach(node)
            log.info("node %s connected from %s", node_id, address)
            try:
                await reader
            finally:
                self._eter.detach(node)
                log.info("node %s disconnected", node_id)
        finally:
            reader.cancel()
            if self._links.get(node_id) is ws:
                del self._links[node_id]


async def _first_message(ws: ServerConnection) -> dict[str, Any] | None:
    try:
        message = json.loads(await asyncio.wait_for(ws.recv(), HELLO_TIMEOUT_S))
    except (TimeoutError, ValueError, ConnectionClosed):
        return None
    return message if isinstance(message, dict) else None


async def _reject(ws: ServerConnection, code: str, msg: str) -> None:
    with suppress(ConnectionClosed):
        await ws.send(json.dumps({"v": PROTOCOL_V, "type": "error", "code": code, "msg": msg}))
    await ws.close(1008, code)


async def _discard_messages(ws: ServerConnection) -> None:
    with suppress(ConnectionClosed):
        async for _ in ws:
            pass


async def _feed(ws: ServerConnection, transport: WebSocketTransport) -> None:
    try:
        async for message in ws:
            if isinstance(message, str):
                transport.feed(message)
    except ConnectionClosed:
        pass
    finally:
        transport.close()


@asynccontextmanager
async def advertise(port: int) -> AsyncIterator[None]:
    """Announce the bridge on the local network as `_eter._tcp` so nodes find it."""
    try:
        ip = _lan_ip()
    except OSError as error:
        log.warning("not advertising over mDNS (no network?): %s", error)
        yield
        return
    host = socket.gethostname().split(".")[0]
    info = AsyncServiceInfo(
        SERVICE_TYPE,
        f"eter bridge on {host}.{SERVICE_TYPE}",
        addresses=[socket.inet_aton(ip)],
        port=port,
        properties={"path": NODE_PATH, "v": str(PROTOCOL_V)},
        server=f"eter-{host}.local.",
    )
    zeroconf = AsyncZeroconf(interfaces=[ip], ip_version=IPVersion.V4Only)
    await zeroconf.async_register_service(info)
    log.info("advertising %s at %s:%d over mDNS", SERVICE_TYPE, ip, port)
    try:
        yield
    finally:
        await zeroconf.async_unregister_service(info)
        await zeroconf.async_close()


def _lan_ip() -> str:
    """The address other devices on the LAN reach this computer at (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.0.2.1", 9))  # TEST-NET-1: only picks the outgoing interface
        return str(probe.getsockname()[0])
