"""Command-line front end (works without GTK).

  python -m swdial.cli tune 7074000 usb
  python -m swdial.cli band 31m
  python -m swdial.cli step +500
  python -m swdial.cli waybar        # JSON for a Waybar custom module
"""
import argparse
import json
import sys

from .bands import BANDS, format_mhz
from .rigctl import Rigctl, RigctlError


def main(argv=None):
    p = argparse.ArgumentParser(prog="swdial")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=6400)
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tune"); t.add_argument("hz", type=int); t.add_argument("mode", nargs="?")
    b = sub.add_parser("band"); b.add_argument("name", choices=BANDS)
    s = sub.add_parser("step"); s.add_argument("hz", type=int)
    sub.add_parser("status")
    sub.add_parser("waybar")
    a = p.parse_args(argv)

    try:
        with Rigctl(a.host, a.port) as rig:
            if a.cmd == "tune":
                if a.mode:
                    rig.set_mode(a.mode)
                rig.set_freq(a.hz)
            elif a.cmd == "band":
                preset = BANDS[a.name]
                rig.set_mode(preset["mode"])
                rig.set_freq(preset["default"])
            elif a.cmd == "step":
                rig.set_freq(rig.get_freq() + a.hz)
            hz, mode = rig.get_freq(), rig.get_mode()
    except (OSError, RigctlError) as e:
        if a.cmd == "waybar":
            print(json.dumps({"text": "📻 off", "class": "off", "tooltip": str(e)}))
            return 0
        print(f"swdial: {e}", file=sys.stderr)
        return 1

    if a.cmd == "waybar":
        print(json.dumps({"text": f"📻 {format_mhz(hz)} {mode}", "class": "on",
                          "tooltip": f"KiwiSDR via kiwiclientd :{a.port}"}))
    else:
        print(f"{format_mhz(hz)} MHz {mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
