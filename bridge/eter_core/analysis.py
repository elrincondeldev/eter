"""Turn raw node data into the summaries tools return. The AI never sees raw arrays."""

from statistics import median
from typing import Any


def noise_floor(rssi_dbm: list[int]) -> int:
    return round(median(rssi_dbm))


def find_peaks(
    start_mhz: float,
    step_khz: float,
    rssi_dbm: list[int],
    min_snr_db: int = 10,
    max_peaks: int = 10,
) -> list[dict[str, Any]]:
    """Runs of consecutive points at least min_snr_db above the noise floor, strongest first."""
    floor = noise_floor(rssi_dbm)
    runs: list[list[int]] = []
    for i, value in enumerate(rssi_dbm):
        if value - floor < min_snr_db:
            continue
        if runs and runs[-1][-1] == i - 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    peaks = []
    for run in runs:
        top = max(run, key=lambda i: rssi_dbm[i])
        peaks.append(
            {
                "freq_mhz": round(start_mhz + top * step_khz / 1000, 4),
                "rssi_dbm": rssi_dbm[top],
                "snr_db": rssi_dbm[top] - floor,
                "width_khz": len(run) * step_khz,
            }
        )
    peaks.sort(key=lambda p: p["rssi_dbm"], reverse=True)
    return peaks[:max_peaks]


def summarize_frame(t_us: list[int], rssi_dbm: int, truncated: bool) -> dict[str, Any]:
    """Shape of one captured frame: alternating pulse/gap durations, pulses at even indexes."""
    pulses = t_us[0::2]
    return {
        "pulses": len(pulses),
        "duration_ms": round(sum(t_us) / 1000, 1),
        "rssi_dbm": rssi_dbm,
        "shortest_pulse_us": min(pulses),
        "longest_pulse_us": max(pulses),
        "truncated": truncated,
    }
