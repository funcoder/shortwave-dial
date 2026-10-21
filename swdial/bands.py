"""Band presets shown as buttons in the window. Frequencies in Hz."""

BANDS = {
    "40m": {"start": 7_000_000, "end": 7_200_000, "default": 7_074_000, "mode": "usb"},
    "31m": {"start": 9_400_000, "end": 9_900_000, "default": 9_410_000, "mode": "am"},
    "25m": {"start": 11_600_000, "end": 12_100_000, "default": 11_955_000, "mode": "am"},
    "20m": {"start": 14_000_000, "end": 14_350_000, "default": 14_074_000, "mode": "usb"},
}

MODES = ("am", "lsb", "usb", "cw")


def clamp_to_band(band: str, hz: int) -> int:
    b = BANDS[band]
    return max(b["start"], min(b["end"], hz))


def format_mhz(hz: int) -> str:
    """7074000 -> '7.074' (the big readout and the Waybar pill)."""
    return f"{hz / 1_000_000:.3f}"
