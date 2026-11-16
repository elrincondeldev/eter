"""MCP server: exposes eter_core to AI agents. Tools exist only if a connected node supports them."""

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from eter_core import Eter, open_eter

INSTRUCTIONS = """\
eter connects you to radio hardware (a "node") near the user. Use it to explore the radio
spectrum, find active transmitters and explain what they probably are.

- Start with node_status to see which radio is connected and its frequency bands.
- scan_spectrum finds active frequencies; listen captures the on/off pattern of transmissions
  on one frequency; identify_frequency tells what a frequency is typically used for.
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
        kind: Annotated[str | None, Field(description="'scan' or 'listen'; omit for all")] = None,
        since_minutes: Annotated[int | None, Field(description="Only the last N minutes")] = None,
        limit: Annotated[int, Field(description="Max events, newest first (max 100)")] = 20,
    ) -> dict[str, Any]:
        """History of earlier scans and listens, newest first, with a short summary of each."""
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
        """Listen on one frequency (on/off keying) and summarize each transmission captured: number
        of pulses, duration, strength and pulse widths. Blocks for `seconds`; tell the user to
        press their remote or trigger their sensor during that time."""
        return eter.listen(freq_mhz, seconds, rssi_threshold_dbm)

    server.add_tool(node_status)
    server.add_tool(identify_frequency)
    server.add_tool(query_events)
    features = eter.features()
    if "rssi_scan" in features:
        server.add_tool(scan_spectrum)
    if "rx_pulses" in features:
        server.add_tool(listen)
    return server


def main() -> None:
    build_server(open_eter()).run()
