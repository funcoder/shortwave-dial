"""Callsign lookup (country from the free cty.dat prefix list) and an ADIF log.

Lookup is offline after one download a week: country, continent, CQ zone and
the beam heading / distance from the receiver. Grids heard in FT8/PSKReporter
are used for a more exact distance when we have them.
"""
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .conditions import _cached_fetch

CTY_URL = "https://www.country-files.com/cty/cty.dat"
LOG = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "shortwave-dial" / "log.adi"
_cty = None


def _check(text: str):
    if "Sov Mil Order of Malta" not in text:
        raise ValueError("not cty.dat")


def load_cty(text: str | None = None):
    """Return (exact_calls, prefixes) dicts -> country info."""
    global _cty
    if text is None and _cty:
        return _cty
    if text is None:
        text, _ = _cached_fetch("cty.dat", CTY_URL, min_interval=7 * 86400, timeout=30, validate=_check)
        text = text or ""
    exact, pref = {}, {}
    for rec in text.split(";"):
        if ":" not in rec:
            continue
        head, _, body = rec.rpartition(":")
        f = [x.strip() for x in head.split(":")]
        if len(f) < 8:
            continue
        info = {"country": f[0], "cq": f[1], "cont": f[3], "lat": float(f[4]), "lon": -float(f[5])}
        for tok in body.replace("\n", "").split(","):
            tok = re.sub(r"[\(\[<{~].*", "", tok.strip())
            if not tok:
                continue
            if tok.startswith("="):
                exact[tok[1:]] = info
            else:
                pref[tok] = info
    _cty = (exact, pref)
    return _cty


def country(call: str, cty=None):
    exact, pref = cty or load_cty()
    call = call.upper().strip()
    if call in exact:
        return exact[call]
    parts = call.split("/")
    base = max(parts, key=len) if len(parts) > 1 else call
    # a short prefix part like EA8/ or /P is usually the location
    for p in parts:
        if 1 <= len(p) <= 4 and p not in ("P", "M", "MM", "AM", "QRP") and any(c.isdigit() for c in p) or len(p) == 1 and p.isalpha() and p not in "PMA":
            base = p if len(p) > 1 else base
    for n in range(len(base), 0, -1):
        if base[:n] in pref:
            return pref[base[:n]]
    return None


def grid_latlon(g: str):
    g = g.strip().upper()
    if not re.fullmatch(r"[A-R]{2}[0-9]{2}([A-X]{2})?", g):
        return None
    lon = (ord(g[0]) - 65) * 20 - 180 + int(g[2]) * 2
    lat = (ord(g[1]) - 65) * 10 - 90 + int(g[3])
    if len(g) == 6:
        return lat + (ord(g[5]) - 65) / 24 + 1 / 48, lon + (ord(g[4]) - 65) / 12 + 1 / 24
    return lat + 0.5, lon + 1


def path(lat1, lon1, lat2, lon2):
    """(km, bearing degrees) great-circle from point 1 to point 2."""
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    d = math.acos(max(-1, min(1, math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl))))
    b = math.degrees(math.atan2(math.sin(dl) * math.cos(p2),
                                math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)))
    return round(d * 6371), round((b + 360) % 360)


def lookup(call: str, rx_lat: float, rx_lon: float, grid: str = "", cty=None) -> dict:
    info = country(call, cty)
    out = {"call": call.upper(), "country": "Unknown", "cont": "", "cq": "", "grid": grid}
    where = grid_latlon(grid) if grid else None
    if info:
        out.update(country=info["country"], cont=info["cont"], cq=info["cq"])
        where = where or (info["lat"], info["lon"])
    if where:
        out["km"], out["bearing"] = path(rx_lat, rx_lon, *where)
    return out


def _f(tag, val):
    val = str(val)
    return f"<{tag}:{len(val)}>{val} " if val else ""


def log_entry(call, hz, band, mode, grid="", snr=None, note="", path_=LOG, when=None) -> str:
    """Append one SWL record (heard, not a two-way contact) to the ADIF log."""
    when = when or datetime.now(timezone.utc)
    path_.parent.mkdir(parents=True, exist_ok=True)
    new = not path_.exists()
    m = {"lsb": "SSB", "usb": "SSB", "am": "AM", "cw": "CW"}.get(mode.lower(), mode.upper())
    if mode.lower() == "usb" and note.startswith("FT8"):
        m = "FT8"
    rec = (_f("CALL", call.upper()) + _f("QSO_DATE", when.strftime("%Y%m%d")) + _f("TIME_ON", when.strftime("%H%M%S"))
           + _f("FREQ", f"{hz / 1e6:.6f}") + _f("BAND", band.upper() if band.endswith("m") else band) + _f("MODE", m)
           + _f("GRIDSQUARE", grid) + _f("RST_RCVD", "" if snr is None else f"{snr:+.0f}")
           + _f("COMMENT", ("SWL " + note).strip()) + "<EOR>\n")
    with open(path_, "a", encoding="utf-8") as f:
        if new:
            f.write("Shortwave Dial SWL log\n<ADIF_VER:5>3.1.4 <PROGRAMID:13>ShortwaveDial <EOH>\n")
        f.write(rec)
    return rec


def read_log(path_=LOG, n=30) -> list:
    if not path_.exists():
        return []
    recs = []
    for r in path_.read_text(encoding="utf-8").split("<EOR>"):
        d = {k.upper(): v for k, _, v in re.findall(r"<(\w+):(\d+)>([^<]*?)\s*(?=<|$)", r)}
        if "CALL" in d:
            recs.append(d)
    return recs[-n:][::-1]


if __name__ == "__main__":
    import sys
    for c in sys.argv[1:] or ["G7MJF", "IE9/DJ4EL", "J79FJ", "W1AW/9", "PT7TAJ"]:
        print(lookup(c, 50.74, -2.64))
