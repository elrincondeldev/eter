"""`eter` command line: the same operations as the MCP server, printed as JSON.

When the bridge daemon (`eter serve`) is running, commands go through it, since it owns the
nodes. Otherwise the CLI opens the node itself (USB, or ETER_SIM).
"""

import asyncio
import json
import logging
import os
import socket
import time
from typing import Annotated, Any

import anyio
import typer
from mcp import Client

from eter_core import Eter, NodeError, open_eter
from eter_core.eter import open_store
from eter_core.nodeserver import NODE_PORT
from eter_mcp.server import mcp_port
from eter_mcp.server import serve as serve_daemon

app = typer.Typer(
    help="eter: radio spectrum access for humans and AI agents.", no_args_is_help=True
)
nodes_app = typer.Typer(help="Wi-Fi nodes: list them, approve new ones, forget old ones.")
app.add_typer(nodes_app, name="nodes")


def _print(result: dict[str, Any]) -> None:
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


def _fail(error: Exception) -> typer.Exit:
    typer.echo(f"error: {error}", err=True)
    return typer.Exit(1)


class _Bridge:
    """Runs operations on the daemon if it is up, else on a node opened by this process."""

    def __init__(self) -> None:
        self.url: str | None = None
        self.eter: Eter | None = None
        if _daemon_running():
            self.url = f"http://127.0.0.1:{mcp_port()}/mcp"
        else:
            self.eter = open_eter()

    def call(self, tool: str, method: str, **kwargs: Any) -> dict[str, Any]:
        """`tool`: MCP tool name; `method`: the Eter method behind it (same arguments)."""
        if self.eter is not None:
            result: dict[str, Any] = getattr(self.eter, method)(**kwargs)
            return result
        assert self.url is not None
        url = self.url

        async def remote() -> Any:
            async with Client(url) as client:
                return await client.call_tool(tool, kwargs)

        reply = anyio.run(remote)
        if reply.is_error or not isinstance(reply.structured_content, dict):
            text = " ".join(getattr(c, "text", "") for c in reply.content)
            raise NodeError("BRIDGE", text or f"{tool} is not available on the bridge")
        return reply.structured_content


def _daemon_running() -> bool:
    if os.environ.get("ETER_SIM") or os.environ.get("ETER_PORT"):
        return False  # explicitly asked for a node of our own
    try:
        socket.create_connection(("127.0.0.1", mcp_port()), timeout=0.3).close()
    except OSError:
        return False
    return True


def _run(tool: str, method: str, **kwargs: Any) -> None:
    try:
        _print(_Bridge().call(tool, method, **kwargs))
    except NodeError as error:
        raise _fail(error) from error


