"""Every callsign we have received, kept between runs, for the world map.

Stored in $XDG_DATA_HOME/shortwave-dial/heard.json (~/.local/share), next to the
log. One entry per call: grid (if it ever sent one), bands, best SNR, first and
last heard (UTC seconds), how many times, and whether it is in the log.
Where a call is on the map comes from its grid, else its country (cty.dat).
"""
import json
import os
import time
from pathlib import Path

from . import lookup as L

PATH = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "shortwave-dial" / "heard.json"


class Heard:
    def __init__(self, path=PATH):
        self.path = path
        try:
            self.calls = json.loads(path.read_text())["calls"]
        except (OSError, ValueError, KeyError, TypeError):
            self.calls = {}

    def add(self, call, grid="", band="", snr=None, when=None):
        """Heard call once more. Returns True if it is new to the map."""
        call = call.upper().strip()
        when = when or time.time()
        e = self.calls.get(call)
        new = e is None
        if new:
            e = self.calls[call] = {"grid": "", "bands": [], "snr": None, "first": when, "last": when,
                                    "n": 0, "logged": False}
        if grid and (len(grid) >= len(e["grid"]) or grid[:4] != e["grid"][:4]):
            e["grid"] = grid.upper()
        if band and band not in e["bands"]:
            e["bands"].append(band)
        if snr is not None and (e["snr"] is None or snr > e["snr"]):
            e["snr"] = round(snr)
        e["first"], e["last"] = min(e["first"], when), max(e["last"], when)
        e["n"] += 1
        return new

    def mark_logged(self, call, grid="", band="", when=None):
        """In the log: on the map too, marked as logged (doesn't count as a hearing)."""
        call = call.upper().strip()
        if call not in self.calls:
            self.add(call, grid, band, when=when)
            self.calls[call]["n"] = 0
        elif grid and not self.calls[call]["grid"]:
            self.calls[call]["grid"] = grid.upper()
        self.calls[call]["logged"] = True

    def merge_log(self, records):
        """Back-fill from the ADIF log (read_log records), so old contacts show up."""
        from datetime import datetime, timezone
        for d in records:
            try:
                when = datetime.strptime(d.get("QSO_DATE", "") + d.get("TIME_ON", "")[:6].ljust(6, "0"),
                                         "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                when = None
            self.mark_logged(d["CALL"], d.get("GRIDSQUARE", ""), d.get("BAND", "").lower(), when)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"calls": self.calls}, separators=(",", ":")))
        tmp.replace(self.path)

    def since(self, t0=0):
        """[(call, entry)] heard (or logged) at or after t0, newest first."""
        return sorted(((c, e) for c, e in self.calls.items() if e["last"] >= t0), key=lambda ce: -ce[1]["last"])


def place(call, entry, cty=None):
    """(lat, lon, country, exact) for a call, or None if we can't tell yet.
    exact = located by its grid square, not just the middle of its country."""
    info = L.country(call, cty) if (cty or L._cty) else None
    name = info["country"] if info else ""
    g = L.grid_latlon(entry.get("grid", "")) if entry.get("grid") else None
    if g:
        return g[0], g[1], name, True
    if info:
        return info["lat"], info["lon"], name, False
    return None
