"""Wi-Fi nodes, pairing and the bridge daemon, against eter_sim (no hardware)."""

import asyncio
import json
import shutil
import socket
import threading
import time
from collections.abc import Callable, Coroutine, Iterator
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import anyio
import pytest
import uvicorn
from mcp import Client
from typer.testing import CliRunner

from eter_cli.main import app
from eter_core import Eter, Node, monitor
from eter_core.nodeserver import NodeServer
from eter_core.storage import Store
from eter_mcp.server import build_server
from eter_sim import SimTransport, run_wifi_node

CAPTURES = Path(__file__).resolve().parents[2] / "captures"
NODE_ID = "esp32s3-000000"


@pytest.fixture
def captures(tmp_path: Path) -> Path:
    directory = tmp_path / "captures"
    directory.mkdir()
    for name in ("spectrum-430-440.ndjson", "synthetic-ook-433.92.ndjson"):
        shutil.copy(CAPTURES / name, directory)
    return directory


def wait_for(condition: Callable[[], bool], timeout_s: float = 5) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


class Loop:
    """An asyncio loop in a background thread, standing in for the daemon's."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._thread.start()

    def start(self, coroutine: Coroutine[Any, Any, Any]) -> "Future[Any]":
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def close(self) -> None:
        async def cancel_all() -> None:
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        asyncio.run_coroutine_threadsafe(cancel_all(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(5)


@pytest.fixture
def loop() -> Iterator[Loop]:
    background = Loop()
    yield background
    background.close()


@pytest.fixture
def bridge(tmp_path: Path, loop: Loop) -> tuple[Eter, str]:
    """A daemon with no node yet, listening for Wi-Fi nodes on a free local port."""
    eter = Eter(None, Store(tmp_path / "eter.db"), node_error="waiting")
    server = NodeServer(eter, host="127.0.0.1", port=0)
    ready = threading.Event()

    async def run() -> None:
        started = asyncio.Event()
        task = asyncio.create_task(server.run(started))
        await started.wait()
        ready.set()
        await task

    loop.start(run())
    assert ready.wait(5)
    return eter, f"ws://127.0.0.1:{server.port}/node"


def test_unpaired_node_waits_until_approved(
    bridge: tuple[Eter, str], loop: Loop, captures: Path, tmp_path: Path
) -> None:
    eter, url = bridge
    token_file = tmp_path / "token"
    loop.start(run_wifi_node(url, captures, token_file))
    wait_for(lambda: eter.store.node_state(NODE_ID) == "pending")
    time.sleep(0.2)
    assert eter.node is None  # ignored while pending
    assert eter.status()["pending_approval"] == [NODE_ID]

    assert eter.store.approve_node(NODE_ID) == "approved"
    wait_for(lambda: eter.node is not None)
    assert eter.node is not None and eter.node.link == "wifi"
    assert eter.store.node_state(NODE_ID) == "paired"
    token = token_file.read_text()
    assert len(token) == 32
    assert token not in json.dumps(eter.store.nodes())  # only its hash is kept
    assert "token" not in eter.node.hello
    # Requests now travel over the WebSocket.
    assert eter.scan(430.0, 440.0, 25)["peaks"][0]["freq_mhz"] == 434.7
    assert eter.status()["link"] == "wifi"


def test_paired_node_reconnects_and_wrong_token_is_rejected(
    bridge: tuple[Eter, str], loop: Loop, captures: Path, tmp_path: Path
) -> None:
    eter, url = bridge
    token_file = tmp_path / "token"
    first = loop.start(run_wifi_node(url, captures, token_file))
    wait_for(lambda: eter.store.node_state(NODE_ID) == "pending")
    eter.store.approve_node(NODE_ID)
    wait_for(lambda: eter.node is not None)

    # The node drops (Wi-Fi loss, reboot): requests fail clearly, then it comes back by itself.
    first.cancel()
    wait_for(lambda: eter.node is None)
    loop.start(run_wifi_node(url, captures, token_file))
    wait_for(lambda: eter.node is not None)
    assert eter.status()["connected"]

    # Same node_id with a wrong token: rejected, and the paired link is not replaced.
    impostor = tmp_path / "impostor-token"
    impostor.write_text("0" * 32)
    loop.start(run_wifi_node(url, captures, impostor)).result()  # returns once rejected
    assert eter.node is not None and eter.status()["connected"]


def test_forgotten_node_must_be_approved_again(
    bridge: tuple[Eter, str], loop: Loop, captures: Path, tmp_path: Path
) -> None:
    eter, url = bridge
    token_file = tmp_path / "token"
    token_file.write_text("f" * 32)  # a token from an earlier pairing
    loop.start(run_wifi_node(url, captures, token_file))
    wait_for(lambda: eter.store.node_state(NODE_ID) == "pending")
    assert eter.store.forget_node(NODE_ID)
    time.sleep(1.5)
    assert eter.node is None


def test_tools_appear_when_a_node_connects(captures: Path, tmp_path: Path) -> None:
    eter = Eter(None, Store(tmp_path / "eter.db"), node_error="waiting")
    server = build_server(eter)
    always = {"node_status", "identify_frequency", "query_events", "list_devices", "label_device"}
    assert set(server._tool_manager._tools) == always
    node = Node(SimTransport(captures), "sim")
    eter.attach(node)
    assert set(server._tool_manager._tools) == always | {
        "scan_spectrum",
        "listen",
        "analyze_capture",
        "monitor",
    }
    eter.detach(node)  # tools stay; calls explain that the node is gone
    assert "scan_spectrum" in server._tool_manager._tools


def test_tool_errors_reach_the_model(captures: Path, tmp_path: Path) -> None:
    eter = Eter(Node(SimTransport(captures), "sim"), Store(tmp_path / "eter.db"))

    async def call() -> Any:
        async with Client(build_server(eter)) as client:
            return await client.call_tool("scan_spectrum", {"start_mhz": 470, "end_mhz": 480})

    result = anyio.run(call)
    assert result.is_error
    assert "OUT_OF_BAND" in result.content[0].text


def test_monitor_keeps_going_when_the_node_drops(
    captures: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(monitor, "RETRY_S", 0.05)
    node = Node(SimTransport(captures), "sim")
    eter = Eter(node, Store(tmp_path / "eter.db"))
    eter.set_monitor("sensors_433", enabled=True)
    wait_for(lambda: eter.monitor.status()["chunks"] >= 1)
    eter.detach(node)
    wait_for(lambda: eter.monitor.status()["error"] is not None)
    assert eter.monitor.running
    chunks = eter.monitor.status()["chunks"]
    eter.attach(Node(SimTransport(captures), "sim"))
    wait_for(lambda: eter.monitor.status()["chunks"] > chunks)
    assert eter.monitor.status()["error"] is None
    eter.set_monitor("sensors_433", enabled=False)


def test_cli_nodes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ETER_DB", str(tmp_path / "cli.db"))
    Store(tmp_path / "cli.db").node_connected(NODE_ID, "192.168.1.23", "0.2.0")
    runner = CliRunner()
    listed = json.loads(runner.invoke(app, ["nodes"]).output)["nodes"]
    assert [(n["node_id"], n["state"]) for n in listed] == [(NODE_ID, "pending")]
    approved = runner.invoke(app, ["nodes", "approve", NODE_ID])
    assert json.loads(approved.output)["state"] == "approved"
    assert runner.invoke(app, ["nodes", "approve", "nope"]).exit_code == 1
    assert runner.invoke(app, ["nodes", "forget", NODE_ID]).exit_code == 0
    assert json.loads(runner.invoke(app, ["nodes"]).output)["nodes"] == []


def test_cli_goes_through_the_running_daemon(
    captures: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    eter = Eter(Node(SimTransport(captures), "sim"), Store(tmp_path / "daemon.db"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    http = build_server(eter).streamable_http_app(host="127.0.0.1")
    server = uvicorn.Server(uvicorn.Config(http, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        wait_for(lambda: server.started)
        monkeypatch.delenv("ETER_SIM", raising=False)
        monkeypatch.delenv("ETER_PORT", raising=False)
        monkeypatch.setenv("ETER_MCP_PORT", str(port))
        monkeypatch.setenv("ETER_DB", str(tmp_path / "cli.db"))  # must not be used
        result = CliRunner().invoke(app, ["scan"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["peaks"][0]["freq_mhz"] == 434.7
        assert eter.events(kind="scan")["count"] == 1  # recorded by the daemon
        failed = CliRunner().invoke(app, ["scan", "--start", "470", "--end", "480"])
        assert failed.exit_code == 1 and "OUT_OF_BAND" in failed.output
    finally:
        server.should_exit = True
        thread.join(5)
