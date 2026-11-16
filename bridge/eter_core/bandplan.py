"""Typical uses of frequencies in the reference node's band (Europe).

A hint for interpreting signals, not a legal reference: allocations differ by country.
"""

# (low_mhz, high_mhz, typical use). Ranges may overlap; all matches apply.
EU_BANDS: list[tuple[float, float, str]] = [
    (380.0, 400.0, "TETRA digital radio for emergency services (encrypted)"),
    (400.15, 406.0, "Weather balloon radiosondes (meteorological aids)"),
    (406.0, 406.1, "COSPAS-SARSAT distress beacons (EPIRB, PLB, ELT)"),
    (410.0, 430.0, "Land mobile radio (business, utilities, private networks)"),
    (430.0, 440.0, "70 cm amateur radio band (licensed operators)"),
    (433.05, 434.79, "ISM/SRD licence-free devices: remotes, car keys, sensors, doorbells"),
    (433.075, 434.775, "LPD433 low-power walkie-talkies, where permitted"),
    (433.87, 433.97, "433.92 MHz: the most common frequency of cheap remotes and sensors"),
    (440.0, 470.0, "Land mobile radio (business, taxis, security, utilities)"),
    (446.0, 446.2, "PMR446 licence-free walkie-talkies"),
]


def identify(freq_mhz: float) -> list[str]:
    return [use for low, high, use in EU_BANDS if low <= freq_mhz <= high]
