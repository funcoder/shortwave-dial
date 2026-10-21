"""Where voice (SSB) hams are: band-plan phone segments + live DX-cluster spots.

Spots come from HamQTH's public DX-cluster feed, fetched at most every 2 minutes
(cached via conditions._cached_fetch). They are worldwide spots, not proof the
receiver hears them, but they show where people are talking right now.
"""
import re
import time
from datetime import datetime, timezone

from .conditions import _cached_fetch

URL = "https://www.hamqth.com/dxc_csv.php?limit=300"
# IARU Region 1 phone segments (Hz) for the ham bands in the dial.
VOICE_SEGMENTS = {"40m": (7_060_000, 7_200_000), "20m": (14_125_000, 14_350_000)}
DIGITAL_DIALS = (7_074_000, 7_078_000, 14_074_000, 14_078_000, 14_080_000)  # FT8/JS8/FT4 watering holes
NOT_VOICE = re.compile(r"\b(FT8|FT4|CW|RTTY|PSK|JS8|WSPR|FST4|Q65|JT65|JT9|SSTV|OLIVIA|MFSK|DIGI|BPM|WPM)\b", re.I)


def sideband(hz: int) -> str:
    return "lsb" if hz < 10_000_000 else "usb"


def parse_cluster(text: str, now: float | None = None) -> dict:
    """Return {band: [ {call, hz, mode, comment, spotter, age_min}, ... ]} newest first."""
    now = now or time.time()
    out = {b: {} for b in VOICE_SEGMENTS}
    for line in text.splitlines():
        p = line.split("^")
        if len(p) < 5:
            continue
        try:
            hz = int(round(float(p[1]) * 1000))
            t = datetime.strptime(p[4].strip(), "%H%M %Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
        comment = p[3].strip()
        if NOT_VOICE.search(comment) or any(abs(hz - d) <= 3000 for d in DIGITAL_DIALS):
            continue
        for band, (lo, hi) in VOICE_SEGMENTS.items():
            if lo <= hz <= hi:
                call = p[2].strip().upper()
                age = max(0, int((now - t) // 60))
                if age > 30:
                    break
                if call not in out[band]:  # first seen = newest
                    out[band][call] = {"call": call, "hz": hz, "mode": sideband(hz), "comment": comment,
                                       "spotter": p[0].strip(), "age_min": age}
                break
    return {b: list(d.values()) for b, d in out.items()}


def _check(text: str):
    if text.count("^") < 8:
        raise ValueError("not a cluster CSV")


def voice_spots() -> dict:
    body, _ = _cached_fetch("hamqth-dxc.csv", URL, min_interval=120, validate=_check)
    return parse_cluster(body) if body else {}


if __name__ == "__main__":
    for band, spots in voice_spots().items():
        print(band, len(spots))
        for s in spots[:10]:
            print(f"  {s['hz']/1e6:.4f} {s['mode'].upper()} {s['call']:<10} {s['age_min']:>2}m  {s['comment']}")
