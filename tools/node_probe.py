# /// script
# requires-python = ">=3.12"
# dependencies = ["pyserial", "matplotlib"]
# ///
"""Talk to an eter node over USB with node protocol v0 (docs/protocol.md).

Phase 2 dev tool, not part of the bridge.

    uv run tools/node_probe.py hello
    uv run tools/node_probe.py scan --start 430 --end 440 --step 25
    uv run tools/node_probe.py listen --freq 433.92 --seconds 10

Captures go to captures/ (format: docs/protocol.md §6); plots go to tmp/.
"""

import argparse
import json
import sys
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import serial
from serial.tools import list_ports

ROOT = Path(__file__).resolve().parent.parent
CAPTURES = ROOT / "captures"
PLOTS = ROOT / "tmp"
FINAL_TYPES = {"hello", "status", "spectrum", "done", "error"}

Message = dict[str, Any]


def find_port() -> str:
    ports = [p.device for p in list_ports.comports() if "usbmodem" in p.device]
    if len(ports) != 1:
        found = [p.device for p in list_ports.comports()]
        sys.exit(f"Could not pick the node's port automatically. Use --port. Found: {found}")
    return ports[0]


class Node:
    def __init__(self, port: str) -> None:
        self.serial = serial.Serial(port, 115200, timeout=0.2)
        time.sleep(0.2)
        self.serial.reset_input_buffer()
        self.next_id = 1

    def request(self, msg: Message, timeout_s: float) -> Iterator[Message]:
        """Send a command and yield its replies until the final one."""
        msg = {"v": 0, "id": self.next_id, **msg}
        self.next_id += 1
        self.serial.write((json.dumps(msg) + "\n").encode())
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            raw = self.serial.readline()
            if not raw:
                continue
            try:
                reply = json.loads(raw)
            except ValueError:
                print(f"[not JSON] {raw!r}", file=sys.stderr)
                continue
            if reply.get("type") == "log":
                print(f"[node {reply.get('level')}] {reply.get('msg')}", file=sys.stderr)
                continue
            if reply.get("re") != msg["id"]:
                continue  # unsolicited (e.g. hello after a reset)
            yield reply
            if reply.get("type") in FINAL_TYPES:
                return
        sys.exit(f"Timed out waiting for the node to answer {msg['type']}")


def save_capture(kind: str, messages: list[Message]) -> Path:
    CAPTURES.mkdir(exist_ok=True)
    path = CAPTURES / f"{kind}-{datetime.now():%Y%m%d-%H%M%S}.ndjson"
    path.write_text("".join(json.dumps(m) + "\n" for m in messages))
    return path


def check(reply: Message) -> Message:
    if reply["type"] == "error":
        sys.exit(f"Node error {reply['code']}: {reply['msg']}")
    return reply


def cmd_hello(node: Node, args: argparse.Namespace) -> None:
    for reply in node.request({"type": "hello"}, timeout_s=3):
        print(json.dumps(check(reply), indent=2))


def cmd_scan(node: Node, args: argparse.Namespace) -> None:
    hello = check(next(node.request({"type": "hello"}, timeout_s=3)))
    req = {
        "type": "scan",
        "radio": args.radio,
        "start_mhz": args.start,
        "end_mhz": args.end,
        "step_khz": args.step,
    }
    points = (args.end - args.start) * 1000 / args.step + 1
    spectrum = check(next(node.request(req, timeout_s=points * 0.005 + 5)))

    rssi = spectrum["rssi_dbm"]
    freqs = [spectrum["start_mhz"] + i * spectrum["step_khz"] / 1000 for i in range(len(rssi))]
    print(f"{len(rssi)} points, noise floor (median) {sorted(rssi)[len(rssi) // 2]} dBm")
    print("Strongest readings:")
    for f, r in sorted(zip(freqs, rssi), key=lambda fr: fr[1], reverse=True)[:5]:
        print(f"  {f:8.3f} MHz  {r:5d} dBm")
    print(f"Capture: {save_capture('spectrum', [hello, spectrum])}")

    import matplotlib.pyplot as plt

    PLOTS.mkdir(exist_ok=True)
    plt.plot(freqs, rssi)
    plt.xlabel("Frequency (MHz)")
    plt.ylabel("RSSI (dBm)")
    plt.title(f"{hello['node_id']} · {args.start}–{args.end} MHz · step {args.step} kHz")
    plt.grid(True)
    plot = PLOTS / f"spectrum-{datetime.now():%Y%m%d-%H%M%S}.png"
    plt.savefig(plot)
    print(f"Plot: {plot}")
    if not args.no_show:
        plt.show()


def cmd_listen(node: Node, args: argparse.Namespace) -> None:
    hello = check(next(node.request({"type": "hello"}, timeout_s=3)))
    req: Message = {
        "type": "rx_pulses",
        "radio": args.radio,
        "freq_mhz": args.freq,
        "seconds": args.seconds,
    }
    if args.threshold is not None:
        req["rssi_threshold_dbm"] = args.threshold
    print(f"Listening on {args.freq} MHz for {args.seconds} s. Press a remote now.")

    messages = [hello]
    for reply in node.request(req, timeout_s=args.seconds + 5):
        check(reply)
        messages.append(reply)
        if reply["type"] == "pulses":
            t = reply["t_us"]
            more = " (truncated)" if reply["truncated"] else ""
            print(
                f"Frame {len(messages) - 1}: {(len(t) + 1) // 2} pulses, "
                f"peak {reply['rssi_dbm']} dBm{more}"
            )
            print(f"  t_us: {' '.join(map(str, t[:24]))}{' …' if len(t) > 24 else ''}")
        elif reply["type"] == "done":
            print(
                f"Done: {reply['frames']} frames ({reply['discarded']} discarded), "
                f"noise floor {reply['noise_floor_dbm']} dBm, "
                f"threshold {reply['threshold_dbm']} dBm"
            )
            if reply["frames"]:
                print(f"Capture: {save_capture(f'pulses-{args.freq}', messages)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", help="serial port (default: the only /dev/*usbmodem*)")
    parser.add_argument("--radio", default="r433")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("hello", help="show the node's identity and capabilities")

    scan = sub.add_parser("scan", help="RSSI sweep, plot and capture")
    scan.add_argument("--start", type=float, default=430.0, help="MHz")
    scan.add_argument("--end", type=float, default=440.0, help="MHz")
    scan.add_argument("--step", type=float, default=25.0, help="kHz")
    scan.add_argument("--no-show", action="store_true", help="save the plot without opening it")

    listen = sub.add_parser("listen", help="capture OOK pulse frames")
    listen.add_argument("--freq", type=float, default=433.92, help="MHz")
    listen.add_argument("--seconds", type=int, default=10)
    listen.add_argument("--threshold", type=float, help="RSSI threshold in dBm (default: auto)")

    args = parser.parse_args()
    node = Node(args.port or find_port())
    {"hello": cmd_hello, "scan": cmd_scan, "listen": cmd_listen}[args.command](node, args)


if __name__ == "__main__":
    main()
