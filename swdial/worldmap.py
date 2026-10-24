"""World map geometry for the map tab: land outlines and a flat (lat/lon) projection.

Land is Natural Earth 110 m (public domain), outer rings only, in data/land.json.
Antarctica and the far north are cut off; nobody we hear lives there.
"""
import json
import math
from pathlib import Path

LAT_TOP, LAT_BOTTOM = 84.0, -58.0
# name: (west, east, south, north). Zoomed views are squashed sideways by cos(latitude),
# so Europe keeps its familiar shape; the world view stays a plain 2:1-ish strip.
VIEWS = {"world": (-180.0, 180.0, LAT_BOTTOM, LAT_TOP), "europe": (-25.0, 45.0, 34.0, 71.0)}
_rings = None


def aspect(view="world"):
    """Width / height of the map for a view."""
    w, e, s, n = VIEWS[view]
    squash = 1.0 if view == "world" else math.cos(math.radians((s + n) / 2))
    return (e - w) * squash / (n - s)


def land_rings():
    """[[(lon, lat), ...], ...] outer coastlines."""
    global _rings
    if _rings is None:
        data = json.loads((Path(__file__).parent / "data" / "land.json").read_text())
        _rings = [[tuple(p) for p in r] for r in data["rings"]]
    return _rings


def fit(w, h, view="world"):
    """(x0, y0, map_w, map_h, view): the biggest map of the right shape centred in w x h."""
    a = aspect(view)
    mw, mh = (w, w / a) if w / h <= a else (h * a, h)
    return (w - mw) / 2, (h - mh) / 2, mw, mh, view


def project(lat, lon, box):
    """lat/lon -> x, y inside box (from fit). World view: off the top or bottom is clamped
    to the edge. Zoomed views: points outside land outside the box (see inside)."""
    x0, y0, mw, mh, view = box
    w, e, s, n = VIEWS[view]
    if view == "world":
        lat = max(s, min(n, lat))
    return x0 + (lon - w) / (e - w) * mw, y0 + (n - lat) / (n - s) * mh


def inside(lat, lon, view):
    w, e, s, n = VIEWS[view]
    return view == "world" or (w <= lon <= e and s <= lat <= n)


def nearest(points, x, y, radius=14):
    """Index of the point (x, y, ...) closest to x, y within radius pixels, or None."""
    best, bd = None, radius ** 2
    for i, p in enumerate(points):
        d = (p[0] - x) ** 2 + (p[1] - y) ** 2
        if d <= bd:
            best, bd = i, d
    return best
