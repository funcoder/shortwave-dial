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


# ----- operator name (online) -----
# QRZ.com or HamQTH when a login is saved in ACCOUNTS, else callook.info (US calls only, no login).
ACCOUNTS = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "shortwave-dial" / "accounts.ini"
QRZ_URL = "https://xmldata.qrz.com/xml/current/"
HAMQTH_URL = "https://www.hamqth.com/xml.php"
_US = re.compile(r"^(K|N|W|A[A-L])[A-Z]?[0-9][A-Z]{1,3}$")
_names, _sessions = {}, {}


def _get(url: str, params: dict, timeout: float) -> str:
    import urllib.parse
    import urllib.request
    from .conditions import USER_AGENT
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _xml(text: str) -> dict:
    """Flatten XML to {tag: text}, namespaces dropped (last one wins)."""
    import xml.etree.ElementTree as ET
    return {e.tag.rpartition("}")[2].lower(): (e.text or "").strip() for e in ET.fromstring(text).iter()}


def _tidy(name: str) -> str:
    """'WELLS, JOSHUA D' -> 'Joshua D Wells'; leave mixed case alone."""
    name = " ".join(name.split())
    if "," in name:  # 'LAST, FIRST' or 'LAST, JR., FIRST'
        parts = [p.strip() for p in name.split(",")]
        name = " ".join([parts[-1], parts[0], *parts[1:-1]])
    return name.title() if name.isupper() else name


def parse_qrz(text: str) -> dict | None:
    d = _xml(text)
    name = _tidy(f"{d.get('fname', '')} {d.get('name', '')}")
    if not name:
        return None
    town = d.get("addr2", "")
    return {"name": name, "qth": town, "grid": d.get("grid", ""), "source": "QRZ.com"}


def parse_hamqth(text: str) -> dict | None:
    d = _xml(text)
    name = _tidy(d.get("adr_name") or d.get("nick", ""))
    if not name:
        return None
    return {"name": name, "qth": d.get("qth", ""), "grid": d.get("grid", ""), "source": "HamQTH"}


def parse_callook(text: str) -> dict | None:
    import json
    d = json.loads(text)
    if d.get("status") != "VALID" or not d.get("name"):
        return None
    town = d.get("address", {}).get("line2", "").split(",")[0]
    return {"name": _tidy(d["name"]), "qth": town.title(), "grid": d.get("location", {}).get("gridsquare", ""),
            "source": "callook.info"}


def accounts(path_=None) -> dict:
    """{'qrz': (user, password), 'hamqth': (...)} from accounts.ini, if present."""
    import configparser
    cp = configparser.ConfigParser()
    cp.read(path_ or ACCOUNTS)
    return {s: (cp[s].get("user", ""), cp[s].get("password", ""))
            for s in ("qrz", "hamqth") if s in cp and cp[s].get("user") and cp[s].get("password")}


def _session_lookup(svc, call, user, pw, timeout):
    """Log in once per run (again if the session expired), then look up."""
    for _ in range(2):
        if svc not in _sessions:
            if svc == "qrz":
                d = _xml(_get(QRZ_URL, {"username": user, "password": pw, "agent": "ShortwaveDial"}, timeout))
                key = d.get("key")
            else:
                d = _xml(_get(HAMQTH_URL, {"u": user, "p": pw}, timeout))
                key = d.get("session_id")
            if not key:
                raise RuntimeError(f"{svc} login: {d.get('error', 'refused')}")
            _sessions[svc] = key
        if svc == "qrz":
            text = _get(QRZ_URL, {"s": _sessions[svc], "callsign": call}, timeout)
        else:
            text = _get(HAMQTH_URL, {"id": _sessions[svc], "callsign": call, "prg": "ShortwaveDial"}, timeout)
        err = _xml(text).get("error", "").lower()
        if "session" in err or "expired" in err or ("invalid" in err and "key" in err):
            _sessions.pop(svc, None)
            continue
        return (parse_qrz if svc == "qrz" else parse_hamqth)(text)
    return None


def operator(call: str, timeout: float = 8.0, accts=None) -> dict | None:
    """Name and town of a callsign holder, or None. Blocks: call from a thread."""
    call = call.upper().strip()
    base = max(call.split("/"), key=len)
    if base in _names:
        return _names[base]
    accts = accounts() if accts is None else accts
    r, errors = None, []
    for svc in ("qrz", "hamqth"):
        if svc in accts:
            try:
                r = _session_lookup(svc, base, *accts[svc], timeout)
                break
            except Exception as e:
                errors.append(str(e))
    if r is None and _US.match(base):
        r = parse_callook(_get(f"https://callook.info/{base}/json", {}, timeout))
    if r is None and errors:
        raise RuntimeError("; ".join(errors))
    _names[base] = r
    return r


def _f(tag, val):
    val = str(val)
    return f"<{tag}:{len(val)}>{val} " if val else ""


def log_entry(call, hz, band, mode, grid="", snr=None, note="", path_=LOG, when=None, rst="") -> str:
    """Append one SWL record (heard, not a two-way contact) to the ADIF log."""
    when = when or datetime.now(timezone.utc)
    path_.parent.mkdir(parents=True, exist_ok=True)
    new = not path_.exists()
    m = {"lsb": "SSB", "usb": "SSB", "am": "AM", "cw": "CW"}.get(mode.lower(), mode.upper())
    if mode.lower() == "usb" and note.startswith("FT8"):
        m = "FT8"
    rec = (_f("CALL", call.upper()) + _f("QSO_DATE", when.strftime("%Y%m%d")) + _f("TIME_ON", when.strftime("%H%M%S"))
           + _f("FREQ", f"{hz / 1e6:.6f}") + _f("BAND", band.upper() if band.endswith("m") else band) + _f("MODE", m)
           + _f("GRIDSQUARE", grid) + _f("RST_RCVD", rst or ("" if snr is None else f"{snr:+.0f}"))
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
