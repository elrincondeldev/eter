"""End-to-end bridge tests against eter_sim (no hardware)."""

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from eter_cli.main import app
from eter_core import Eter, Node, NodeError
from eter_core.analysis import find_peaks
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
    assert frame["pulses"] == 75
    assert (frame["shortest_pulse_us"], frame["longest_pulse_us"]) == (350, 1050)
    assert "note" not in result


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
    always = {"node_status", "identify_frequency", "query_events"}
    assert tool_names(eter) == always | {"scan_spectrum", "listen"}
    no_node = Eter(None, Store(tmp_path / "other.db"), node_error="no node")
    assert tool_names(no_node) == always


def test_cli_scan(captures: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ETER_SIM", str(captures))
    monkeypatch.setenv("ETER_DB", str(tmp_path / "cli.db"))
    result = CliRunner().invoke(app, ["scan"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["peaks"][0]["freq_mhz"] == 434.7
