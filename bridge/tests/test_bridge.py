"""End-to-end bridge tests against eter_sim (no hardware)."""

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from eter_cli.main import app
from eter_core import Eter, Node, NodeError, decode
from eter_core.analysis import analyze_frame, find_peaks
from eter_core.storage import Store
from eter_mcp.server import build_server
from eter_sim import SimTransport

CAPTURES = Path(__file__).resolve().parents[2] / "captures"
FIXTURES = ["spectrum-430-440.ndjson", "synthetic-ook-433.92.ndjson"]


@pytest.fixture
def captures(tmp_path: Path) -> Path:
    directory = tmp_path / "captures"
    directory.mkdir()
    for name in FIXTURES:
        shutil.copy(CAPTURES / name, directory)
    return directory


@pytest.fixture
def eter(captures: Path, tmp_path: Path) -> Eter:
    return Eter(Node(SimTransport(captures)), Store(tmp_path / "eter.db"))


def test_find_peaks_groups_adjacent_points() -> None:
    rssi = [-90] * 10 + [-70, -60, -72] + [-90] * 10
    peaks = find_peaks(430.0, 25, rssi)
    assert peaks == [{"freq_mhz": 430.275, "rssi_dbm": -60, "snr_db": 30, "width_khz": 75}]


def test_scan_finds_recorded_peak(eter: Eter) -> None:
    result = eter.scan(430.0, 440.0, 25)
    assert result["noise_floor_dbm"] == -90
    top = result["peaks"][0]
    assert top["freq_mhz"] == 434.7
    assert any("ISM" in use for use in top["likely_uses"])
    assert "overload_warning" not in result  # the recorded peak is -62 dBm


def test_scan_warns_about_overload(eter: Eter) -> None:
    assert isinstance(eter.node, Node)
    sim = eter.node._transport
    assert isinstance(sim, SimTransport)
    sim.spectra[0]["rssi_dbm"][188] = -40  # 434.7 MHz, as if the key were held at the antenna
    assert "overload_warning" in eter.scan(430.0, 440.0, 25)


def test_scan_out_of_band_is_a_node_error(eter: Eter) -> None:
    with pytest.raises(NodeError) as error:
        eter.scan(470.0, 480.0, 25)
    assert error.value.code == "OUT_OF_BAND"


def test_listen_summarizes_frames(eter: Eter) -> None:
    result = eter.listen(433.92, seconds=1)
    assert result["frames"] == 1
    frame = result["frame_summaries"][0]
    assert frame["pulses"] == 100
    assert (frame["shortest_pulse_us"], frame["longest_pulse_us"]) == (464, 1392)


# The synthetic frame is an EV1527-style remote: rtl_433 reports it as Generic-Remote id 45967.
needs_rtl_433 = pytest.mark.skipif(not decode.available(), reason="rtl_433 not installed")
REMOTE = "Generic-Remote/45967"


@needs_rtl_433
def test_listen_decodes_and_remembers_devices(eter: Eter) -> None:
    result = eter.listen(433.92, seconds=1)
    assert [d["device"] for d in result["devices"]] == [REMOTE]
    assert result["devices"][0]["data"]["cmd"] == 10
    assert result["frame_summaries"][0]["decoded_as"] == [REMOTE]
    assert "note" not in result

    eter.listen(433.92, seconds=1)
    (device,) = eter.devices()["devices"]
    assert (device["key"], device["seen_count"], device["label"]) == (REMOTE, 2, None)


@needs_rtl_433
def test_label_device(eter: Eter) -> None:
    eter.listen(433.92, seconds=1)
    eter.label_device(REMOTE, "Doorbell", owned=True)
    (device,) = eter.devices()["devices"]
    assert (device["label"], device["owned"]) == ("Doorbell", True)
    assert eter.listen(433.92, seconds=1)["devices"][0]["label"] == "Doorbell"
    with pytest.raises(NodeError):
        eter.label_device("Nope/1", "x")


def test_analyze_capture_finds_pwm_bits(eter: Eter) -> None:
    event_id = eter.listen(433.92, seconds=1)["event_id"]
    (frame,) = eter.analyze(event_id)["analysis"]
    assert [c["width_us"] for c in frame["pulse_widths_us"]] == [464, 1392]
    assert frame["modulation_guess"] == "PWM"
    # Each repeat is the 24-bit code followed by the next sync pulse (short: a trailing 0),
    # except the last one, which ends the frame without a sync.
    code = "101100111000111100001010"
    assert [row["binary"] for row in frame["rows"]] == [code + "0"] * 3 + [code]
    assert frame["rows"][3]["hex"] == "b38f0a"
    assert frame["repeats_of_most_common_row"] == 3
    with pytest.raises(NodeError):
        eter.analyze(eter.scan(430.0, 440.0, 25)["event_id"])


def test_analyze_splits_rows_at_header_pulses() -> None:
    # Gate-remote style (synthetic): each packet starts with a rare 1500 us header pulse, then
    # 12 PWM bits of 500/1000 us. No long gaps separate the packets.
    bits = "101100111000"
    packet = [1500, 1500]
    for bit in bits:
        packet += [1000, 500] if bit == "1" else [500, 1000]
    frame = analyze_frame((packet * 3)[:-1])
    assert frame["modulation_guess"] == "PWM"
    assert frame["marker_pulse_us"] == [1500]
    assert [row["binary"] for row in frame["rows"]] == [bits] * 3


def test_ook_file_format() -> None:
    assert decode.to_ook([500, 1000, 600]).splitlines()[3:] == [
        ";ook 2 pulses",
        "500 1000",
        f"600 {decode.TRAILING_GAP_US}",
        ";end",
    ]


def test_events_record_history(eter: Eter) -> None:
    eter.scan(430.0, 440.0, 25)
    eter.listen(433.92, seconds=1)
    history = eter.events()
    assert [e["kind"] for e in history["events"]] == ["listen", "scan"]
    assert eter.events(kind="scan")["count"] == 1


def test_identify(eter: Eter) -> None:
    result = eter.identify(446.1)
    assert result["receivable_by"] == ["r433"]
    assert any("PMR446" in use for use in result["likely_uses"])


def tool_names(eter: Eter) -> set[str]:
    return set(build_server(eter)._tool_manager._tools)


def test_mcp_tools_follow_capabilities(eter: Eter, tmp_path: Path) -> None:
    always = {"node_status", "identify_frequency", "query_events", "list_devices", "label_device"}
    assert tool_names(eter) == always | {"scan_spectrum", "listen", "analyze_capture"}
    no_node = Eter(None, Store(tmp_path / "other.db"), node_error="no node")
    assert tool_names(no_node) == always


def test_cli_scan(captures: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ETER_SIM", str(captures))
    monkeypatch.setenv("ETER_DB", str(tmp_path / "cli.db"))
    result = CliRunner().invoke(app, ["scan"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["peaks"][0]["freq_mhz"] == 434.7
