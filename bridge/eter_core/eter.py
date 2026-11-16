"""The operations every facade (MCP, CLI) exposes, on top of one node and the event store."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from eter_core import analysis, bandplan
from eter_core.node import Message, Node, NodeError
from eter_core.storage import Store
from eter_core.transport import SerialTransport, Transport, find_serial_port

SCAN_NOTE = (
    "Readings are instantaneous: a short burst (a remote pressed once) is easy to miss. "
    "Repeat the scan while the transmitter is active, or use listen on a peak."
)
# Above this, a transmitter a few metres away can overload the receiver and show up at
# several wrong frequencies at once (seen on the reference node with a key fob held close).
OVERLOAD_DBM = -50
OVERLOAD_NOTE = (
    "Some peaks are very strong (above -50 dBm): the transmitter is probably very close and "
    "may overload the receiver, so the same transmitter can appear at several frequencies. "
    "Move it a few metres from the antenna and scan again before drawing conclusions."
)
FSK_NOTE = (
    "Transmissions were detected but had too few on/off pulses to keep. The signal is probably "
    "FSK or another constant-carrier modulation, which on/off (OOK) capture cannot read."
)


class Eter:
    def __init__(self, node: Node | None, store: Store, node_error: str | None = None) -> None:
        self.node = node
        self.store = store
        self.node_error = node_error

    def features(self) -> set[str]:
        if self.node is None:
            return set()
        return {feature for radio in self.node.radios for feature in radio["features"]}

    def status(self) -> dict[str, Any]:
        if self.node is None:
            return {"connected": False, "error": self.node_error}
        status = self.node.request({"type": "status"}, timeout_s=3)[-1]
        radios = [
            {k: r[k] for k in ("id", "chip", "bands_mhz", "tuned_mhz", "modulations", "features")}
            for r in self.node.radios
        ]
        return {
            "connected": True,
            "node_id": self.node.node_id,
            "fw": status["fw"],
            "uptime_s": status["uptime_s"],
            "radios": radios,
        }

    def _radio(self, feature: str) -> tuple[Node, Message]:
        if self.node is None:
            raise NodeError("NO_NODE", f"no node connected: {self.node_error}")
        radio = self.node.radio_with(feature)
        if radio is None:
            raise NodeError("UNSUPPORTED", f"no connected radio supports {feature}")
        return self.node, radio

    def scan(self, start_mhz: float, end_mhz: float, step_khz: float = 25) -> dict[str, Any]:
        node, radio = self._radio("rssi_scan")
        points = (end_mhz - start_mhz) * 1000 / step_khz + 1
        request = {
            "type": "scan",
            "radio": radio["id"],
            "start_mhz": start_mhz,
            "end_mhz": end_mhz,
            "step_khz": step_khz,
        }
        replies = node.request(request, timeout_s=points * 0.005 + 5)
        rssi = replies[-1]["rssi_dbm"]
        peaks = analysis.find_peaks(start_mhz, step_khz, rssi)
        for peak in peaks:
            peak["likely_uses"] = bandplan.identify(peak["freq_mhz"])
        summary: dict[str, Any] = {
            "radio": radio["id"],
            "range_mhz": [start_mhz, end_mhz],
            "step_khz": step_khz,
            "points": len(rssi),
            "noise_floor_dbm": analysis.noise_floor(rssi),
            "peaks": peaks,
            "note": SCAN_NOTE,
        }
        if any(peak["rssi_dbm"] > OVERLOAD_DBM for peak in peaks):
            summary["overload_warning"] = OVERLOAD_NOTE
        summary["event_id"] = self.store.add_event(
            node.node_id, "scan", radio["id"], start_mhz, summary, replies
        )
        return summary

    def listen(
        self, freq_mhz: float, seconds: int = 10, rssi_threshold_dbm: float | None = None
    ) -> dict[str, Any]:
        node, radio = self._radio("rx_pulses")
        request: Message = {
            "type": "rx_pulses",
            "radio": radio["id"],
            "freq_mhz": freq_mhz,
            "seconds": seconds,
        }
        if rssi_threshold_dbm is not None:
            request["rssi_threshold_dbm"] = rssi_threshold_dbm
        replies = node.request(request, timeout_s=seconds + 5)
        done = replies[-1]
        frames = [
            analysis.summarize_frame(m["t_us"], m["rssi_dbm"], m["truncated"])
            for m in replies
            if m["type"] == "pulses"
        ]
        summary: dict[str, Any] = {
            "radio": radio["id"],
            "freq_mhz": freq_mhz,
            "seconds": seconds,
            "noise_floor_dbm": done["noise_floor_dbm"],
            "threshold_dbm": done["threshold_dbm"],
            "frames": done["frames"],
            "discarded": done["discarded"],
            "frame_summaries": frames[:20],
        }
        if done["discarded"] and not done["frames"]:
            summary["note"] = FSK_NOTE
        summary["event_id"] = self.store.add_event(
            node.node_id, "listen", radio["id"], freq_mhz, summary, replies
        )
        return summary

    def identify(self, freq_mhz: float) -> dict[str, Any]:
        result: dict[str, Any] = {"freq_mhz": freq_mhz, "likely_uses": bandplan.identify(freq_mhz)}
        if self.node is not None:
            result["receivable_by"] = [
                r["id"]
                for r in self.node.radios
                if any(low <= freq_mhz <= high for low, high in r["bands_mhz"])
            ]
        return result

    def events(
        self, kind: str | None = None, since_minutes: int | None = None, limit: int = 20
    ) -> dict[str, Any]:
        since = None
        if since_minutes is not None:
            since = (datetime.now(UTC) - timedelta(minutes=since_minutes)).isoformat(
                timespec="seconds"
            )
        events = self.store.events(kind, since, min(limit, 100))
        for event in events:
            event["summary"] = _brief(event["kind"], event["summary"])
        return {"count": len(events), "events": events}


def _brief(kind: str, summary: dict[str, Any]) -> dict[str, Any]:
    """Compact version of a stored summary for history listings."""
    if kind == "scan":
        return {"noise_floor_dbm": summary["noise_floor_dbm"], "top_peaks": summary["peaks"][:3]}
    return {k: summary[k] for k in ("frames", "discarded", "threshold_dbm")}


def open_eter() -> Eter:
    """Connect using the environment: ETER_SIM (captures dir), ETER_PORT, ETER_DB."""
    db = Path(os.environ.get("ETER_DB") or Path.home() / ".eter" / "eter.db")
    db.parent.mkdir(parents=True, exist_ok=True)
    store = Store(db)
    try:
        transport: Transport
        if sim := os.environ.get("ETER_SIM"):
            from eter_sim import SimTransport

            transport = SimTransport(Path(sim))
        else:
            transport = SerialTransport(os.environ.get("ETER_PORT") or find_serial_port())
        return Eter(Node(transport), store)
    except (OSError, NodeError) as error:
        return Eter(None, store, node_error=str(error))
