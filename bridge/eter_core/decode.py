"""Decode captured OOK frames with rtl_433.

rtl_433 is GPL: it runs only as an external process and is never linked or copied into eter.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

# The node ends a frame on its last pulse; rtl_433 needs a gap after it to close the packet.
TRAILING_GAP_US = 20000


def available() -> bool:
    return shutil.which("rtl_433") is not None


def to_ook(t_us: list[int]) -> str:
    """One frame as an rtl_433 pulse data file (.ook): `pulse gap` pairs in microseconds."""
    t = [*t_us, TRAILING_GAP_US]
    lines = [";pulse data", ";version 1", ";timescale 1us", f";ook {len(t) // 2} pulses"]
    lines += [f"{t[i]} {t[i + 1]}" for i in range(0, len(t), 2)]
    lines.append(";end")
    return "\n".join(lines) + "\n"


def decode_frame(t_us: list[int]) -> list[dict[str, Any]]:
    """Messages rtl_433 decodes from one frame, without duplicates (remotes repeat their code)."""
    rtl_433 = shutil.which("rtl_433")
    if rtl_433 is None:
        return []
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "frame.ook"
        path.write_text(to_ook(t_us))
        result = subprocess.run(
            [rtl_433, "-r", str(path), "-F", "json"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    decoded: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        message.pop("time", None)
        if message not in decoded:
            decoded.append(message)
    return decoded


def device_key(message: dict[str, Any]) -> str:
    """Stable identity of a decoded transmitter: model, id and channel when present."""
    parts = [str(message["model"])]
    parts += [str(message[k]) for k in ("id", "channel") if k in message]
    return "/".join(parts)
