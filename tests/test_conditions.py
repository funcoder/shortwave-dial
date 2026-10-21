from datetime import datetime, timezone
from pathlib import Path

import pytest

from swdial import conditions as c

FIX = Path(__file__).parent / "fixtures"
LAT, LON = 50.74, -2.64  # Wessex KiwiSDR V2


def test_maidenhead():
    assert c.maidenhead(LAT, LON) == "IO80"
    assert c.maidenhead(LAT, LON, 6).startswith("IO80")
    lat, lon = c.locator_to_latlon("IO80")
    assert 50 < lat < 51 and -4 < lon < -2
    assert c.locator_to_latlon("??") is None


def test_sun_noon_vs_midnight():
    # Solar noon at -2.64 lon is ~12:25 UTC (minus equation of time).
    noon = c.sun_elevation(LAT, LON, datetime(2026, 6, 21, 12, 10, tzinfo=timezone.utc))
    midnight = c.sun_elevation(LAT, LON, datetime(2026, 6, 21, 0, 10, tzinfo=timezone.utc))
    assert 60 < noon < 64          # 90 - 50.74 + 23.44 = 62.7
    assert -18 < midnight < -14    # -(90 - 50.74 - 23.44) = -15.8
    winter = c.sun_elevation(LAT, LON, datetime(2026, 12, 21, 12, 10, tzinfo=timezone.utc))
    assert 14 < winter < 17        # 90 - 50.74 - 23.44 = 15.8
    assert c.sun_phase(LAT, LON, datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)) == "day"
    assert c.sun_phase(LAT, LON, datetime(2026, 12, 21, 2, 0, tzinfo=timezone.utc)) == "night"


def test_measured_thresholds():
    assert c.rate_measured(0) == "poor"
    assert c.rate_measured(2) == "poor"
    assert c.rate_measured(3) == "fair"
    assert c.rate_measured(14) == "fair"
    assert c.rate_measured(15) == "good"
    assert c.rate_measured(400) == "good"


def test_model_rules():
    solar = {"sfi": 140, "k": 2, "sfi_estimated": False, "calls": {}}
    assert c.rate_model("20m", "day", solar)[0] == "good"
    assert c.rate_model("20m", "night", {**solar, "sfi": 100})[0] == "poor"
    assert c.rate_model("40m", "night", solar)[0] == "good"
    assert c.rate_model("40m", "day", solar)[0] == "fair"
    assert c.rate_model("31m", "greyline", solar)[0] == "good"
    assert c.rate_model("25m", "evening", solar)[0] == "good"
    # K >= 5 knocks everything down one step
    assert c.rate_model("40m", "night", {**solar, "k": 6})[0] == "fair"
    r, reason = c.rate_model("20m", "night", solar)
    assert "Night here" in reason and "SFI 140" in reason


def test_parse_hamqsl_fixture():
    s = c.parse_hamqsl((FIX / "hamqsl_solar.xml").read_text())
    assert s["k"] == 3
    # Fixture has <solarflux>0</solarflux>: SFI estimated from sunspots (66)
    assert s["sfi_estimated"] and 100 < s["sfi"] < 130
    assert s["calls"][("80m-40m", "night")] == "fair"
    assert s["calls"][("30m-20m", "day")] == "poor"


def test_parse_pskreporter_fixture():
    p = c.parse_pskreporter((FIX / "pskreporter_io80.xml").read_text(), LAT, LON)
    assert p["reports"] == 384
    assert p["bands"]["40m"]["senders"] == 125
    assert p["bands"]["20m"]["senders"] == 48
    assert p["bands"]["31m"]["senders"] == 0
    assert p["bands"]["40m"]["max_km"] > 3000


def test_band_conditions_from_fixtures():
    now = datetime(2026, 10, 21, 22, 56, tzinfo=timezone.utc)
    res = c.band_conditions(LAT, LON, now,
                            psk_xml=(FIX / "pskreporter_io80.xml").read_text(),
                            solar_xml=(FIX / "hamqsl_solar.xml").read_text())
    assert set(res) == {"40m", "31m", "25m", "20m"}
    assert res["40m"] == {"rating": "good", "source": "measured",
                          "reason": res["40m"]["reason"]}
    assert res["40m"]["reason"].startswith("125 stations heard nearby in 15 min")
    assert res["31m"]["source"] == "model" and res["31m"]["rating"] == "good"
    assert res["25m"]["source"] == "model"


def test_offline_falls_back_to_model():
    now = datetime(2026, 10, 21, 22, 56, tzinfo=timezone.utc)
    res = c.band_conditions(LAT, LON, now, offline=True)
    assert all(r["source"] == "model" for r in res.values())
    assert all("no solar data" in r["reason"] for r in res.values())


def test_bad_feeds_fall_back(monkeypatch):
    monkeypatch.setattr(c, "_cached_fetch", lambda *a, **k: (None, None))
    res = c.band_conditions(LAT, LON)
    assert all(r["rating"] in ("good", "fair", "poor") for r in res.values())
    res = c.band_conditions(LAT, LON, psk_xml="not xml", solar_xml="<<<")
    assert all(r["source"] == "model" for r in res.values())


def test_empty_grid_uses_model():
    empty = '<?xml version="1.0"?><receptionReports></receptionReports>'
    res = c.band_conditions(LAT, LON, psk_xml=empty, solar_xml=None, offline=True)
    assert res["40m"]["source"] == "model"


def test_cache_respects_interval(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    calls = []

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return b"<x/>"

    def fake_urlopen(req, timeout):
        calls.append(req.full_url)
        return Resp()

    monkeypatch.setattr(c.urllib.request, "urlopen", fake_urlopen)
    assert c._cached_fetch("t.xml", "http://example/t", 300)[0] == "<x/>"
    assert c._cached_fetch("t.xml", "http://example/t", 300)[0] == "<x/>"
    assert len(calls) == 1
