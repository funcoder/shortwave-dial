"""Band conditions at a receiver: GOOD / FAIR / POOR per band.

Two inputs, combined:

* Measured (40m, 20m): PSKReporter reception reports from the last 15 min
  heard by receivers in the receiver's 4-char Maidenhead grid square.
* Model (all bands): sun elevation at the receiver (day / greyline / night,
  computed here with the NOAA solar position equations) plus solar data
  from hamqsl.com (SFI, K index, N0NBH day/night band calls).

Measured counts win when present; otherwise the model is used.  Every
network failure falls back quietly (stale cache, then model only).

Fair use: PSKReporter is asked at most once per 5 minutes per grid and
hamqsl at most once per 15 minutes; results are cached under
$XDG_RUNTIME_DIR/shortwave-dial (or /tmp/shortwave-dial-<uid>).

CLI:  python -m swdial.conditions --lat 50.74 --lon -2.64
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from .bands import BANDS

GOOD, FAIR, POOR, UNKNOWN = "good", "fair", "poor", "unknown"
_ORDER = [POOR, FAIR, GOOD]
EMOJI = {GOOD: "🟢", FAIR: "🟡", POOR: "🔴", UNKNOWN: "⚪"}

APP_NAME = "ShortwaveDial-conditions"
USER_AGENT = "ShortwaveDial/0.1 (band conditions; +https://github.com/jks-prv/kiwiclient user)"
PSK_URL = "https://retrieve.pskreporter.info/query"
HAMQSL_URL = "https://www.hamqsl.com/solarxml.php"
PSK_MIN_INTERVAL = 5 * 60      # PSKReporter asks for >= 5 min between queries
HAMQSL_MIN_INTERVAL = 15 * 60  # hamqsl updates roughly hourly
STALE_OK = 60 * 60             # use an old cache this long if the network is down
WINDOW_S = 900                 # 15 min of reception reports

# Bands we can measure from PSKReporter (ham bands with FT8 etc. activity).
MEASURED_BANDS = ("40m", "20m")
# Unique-sender thresholds for 15 min of reports.
GOOD_MIN, FAIR_MIN = 15, 3


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

def maidenhead(lat: float, lon: float, chars: int = 4) -> str:
    """lat/lon -> Maidenhead locator (4 or 6 chars).  50.74,-2.64 -> 'IO80'."""
    lon = min(max(lon + 180.0, 0.0), 359.999999)
    lat = min(max(lat + 90.0, 0.0), 179.999999)
    out = chr(ord("A") + int(lon // 20)) + chr(ord("A") + int(lat // 10))
    out += str(int((lon % 20) // 2)) + str(int(lat % 10))
    if chars >= 6:
        out += chr(ord("a") + int((lon % 2) * 12)) + chr(ord("a") + int((lat % 1) * 24))
    return out


def locator_to_latlon(loc: str) -> tuple[float, float] | None:
    """Centre of a 2/4/6/8-char Maidenhead square, or None if malformed."""
    loc = (loc or "").strip()
    if len(loc) < 2:
        return None
    try:
        lon = (ord(loc[0].upper()) - 65) * 20 - 180.0
        lat = (ord(loc[1].upper()) - 65) * 10 - 90.0
        lon_w, lat_w = 20.0, 10.0
        if len(loc) >= 4:
            lon += int(loc[2]) * 2
            lat += int(loc[3])
            lon_w, lat_w = 2.0, 1.0
        if len(loc) >= 6:
            lon += (ord(loc[4].lower()) - 97) * (2 / 24)
            lat += (ord(loc[5].lower()) - 97) * (1 / 24)
            lon_w, lat_w = 2 / 24, 1 / 24
        if len(loc) >= 8:
            lon += int(loc[6]) * (2 / 240)
            lat += int(loc[7]) * (1 / 240)
            lon_w, lat_w = 2 / 240, 1 / 240
    except (ValueError, IndexError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat + lat_w / 2, lon + lon_w / 2


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(a)))


# --------------------------------------------------------------------------
# Sun position (NOAA general solar position equations)
# --------------------------------------------------------------------------

def sun_elevation(lat: float, lon: float, when: datetime) -> float:
    """Sun elevation in degrees above the horizon (no refraction)."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when = when.astimezone(timezone.utc)
    doy = when.timetuple().tm_yday
    hours = when.hour + when.minute / 60 + when.second / 3600
    days_in_year = 366 if (when.year % 4 == 0 and (when.year % 100 or when.year % 400 == 0)) else 365
    g = 2 * math.pi / days_in_year * (doy - 1 + (hours - 12) / 24)  # fractional year (rad)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    true_solar_min = hours * 60 + eqtime + 4 * lon
    ha = math.radians(true_solar_min / 4 - 180)
    la = math.radians(lat)
    cos_zen = math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)
    return 90.0 - math.degrees(math.acos(max(-1.0, min(1.0, cos_zen))))


