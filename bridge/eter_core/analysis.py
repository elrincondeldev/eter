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


def cluster_widths(values: list[int]) -> list[dict[str, int]]:
    """Group durations within 1.3x of each other: the distinct widths a protocol uses.

    1.3x keeps receiver jitter (about ±20 %) together and still separates the common 1:1.5
    ratio of a 1000 us data pulse and a 1500 us header.
    """
    clusters: list[list[int]] = []
    for value in sorted(values):
        if clusters and value <= 1.3 * sum(clusters[-1]) / len(clusters[-1]):
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [{"width_us": round(sum(c) / len(c)), "count": len(c)} for c in clusters]


def _main(clusters: list[dict[str, int]], total: int) -> list[dict[str, int]]:
    """Widths that carry data: at least 10% of the durations. Rarer ones are sync/header marks."""
    return [c for c in clusters if c["count"] >= 0.1 * total]


def analyze_frame(t_us: list[int], max_rows: int = 10) -> dict[str, Any]:
    """Guess how a frame encodes bits, the way rtl_433's pulse analyzer does (simplified).

    Rows (packets, often repeated) are separated by a gap much longer than any data pulse, or
    start with a marker: a rare pulse longer than the data pulses (a header or sync mark).
    Inside a row: two data pulse widths mean pulse-width modulation (long pulse = 1); one pulse
    width and two gap widths mean pulse-position modulation (long gap = 1).
    """
    pulses, gaps = t_us[0::2], t_us[1::2]
    pulse_clusters = cluster_widths(pulses)
    data_pulses = _main(pulse_clusters, len(pulses))
    longest_data_us = data_pulses[-1]["width_us"]
    marker_us = 1.25 * longest_data_us
    reset_us = 4 * longest_data_us
    row_gaps = [g for g in gaps if g <= reset_us]
    gap_clusters = cluster_widths(row_gaps) if row_gaps else []
    data_gaps = _main(gap_clusters, len(row_gaps))

    # Split into rows of (pulse, following gap) pairs; marker pulses are not data.
    rows: list[list[tuple[int, int | None]]] = [[]]
    for i, pulse in enumerate(pulses):
        gap = gaps[i] if i < len(gaps) else None
        if pulse > marker_us:
            rows.append([])
        else:
            rows[-1].append((pulse, gap))
        if gap is not None and gap > reset_us:
            rows.append([])

    if len(data_pulses) == 2:
        modulation = "PWM"
        threshold = (data_pulses[0]["width_us"] + data_pulses[1]["width_us"]) / 2
        bit_rows = ["".join("1" if p > threshold else "0" for p, _ in row) for row in rows]
    elif len(data_pulses) == 1 and len(data_gaps) == 2:
        modulation = "PPM"
        threshold = (data_gaps[0]["width_us"] + data_gaps[1]["width_us"]) / 2
        bit_rows = [
            "".join(
                "1" if g > threshold else "0" for _, g in row if g is not None and g <= reset_us
            )
            for row in rows
        ]
    else:
        modulation = "unknown"
        bit_rows = []

    bit_rows = [bits for bits in bit_rows if len(bits) >= 4]
    most_common = max(set(bit_rows), key=bit_rows.count) if bit_rows else None
    return {
        "pulses": len(pulses),
        "pulse_widths_us": pulse_clusters,
        "gap_widths_us": gap_clusters,
        "marker_pulse_us": [c["width_us"] for c in pulse_clusters if c["width_us"] > marker_us],
        "row_break_gap_us": reset_us,
        "modulation_guess": modulation,
        "rows": [
            {"bits": len(bits), "binary": bits, "hex": f"{int(bits, 2):0{(len(bits) + 3) // 4}x}"}
            for bits in bit_rows[:max_rows]
        ],
        "repeats_of_most_common_row": bit_rows.count(most_common) if most_common else 0,
    }
