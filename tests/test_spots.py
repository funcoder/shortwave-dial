from pathlib import Path

from swdial.conditions import parse_spots

FIX = Path(__file__).parent / "fixtures" / "pskreporter_io80.xml"


def test_spots_have_frequencies_in_band():
    spots = parse_spots(FIX.read_text(), 50.74, -2.64)
    assert spots["40m"], "fixture has 40m spots"
    for s in spots["40m"]:
        assert 7_000_000 <= s["hz"] <= 7_200_000
        assert s["call"]
    calls = [s["call"] for s in spots["40m"]]
    assert len(calls) == len(set(calls)), "one entry per station"