def sun_phase(lat: float, lon: float, when: datetime) -> str:
    """'day' (>= +6 deg), 'greyline' (-6..+6), 'evening' (setting, down to -18),
    or 'night'."""
    el = sun_elevation(lat, lon, when)
    if el >= 6:
        return "day"
    if el > -6:
        return "greyline"
    if el > -18 and sun_elevation(lat, lon, when + timedelta(minutes=10)) < el:
        return "evening"
    return "night"


# --------------------------------------------------------------------------
# Cache + fetch
# --------------------------------------------------------------------------

def cache_dir() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR")
    path = os.path.join(base, "shortwave-dial") if base else f"/tmp/shortwave-dial-{os.getuid()}"
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _cached_fetch(name: str, url: str, min_interval: int, timeout: float = 20.0) -> tuple[str | None, float | None]:
    """Return (body, fetched_at).  Never hits the network more often than
    min_interval for this cache name; serves a stale copy (<= STALE_OK) when
    the network fails.  (None, None) when there is nothing usable."""
    path = os.path.join(cache_dir(), name)
    meta = path + ".json"
    body, fetched_at, last_try = None, None, 0.0
    try:
        with open(meta) as f:
            m = json.load(f)
        fetched_at, last_try = m.get("fetched_at"), m.get("last_try", 0.0)
        with open(path, encoding="utf-8") as f:
            body = f.read()
    except (OSError, ValueError):
        pass

    now = time.time()
    if now - last_try < min_interval:  # too soon to ask again: use what we have
        if body is not None and fetched_at and now - fetched_at <= STALE_OK:
            return body, fetched_at
        return None, None

    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            new = r.read().decode("utf-8", "replace")
        ET.fromstring(new)  # must be XML, or we keep the old copy
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        body, fetched_at = new, now
    except Exception:
        pass
    try:
        with open(meta, "w") as f:
            json.dump({"fetched_at": fetched_at, "last_try": now, "url": url}, f)
    except OSError:
        pass
    if body is not None and fetched_at and now - fetched_at <= STALE_OK:
        return body, fetched_at
    return None, None


def psk_query_url(grid: str) -> str:
    q = {
        "receiverCallsign": grid,
        "modify": "grid",
        "flowStartSeconds": f"-{WINDOW_S}",
        "rronly": "1",
        "noactive": "1",
        "appName": APP_NAME,
    }
    return PSK_URL + "?" + urllib.parse.urlencode(q)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def parse_pskreporter(xml_text: str, lat: float, lon: float) -> dict:
    """-> {"reports": n, "receivers": set, "bands": {band: {"senders": n,
    "max_km": km}}}.  Bands are the ones in swdial.bands."""
    root = ET.fromstring(xml_text)
    senders: dict[str, set] = {b: set() for b in BANDS}
    max_km: dict[str, float] = {b: 0.0 for b in BANDS}
    receivers, reports = set(), 0
    for rr in root.iter("receptionReport"):
        reports += 1
        receivers.add(rr.get("receiverCallsign", ""))
        try:
            hz = int(rr.get("frequency", "0"))
        except ValueError:
            continue
        for b, spec in BANDS.items():
            if spec["start"] <= hz <= spec["end"]:
                call = (rr.get("senderCallsign") or "").upper()
                if call:
                    senders[b].add(call)
                ll = locator_to_latlon(rr.get("senderLocator", ""))
                if ll:
                    max_km[b] = max(max_km[b], distance_km(lat, lon, *ll))
                break
    return {
        "reports": reports,
        "receivers": receivers,
        "bands": {b: {"senders": len(senders[b]), "max_km": round(max_km[b])} for b in BANDS},
    }


def _num(text: str | None) -> float | None:
    try:
        return float((text or "").strip())
    except ValueError:
        return None


