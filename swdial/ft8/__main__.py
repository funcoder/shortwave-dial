"""python -m swdial.ft8 file.wav [--base-hz 7074000] [--start HHMMSS] [--offset S | --auto-offset]

Prints: HHMMSS  snr_db  dt_s  audio_hz  message
"""
import argparse
import sys
import time

from .decode import decode_wav


def _hms_to_s(s: str) -> float:
    s = s.replace(":", "")
    return int(s[0:2]) * 3600 + int(s[2:4]) * 60 + float(s[4:] or 0)


def _fmt(t: float) -> str:
    t = int(round(t)) % 86400
    return f"{t // 3600:02d}{(t // 60) % 60:02d}{t % 60:02d}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m swdial.ft8", description=__doc__.splitlines()[0])
    ap.add_argument("wav")
    ap.add_argument("--base-hz", type=float, default=None,
                    help="dial frequency (USB); adds an RF column, e.g. 7074000")
    ap.add_argument("--start", default=None, help="UTC time of the first sample, HHMMSS[.f]")
    ap.add_argument("--offset", type=float, default=None,
                    help="seconds the first sample is past a 15 s slot boundary")
    ap.add_argument("--auto-offset", action="store_true",
                    help="unaligned recording: try several offsets, keep the best")
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--timing", action="store_true", help="print decode time to stderr")
    a = ap.parse_args(argv)

    start_s = _hms_to_s(a.start) if a.start else None
    offset = a.offset
    if offset is None:
        offset = (start_s % 15.0) if start_s is not None else 0.0
    t0 = time.time()
    if a.auto_offset:
        best = None
        for off in (0.0, 3.0, 6.0, 9.0, 12.0):
            r = decode_wav(a.wav, start_offset_s=off, passes=a.passes)
            if best is None or len(r) > len(best[1]):
                best = (off, r)
        offset, res = best
    else:
        res = decode_wav(a.wav, start_offset_s=offset, passes=a.passes)
    elapsed = time.time() - t0

    for r in sorted(res, key=lambda r: (r["slot"], r["freq_hz"])):
        if start_s is not None:
            stamp = _fmt(start_s - offset + r["slot"])
        else:
            stamp = _fmt(r["slot"])
        line = f"{stamp}  {r['snr']:+4d}  {r['dt']:+5.1f}  {r['freq_hz']:6.0f}  {r['msg']}"
        if a.base_hz:
            line += f"    ({(a.base_hz + r['freq_hz']) / 1e3:.3f} kHz)"
        print(line)
    if a.timing:
        print(f"# {len(res)} decodes in {elapsed:.2f} s (offset {offset:g} s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