@app.command()
def serve(
    usb: Annotated[bool, typer.Option(help="also use the node plugged in over USB")] = False,
    port: Annotated[int, typer.Option(help="port Wi-Fi nodes connect to")] = NODE_PORT,
) -> None:
    """Run the bridge: Wi-Fi nodes connect to it, and Claude Code and the CLI use it (MCP at
    http://127.0.0.1:8766/mcp, or ETER_MCP_PORT). Ctrl+C stops it."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("websockets").setLevel(logging.WARNING)  # one line per connection otherwise
    eter = open_eter(connect=usb)
    if usb and eter.node is None:
        typer.echo(f"warning: no USB node ({eter.node_error}); waiting for Wi-Fi nodes", err=True)
    try:
        asyncio.run(serve_daemon(eter, node_port=port, port=mcp_port()))
    except KeyboardInterrupt:
        pass


@nodes_app.callback(invoke_without_command=True)
def nodes(ctx: typer.Context) -> None:
    """Wi-Fi nodes that have connected to this bridge, with their pairing state."""
    if ctx.invoked_subcommand is None:
        _print({"nodes": open_store().nodes()})


@nodes_app.command()
def approve(node_id: Annotated[str, typer.Argument(help="node_id from `eter nodes`")]) -> None:
    """Let a pending node use this bridge. It is paired as soon as it is connected to the
    running bridge (`eter serve`)."""
    state = open_store().approve_node(node_id)
    if state is None:
        raise _fail(NodeError("INVALID", f"unknown node {node_id!r}; see `eter nodes`"))
    _print({"node_id": node_id, "state": state})


@nodes_app.command()
def forget(node_id: Annotated[str, typer.Argument(help="node_id from `eter nodes`")]) -> None:
    """Unpair a node. If it connects again it waits for approval, with a new token."""
    if not open_store().forget_node(node_id):
        raise _fail(NodeError("INVALID", f"unknown node {node_id!r}; see `eter nodes`"))
    _print({"node_id": node_id, "state": "forgotten"})


@app.command()
def status() -> None:
    """Connected node, firmware and radios."""
    _run("node_status", "status")


@app.command()
def monitor(
    preset: Annotated[str, typer.Option(help="sensors_433")] = "sensors_433",
    minutes: Annotated[float, typer.Option(help="how long; Ctrl+C stops earlier")] = 5.0,
) -> None:
    """Listen in the background for a while, announcing devices as they are decoded."""
    bridge = _Bridge()

    def monitor_call(enabled: bool) -> dict[str, Any]:
        # Starting the preset that is already running just returns its status.
        return bridge.call("monitor", "set_monitor", preset=preset, enabled=enabled)

    try:
        status = monitor_call(True)
        typer.echo(f"Monitoring {preset} for {minutes:g} min (Ctrl+C to stop)...", err=True)
        announced: set[str] = set()
        deadline = time.monotonic() + minutes * 60
        try:
            while time.monotonic() < deadline and status["running"]:
                time.sleep(1)
                status = monitor_call(True)
                for device in status.get("devices_seen", []):
                    if device not in announced:
                        announced.add(device)
                        typer.echo(f"{time.strftime('%H:%M:%S')} new device: {device}", err=True)
        except KeyboardInterrupt:
            pass
        _print(monitor_call(False))
    except NodeError as error:
        raise _fail(error) from error


@app.command()
def scan(
    start: Annotated[float, typer.Option(help="MHz")] = 430.0,
    end: Annotated[float, typer.Option(help="MHz")] = 440.0,
    step: Annotated[float, typer.Option(help="kHz")] = 25.0,
) -> None:
    """Sweep a range: noise floor and strongest peaks."""
    _run("scan_spectrum", "scan", start_mhz=start, end_mhz=end, step_khz=step)


@app.command()
def listen(
    freq: Annotated[float, typer.Option(help="MHz")] = 433.92,
    seconds: Annotated[int, typer.Option(help="1-60")] = 10,
    threshold: Annotated[float | None, typer.Option(help="dBm; default noise floor + 15")] = None,
) -> None:
    """Capture on/off transmissions on one frequency."""
    _run("listen", "listen", freq_mhz=freq, seconds=seconds, rssi_threshold_dbm=threshold)


@app.command()
def identify(freq: Annotated[float, typer.Argument(help="MHz")]) -> None:
    """Typical uses of a frequency."""
    _run("identify_frequency", "identify", freq_mhz=freq)


@app.command()
def events(
    kind: Annotated[str | None, typer.Option(help="scan or listen")] = None,
    since: Annotated[int | None, typer.Option(help="minutes")] = None,
    limit: Annotated[int, typer.Option()] = 20,
) -> None:
    """History of scans and listens, newest first."""
    _run("query_events", "events", kind=kind, since_minutes=since, limit=limit)


@app.command()
def analyze(
    event_id: Annotated[int, typer.Argument(help="event_id of a listen")],
    frames: Annotated[int, typer.Option(help="frames to analyze")] = 3,
) -> None:
    """Pulse widths, modulation guess and bits of the frames of a listen."""
    _run("analyze_capture", "analyze", event_id=event_id, max_frames=frames)


@app.command()
def devices(limit: Annotated[int, typer.Option()] = 50) -> None:
    """Decoded devices, most recently seen first."""
    _run("list_devices", "devices", limit=limit)


@app.command()
def label(
    device: Annotated[str, typer.Argument(help="device key from `eter devices`")],
    name: Annotated[str, typer.Argument(help="label to give it")],
    owned: Annotated[bool, typer.Option(help="the device is yours")] = False,
) -> None:
    """Name a decoded device."""
    _run("label_device", "label_device", device=device, label=name, owned=owned)