def estimate_sfi_from_ssn(ssn: float) -> float:
    """Rough SFI from sunspot number (used when hamqsl reports SFI 0)."""
    return 63.7 + 0.728 * ssn + 0.00089 * ssn * ssn


def parse_hamqsl(xml_text: str) -> dict:
    """-> {"sfi": float|None, "sfi_estimated": bool, "k": float|None,
    "ssn": float|None, "updated": str, "calls": {("80m-40m","day"): "fair", ...}}"""
    root = ET.fromstring(xml_text)
    sd = root.find("solardata")
    if sd is None:
        sd = root
    sfi, k, ssn = _num(sd.findtext("solarflux")), _num(sd.findtext("kindex")), _num(sd.findtext("sunspots"))
    estimated = False
    if (sfi is None or sfi <= 0) and ssn is not None:
        sfi, estimated = round(estimate_sfi_from_ssn(ssn)), True
    elif sfi is not None and sfi <= 0:
        sfi = None
    calls = {}
    for b in sd.iter("band"):
        r = (b.text or "").strip().lower()
        if r in _ORDER:
            calls[(b.get("name", ""), b.get("time", ""))] = r
    return {"sfi": sfi, "sfi_estimated": estimated, "k": k, "ssn": ssn,
            "updated": (sd.findtext("updated") or "").strip(), "calls": calls}


# --------------------------------------------------------------------------
# Rating rules
# --------------------------------------------------------------------------

def rate_measured(senders: int) -> str:
    if senders >= GOOD_MIN:
        return GOOD
    if senders >= FAIR_MIN:
        return FAIR
    return POOR


def _step(rating: str, n: int) -> str:
    return _ORDER[max(0, min(2, _ORDER.index(rating) + n))]


def _blend(mine: str, theirs: str | None) -> str:
    """Heuristic vs N0NBH call: agree -> keep; one apart -> the lower;
    two apart -> fair."""
    if theirs is None or theirs == mine:
        return mine
    a, b = _ORDER.index(mine), _ORDER.index(theirs)
    return _ORDER[min(a, b)] if abs(a - b) == 1 else FAIR


_HAMQSL_GROUP = {"40m": "80m-40m", "20m": "30m-20m"}
_PHASE_WORDS = {"day": "Daytime here", "greyline": "Greyline here",
                "evening": "Evening here", "night": "Night here"}


