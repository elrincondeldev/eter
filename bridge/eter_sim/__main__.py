"""Run a simulated Wi-Fi node against a running bridge (`eter serve`), reconnecting like the
real firmware: uv run python -m eter_sim --captures ../captures"""

import argparse
import asyncio
from pathlib import Path

from websockets.exceptions import WebSocketException

from eter_sim import run_wifi_node


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge", default="ws://127.0.0.1:8765/node")
    parser.add_argument("--captures", type=Path, required=True, help="captures directory")
    parser.add_argument("--token-file", type=Path, default=Path.home() / ".eter" / "sim-token")
    args = parser.parse_args()
    args.token_file.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            asyncio.run(run_wifi_node(args.bridge, args.captures, args.token_file))
            print("disconnected; reconnecting in 2 s")
        except (OSError, WebSocketException) as error:
            print(f"cannot reach the bridge ({error}); retrying in 2 s")
        except KeyboardInterrupt:
            return
        try:
            asyncio.run(asyncio.sleep(2))
        except KeyboardInterrupt:
            return


if __name__ == "__main__":
    main()
