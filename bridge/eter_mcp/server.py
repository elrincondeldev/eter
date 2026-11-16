"""MCP server: exposes eter_core to AI agents. Tools exist only if a connected node supports them."""

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from eter_core import Eter, open_eter

INSTRUCTIONS = """\
eter connects you to radio hardware (a "node") near the user. Use it to explore the radio
spectrum, find active transmitters and explain what they probably are.

- Start with node_status to see which radio is connected and its frequency bands.
- scan_spectrum finds active frequencies; listen captures transmissions on one frequency and
  decodes known devices (sensors, remotes); identify_frequency tells what a frequency is
  typically used for.
- Devices decoded by listen are remembered: list_devices shows them; label_device names the
  user's own ones. For frames no decoder recognised, analyze_capture shows pulse widths and bits.
- monitor keeps listening in the background (e.g. preset sensors_433) while the session lasts;
  what it decodes shows up in list_devices and query_events(kind="monitor").
- Results are summaries, never raw samples. Units are in field names (freq_mhz, rssi_dbm).
- RSSI: about -100 dBm is nothing, -40 dBm is very strong.
- Transmitters near the user may belong to neighbours: describe them, never publish their data.
"""


def build_server(eter: Eter) -> MCPServer:
    server = MCPServer("eter", instructions=INSTRUCTIONS)

    def node_status() -> dict[str, Any]:
        """Show the connected radio node: identity, firmware, uptime, and each radio's frequency
        bands and features. If no node is connected, explains why."""
        return eter.status()

    def identify_frequency(
        freq_mhz: Annotated[float, Field(description="Frequency in MHz, e.g. 433.92")],
    ) -> dict[str, Any]:
        """Typical uses of a frequency in Europe (a hint, not a legal reference), and which
        connected radios can receive it."""
        return eter.identify(freq_mhz)

    def query_events(
        kind: Annotated[
            str | None, Field(description="'scan', 'listen' or 'monitor'; omit for all")
        ] = None,
        since_minutes: Annotated[int | None, Field(description="Only the last N minutes")] = None,
        limit: Annotated[int, Field(description="Max events, newest first (max 100)")] = 20,
    ) -> dict[str, Any]:
        """History of earlier scans, listens and background monitor captures (only those that
        heard something), newest first, with a short summary of each."""
        return eter.events(kind, since_minutes, limit)

    def scan_spectrum(
        start_mhz: Annotated[float, Field(description="Start of the range in MHz")] = 430.0,
        end_mhz: Annotated[float, Field(description="End of the range in MHz")] = 440.0,
        step_khz: Annotated[float, Field(description="Step in kHz, 5-1000 (max 1001 points)")] = 25,
    ) -> dict[str, Any]:
        """Sweep a frequency range and return the noise floor and the strongest peaks, each with
        its likely uses. Takes about 1.5 ms per point (10 MHz at 25 kHz: under a second).
        Readings are instantaneous, so ask the user to keep a transmitter active while scanning."""
        return eter.scan(start_mhz, end_mhz, step_khz)

    def listen(
        freq_mhz: Annotated[
            float, Field(description="Frequency in MHz, e.g. a scan peak")
        ] = 433.92,
        seconds: Annotated[int, Field(description="How long to listen, 1-60")] = 10,
        rssi_threshold_dbm: Annotated[
            float | None, Field(description="Detection threshold; default noise floor + 15 dB")
        ] = None,
    ) -> dict[str, Any]:
        """Listen on one frequency (on/off keying), decode known devices (weather stations,
        sensors, remotes) and summarize each transmission: pulses, duration, strength and pulse
        widths. Blocks for `seconds`; tell the user to press their remote or trigger their sensor
        during that time. Decoded devices are remembered for list_devices."""
        return eter.listen(freq_mhz, seconds, rssi_threshold_dbm)

    def analyze_capture(
        event_id: Annotated[int, Field(description="event_id returned by listen")],
        max_frames: Annotated[int, Field(description="Frames to analyze, first ones")] = 3,
    ) -> dict[str, Any]:
        """Analyze the frames of an earlier listen: distinct pulse and gap widths, modulation guess
        (PWM or PPM), bit rows in binary and hex, and how many rows repeat. Use it to reason about
        signals no decoder recognised, e.g. to tell which bits change between two button presses."""
        return eter.analyze(event_id, max_frames)

    def list_devices(
        limit: Annotated[int, Field(description="Max devices, most recently seen first")] = 50,
    ) -> dict[str, Any]:
        """Devices decoded so far: model, user label, whether the user owns it, when it was first and
        last seen, how often, signal strength and its last decoded data (e.g. temperature)."""
        return eter.devices(limit)

    def label_device(
        device: Annotated[
            str, Field(description="Device key from list_devices, e.g. 'Nexus-TH/42/1'")
        ],
        label: Annotated[str, Field(description="Name, e.g. 'Living room thermometer'")],
        owned: Annotated[
            bool, Field(description="True only if the user says it is theirs")
        ] = False,
    ) -> dict[str, Any]:
        """Give a decoded device a name and mark whether it belongs to the user. Only mark owned=true
        when the user confirms the device is theirs."""
        return eter.label_device(device, label, owned)

    def monitor(
        preset: Annotated[
            str, Field(description="What to listen for; 'sensors_433'")
        ] = "sensors_433",
        enabled: Annotated[bool, Field(description="true to start, false to stop")] = True,
    ) -> dict[str, Any]:
        """Start or stop background listening. While it runs (only as long as this session), the
        node listens in 5 s chunks and decoded devices are recorded; your own scan or listen
        requests go first and wait at most one chunk. Returns the monitor status: chunks, frames
        and devices seen so far. node_status also reports it."""
        return eter.set_monitor(preset, enabled)

    server.add_tool(node_status)
    server.add_tool(identify_frequency)
    server.add_tool(query_events)
    server.add_tool(list_devices)
    server.add_tool(label_device)
    features = eter.features()
    if "rssi_scan" in features:
        server.add_tool(scan_spectrum)
    if "rx_pulses" in features:
        server.add_tool(listen)
        server.add_tool(analyze_capture)
        server.add_tool(monitor)
    return server


def main() -> None:
    build_server(open_eter()).run()
