"""Shortwave broadcast stations on air now, from the free EiBi schedule.

EiBi (eibispace.de) publishes a season CSV: A = late March to late October,
B = late October to late March. We fetch it at most once a day (cached).
"""
import time
from datetime import date, datetime, timedelta, timezone

from .conditions import _cached_fetch

LANG = {"E": "English", "F": "French", "S": "Spanish", "A": "Arabic", "C": "Chinese", "R": "Russian",
        "G": "German", "P": "Portuguese", "HI": "Hindi", "J": "Japanese", "K": "Korean", "I": "Italian",
        "T": "Thai", "TU": "Turkish", "FA": "Farsi", "VN": "Vietnamese", "IN": "Indonesian", "M": "Mandarin",
        "BE": "Bengali", "UR": "Urdu", "SWA": "Swahili", "HA": "Hausa", "TB": "Tibetan"}
DAYS = {"Mo": 1, "Tu": 2, "We": 3, "Th": 4, "Fr": 5, "Sa": 6, "Su": 7}


def _last_sunday(year: int, month: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def season(today: date | None = None) -> str:
    t = today or datetime.now(timezone.utc).date()
    yy = t.year % 100
    if _last_sunday(t.year, 3) <= t < _last_sunday(t.year, 10):
        return f"a{yy:02d}"
    return f"b{(yy if t.month >= 10 else yy - 1):02d}"


def _on_day(days: str, wd: int) -> bool:
    days = days.strip()
    if not days or days.startswith("irr") or days.startswith("alt"):
        return True
    if days.isdigit():
        return str(wd) in days
    if "-" in days and days[:2] in DAYS and days[-2:] in DAYS:
        a, b = DAYS[days[:2]], DAYS[days[-2:]]
        return a <= wd <= b if a <= b else wd >= a or wd <= b
    if days[:2] in DAYS:
        return any(DAYS.get(days[i:i + 2]) == wd for i in range(0, len(days), 2))
    return True


def _on_air(span: str, hhmm: int) -> bool:
    try:
        a, b = (int(x) for x in span.split("-"))
    except ValueError:
        return False
    if b == 2400:
        b = 2359
    return a <= hhmm < b if a <= b else hhmm >= a or hhmm < b


def parse(text: str, lo_hz: int, hi_hz: int, now: datetime | None = None) -> list:
    now = now or datetime.now(timezone.utc)
    hhmm, wd = now.hour * 100 + now.minute, now.isoweekday()
    out = {}
    for line in text.splitlines()[1:]:
        p = line.split(";")
        if len(p) < 8:
            continue
        try:
            hz = int(round(float(p[0]) * 1000))
        except ValueError:
            continue
        if not lo_hz <= hz <= hi_hz or not _on_air(p[1], hhmm) or not _on_day(p[2], wd):
            continue
        if any(w in p[4] for w in ("Fax", "RTTY", "Jammer", "Volmet")):
            continue
        key = (hz, p[4])
        lang = p[5].strip()
        out.setdefault(key, {"hz": hz, "station": p[4].strip(), "country": p[3].strip(),
                             "lang": LANG.get(lang, lang), "target": p[6].strip(), "time": p[1]})
    return sorted(out.values(), key=lambda s: s["hz"])


def _check(text: str):
    if not text.startswith("kHz:"):
        raise ValueError("not an EiBi CSV")


def stations_now(lo_hz: int, hi_hz: int) -> list:
    s = season()
    body, _ = _cached_fetch(f"eibi-{s}.csv", f"http://www.eibispace.de/dx/sked-{s}.csv",
                            min_interval=86400, timeout=30, validate=_check)
    return parse(body, lo_hz, hi_hz) if body else []


if __name__ == "__main__":
    print("season", season())
    for lo, hi in ((9_400_000, 9_900_000), (11_600_000, 12_100_000)):
        st = stations_now(lo, hi)
        print(lo, len(st))
        for x in st[:8]:
            print(f"  {x['hz']/1e3:>7.0f} kHz  {x['station']:<28} {x['lang']:<10} {x['target']}")