def rate_model(band: str, phase: str, solar: dict | None) -> tuple[str, str]:
    """Heuristic rating from day/night + solar data.  -> (rating, reason)."""
    sfi = solar.get("sfi") if solar else None
    k = solar.get("k") if solar else None
    s = sfi if sfi is not None else 100.0  # assume middling if unknown
    dark = phase in ("night", "evening")

    if band == "20m":
        if phase == "day":
            r = GOOD if s >= 90 else FAIR
        elif phase == "greyline":
            r = FAIR
        else:
            r = FAIR if s >= 150 else POOR
    elif band == "40m":
        r = GOOD if (dark or phase == "greyline") else FAIR
    elif band == "31m":
        r = GOOD if (dark or phase == "greyline") else FAIR
    elif band == "25m":
        if phase in ("day", "greyline", "evening"):
            r = GOOD if s >= 80 else FAIR
        else:
            r = FAIR if s >= 120 else POOR
    else:
        r = FAIR

    if solar and band in _HAMQSL_GROUP:
        t = "day" if phase == "day" else "night"
        r = _blend(r, solar["calls"].get((_HAMQSL_GROUP[band], t)))

    bits = [_PHASE_WORDS[phase]]
    if sfi is not None:
        bits.append(f"SFI ~{sfi:.0f}" if solar.get("sfi_estimated") else f"SFI {sfi:.0f}")
    else:
        bits.append("no solar data")
    if k is not None:
        bits.append(f"K {k:.0f}")
        if k >= 5:
            r = _step(r, -1)
            bits.append("storm")
    return r, ", ".join(bits)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def band_conditions(lat: float, lon: float, now: datetime | None = None, *,
                    psk_xml: str | None = None, solar_xml: str | None = None,
                    offline: bool = False) -> dict:
    """band -> {"rating": good|fair|poor|unknown, "reason": str,
    "source": "measured"|"model"|"none"}.

    psk_xml / solar_xml let tests (or callers with their own fetch) supply
    feed text; otherwise feeds are fetched with caching.  offline=True skips
    the network entirely (model from sun position only)."""
    now = now or datetime.now(timezone.utc)
    grid = maidenhead(lat, lon)
    out: dict[str, dict] = {}

    solar = None
    if solar_xml is None and not offline:
        solar_xml, _ = _cached_fetch("hamqsl_solar.xml", HAMQSL_URL, HAMQSL_MIN_INTERVAL)
    if solar_xml:
        try:
            solar = parse_hamqsl(solar_xml)
        except ET.ParseError:
            solar = None

    psk = None
    if psk_xml is None and not offline:
        psk_xml, _ = _cached_fetch(f"psk_{grid}.xml", psk_query_url(grid), PSK_MIN_INTERVAL, timeout=45)
    if psk_xml:
        try:
            psk = parse_pskreporter(psk_xml, lat, lon)
        except ET.ParseError:
            psk = None
    # No reports at all in the grid = no listeners online, not a dead band.
    if psk is not None and psk["reports"] == 0:
        psk = None

    try:
        phase = sun_phase(lat, lon, now)
    except Exception:
        phase = None

    for band in BANDS:
        try:
            if psk is not None and band in MEASURED_BANDS:
                info = psk["bands"][band]
                n = info["senders"]
                reason = f"{n} station{'s' if n != 1 else ''} heard nearby in 15 min"
                if n and info["max_km"]:
                    reason += f", farthest {info['max_km']:,} km"
                out[band] = {"rating": rate_measured(n), "reason": reason, "source": "measured"}
            elif phase is not None:
                r, reason = rate_model(band, phase, solar)
                out[band] = {"rating": r, "reason": reason, "source": "model"}
            else:
                raise RuntimeError("no data")
        except Exception:
            out[band] = {"rating": UNKNOWN, "reason": "No data", "source": "none"}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m swdial.conditions",
                                 description="Rate each band GOOD/FAIR/POOR at a receiver.")
    ap.add_argument("--lat", type=float, default=50.74, help="receiver latitude (default Wessex KiwiSDR)")
    ap.add_argument("--lon", type=float, default=-2.64, help="receiver longitude")
    ap.add_argument("--offline", action="store_true", help="no network, sun position only")
    ap.add_argument("--json", action="store_true", help="print JSON instead of lines")
    a = ap.parse_args(argv)
    res = band_conditions(a.lat, a.lon, offline=a.offline)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    for band, r in res.items():
        print(f"{EMOJI[r['rating']]} {band:<4} {r['rating'].upper():<7} {r['reason']} ({r['source']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())


# --------------------------------------------------------------------------
# Individual spots (frequencies you can jump to). Reuses the same cached
# PSKReporter file as band_conditions, so no extra queries.
# --------------------------------------------------------------------------

def parse_spots(xml_text: str, lat: float, lon: float) -> dict:
    """-> band -> list of {call, hz, mode, km, snr, t}, one per sender (latest),
    newest first."""
    root = ET.fromstring(xml_text)
    best: dict[str, dict] = {b: {} for b in BANDS}
    for rr in root.iter("receptionReport"):
        try:
            hz = int(rr.get("frequency", "0"))
            t = int(rr.get("flowStartSeconds", "0"))
        except ValueError:
            continue
        call = (rr.get("senderCallsign") or "").upper()
        if not call:
            continue
        for b, spec in BANDS.items():
            if spec["start"] <= hz <= spec["end"]:
                ll = locator_to_latlon(rr.get("senderLocator", ""))
                km = round(distance_km(lat, lon, *ll)) if ll else None
                prev = best[b].get(call)
                if prev is None or t > prev["t"]:
                    best[b][call] = {"call": call, "hz": hz, "mode": rr.get("mode", ""),
                                     "km": km, "snr": _num(rr.get("sNR")), "t": t,
                                     "grid": (rr.get("senderLocator") or "")[:4]}
                break
    return {b: sorted(v.values(), key=lambda s: -s["t"]) for b, v in best.items()}


def heard_spots(lat: float, lon: float, *, psk_xml: str | None = None) -> dict:
    grid = maidenhead(lat, lon)
    if psk_xml is None:
        psk_xml, _ = _cached_fetch(f"psk_{grid}.xml", psk_query_url(grid), PSK_MIN_INTERVAL, timeout=45)
    if not psk_xml:
        return {b: [] for b in BANDS}
    try:
        return parse_spots(psk_xml, lat, lon)
    except ET.ParseError:
        return {b: [] for b in BANDS}
